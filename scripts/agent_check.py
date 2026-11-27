#!/usr/bin/env python3
"""Select and run VaultBridge verification for the current change.

Exit codes are stable: 0 means all selected automated checks passed (a separately
reported manual gate may still remain), 1 means a check failed, 2 means discovery
or invocation failed, and 3 means a required check was unavailable. Dry-run exits
0 after selection and never runs verification commands.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID, uuid4

if __package__:
    from . import agent_validation
else:
    import agent_validation

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMAGE_TAG = "vaultbridge:agent-check"
AREAS = ("docs", "python", "semantic", "api", "ui", "mcp", "docker", "truenas/deployment")
TRUSTED_BASE_REFS = ("refs/remotes/origin/main", "refs/heads/main")

WORKFLOW_TESTS = (
    "tests/test_agent_task.py",
    "tests/test_agent_check.py",
    "tests/test_agent_finish.py",
    "tests/test_agent_review.py",
    "tests/test_agent_validation.py",
    "tests/test_agent_evidence.py",
)
WORKFLOW_PYTHON_PATHS = frozenset({
    "scripts/agent_task.py",
    "scripts/agent_check.py",
    "scripts/agent_finish.py",
    "scripts/agent_review.py",
    "scripts/agent_validation.py",
    "scripts/agent_evidence.py",
    "scripts/agent_evidence_io.py",
    *WORKFLOW_TESTS,
})
WORKFLOW_PATHS = WORKFLOW_PYTHON_PATHS | {"docs/CODEX_PLAYBOOK.md"}
PYTEST_PARALLEL_ARGS = ("-n", "4", "--dist", "load", "--max-worker-restart", "0", "-p", "scripts.agent_pytest")
XDIST_VERSION = "3.8.0"

PASS = "PASS"
FAIL = "FAIL"
NOT_REQUIRED = "SKIP / NOT REQUIRED"
UNAVAILABLE = "REQUIRED BUT UNAVAILABLE"
MANUAL = "MANUAL / AGENT VERIFICATION REQUIRED"
TRUENAS_PATHS = {
    ".env.truenas.example",
    "README_TRUENAS.md",
    "compose.truenas.yml",
    "make-bundle.ps1",
    "truenas-install.yml",
}
TRUENAS_PREFIX = "ix-dev/community/vaultbridge/"
UI_BACKEND_PATHS = {
    "app/api/dependencies.py",
    "app/api/health.py",
    "app/api/notes.py",
    "app/api/search.py",
    "app/core/http_security.py",
    "app/main.py",
}
TRUENAS_MANUAL = "TrueNAS deployment/package validation"
ACTION_OPENAPI_MANUAL = (
    "ChatGPT Action/OpenAPI operation IDs must be checked against actual FastAPI endpoints"
)
PLAYWRIGHT_PACKAGE_UNAVAILABLE = (
    "Playwright Python package unavailable; install development dependencies from requirements-dev.txt"
)
PLAYWRIGHT_CHROMIUM_UNAVAILABLE = (
    "Playwright Chromium browser unavailable; run: python -m playwright install chromium"
)


class DiscoveryError(RuntimeError):
    """Changed files could not be determined safely."""


@dataclass(frozen=True)
class Discovery:
    files: tuple[str, ...]
    base: str
    base_ref: str | None = None
    merge_base: str | None = None
    base_commit: str | None = None
    workflow_only: bool = False
    raw_changed_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    needs_docker: bool = False
    needs_compose: bool = False
    needs_playwright: bool = False
    needs_chromium: bool = False
    needs_xdist: bool = False


@dataclass(frozen=True)
class CheckResult:
    check: Check
    status: str
    detail: str
    started_at: str
    ended_at: str
    elapsed_ns: int
    exit_code: int | None
    launch_attempted: bool


CommandRunner = Callable[[Check], int]
AvailabilityChecker = Callable[[Check], str | None]


def _git(root: Path, *args: str, check: bool = True) -> bytes:
    try:
        completed = subprocess.run(
            [*agent_validation.GIT_READ_COMMAND, *args],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise DiscoveryError("Git could not be launched during change discovery") from error
    if check and completed.returncode != 0:
        raise DiscoveryError(f"git {args[0]} failed during change discovery")
    return completed.stdout if completed.returncode == 0 else b""


def _git_ref_exists(root: Path, ref: str) -> bool:
    return bool(_git(
        root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}", check=False,
    ))


def choose_base_ref(
    root: Path,
    requested: str | None,
    *,
    managed: bool = False,
    environment: Mapping[str, str] = os.environ,
) -> tuple[str, bool]:
    if requested is not None:
        if managed:
            raise DiscoveryError("managed validation requires the trusted comparison base")
        if not _git_ref_exists(root, requested):
            raise DiscoveryError("the requested base does not resolve to a commit")
        return requested, False

    if not managed and environment.get("GITHUB_ACTIONS") == "true":
        github_base = environment.get("GITHUB_BASE_REF", "").strip()
        if github_base:
            for candidate in (f"refs/remotes/origin/{github_base}", f"refs/heads/{github_base}"):
                if _git_ref_exists(root, candidate):
                    return candidate, False

    for candidate in TRUSTED_BASE_REFS:
        if _git_ref_exists(root, candidate):
            return candidate, True
    if managed:
        raise DiscoveryError("could not determine the trusted main comparison base")

    # Retain alternate default branches for standalone comparisons.
    candidates: list[str] = []
    remote_head = _git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if remote_head:
        candidates.append(remote_head.decode("utf-8", "replace").strip())

    candidates.extend(("origin/master", "master", "origin/trunk", "trunk"))
    for candidate in dict.fromkeys(candidates):
        if candidate and _git_ref_exists(root, candidate):
            return candidate, False
    raise DiscoveryError("could not determine a base reference; pass --base explicitly")


def _nul_paths(output: bytes) -> set[str]:
    return {_relative_path(os.fsdecode(item)) for item in _nul_fields(output)}


def _nul_fields(output: bytes) -> list[bytes]:
    if not output:
        return []
    fields = output.split(b"\0")
    if fields[-1] or not all(fields[:-1]):
        raise DiscoveryError("invalid NUL-delimited Git discovery output")
    return fields[:-1]


def _diff_paths(output: bytes) -> tuple[set[str], bool]:
    files: set[str] = set()
    unambiguous = True
    fields = iter(_nul_fields(output))
    for status in fields:
        if status in {b"M", b"D", b"A", b"T", b"U"}:
            path_count = 1
        elif status in {b"R100", b"C100"}:
            path_count = 2
        else:
            raise DiscoveryError("unrecognized Git change status")
        for _ in range(path_count):
            path = next(fields, None)
            if path is None:
                raise DiscoveryError("incomplete Git change record")
            files.add(_relative_path(os.fsdecode(path)))
        # A copy match can have multiple identical sources. Additions,
        # type changes and unmerged entries also need the broader checks.
        unambiguous &= status not in {b"A", b"C100", b"T", b"U"}
    return files, unambiguous


def _workflow_path_set(paths: Sequence[str] | set[str]) -> bool:
    return WORKFLOW_PATHS.issuperset(paths) and not WORKFLOW_PYTHON_PATHS.isdisjoint(paths)


def _relative_path(path: str) -> str:
    try:
        agent_validation.portable_path_parts(path)
    except ValueError as error:
        raise DiscoveryError("invalid repository-relative changed file") from error
    return path


def _tracked_supplied_paths(root: Path) -> set[str]:
    args = ("ls-files", "--cached", "-z")
    try:
        output = _git(root, *args)
    except DiscoveryError:
        marker = root / ".git"
        if not marker.exists() and "GIT_DIR" not in os.environ:
            # Explicit paths also work in an exported tree without an index.
            return set()
        if sys.platform != "linux" or "microsoft" not in os.uname().release.lower() or not marker.is_file():
            raise
        prefix, _, pointer = marker.read_text(encoding="utf-8").strip().partition(": ")
        if prefix != "gitdir" or not PureWindowsPath(pointer).is_absolute():
            raise
        # A worktree created by Windows Git can have a drive-qualified gitdir.
        converted = subprocess.run(["wslpath", "-u", pointer], capture_output=True, check=False)
        git_dir = Path(os.fsdecode(converted.stdout.rstrip(b"\r\n")))
        if converted.returncode != 0 or not git_dir.is_absolute():
            raise DiscoveryError("could not resolve the Git worktree directory")
        output = _git(root, "--git-dir", str(git_dir), "--work-tree", str(root), *args)
    return {os.fsdecode(item) for item in output.split(b"\0") if item}


def _disk_spelling(root: Path, relative: str) -> tuple[str, list[set[str]]]:
    parent = root
    siblings = []
    for name in relative.split("/"):
        names = set(os.listdir(parent))
        siblings.append(names)
        if name not in names:
            target = (parent / name).lstat()
            matches = [
                entry for entry in names
                if target.st_ino and os.path.samestat(target, (parent / entry).lstat())
            ]
            if len(matches) != 1:
                raise DiscoveryError("ambiguous changed-file identity")
            name = matches[0]
        parent /= name
    return _relative_path(parent.relative_to(root).as_posix()), siblings


def _possible_index_alias(parts: list[str], name: str) -> bool:
    indexed_parts = name.split("/")
    # Leave Unicode and 8.3 spellings to the filesystem's name lookup.
    return len(indexed_parts) == len(parts) and all(
        indexed.casefold() == supplied.casefold()
        or not indexed.isascii() or not supplied.isascii()
        or "~" in indexed or "~" in supplied
        for indexed, supplied in zip(indexed_parts, parts, strict=True)
    )


def _index_entry_matches(root: Path, name: str, target: os.stat_result) -> bool:
    try:
        indexed = (root / _relative_path(name)).lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return os.path.samestat(target, indexed)


def _canonical_existing_path(root: Path, path: Path, relative: str) -> str:
    try:
        target = path.stat()
    except FileNotFoundError:
        return relative
    if not stat.S_ISREG(target.st_mode):
        return relative

    tracked = _tracked_supplied_paths(root)
    if relative in tracked:
        parts = relative.split("/")
        if not any(
            name != relative and _possible_index_alias(parts, name)
            and (not target.st_ino or _index_entry_matches(root, name, target))
            for name in tracked
        ):
            return relative

    # Verify that matching file identities also share a directory entry.
    spelling, siblings = _disk_spelling(root, relative)
    parts = spelling.split("/")
    if not target.st_ino:
        raise DiscoveryError("changed-file identity is unavailable")
    matches = []
    for name in tracked:
        indexed_parts = name.split("/")
        if len(indexed_parts) != len(parts) or any(
            indexed_part != part and indexed_part in entries
            for indexed_part, part, entries in zip(indexed_parts, parts, siblings, strict=True)
        ):
            continue
        # Hard links can have the same inode under different directory entries.
        if _index_entry_matches(root, name, target) and (
            name == spelling or _disk_spelling(root, name)[0] == spelling
        ):
            matches.append(name)
    if len(matches) > 1:
        raise DiscoveryError("ambiguous changed-file identity in the Git index")
    return matches[0] if matches else spelling


def _relative_to_native_root(path: Path, root: Path) -> Path:
    try:
        relative = path.relative_to(root)
        if not relative.parts or path.parents[len(relative.parts) - 1].as_posix() == root.as_posix():
            return relative
    except ValueError:
        pass
    # POSIX path subtraction is case-sensitive even on a case-insensitive mount.
    for parent in path.parents:
        try:
            if parent.samefile(root):
                return path.relative_to(parent)
        except FileNotFoundError:
            continue
    raise ValueError("path has no repository ancestor")


def _normalize_supplied_path(root: Path, raw_path: str) -> str:
    candidate = Path(raw_path)
    portable = raw_path.replace("\\", "/")
    native_posix_absolute = candidate.is_absolute() and os.name != "nt"
    components = raw_path if native_posix_absolute else portable
    if ".." in components.split("/") or any(ord(char) < 32 or ord(char) == 127 for char in raw_path):
        raise DiscoveryError("invalid repository-relative changed file")
    if candidate.is_absolute():
        if not native_posix_absolute:
            # Reject Win32 aliases before resolve() can erase the spelling.
            suffix = portable[len(candidate.anchor.replace("\\", "/")):]
            if any(part.endswith((".", " ")) or ":" in part for part in suffix.split("/")):
                raise DiscoveryError("invalid repository-relative changed file")
            if candidate.drive.startswith("\\\\") and candidate.drive.casefold() != root.drive.casefold():
                raise DiscoveryError("changed file uses an unsupported Windows path namespace")
        try:
            root = root.resolve()
            candidate = candidate.resolve()
            relative = _relative_to_native_root(candidate, root)
        except (ValueError, OSError, RuntimeError) as error:
            raise DiscoveryError("changed file is outside the repository") from error
        try:
            return _canonical_existing_path(root, candidate, _relative_path(relative.as_posix()))
        except (OSError, UnicodeError) as error:
            raise DiscoveryError("could not establish changed-file identity") from error
    windows = PureWindowsPath(raw_path)
    if windows.drive or windows.root or PurePosixPath(raw_path).is_absolute():
        raise DiscoveryError("changed file uses an unsupported absolute or drive-relative path")
    return _relative_path(portable.removeprefix("./"))


def _resolved_base(root: Path, expression: str) -> tuple[str, str | None]:
    commit = _git(
        root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{expression}^{{commit}}",
        check=False,
    ).decode("ascii", "replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise DiscoveryError("the selected base could not be resolved")
    symbolic = _git(
        root, "rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", expression,
        check=False,
    ).decode("utf-8", "replace").strip()
    if re.fullmatch(r"refs/(heads|remotes|tags)/[A-Za-z0-9_./-]+", symbolic):
        # Confirm the canonical ref still names the commit used for comparison.
        target = _git(
            root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{symbolic}^{{commit}}",
            check=False,
        ).decode("ascii", "replace").strip()
        if target == commit:
            return commit, symbolic
    return commit, None


def discover_changed_files(
    root: Path,
    *,
    base_ref: str | None = None,
    supplied_files: Sequence[str] = (),
    managed: bool = False,
) -> Discovery:
    if supplied_files:
        if managed:
            raise DiscoveryError("managed validation requires Git-discovered scope")
        files = tuple(sorted({_normalize_supplied_path(root, path) for path in supplied_files}))
        return Discovery(files=files, base="explicit changed files")

    try:
        agent_validation.require_checkout_environment()
    except ValueError as error:
        raise DiscoveryError(str(error)) from error
    top_level = _git(root, "rev-parse", "--show-toplevel").rstrip(b"\r\n")
    if not top_level or os.path.normcase(os.path.realpath(os.fsdecode(top_level))) != (
        os.path.normcase(os.path.realpath(root))
    ):
        raise DiscoveryError("change discovery requires the Git repository root")
    try:
        index_paths = agent_validation.visible_index_paths(_git(root, "ls-files", "--cached", "-v", "-z"))
        absent_attributes = agent_validation.require_supported_checkout_attributes(root, index_paths)
        checkout_settings = agent_validation.checkout_eol_settings(root)
    except ValueError as error:
        raise DiscoveryError(str(error)) from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise DiscoveryError("could not inspect Git checkout settings and attributes") from error

    comparison_ref, trusted = choose_base_ref(root, base_ref, managed=managed)
    base_commit, canonical_ref = _resolved_base(root, comparison_ref)
    if trusted and canonical_ref != comparison_ref:
        raise DiscoveryError("could not verify the trusted main comparison reference")
    merge_base = _git(root, "merge-base", "--all", "HEAD", base_commit).decode("ascii", "replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", merge_base):
        raise DiscoveryError("could not establish a unique merge base for HEAD and the selected base")

    # Exact matches identify copies from unchanged paths as well as renames.
    # Edited renames still show both endpoints as a deletion and an addition.
    diff_args = (
        "diff", "--no-ext-diff", "--no-relative", "--ignore-submodules=none",
        "--find-renames=100%", "--find-copies=100%", "--find-copies-harder", "--name-status", "-z",
    )
    files: set[str] = set()
    unambiguous = True
    for revisions in ((merge_base, "HEAD"), ("--cached",), ()):
        paths, complete = _diff_paths(_git(root, *diff_args, *revisions))
        files.update(paths)
        unambiguous &= complete
    untracked = _nul_paths(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    files.update(untracked)
    try:
        raw_changed = agent_validation.checkout_byte_changes(
            root, index_paths, files, checkout_settings, absent_attributes,
        )
    except ValueError as error:
        raise DiscoveryError(str(error)) from error
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        raise DiscoveryError("could not verify tracked checkout bytes") from error
    files.update(raw_changed)
    return Discovery(
        files=tuple(sorted(files)),
        base=f"{canonical_ref or base_commit} (merge base {merge_base[:12]})",
        base_ref=canonical_ref,
        merge_base=merge_base,
        base_commit=base_commit,
        workflow_only=trusted and unambiguous and not untracked and _workflow_path_set(files),
        raw_changed_files=tuple(sorted(raw_changed)),
    )


def classify_file(path: str) -> set[str]:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix().removeprefix("./")
    areas: set[str] = set()

    if normalized.startswith("docs/") or normalized.endswith(".md"):
        areas.add("docs")

    if (
        (normalized.startswith(("app/", "tests/", "scripts/")) and normalized.endswith(".py"))
        or PurePosixPath(normalized).name == ".gitattributes"
        or normalized.startswith(("tests/fixtures/", ".github/workflows/"))
        or normalized == "pyproject.toml"
        or normalized.startswith("requirements")
    ):
        areas.add("python")

    semantic_files = {
        "app/cli.py",
        "app/core/config.py",
        "app/semantic.py",
        "app/repositories/semantic.py",
        "app/services/duplicate_candidates.py",
        "app/services/indexer.py",
        "app/services/semantic_search.py",
        "requirements.txt",
        "tests/test_cli.py",
        "tests/test_config.py",
        "tests/test_duplicate_candidates.py",
        "tests/test_indexer.py",
        "tests/test_semantic_ranking.py",
        "tests/test_semantic_repository.py",
        "tests/test_semantic_search_service.py",
    }
    if normalized in semantic_files or normalized.startswith("tests/eval/"):
        areas.add("semantic")

    if (
        normalized.startswith("app/api/")
        or normalized in {
            "action_openapi.yaml",
            "app/core/config.py",
            "app/main.py",
            "app/core/http_security.py",
            "tests/test_api.py",
            "tests/test_api_versioning.py",
            "tests/test_config.py",
            "tests/test_health.py",
        }
    ):
        areas.add("api")

    if (
        normalized.startswith("app/ui/")
        or normalized.startswith("tests/e2e/")
        or normalized == "tests/test_ui.py"
        or normalized in UI_BACKEND_PATHS
    ):
        areas.add("ui")

    if normalized in {
        ".env.example",
        "app/core/config.py",
        "app/main.py",
        "app/mcp_server.py",
        "app/mcp_http.py",
        "docker-compose.yml",
        "requirements.txt",
        "scripts/smoke_mcp_http.py",
        "tests/test_config.py",
        "tests/test_mcp_server.py",
        "tests/test_mcp_http.py",
    }:
        areas.add("mcp")

    if normalized in {
        ".dockerignore",
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
        "compose.truenas.yml",
        "requirements.txt",
        "scripts/smoke_mcp_http.py",
    }:
        areas.add("docker")

    if normalized in TRUENAS_PATHS or normalized.startswith(TRUENAS_PREFIX):
        areas.add("truenas/deployment")

    return areas


def classify_files(paths: Sequence[str]) -> set[str]:
    return set().union(*(classify_file(path) for path in paths)) if paths else set()


def select_manual_requirements(paths: Sequence[str], areas: set[str]) -> tuple[str, ...]:
    requirements: list[str] = []
    if any(
        (path in TRUENAS_PATHS or path.startswith(TRUENAS_PREFIX))
        and path != "compose.truenas.yml"
        for path in paths
    ):
        requirements.append(TRUENAS_MANUAL)
    if "action_openapi.yaml" in paths:
        requirements.append(ACTION_OPENAPI_MANUAL)
    return tuple(requirements)


def available_checks(*, parallel_pytest: bool = False) -> tuple[Check, ...]:
    python = sys.executable
    python_path = (("PYTHONPATH", str(REPOSITORY_ROOT)),)
    pytest_args = PYTEST_PARALLEL_ARGS if parallel_pytest else ()
    pytest_environment = (*python_path, ("PYTEST_ADDOPTS", ""))
    return (
        Check("ruff", "ruff", (python, "-m", "ruff", "check", "app", "tests", "scripts")),
        Check(
            "pytest",
            "pytest (unit/integration, includes tests/eval)",
            (python, "-m", "pytest", "-q", "--ignore=tests/e2e", *pytest_args),
            pytest_environment,
            needs_xdist=parallel_pytest,
        ),
        Check(
            "pytest-workflow",
            "pytest (developer workflow)",
            (python, "-m", "pytest", "-q", *WORKFLOW_TESTS, *pytest_args),
            pytest_environment,
            needs_xdist=parallel_pytest,
        ),
        Check("compileall", "compileall", (python, "-m", "compileall", "-q", "app")),
        Check(
            "playwright-e2e",
            "Playwright E2E (Chromium)",
            (
                python,
                "-m",
                "pytest",
                "-q",
                "tests/e2e",
                "--browser=chromium",
                "--tracing=retain-on-failure",
                "--output=test-results/playwright",
            ),
            python_path,
            needs_playwright=True,
            needs_chromium=True,
        ),
        Check(
            "compose",
            "docker compose config",
            ("docker", "compose", "config", "--quiet"),
            (("API_KEY", "agent-check-placeholder-secret"), ("OBSIDIAN_VAULT_PATH", "/tmp/vault")),
            needs_docker=True,
            needs_compose=True,
        ),
        Check(
            "truenas-compose",
            "docker compose config (compose.truenas.yml)",
            (
                "docker",
                "compose",
                "-f",
                "compose.truenas.yml",
                "config",
                "--quiet",
                "--no-env-resolution",
            ),
            (("API_KEY", "agent-check-placeholder-secret"), ("OBSIDIAN_VAULT_PATH", "/tmp/vault")),
            needs_docker=True,
            needs_compose=True,
        ),
        Check(
            "docker-build",
            "docker build",
            ("docker", "build", "-t", IMAGE_TAG, "."),
            needs_docker=True,
        ),
        Check(
            "mcp-dependency",
            "MCP dependency in image",
            (
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "python",
                IMAGE_TAG,
                "-c",
                "from importlib.metadata import version; import app.mcp_server; print(version('mcp'))",
            ),
            needs_docker=True,
        ),
        Check(
            "mcp-stdio",
            "MCP stdio container smoke",
            (
                "docker",
                "run",
                "--rm",
                "-i",
                "--entrypoint",
                "sh",
                IMAGE_TAG,
                "-c",
                "mkdir -p /tmp/vault /tmp/data && VAULT_PATH=/tmp/vault "
                "SEMANTIC_DATA_PATH=/tmp/data python -m app.mcp_server </dev/null",
            ),
            needs_docker=True,
        ),
        Check(
            "mcp-http",
            "MCP HTTP container smoke",
            (python, "scripts/smoke_mcp_http.py", "--image", IMAGE_TAG),
            needs_docker=True,
        ),
    )


def select_checks(
    areas: set[str], paths: Sequence[str] = (), *, workflow_only: bool = False,
    parallel_pytest: bool = False,
) -> tuple[Check, ...]:
    selected: set[str] = set()
    if areas & {"python", "semantic", "api", "ui", "mcp", "docker"} or any(
        not classify_file(path) for path in paths
    ):
        pytest_key = (
            "pytest-workflow"
            if workflow_only and areas <= {"python", "docs"} and _workflow_path_set(paths)
            else "pytest"
        )
        selected.update(("ruff", pytest_key, "compileall"))
    if "ui" in areas:
        selected.add("playwright-e2e")
    if "docker" in areas:
        selected.update(("compose", "docker-build"))
    if "compose.truenas.yml" in paths:
        selected.add("truenas-compose")
    if "mcp" in areas:
        selected.update(("docker-build", "mcp-dependency", "mcp-stdio", "mcp-http"))
    return tuple(check for check in available_checks(parallel_pytest=parallel_pytest) if check.key in selected)


def _format_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)


def _display_command(check: Check) -> str:
    command = command_identity(check)
    return _format_command(command) if command else "(command identity unavailable)"


def default_command_runner(check: Check) -> int:
    environment = os.environ.copy()
    environment.update(dict(check.environment))
    completed = subprocess.run(check.command, cwd=REPOSITORY_ROOT, env=environment, check=False)
    return completed.returncode


def playwright_package_available() -> bool:
    return (
        importlib.util.find_spec("playwright") is not None
        and importlib.util.find_spec("pytest_playwright") is not None
    )


def playwright_chromium_executable() -> Path:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path)


def default_availability_checker() -> AvailabilityChecker:
    cache: dict[str, str | None] = {}

    def probe(check: Check) -> str | None:
        if check.needs_xdist and agent_validation.package_version("pytest-xdist") != XDIST_VERSION:
            return f"pytest-xdist=={XDIST_VERSION} required; run python -m pip install pytest-xdist=={XDIST_VERSION}"
        if check.needs_playwright:
            if "playwright" not in cache:
                package_available = playwright_package_available()
                cache["playwright"] = None if package_available else PLAYWRIGHT_PACKAGE_UNAVAILABLE
            if cache["playwright"] is not None:
                return cache["playwright"]
            if check.needs_chromium:
                if "chromium" not in cache:
                    try:
                        executable = playwright_chromium_executable()
                        cache["chromium"] = (
                            None if executable.is_file() else PLAYWRIGHT_CHROMIUM_UNAVAILABLE
                        )
                    except Exception:
                        cache["chromium"] = PLAYWRIGHT_CHROMIUM_UNAVAILABLE
                if cache["chromium"] is not None:
                    return cache["chromium"]
        if not check.needs_docker:
            return None
        if "docker" not in cache:
            if shutil.which("docker") is None:
                cache["docker"] = "docker executable was not found"
            else:
                result = subprocess.run(
                    ["docker", "info", "--format", "{{.ServerVersion}}"],
                    cwd=REPOSITORY_ROOT,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                cache["docker"] = None if result.returncode == 0 else "Docker daemon is unavailable"
        if cache["docker"] is not None:
            return cache["docker"]
        if check.needs_compose:
            if "compose" not in cache:
                result = subprocess.run(
                    ["docker", "compose", "version"],
                    cwd=REPOSITORY_ROOT,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                cache["compose"] = None if result.returncode == 0 else "Docker Compose is unavailable"
            return cache["compose"]
        return None

    return probe


def execute_checks(
    checks: Sequence[Check],
    *,
    runner: CommandRunner = default_command_runner,
    availability: AvailabilityChecker | None = None,
    clock: agent_validation.Clock = agent_validation.Clock(),
) -> tuple[CheckResult, ...]:
    availability = availability or default_availability_checker()
    results: list[CheckResult] = []
    for check in checks:
        started_at = clock.stamp()
        started_ns = clock.monotonic_ns()
        unavailable_reason = availability(check)
        exit_code = None
        launch_attempted = not unavailable_reason
        if unavailable_reason:
            status, detail = UNAVAILABLE, unavailable_reason
        else:
            print(f"RUN {check.label}: {_display_command(check)}", flush=True)
            try:
                exit_code = runner(check)
            except OSError as error:
                status, detail = UNAVAILABLE, str(error)
            else:
                status = PASS if exit_code == 0 else FAIL
                detail = f"exit {exit_code}"
        elapsed_ns = clock.monotonic_ns() - started_ns
        results.append(
            CheckResult(
                check, status, detail, started_at, clock.stamp(), elapsed_ns,
                exit_code, launch_attempted,
            )
        )
    return tuple(results)


def result_exit_code(results: Sequence[CheckResult]) -> int:
    if any(result.status == FAIL for result in results):
        return 1
    if any(result.status == UNAVAILABLE for result in results):
        return 3
    return 0


def print_selection(
    discovery: Discovery,
    areas: set[str],
    checks: Sequence[Check],
    manual_requirements: Sequence[str],
    *,
    dry_run: bool,
) -> None:
    print(f"Change base: {discovery.base}")
    print("Changed files:")
    if discovery.files:
        for path in discovery.files:
            detail = " (checkout bytes differ)" if path in discovery.raw_changed_files else ""
            print(f"  {path}{detail}")
    else:
        print("  (none)")
    print("Detected areas:")
    detected = [area for area in AREAS if area in areas]
    print("  " + ("\n  ".join(detected) if detected else "(none)"))
    print("Automated checks:")
    if checks:
        for check in checks:
            if dry_run:
                print(f"  WOULD RUN {check.label}: {_display_command(check)}")
            else:
                print(f"  SELECTED {check.label}")
    else:
        print(f"  {NOT_REQUIRED} no automated checks selected")
    if not any(check.needs_docker for check in checks):
        print(f"  {NOT_REQUIRED} Docker verification")
    if not any(check.key.startswith("mcp-") for check in checks):
        print(f"  {NOT_REQUIRED} MCP container verification")
    print("Agent verification:")
    if manual_requirements:
        for requirement in manual_requirements:
            print(f"  {MANUAL} {requirement}")
    else:
        print(f"  {NOT_REQUIRED} manual verification")


def print_results(results: Sequence[CheckResult], manual_requirements: Sequence[str]) -> None:
    print("Result summary:")
    if results:
        for result in results:
            message = (
                "command could not be launched"
                if result.status == UNAVAILABLE and result.launch_attempted
                else result.detail
            )
            detail = f" ({message})" if message else ""
            print(f"  {result.status} {result.check.label}{detail} [{result.elapsed_ns / 1e9:.3f}s]")
    else:
        print(f"  {NOT_REQUIRED} no automated checks selected")
    if manual_requirements:
        for requirement in manual_requirements:
            print(f"  {MANUAL} {requirement}")
    else:
        print(f"  {NOT_REQUIRED} manual verification")


def command_identity(check: Check) -> list[str] | None:
    if not any(
        item.key == check.key and item.command == check.command
        for item in available_checks(parallel_pytest=check.needs_xdist)
    ):
        return None
    executable, *args = check.command
    return ["python" if executable == sys.executable else executable, *args]


def scope_record(discovery: Discovery, areas: set[str], manual: Sequence[str]) -> dict:
    return {
        "base_ref": discovery.base_ref,
        "base_commit": discovery.base_commit,
        "merge_base": discovery.merge_base,
        "change_discovery": "git" if discovery.base_commit else "explicit",
        "raw_changed_files": list(discovery.raw_changed_files),
        "changed_files": [
            {"path": path, "areas": sorted(classify_file(path))} for path in discovery.files
        ],
        "affected_areas": sorted(areas),
        "manual_requirements": list(manual),
    }


def _report_checks(results: Sequence[CheckResult]) -> list[dict]:
    checks = []
    for result in results:
        check = result.check
        command = command_identity(check)
        checks.append({
            "id": check.key,
            "selected_command": command,
            "executed_command": command if result.exit_code is not None else None,
            "launch_attempted": result.launch_attempted,
            "started_at": result.started_at,
            "ended_at": result.ended_at,
            "elapsed_ns": result.elapsed_ns,
            "outcome": "UNAVAILABLE" if result.status == UNAVAILABLE else result.status,
            "exit_code": result.exit_code,
            "required": True,
            "unavailable_stage": (
                "launch" if result.launch_attempted else "prerequisite"
            ) if result.status == UNAVAILABLE else None,
        })
    return checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="select checks without running them")
    parser.add_argument("--base", help="override the Git base reference")
    parser.add_argument("--parallel-pytest", action="store_true", help="run the selected pytest suite in parallel")
    parser.add_argument("--run-id", type=UUID, help=argparse.SUPPRESS)
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        metavar="PATH",
        help="use an explicit changed file; repeat for multiple files and skip Git discovery",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner = default_command_runner,
    availability: AvailabilityChecker | None = None,
    clock: agent_validation.Clock = agent_validation.Clock(),
) -> int:
    args = build_parser().parse_args(argv)
    managed = args.run_id is not None
    if managed and args.dry_run:
        print("ERROR managed validation requires check execution", file=sys.stderr)
        return 2
    source_before = None if args.dry_run else agent_validation.source_identity(REPOSITORY_ROOT)
    try:
        discovery = discover_changed_files(
            REPOSITORY_ROOT,
            base_ref=args.base,
            supplied_files=args.changed_file,
            managed=managed,
        )
    except DiscoveryError as error:
        print(f"ERROR changed-file discovery: {error}", file=sys.stderr)
        return 2
    if managed and source_before is None:
        print("ERROR managed validation could not establish source identity", file=sys.stderr)
        return 2

    areas = classify_files(discovery.files)
    checks = select_checks(
        areas, discovery.files, workflow_only=discovery.workflow_only, parallel_pytest=args.parallel_pytest,
    )
    manual_requirements = select_manual_requirements(discovery.files, areas)
    print_selection(
        discovery,
        areas,
        checks,
        manual_requirements,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print("Overall: DRY RUN")
        return 0

    started_at = clock.stamp()
    started_ns = clock.monotonic_ns()
    results = execute_checks(checks, runner=runner, availability=availability, clock=clock)
    elapsed_ns = clock.monotonic_ns() - started_ns
    ended_at = clock.stamp()
    source_after = agent_validation.source_identity(REPOSITORY_ROOT)
    print_results(results, manual_requirements)
    exit_code = result_exit_code(results)
    if managed and exit_code == 0 and source_before != source_after:
        print("ERROR managed validation source changed or became unavailable", file=sys.stderr)
        return 2
    outcome = {0: "PASS", 1: "FAIL", 3: "INCOMPLETE"}[exit_code]
    if exit_code == 0 and manual_requirements:
        print("Overall: AUTOMATED PASS; MANUAL VERIFICATION REMAINS")
    elif exit_code == 0:
        print("Overall: PASS")
    elif exit_code == 3:
        print("Overall: INCOMPLETE")
    else:
        print("Overall: FAIL")
    print(f"Automated verification elapsed: {elapsed_ns / 1e9:.3f}s")
    try:
        agent_validation.write_report(REPOSITORY_ROOT, {
            "schema_version": agent_validation.SCHEMA_VERSION,
            "run_id": str(args.run_id or uuid4()),
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_ns": elapsed_ns,
            "source_before": source_before,
            "source_after": source_after,
            "source_unchanged": source_before is not None and source_before == source_after,
            **scope_record(discovery, areas, manual_requirements),
            "parallel_pytest": args.parallel_pytest,
            "checks": _report_checks(results),
            "overall_outcome": outcome,
            "exit_code": exit_code,
            "environment": agent_validation.environment_identity(),
        })
    except (OSError, RuntimeError, ValueError):
        print("Validation report could not be written; see terminal results.", file=sys.stderr)
    else:
        print(f"Validation report: {agent_validation.REPORT_PATH.as_posix()}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
