"""Private local records for an agent_check execution."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

if __package__:
    from .agent_evidence_io import repository_reader
else:
    from agent_evidence_io import repository_reader

REPORT_PATH = Path(".agent/validation-results.json")
SCHEMA_VERSION = 3


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate JSON field")
    return fields


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _parse_json(raw: bytes) -> dict:
    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def valid_timing(record: dict) -> bool:
    try:
        return (
            all(
                datetime.fromisoformat(record[field]).utcoffset() == timedelta()
                for field in ("started_at", "ended_at")
            )
            and type(record["elapsed_ns"]) is int
            and record["elapsed_ns"] >= 0
        )
    except (KeyError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Clock:
    utc: Callable[[], datetime] = lambda: datetime.now(UTC)
    monotonic_ns: Callable[[], int] = time.monotonic_ns

    def stamp(self) -> str:
        return self.utc().isoformat(timespec="microseconds")


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


def portable_path_parts(path: str) -> tuple[str, ...]:
    parts = tuple(path.split("/"))
    if any(not part or part.endswith((" ", ".")) for part in parts) or any(
        ord(char) < 32 or ord(char) == 127 or char in "\\:" for char in path
    ):
        raise ValueError("invalid portable repository-relative path")
    return parts


def _tracked_paths(root: Path, head: str) -> tuple[bytes, ...]:
    # HEAD also covers a staged removal whose working copy still exists.
    indexed = _git(root, "ls-files", "--cached", "-z")
    committed = _git(root, "ls-tree", "-r", "--name-only", "-z", head)
    return tuple(sorted((set(indexed.split(b"\0")) | set(committed.split(b"\0"))) - {b""}))


def _file_stamp(stat_result: os.stat_result) -> tuple[int, ...]:
    if not stat.S_ISREG(stat_result.st_mode) or (
        getattr(stat_result, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise OSError("tracked validation input is not a regular file")
    return (
        stat_result.st_dev, stat_result.st_ino, stat_result.st_size, stat_result.st_mtime_ns,
        stat_result.st_mode, stat_result.st_ctime_ns,
    )


def _path_stamp(path: Path) -> tuple[int, ...] | None:
    try:
        return _file_stamp(path.lstat())
    except FileNotFoundError:
        return None


def _raw_tracked_digest(root: Path, head: str) -> str:
    names = _tracked_paths(root, head)
    inputs = [(name, portable_path_parts(os.fsdecode(name))) for name in names]
    before = [_path_stamp(root.joinpath(*parts)) for _, parts in inputs]
    digest = hashlib.sha256(b"vaultbridge/raw-tracked/v1\0")
    with repository_reader(root) as reader:
        for (name, parts), expected in zip(inputs, before, strict=True):
            digest.update(name + b"\0")
            try:
                with reader(parts) as stream:
                    opened = _file_stamp(os.fstat(stream.fileno()))
                    # Windows can report different mode/ctime details by name
                    # and by descriptor. Compare those within each API.
                    if expected is None or opened[:4] != expected[:4]:
                        raise OSError("tracked validation input changed during fingerprinting")
                    content = hashlib.file_digest(stream, "sha256").digest()
                    if _file_stamp(os.fstat(stream.fileno())) != opened:
                        raise OSError("tracked validation input changed during fingerprinting")
            except FileNotFoundError:
                if expected is not None:
                    raise
                digest.update(b"M")
            else:
                digest.update(b"F" + content)
    after = [_path_stamp(root.joinpath(*parts)) for _, parts in inputs]
    if after != before or names != _tracked_paths(root, head):
        raise OSError("tracked validation inputs changed during fingerprinting")
    return digest.hexdigest()


def source_identity(root: Path) -> dict[str, str] | None:
    """Identify the Git-visible delta and the exact bytes of all tracked inputs."""
    try:
        root = root.resolve()
        head = _git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
        status = _git(
            root, "status", "--porcelain=v1", "-z", "--no-renames",
            "--untracked-files=all", "--ignore-submodules=none",
        )
        index_delta = _git(
            root, "diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames", head,
        )
        worktree_delta = _git(root, "diff", "--raw", "-z", "--no-abbrev", "--no-renames")
        track_mode = _git(root, "config", "--type=bool", "--default=false", "--get", "core.filemode").strip() == b"true"
        digest = hashlib.sha256()
        digest.update(hashlib.sha256(status).digest())
        digest.update(hashlib.sha256(index_delta).digest())
        digest.update(hashlib.sha256(worktree_delta).digest())
        for entry in sorted(filter(None, status.split(b"\0"))):
            path = root / os.fsdecode(entry[3:])
            if path.parent.resolve() != path.parent:
                return None
            if path.is_symlink():
                digest.update(b"L" + hashlib.sha256(os.fsencode(os.readlink(path))).digest())
            elif path.is_file():
                with path.open("rb") as stream:
                    executable = track_mode and bool(os.fstat(stream.fileno()).st_mode & 0o111)
                    digest.update(b"F" + bytes((executable,)) + hashlib.file_digest(stream, "sha256").digest())
            elif path.exists():
                # For example, a dirty nested repository. A partial identity is misleading.
                return None
            else:
                digest.update(b"D")
        return {
            "head": head,
            "git_visible_delta_sha256": digest.hexdigest(),
            "raw_tracked_sha256": _raw_tracked_digest(root, head),
        }
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError):
        return None


def environment_identity() -> dict:
    packages = {}
    for package in ("ruff", "pytest", "playwright", "pytest-playwright"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    system = platform.system()
    machine = platform.machine().lower()
    return {
        "system": system,
        "machine": machine if machine in {"amd64", "x86_64", "x86", "i386", "i686", "arm64", "aarch64"} else None,
        "wsl": system == "Linux" and "microsoft" in platform.release().lower(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "tool_versions": packages,
    }


def report_path(root: Path) -> Path:
    path = root.resolve() / REPORT_PATH
    if path.parent.resolve() != path.parent:
        raise OSError("validation report directory is redirected")
    return path


def write_report(root: Path, report: dict) -> None:
    path = report_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=".validation-results-", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_report(root: Path, run_id: str) -> dict | None:
    try:
        path = report_path(root)
        if path.resolve() != path:
            return None
        report = _parse_json(path.read_bytes())
    except (OSError, RuntimeError, ValueError):
        return None
    if (
        type(report.get("schema_version")) is int
        and report["schema_version"] == SCHEMA_VERSION
        and report.get("run_id") == run_id
    ):
        return report
    return None


@dataclass(frozen=True)
class FocusedRecord:
    name: str
    sha256: str
    summary: str


def focused_records(
    root: Path, names: list[str], source: dict, *, expected_digests: list[str] | None = None,
) -> tuple[FocusedRecord, ...]:
    if expected_digests is not None and len(expected_digests) != len(names):
        raise ValueError("each focused result requires its approved SHA-256")
    records = []
    for index, name in enumerate(names):
        if not re.fullmatch(r"\.agent/focused-(windows|wsl)-[0-9T]+\.json", name):
            raise ValueError("expected a local focused Windows or WSL results path")
        path = report_path(root).parent / Path(name).name
        if path.resolve() != path:
            raise ValueError("focused results path is redirected")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_digests is not None and digest != expected_digests[index]:
            raise ValueError("focused record SHA-256 does not match the approved record")
        report = _parse_json(raw)
        command = report.get("command")
        counts = report.get("counts")
        environment = report.get("environment")
        windows = name.startswith(".agent/focused-windows-")
        if (
            type(report.get("schema_version")) is not int
            or report["schema_version"] != 1
            or not valid_timing(report)
            or report.get("source_before") != source
            or report.get("source_after") != source
            or type(report.get("exit_code")) is not int
            or report["exit_code"] != 0
            or not isinstance(environment, dict)
            or environment.get("system") != ("Windows" if windows else "Linux")
            or environment.get("wsl") is not (not windows)
            or not isinstance(command, list)
            or command[:6] != ["python", "-m", "pytest", "-o", "addopts=", "-q"]
            or len(command) < 7
            or not all(isinstance(arg, str) and re.fullmatch(r"tests/test_agent_[a-z_]+\.py", arg)
                       for arg in command[6:])
            or not isinstance(counts, dict)
            or set(counts) != {"passed", "failed", "skipped"}
            or not all(type(count) is int and count >= 0 for count in counts.values())
            or counts["passed"] == 0
            or counts["failed"] != 0
        ):
            raise ValueError("focused results do not show a successful run on the validated source")
        summary = (
            f"- `{name}` ({'Windows' if windows else 'WSL/Linux'}, {report['started_at']} to "
            f"{report['ended_at']}, {report['elapsed_ns'] / 1e9:.3f}s)\n"
            f"  - `{' '.join(command)}`\n"
            f"  - {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped\n"
            f"  - Record SHA-256: `{digest}`"
        )
        records.append(FocusedRecord(name, digest, summary))
    return tuple(records)
