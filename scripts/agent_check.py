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
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMAGE_TAG = "vaultbridge:agent-check"
AREAS = ("docs", "python", "semantic", "api", "ui", "mcp", "docker", "truenas/deployment")

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


@dataclass(frozen=True)
class CheckResult:
    check: Check
    status: str
    detail: str = ""


CommandRunner = Callable[[Check], int]
AvailabilityChecker = Callable[[Check], str | None]


def _git(root: Path, *args: str, check: bool = True) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise DiscoveryError(detail or f"git {' '.join(args)} failed")
    return completed.stdout if completed.returncode == 0 else b""


def _git_ref_exists(root: Path, ref: str) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def choose_base_ref(
    root: Path,
    requested: str | None,
    *,
    environment: Mapping[str, str] = os.environ,
) -> str:
    if requested:
        if not _git_ref_exists(root, requested):
            raise DiscoveryError(f"base reference does not exist: {requested}")
        return requested

    candidates: list[str] = []
    github_base = environment.get("GITHUB_BASE_REF", "").strip()
    if github_base:
        candidates.extend((f"origin/{github_base}", github_base))

    remote_head = _git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if remote_head:
        candidates.append(remote_head.decode("utf-8", "replace").strip())

    candidates.extend(("origin/main", "main", "origin/master", "master", "origin/trunk", "trunk"))
    for candidate in dict.fromkeys(candidates):
        if candidate and _git_ref_exists(root, candidate):
            return candidate
    raise DiscoveryError("could not determine a base reference; pass --base explicitly")


def _nul_paths(output: bytes) -> set[str]:
    return {
        os.fsdecode(item).replace("\\", "/")
        for item in output.split(b"\0")
        if item
    }


def _normalize_supplied_path(root: Path, raw_path: str) -> str:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise DiscoveryError(f"changed file is outside the repository: {raw_path}") from error
    normalized = PurePosixPath(str(candidate).replace("\\", "/"))
    if not normalized.parts or normalized.is_absolute() or ".." in normalized.parts:
        raise DiscoveryError(f"invalid repository-relative changed file: {raw_path}")
    return normalized.as_posix().removeprefix("./")


def discover_changed_files(
    root: Path,
    *,
    base_ref: str | None = None,
    supplied_files: Sequence[str] = (),
) -> Discovery:
    if supplied_files:
        files = tuple(sorted({_normalize_supplied_path(root, path) for path in supplied_files}))
        return Discovery(files=files, base="explicit changed files")

    _git(root, "rev-parse", "--show-toplevel")
    base = choose_base_ref(root, base_ref)
    merge_base = _git(root, "merge-base", "HEAD", base).decode("ascii", "replace").strip()
    if not merge_base:
        raise DiscoveryError(f"could not find a merge base for HEAD and {base}")

    files: set[str] = set()
    files.update(
        _nul_paths(_git(root, "diff", "--no-renames", "--name-only", "-z", merge_base, "HEAD"))
    )
    files.update(_nul_paths(_git(root, "diff", "--cached", "--no-renames", "--name-only", "-z")))
    files.update(_nul_paths(_git(root, "diff", "--no-renames", "--name-only", "-z")))
    files.update(_nul_paths(_git(root, "ls-files", "--others", "--exclude-standard", "-z")))
    return Discovery(
        files=tuple(sorted(files)),
        base=f"{base} (merge base {merge_base[:12]})",
        base_ref=base,
        merge_base=merge_base,
    )


def classify_file(path: str) -> set[str]:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix().removeprefix("./")
    areas: set[str] = set()

    if normalized.startswith("docs/") or normalized.endswith(".md"):
        areas.add("docs")

    if (
        (normalized.startswith(("app/", "tests/", "scripts/")) and normalized.endswith(".py"))
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


def available_checks() -> tuple[Check, ...]:
    python = sys.executable
    python_path = (("PYTHONPATH", str(REPOSITORY_ROOT)),)
    return (
        Check("ruff", "ruff", (python, "-m", "ruff", "check", "app", "tests", "scripts")),
        Check(
            "pytest",
            "pytest (unit/integration, includes tests/eval)",
            (python, "-m", "pytest", "-q", "--ignore=tests/e2e"),
            python_path,
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


def select_checks(areas: set[str], paths: Sequence[str] = ()) -> tuple[Check, ...]:
    selected: set[str] = set()
    if areas & {"python", "semantic", "api", "ui", "mcp", "docker"}:
        selected.update(("ruff", "pytest", "compileall"))
    if "ui" in areas:
        selected.add("playwright-e2e")
    if "docker" in areas:
        selected.update(("compose", "docker-build"))
    if "compose.truenas.yml" in paths:
        selected.add("truenas-compose")
    if "mcp" in areas:
        selected.update(("docker-build", "mcp-dependency", "mcp-stdio", "mcp-http"))
    return tuple(check for check in available_checks() if check.key in selected)


def _format_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)


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
) -> tuple[CheckResult, ...]:
    availability = availability or default_availability_checker()
    results: list[CheckResult] = []
    for check in checks:
        unavailable_reason = availability(check)
        if unavailable_reason:
            results.append(CheckResult(check, UNAVAILABLE, unavailable_reason))
            continue
        print(f"RUN {check.label}: {_format_command(check.command)}", flush=True)
        try:
            return_code = runner(check)
        except OSError as error:
            results.append(CheckResult(check, UNAVAILABLE, str(error)))
            continue
        results.append(
            CheckResult(check, PASS if return_code == 0 else FAIL, f"exit {return_code}")
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
            print(f"  {path}")
    else:
        print("  (none)")
    print("Detected areas:")
    detected = [area for area in AREAS if area in areas]
    print("  " + ("\n  ".join(detected) if detected else "(none)"))
    print("Automated checks:")
    if checks:
        for check in checks:
            if dry_run:
                print(f"  WOULD RUN {check.label}: {_format_command(check.command)}")
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
            detail = f" ({result.detail})" if result.detail else ""
            print(f"  {result.status} {result.check.label}{detail}")
    else:
        print(f"  {NOT_REQUIRED} no automated checks selected")
    if manual_requirements:
        for requirement in manual_requirements:
            print(f"  {MANUAL} {requirement}")
    else:
        print(f"  {NOT_REQUIRED} manual verification")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="select checks without running them")
    parser.add_argument("--base", help="override the Git base reference")
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
) -> int:
    args = build_parser().parse_args(argv)
    try:
        discovery = discover_changed_files(
            REPOSITORY_ROOT,
            base_ref=args.base,
            supplied_files=args.changed_file,
        )
    except DiscoveryError as error:
        print(f"ERROR changed-file discovery: {error}", file=sys.stderr)
        return 2

    areas = classify_files(discovery.files)
    checks = select_checks(areas, discovery.files)
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

    results = execute_checks(checks, runner=runner, availability=availability)
    print_results(results, manual_requirements)
    exit_code = result_exit_code(results)
    if exit_code == 0 and manual_requirements:
        print("Overall: AUTOMATED PASS; MANUAL VERIFICATION REMAINS")
    elif exit_code == 0:
        print("Overall: PASS")
    elif exit_code == 3:
        print("Overall: INCOMPLETE")
    else:
        print("Overall: FAIL")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
