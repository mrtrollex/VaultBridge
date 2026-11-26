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
GIT_READ_COMMAND = ("git", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false")
GIT_SOURCE_OVERRIDES = frozenset({
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_REPLACE_REF_BASE",
    "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE", "GIT_ATTR_SOURCE", "GIT_ATTR_NOSYSTEM",
    "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
})


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


def _git(
    root: Path, *args: str, input: bytes | None = None, allowed: tuple[int, ...] = (0,),
    env: dict[str, str] | None = None,
) -> bytes:
    result = subprocess.run(
        [*GIT_READ_COMMAND, *args], cwd=root, input=input, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    if result.returncode not in allowed:
        result.check_returncode()
    return result.stdout


def require_checkout_environment() -> None:
    if GIT_SOURCE_OVERRIDES.intersection(os.environ):
        raise ValueError("Git environment overrides the checkout's source or index")


def portable_path_parts(path: str) -> tuple[str, ...]:
    parts = tuple(path.split("/"))
    if any(not part or part.endswith((" ", ".")) for part in parts) or any(
        ord(char) < 32 or ord(char) == 127 or char in "\\:" for char in path
    ):
        raise ValueError("invalid portable repository-relative path")
    return parts


def visible_index_paths(output: bytes) -> set[bytes]:
    """Return the inventory only if every index entry is fully visible."""
    paths = set()
    if output:
        if not output.endswith(b"\0"):
            raise ValueError("incomplete Git index visibility output")
        for entry in output[:-1].split(b"\0"):
            # -v marks assume-unchanged entries lowercase and skip-worktree
            # entries S. H is the ordinary, tracked, non-conflicted entry.
            if not entry.startswith(b"H "):
                raise ValueError("Git index contains hidden, unresolved, or unrecognized entries")
            path = entry[2:]
            portable_path_parts(os.fsdecode(path))
            if path in paths:
                raise ValueError("duplicate entry in Git index visibility output")
            paths.add(path)
    return paths


def _nul_records(output: bytes) -> list[bytes]:
    if not output:
        return []
    if not output.endswith(b"\0"):
        raise ValueError("incomplete Git checkout inspection output")
    return output[:-1].split(b"\0")


def checkout_eol_settings(root: Path) -> tuple[bytes, bytes]:
    output = _git(
        root, "config", "--null", "--get-regexp", r"^core\.(autocrlf|eol)$", allowed=(0, 1),
    )
    settings = {b"core.autocrlf": b"false", b"core.eol": b"native"}
    for record in _nul_records(output):
        key, _, value = record.partition(b"\n")
        if key not in settings:
            raise ValueError("unrecognized Git checkout configuration")
        settings[key] = value.lower()
    autocrlf, eol = settings[b"core.autocrlf"], settings[b"core.eol"]
    if autocrlf != b"input":
        try:
            autocrlf = _git(root, "config", "--type=bool", "--default=false", "--get", "core.autocrlf").strip()
        except subprocess.CalledProcessError as error:
            raise ValueError("unsupported Git line-ending configuration") from error
    if autocrlf not in {b"false", b"true", b"input"} or eol not in {b"lf", b"crlf", b"native"}:
        raise ValueError("unsupported Git line-ending configuration")
    return autocrlf, eol


def _require_indexed_attribute_files(root: Path, paths: set[bytes]) -> None:
    directories: set[tuple[bytes, ...]] = {()}
    for path in paths:
        parts = path.split(b"/")
        directories.update(tuple(parts[:depth]) for depth in range(1, len(parts)))
    for directory in sorted(directories, key=len):
        parent = root.joinpath(*(os.fsdecode(part) for part in directory))
        try:
            state = parent.lstat()
            if not stat.S_ISDIR(state.st_mode) or (
                getattr(state, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("checkout attribute ancestry is redirected or unavailable")
            attributes = (parent / ".gitattributes").lstat()
        except FileNotFoundError:
            continue
        _file_stamp(attributes)
        if b"/".join((*directory, b".gitattributes")) not in paths:
            raise ValueError("unindexed worktree .gitattributes can override checkout attributes")


def _checkout_attribute_map(output: bytes, paths: set[bytes]) -> dict[tuple[bytes, bytes], bytes]:
    fields = _nul_records(output)
    if len(fields) % 3:
        raise ValueError("incomplete Git checkout attribute inspection")
    conversion = {}
    for path, attr, value in zip(fields[::3], fields[1::3], fields[2::3], strict=True):
        if path not in paths or not attr:
            raise ValueError("mismatched Git checkout attribute inspection")
        if attr in {b"filter", b"ident", b"working-tree-encoding"}:
            # --all omits unspecified attributes. Named values such as a
            # filter called "unset" cannot masquerade as disabled attributes.
            raise ValueError("unsupported Git checkout transformation")
        if attr in {b"text", b"eol", b"crlf"}:
            if (path, attr) in conversion:
                raise ValueError("duplicate Git checkout attribute inspection result")
            conversion[path, attr] = value
    return conversion


def _git_repository_attributes(root: Path, absent_attributes: Path, *args: str, input: bytes | None = None) -> bytes:
    return _git(
        root, "-c", f"core.attributesFile={absent_attributes.as_posix()}", *args,
        input=input, env={**os.environ, "GIT_ATTR_NOSYSTEM": "1"},
    )


def require_supported_checkout_attributes(root: Path, paths: set[bytes]) -> Path:
    """Screen attribute sources before a normalized Git read can run a driver."""
    _require_indexed_attribute_files(root, paths)
    private_attributes = Path(os.fsdecode(_git(
        root, "rev-parse", "--path-format=absolute", "--git-path", "info/attributes",
    ).rstrip(b"\r\n")))
    if not private_attributes.is_absolute():
        raise ValueError("could not locate Git's private checkout attributes")
    try:
        private_attributes.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("checkout inspection does not support private Git attribute overrides")

    args = ("check-attr", "-z", "--stdin", "--all")
    inputs = b"".join(path + b"\0" for path in sorted(paths))
    effective = _checkout_attribute_map(_git(root, *args, input=inputs), paths)
    repository = _checkout_attribute_map(
        _git_repository_attributes(root, private_attributes, *args, input=inputs), paths,
    )
    if effective != repository:
        raise ValueError("global or system Git attributes affect checkout conversion")
    return private_attributes


def _checkout_adds_crlf(index_eol: bytes, attr: bytes, settings: tuple[bytes, bytes]) -> bool:
    autocrlf, core_eol = settings
    if attr not in {
        b"", b"-text", b"text", b"text eol=lf", b"text eol=crlf",
        b"text=auto", b"text=auto eol=lf", b"text=auto eol=crlf",
    } or index_eol not in {b"lf", b"crlf", b"mixed", b"none", b"-text"}:
        raise ValueError("unrecognized Git checkout line-ending metadata")
    if attr == b"-text" or attr.endswith(b"eol=lf") or index_eol in {b"none", b"crlf"}:
        return False
    if not attr:
        want_crlf = autocrlf == b"true"
    elif attr.endswith(b"eol=crlf"):
        want_crlf = True
    else:
        want_crlf = autocrlf == b"true" or (
            autocrlf == b"false" and (core_eol == b"crlf" or core_eol == b"native" and os.linesep == "\r\n")
        )
    if not want_crlf:
        return False
    if index_eol == b"lf":
        return True
    if not attr or attr.startswith(b"text=auto"):
        # Git's automatic mode leaves binary blobs and blobs with CR alone.
        return False
    raise ValueError("ambiguous line endings in a forced-CRLF Git blob")


def checkout_byte_changes(
    root: Path, index_paths: set[bytes], visible_changes: set[str], settings: tuple[bytes, bytes],
    absent_attributes: Path,
) -> set[str]:
    """Identify working-tree bytes that differ from a supported index checkout."""
    output = _git_repository_attributes(
        root, absent_attributes, "ls-files", "--cached", "--stage", "--eol", "-z",
    )
    paths = set()
    changed = set()
    entry_pattern = rb"(100644|100755) ([0-9a-f]{40}|[0-9a-f]{64}) 0"
    eol_pattern = rb"i/([^ ]+) +w/[^ ]* +attr/(.*)"
    with repository_reader(root) as reader:
        for record in _nul_records(output):
            header, _, remainder = record.partition(b"\t")
            eol_field, _, raw_path = remainder.partition(b"\t")
            entry = re.fullmatch(entry_pattern, header)
            eol = re.fullmatch(eol_pattern, eol_field)
            if entry is None or eol is None or raw_path not in index_paths or raw_path in paths:
                raise ValueError("incomplete or unsupported Git checkout index entry")
            paths.add(raw_path)
            path = os.fsdecode(raw_path)
            if path in visible_changes:
                continue
            adds_crlf = _checkout_adds_crlf(eol[1], eol[2].rstrip(), settings)
            try:
                with reader(portable_path_parts(path)) as stream:
                    stamp = _file_stamp(os.fstat(stream.fileno()))
                    raw = stream.read()
                    if stamp != _file_stamp(os.fstat(stream.fileno())):
                        raise OSError("tracked input changed during checkout-byte inspection")
            except FileNotFoundError:
                changed.add(path)
                continue
            content = raw.replace(b"\r\n", b"\n") if adds_crlf else raw
            if adds_crlf and (b"\r" in content or content.replace(b"\n", b"\r\n") != raw):
                changed.add(path)
                continue
            oid = entry[2]
            digest = hashlib.new("sha1" if len(oid) == 40 else "sha256", usedforsecurity=False)
            digest.update(f"blob {len(content)}\0".encode("ascii"))
            digest.update(content)
            if digest.hexdigest().encode("ascii") != oid:
                changed.add(path)
    if paths != index_paths:
        raise ValueError("Git index inventory changed during checkout inspection")
    return changed


def _index_paths(root: Path) -> set[bytes]:
    return visible_index_paths(_git(root, "ls-files", "--cached", "-v", "-z"))


def _tracked_paths(root: Path, head: str, indexed: set[bytes]) -> tuple[bytes, ...]:
    # HEAD also covers a staged removal whose working copy still exists.
    committed = _git(root, "ls-tree", "-r", "--name-only", "-z", head)
    return tuple(sorted((indexed | set(committed.split(b"\0"))) - {b""}))


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


def _raw_tracked_digest(root: Path, head: str, *, indexed: set[bytes] | None = None) -> str:
    names = _tracked_paths(root, head, _index_paths(root) if indexed is None else indexed)
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
    if after != before or names != _tracked_paths(root, head, _index_paths(root)):
        raise OSError("tracked validation inputs changed during fingerprinting")
    return digest.hexdigest()


def source_identity(root: Path) -> dict[str, str] | None:
    """Identify the Git-visible delta and the exact bytes of all tracked inputs."""
    try:
        require_checkout_environment()
        root = root.resolve()
        indexed = _index_paths(root)
        require_supported_checkout_attributes(root, indexed)
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
            "raw_tracked_sha256": _raw_tracked_digest(root, head, indexed=indexed),
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
