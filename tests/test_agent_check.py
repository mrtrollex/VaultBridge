from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import agent_check


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
    responses = {
        ("rev-parse", "--show-toplevel"): f"{tmp_path}\n".encode(),
        ("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"): b"refs/remotes/origin/main\n",
        ("merge-base", "HEAD", "refs/remotes/origin/main"): b"0123456789abcdef\n",
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

    assert results == (
        agent_check.CheckResult(check, agent_check.UNAVAILABLE, "executable unavailable"),
    )
    assert agent_check.result_exit_code(results) == 3
