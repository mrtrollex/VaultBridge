from __future__ import annotations

import ctypes
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import agent_check, agent_validation


@pytest.fixture(autouse=True)
def isolated_report(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_check, "REPOSITORY_ROOT", tmp_path)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("docs/CODEX_PLAYBOOK.md", {"docs"}),
        ("app/api/notes.py", {"python", "api", "ui"}),
        ("app/services/semantic_search.py", {"python", "semantic"}),
        ("tests/eval/retrieval_cases.json", {"semantic"}),
        ("app/ui/assets/app.js", {"ui"}),
        ("tests/e2e/test_dashboard.py", {"python", "ui"}),
        ("app/mcp_http.py", {"python", "mcp"}),
        ("app/core/config.py", {"python", "semantic", "api", "mcp"}),
        ("Dockerfile", {"docker"}),
        ("compose.truenas.yml", {"docker", "truenas/deployment"}),
        ("README_TRUENAS.md", {"docs", "truenas/deployment"}),
        ("ix-dev/community/vaultbridge/app.yaml", {"truenas/deployment"}),
    ],
)
def test_classify_file_uses_repository_paths(path, expected):
    assert agent_check.classify_file(path) == expected


def test_discovery_unions_branch_index_worktree_and_untracked(monkeypatch, tmp_path):
    base = "a" * 40
    reference = "refs/remotes/origin/main"
    responses = {
        ("rev-parse", "--show-toplevel"): f"{tmp_path}\n".encode(),
        ("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"): b"refs/remotes/origin/main\n",
        ("rev-parse", "--verify", "--quiet", "--end-of-options", f"{reference}^{{commit}}"): base.encode(),
        ("rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", reference): reference.encode(),
        ("merge-base", "HEAD", base): b"0123456789abcdef\n",
        ("diff", "--no-renames", "--name-only", "-z", "0123456789abcdef", "HEAD"): b"app/main.py\0",
        ("diff", "--cached", "--no-renames", "--name-only", "-z"): b"tests/test_api.py\0",
        ("diff", "--no-renames", "--name-only", "-z"): b"docs/guide with spaces.md\0",
        ("ls-files", "--others", "--exclude-standard", "-z"): b"scripts/new.py\0",
    }

    def fake_git(root: Path, *args: str, check: bool = True) -> bytes:
        assert root == tmp_path
        return responses[args]

    monkeypatch.setattr(agent_check, "_git", fake_git)
    monkeypatch.setattr(agent_check, "_git_ref_exists", lambda root, ref: ref == "refs/remotes/origin/main")

    discovery = agent_check.discover_changed_files(tmp_path)

    assert discovery.files == (
        "app/main.py",
        "docs/guide with spaces.md",
        "scripts/new.py",
        "tests/test_api.py",
    )
    assert discovery.base == "refs/remotes/origin/main (merge base 0123456789ab)"
    assert discovery.base_commit == base


def test_explicit_changed_files_are_deterministic_and_skip_git(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_check,
        "_git",
        lambda *args, **kwargs: pytest.fail("Git should not run for explicit changed files"),
    )

    discovery = agent_check.discover_changed_files(
        tmp_path,
        supplied_files=("tests\\test_ui.py", "tests/test_ui.py", "app/main.py"),
    )

    assert discovery.files == ("app/main.py", "tests/test_ui.py")
    assert discovery.base == "explicit changed files"


@pytest.mark.parametrize("path", [
    r"C:\SomeUser\private.py", "C:/SomeUser/private.py", r"C:SomeUser\private.py",
    r"\\server\shared\private.py", "/home/SomeUser/private.py", "docs/../private.py",
    "docs//private.py", "docs/./private.py", "docs/report.py:secret", "docs/private.py\n",
])
def test_rejects_absolute_foreign_paths_traversal_and_malformed_components(tmp_path, capsys, path):
    assert agent_check.main(["--changed-file", path]) == 2
    message = capsys.readouterr().err
    assert "ERROR changed-file discovery:" in message
    assert "SomeUser" not in message and "secret" not in message
    assert not (tmp_path / agent_validation.REPORT_PATH).exists()


def test_preserves_normal_native_absolute_and_portable_relative_paths(tmp_path):
    target = tmp_path / "docs" / "guide with spaces.md"
    discovery = agent_check.discover_changed_files(
        tmp_path, supplied_files=(str(target), "./docs/guide with spaces.md", r"docs\guide with spaces.md"),
    )
    assert discovery.files == ("docs/guide with spaces.md",)


def test_native_absolute_aliases_select_the_canonical_python_checks(tmp_path, monkeypatch, capsys):
    relative = "tests/test_agent_task.py"
    if os.name == "nt":
        root = Path(__file__).resolve().parents[1]
        canonical = root / relative
        get_short_name = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short_name.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        get_short_name.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        assert 0 < get_short_name(str(canonical), buffer, len(buffer)) < len(buffer)
        alias = Path(buffer.value)
        assert alias.samefile(canonical)
        inputs = [(root, canonical), (root, alias)]
    else:
        inputs = []
        for ancestor in ("checkout:parent", "checkout.parent."):
            root = tmp_path / ancestor / "repository"
            canonical = root / relative
            canonical.parent.mkdir(parents=True)
            canonical.touch()
            alias = tmp_path / f"alias-{len(inputs)}"
            alias.symlink_to(root, target_is_directory=True)
            inputs.extend(((root, canonical), (root, alias / relative)))

    for root, target in inputs:
        monkeypatch.setattr(agent_check, "REPOSITORY_ROOT", root)
        assert agent_check.discover_changed_files(root, supplied_files=(str(target),)).files == (relative,)
        assert agent_check.main(["--dry-run", "--changed-file", str(target)]) == 0
        output = capsys.readouterr().out
        assert [line.strip().split(":")[0] for line in output.splitlines() if "WOULD RUN" in line] == [
            "WOULD RUN ruff", "WOULD RUN pytest (unit/integration, includes tests/eval)", "WOULD RUN compileall",
        ]
        assert agent_check.main(["--dry-run", "--changed-file", str(root / "tests" / ".." / relative)]) == 2
    outside = tmp_path / "outside.py"
    outside.touch()
    assert agent_check.main(["--dry-run", "--changed-file", str(outside)]) == 2
    assert "outside the repository" in capsys.readouterr().err


def test_native_absolute_case_alias_selects_all_tracked_checks(tmp_path, capsys):
    canonical = tmp_path / "app/main.py"
    canonical.parent.mkdir()
    canonical.touch()
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "--", "app/main.py"], cwd=tmp_path, check=True)
    alias = tmp_path / "APP/MAIN.PY"
    if not alias.exists():
        # On case-sensitive filesystems, exercise a real alias to the same file.
        alias.parent.mkdir()
        alias.symlink_to(canonical)
    assert alias.samefile(canonical)
    expected = (
        "ruff", "pytest", "compileall", "playwright-e2e",
        "docker-build", "mcp-dependency", "mcp-stdio", "mcp-http",
    )

    outputs = []
    for path in (canonical, alias):
        files = agent_check.discover_changed_files(tmp_path, supplied_files=(str(path),)).files
        checks = agent_check.select_checks(agent_check.classify_files(files), files)
        assert files == ("app/main.py",)
        assert tuple(check.key for check in checks) == expected
        assert agent_check.main(["--dry-run", "--changed-file", str(path)]) == 0
        outputs.append(capsys.readouterr().out)
    assert outputs[0] == outputs[1]
    assert outputs[0].count("WOULD RUN") == len(expected)


def test_native_absolute_exact_matches_check_index_ambiguity(tmp_path, monkeypatch, capsys):
    names = ("app/main.py", "APP/MAIN.PY", "App/Main.py")
    canonical, uppercase, mixed = (tmp_path / name for name in names)
    canonical.parent.mkdir()
    canonical.touch()
    if not uppercase.exists():
        uppercase.parent.mkdir()
        uppercase.touch()
    same_file = canonical.samefile(uppercase)

    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "isolated-index"))

    def git(*args, **kwargs):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, check=True, **kwargs,
        ).stdout.decode("ascii").strip()

    git("init", "--quiet")
    blob = git("hash-object", "-w", names[0])
    entries = "".join(f"100644 {blob}\t{name}\n" for name in names[:2]).encode("ascii")
    git("update-index", "--index-info", input=entries)
    assert set(git("ls-files").splitlines()) == set(names[:2])

    if same_file:
        assert canonical.samefile(mixed)
        for path in (canonical, uppercase, mixed):
            assert agent_check.main(["--dry-run", "--changed-file", str(path)]) == 2
            output = capsys.readouterr()
            assert "ambiguous changed-file identity in the Git index" in output.err
            assert not output.out
    else:
        for name, path, count in zip(names[:2], (canonical, uppercase), (8, 0), strict=True):
            assert agent_check.discover_changed_files(tmp_path, supplied_files=(str(path),)).files == (name,)
            assert agent_check.main(["--dry-run", "--changed-file", str(path)]) == 0
            assert capsys.readouterr().out.count("WOULD RUN") == count


def test_native_absolute_new_source_path_keeps_python_checks(tmp_path, capsys):
    tracked = tmp_path / "notes.txt"
    tracked.touch()
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "--", "notes.txt"], cwd=tmp_path, check=True)
    new = tmp_path / "app/new.py"
    new.parent.mkdir()
    for exists in (False, True):
        if exists:
            # A different directory entry with the same identity is still a new source path.
            os.link(tracked, new)
        files = agent_check.discover_changed_files(tmp_path, supplied_files=(str(new),)).files
        assert files == ("app/new.py",)
        assert agent_check.main(["--dry-run", "--changed-file", str(new)]) == 0
        assert "WOULD RUN pytest" in capsys.readouterr().out


def test_existing_native_absolute_source_fails_closed_when_identity_is_unavailable(
    tmp_path, monkeypatch, capsys,
):
    path = tmp_path / "app/new.py"
    path.parent.mkdir()
    path.touch()
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)

    def unavailable(directory):
        raise PermissionError("directory identity unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(agent_check.os, "listdir", unavailable)
        assert agent_check.main(["--dry-run", "--changed-file", str(path)]) == 2
    output = capsys.readouterr()
    assert "could not establish changed-file identity" in output.err
    assert "Overall: DRY RUN" not in output.out


@pytest.mark.parametrize("suffix", [".", " "])
def test_windows_aliases_cannot_suppress_required_python_checks(tmp_path, capsys, suffix):
    path = "tests/test_agent_task.py"
    assert agent_check.main(["--dry-run", "--changed-file", path]) == 0
    assert "WOULD RUN pytest" in capsys.readouterr().out
    for alias in (path + suffix, str(tmp_path / path) + suffix, f"tests{suffix}/test_agent_task.py"):
        assert agent_check.main(["--dry-run", "--changed-file", alias]) == 2
        output = capsys.readouterr()
        assert "invalid repository-relative changed file" in output.err
        assert "Overall: DRY RUN" not in output.out
    with pytest.raises(agent_check.DiscoveryError, match="invalid repository-relative changed file"):
        agent_check._nul_paths((path + suffix).encode() + b"\0")


def test_real_git_cross_domain_renames_preserve_source_requirements(tmp_path):
    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    git("init", "--initial-branch", "main")
    (tmp_path / "app" / "ui" / "assets").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "app" / "mcp_server.py").write_text("MCP = True\n", encoding="utf-8")
    (tmp_path / "app" / "ui" / "assets" / "app.js").write_text("export {};\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=Agent Check", "-c", "user.email=agent@example.invalid", "commit", "-m", "base")
    git("switch", "-c", "feature")
    git("mv", "app/mcp_server.py", "docs/mcp_server.py")
    git("mv", "app/ui/assets/app.js", "docs/app.js")
    git("-c", "user.name=Agent Check", "-c", "user.email=agent@example.invalid", "commit", "-m", "renames")

    discovery = agent_check.discover_changed_files(tmp_path, base_ref="main")
    areas = agent_check.classify_files(discovery.files)
    selected = {check.key for check in agent_check.select_checks(areas, discovery.files)}

    assert {
        "app/mcp_server.py",
        "docs/mcp_server.py",
        "app/ui/assets/app.js",
        "docs/app.js",
    } <= set(discovery.files)
    assert {"mcp", "ui"} <= areas
    assert {"docker-build", "mcp-stdio", "mcp-http", "playwright-e2e"} <= selected
    assert agent_check.select_manual_requirements(discovery.files, areas) == ()


def test_check_selection_reuses_full_pytest_for_semantic_evaluation():
    keys = {check.key for check in agent_check.select_checks({"python", "semantic"})}

    assert keys == {"ruff", "pytest", "compileall"}
    ruff_check = next(check for check in agent_check.available_checks() if check.key == "ruff")
    assert ruff_check.command[-3:] == ("app", "tests", "scripts")
    pytest_check = next(check for check in agent_check.available_checks() if check.key == "pytest")
    assert pytest_check.label == "pytest (unit/integration, includes tests/eval)"
    assert pytest_check.command[-1] == "--ignore=tests/e2e"


def test_ui_selection_adds_playwright_e2e_with_chromium_only():
    checks = agent_check.select_checks({"ui"})
    keys = {check.key for check in checks}
    e2e = next(check for check in checks if check.key == "playwright-e2e")

    assert keys == {"ruff", "pytest", "compileall", "playwright-e2e"}
    assert e2e.needs_playwright is True
    assert e2e.needs_chromium is True
    assert "--browser=chromium" in e2e.command
    assert "--tracing=retain-on-failure" in e2e.command


@pytest.mark.parametrize(
    "path",
    sorted(agent_check.UI_BACKEND_PATHS),
)
def test_dashboard_backend_dependencies_select_playwright_e2e(path):
    areas = agent_check.classify_file(path)
    keys = {check.key for check in agent_check.select_checks(areas, [path])}

    assert "ui" in areas
    assert "playwright-e2e" in keys


def test_mcp_selection_adds_existing_container_smokes():
    keys = {check.key for check in agent_check.select_checks({"python", "mcp"})}

    assert keys == {
        "ruff",
        "pytest",
        "compileall",
        "docker-build",
        "mcp-dependency",
        "mcp-stdio",
        "mcp-http",
    }


def test_truenas_only_path_requires_manual_verification(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "ix-dev/community/vaultbridge/app.yaml"],
        runner=lambda check: pytest.fail(f"unexpected automated check: {check.key}"),
        availability=lambda check: None,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SKIP / NOT REQUIRED no automated checks selected" in output
    assert "MANUAL / AGENT VERIFICATION REQUIRED TrueNAS deployment/package validation" in output
    assert "Overall: AUTOMATED PASS; MANUAL VERIFICATION REMAINS" in output
    assert "Overall: PASS\n" not in output


def test_truenas_compose_selects_validation_for_exact_file():
    path = "compose.truenas.yml"
    checks = agent_check.select_checks(agent_check.classify_file(path), [path])
    truenas_check = next(check for check in checks if check.key == "truenas-compose")

    assert truenas_check.command == (
        "docker",
        "compose",
        "-f",
        "compose.truenas.yml",
        "config",
        "--quiet",
        "--no-env-resolution",
    )
    assert truenas_check.needs_docker is True
    assert truenas_check.needs_compose is True


def test_truenas_compose_is_incomplete_when_docker_is_unavailable(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "compose.truenas.yml"],
        runner=lambda check: 0,
        availability=lambda check: "Docker daemon is unavailable" if check.needs_docker else None,
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert "REQUIRED BUT UNAVAILABLE docker compose config (compose.truenas.yml)" in output
    assert "Overall: INCOMPLETE" in output


def test_action_openapi_requires_manual_runtime_mapping_verification(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "action_openapi.yaml"],
        runner=lambda check: 0,
        availability=lambda check: None,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert (
        "MANUAL / AGENT VERIFICATION REQUIRED ChatGPT Action/OpenAPI operation IDs must be checked "
        "against actual FastAPI endpoints"
    ) in output
    assert "Overall: AUTOMATED PASS; MANUAL VERIFICATION REMAINS" in output
    assert "Overall: PASS\n" not in output


def test_dry_run_runs_no_verification_commands(capsys):
    def unexpected_runner(check):
        pytest.fail(f"dry-run executed {check.key}")

    exit_code = agent_check.main(
        ["--dry-run", "--changed-file", "app/ui/assets/app.js"],
        runner=unexpected_runner,
        availability=lambda check: pytest.fail(f"dry-run probed {check.key}"),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Detected areas:\n  ui" in output
    assert "WOULD RUN pytest (unit/integration, includes tests/eval)" in output
    assert "WOULD RUN Playwright E2E (Chromium)" in output
    assert "SKIP / NOT REQUIRED manual verification" in output
    assert "Overall: DRY RUN" in output


def test_required_docker_unavailable_is_incomplete_and_nonzero(capsys):
    executed: list[str] = []

    exit_code = agent_check.main(
        ["--changed-file", "Dockerfile"],
        runner=lambda check: executed.append(check.key) or 0,
        availability=lambda check: "Docker daemon is unavailable" if check.needs_docker else None,
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert executed == ["ruff", "pytest", "compileall"]
    assert "REQUIRED BUT UNAVAILABLE docker compose config" in output
    assert "REQUIRED BUT UNAVAILABLE docker build" in output
    assert "Overall: INCOMPLETE" in output


def test_failed_required_check_returns_one(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "app/main.py"],
        runner=lambda check: 7 if check.key == "pytest" else 0,
        availability=lambda check: None,
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL pytest (unit/integration, includes tests/eval) (exit 7)" in output
    assert "Overall: FAIL" in output


def test_ui_check_distinguishes_missing_playwright_package(capsys):
    executed: list[str] = []

    exit_code = agent_check.main(
        ["--changed-file", "app/ui/assets/app.js"],
        runner=lambda check: executed.append(check.key) or 0,
        availability=lambda check: (
            agent_check.PLAYWRIGHT_PACKAGE_UNAVAILABLE
            if check.key == "playwright-e2e"
            else None
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert "playwright-e2e" not in executed
    assert "REQUIRED BUT UNAVAILABLE Playwright E2E (Chromium)" in output
    assert "Playwright Python package unavailable" in output
    assert "Overall: INCOMPLETE" in output


def test_ui_check_distinguishes_missing_chromium(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "tests/test_ui.py"],
        runner=lambda check: 0,
        availability=lambda check: (
            agent_check.PLAYWRIGHT_CHROMIUM_UNAVAILABLE
            if check.key == "playwright-e2e"
            else None
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert "Playwright Chromium browser unavailable" in output
    assert "python -m playwright install chromium" in output


def test_default_availability_checker_detects_missing_playwright_package(monkeypatch):
    monkeypatch.setattr(agent_check, "playwright_package_available", lambda: False)
    check = next(check for check in agent_check.available_checks() if check.key == "playwright-e2e")

    assert agent_check.default_availability_checker()(check) == agent_check.PLAYWRIGHT_PACKAGE_UNAVAILABLE


def test_default_availability_checker_detects_missing_chromium(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_check, "playwright_package_available", lambda: True)
    monkeypatch.setattr(
        agent_check,
        "playwright_chromium_executable",
        lambda: tmp_path / "missing-chromium",
    )
    check = next(check for check in agent_check.available_checks() if check.key == "playwright-e2e")

    assert agent_check.default_availability_checker()(check) == agent_check.PLAYWRIGHT_CHROMIUM_UNAVAILABLE


@pytest.mark.parametrize(("e2e_return_code", "expected_status"), [(0, "PASS"), (7, "FAIL")])
def test_ui_check_reports_e2e_success_or_failure(capsys, e2e_return_code, expected_status):
    exit_code = agent_check.main(
        ["--changed-file", "app/ui/index.html"],
        runner=lambda check: e2e_return_code if check.key == "playwright-e2e" else 0,
        availability=lambda check: None,
    )

    output = capsys.readouterr().out
    assert f"{expected_status} Playwright E2E (Chromium)" in output
    assert exit_code == (0 if e2e_return_code == 0 else 1)


def test_command_start_failure_is_required_but_unavailable():
    check = next(check for check in agent_check.available_checks() if check.key == "ruff")

    results = agent_check.execute_checks(
        [check],
        runner=lambda selected: (_ for _ in ()).throw(OSError("executable unavailable")),
        availability=lambda selected: None,
    )

    result, = results
    assert (result.check, result.status, result.detail) == (
        check, agent_check.UNAVAILABLE, "executable unavailable",
    )
    assert result.launch_attempted is True
    assert result.exit_code is None
    assert agent_check.result_exit_code(results) == 3


def test_wall_clock_adjustment_does_not_change_elapsed_duration():
    check = agent_check.available_checks()[0]
    first = datetime(2026, 11, 22, 12, tzinfo=UTC)
    timestamps = iter((first, first - timedelta(seconds=1)))
    ticks = iter((100, 450))

    result, = agent_check.execute_checks(
        [check],
        runner=lambda selected: 7,
        availability=lambda selected: None,
        clock=agent_validation.Clock(timestamps.__next__, ticks.__next__),
    )

    assert result.started_at == "2026-11-22T12:00:00.000000+00:00"
    assert result.ended_at == "2026-11-22T11:59:59.000000+00:00"
    assert result.elapsed_ns == 350
    assert result.exit_code == 7
    assert agent_check.result_exit_code([result]) == 1
