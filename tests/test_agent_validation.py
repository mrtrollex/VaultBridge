from __future__ import annotations

import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import agent_check, agent_finish, agent_review, agent_validation


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    (tmp_path / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    (tmp_path / "base.md").write_text("base\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
    monkeypatch.setattr(agent_check, "REPOSITORY_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def workflow_repository(repository):
    workflow = repository / "scripts/agent_review.py"
    workflow.parent.mkdir()
    workflow.write_text("# base\n", encoding="utf-8")
    agent_validation._git(repository, "add", "scripts/agent_review.py")
    agent_validation._git(
        repository, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "workflow base",
    )
    workflow.write_text("# changed\n", encoding="utf-8")
    return repository


@pytest.mark.parametrize("narrowed_report", [False, True])
def test_managed_finish_reconstructs_trusted_base_despite_ci_environment(
    workflow_repository, monkeypatch, capsys, narrowed_report,
):
    root = workflow_repository
    _commit_runtime_on_feature(root)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_BASE_REF", "feature")
    checks_run = []

    def pass_check(check):
        checks_run.append(check.key)
        return 0

    def runner(command, cwd):
        if command[1].endswith("agent_check.py"):
            if not narrowed_report:
                return agent_check.main(command[2:], runner=pass_check, availability=lambda check: None)

            # Recreate the earlier report with a feature-based scope, a valid
            # source fingerprint, and a successful focused check for this run ID.
            assert agent_check.main(["--base", "feature"], runner=pass_check, availability=lambda check: None) == 0
            report = json.loads((root / agent_validation.REPORT_PATH).read_bytes())
            assert report["base_ref"] == "refs/heads/feature"
            assert [record["path"] for record in report["changed_files"]] == ["scripts/agent_review.py"]
            focused = next(check for check in agent_check.available_checks() if check.key == "pytest-workflow")
            focused_command = agent_check.command_identity(focused)
            report["checks"][1].update(
                id=focused.key, selected_command=focused_command, executed_command=focused_command,
            )
            report["run_id"] = command[command.index("--run-id") + 1]
            agent_validation.write_report(root, report)
            return 0
        assert not narrowed_report
        return agent_review.main(command[2:], root=cwd)

    assert agent_finish.main(
        ["--task", "Workflow task."], root=root, runner=runner,
    ) == (2 if narrowed_report else 0)
    assert checks_run == ["ruff", "pytest", "compileall"]
    packet = root / agent_finish.PACKET_PATH
    if narrowed_report:
        assert "validation scope does not match" in capsys.readouterr().err
        assert not packet.exists()
    else:
        content = packet.read_text(encoding="utf-8")
        assert "refs/heads/main" in content and "app/module.py" in content
        assert "- pytest: PASS" in content


def _commit_runtime_on_feature(root):
    git = agent_validation._git
    git(root, "switch", "-c", "feature")
    runtime = root / "app/module.py"
    runtime.parent.mkdir()
    runtime.write_text("VALUE = 1\n", encoding="utf-8")
    git(root, "add", "app/module.py")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "runtime")


def test_managed_review_rejects_report_after_trusted_base_moves(workflow_repository):
    root = workflow_repository
    _commit_runtime_on_feature(root)
    assert agent_check.main([], runner=lambda check: 0, availability=lambda check: None) == 0
    report = json.loads((root / agent_validation.REPORT_PATH).read_bytes())
    agent_validation._git(root, "update-ref", "refs/heads/main", "HEAD")
    assert agent_validation.source_identity(root) == report["source_before"]
    with pytest.raises(agent_check.DiscoveryError, match="validation scope does not match"):
        agent_review.capture_validated_state(root, report["run_id"])


@pytest.mark.parametrize(("flag", "documentation"), [
    ("--skip-worktree", False), ("--assume-unchanged", True),
])
def test_invisible_index_blocks_clean_or_docs_only_finish_and_review(repository, flag, documentation):
    git = agent_validation._git
    runtime = repository / "runtime.py"
    runtime.write_text("VALUE = 1\n", encoding="utf-8")
    git(repository, "add", "runtime.py")
    git(repository, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "runtime")
    if documentation:
        (repository / "base.md").write_text("Updated documentation.\n", encoding="utf-8")
    assert agent_check.main([], runner=lambda check: pytest.fail("unexpected check")) == 0
    report = json.loads((repository / agent_validation.REPORT_PATH).read_bytes())
    assert report["checks"] == []
    assert agent_review.capture_validated_state(repository, report["run_id"])[1] == report

    git(repository, "update-index", flag, "runtime.py")
    # Bytes remain identical to the report. The visibility flag alone makes
    # the earlier source observation insufficient for the current handoff.
    assert agent_validation.source_identity(repository) is None
    with pytest.raises(agent_check.DiscoveryError, match="validation source does not match"):
        agent_review.capture_validated_state(repository, report["run_id"])
    runtime.write_text("VALUE = 2\n", encoding="utf-8")
    packet = repository / agent_finish.PACKET_PATH
    packet.write_text("prior packet", encoding="utf-8")

    def runner(command, root):
        assert command[1].endswith("agent_check.py")
        return agent_check.main(command[2:], runner=lambda check: pytest.fail("unexpected check"))

    assert agent_finish.main(["--task", "Workflow task."], root=repository, runner=runner) == 2
    assert not packet.exists()


def test_visibility_lost_after_checks_blocks_managed_handoff(workflow_repository):
    root = workflow_repository

    def run(check):
        if check.key == "compileall":
            agent_validation._git(root, "update-index", "--assume-unchanged", "base.md")
        return 0

    def runner(command, cwd):
        assert command[1].endswith("agent_check.py")
        return agent_check.main(command[2:], runner=run, availability=lambda check: None)

    assert agent_finish.main(["--task", "Workflow task."], root=root, runner=runner) == 2
    assert not (root / agent_finish.PACKET_PATH).exists()
    assert not (root / agent_validation.REPORT_PATH).exists()


@pytest.mark.parametrize("outcome", ("PASS", "FAIL", "UNAVAILABLE"))
def test_required_workflow_check_controls_packet_publication(workflow_repository, outcome):
    commands = []

    def run(check):
        commands.append(check.command)
        if check.key == "pytest-workflow":
            if outcome == "UNAVAILABLE":
                raise FileNotFoundError("pytest command unavailable")
            if outcome == "FAIL":
                return 7
        return 0

    def runner(command, root):
        if command[1].endswith("agent_check.py"):
            return agent_check.main(command[2:], runner=run, availability=lambda check: None)
        assert outcome == "PASS"
        return agent_review.main(command[2:], root=root)

    root = workflow_repository
    expected_exit = {"PASS": 0, "FAIL": 1, "UNAVAILABLE": 3}[outcome]
    assert agent_finish.main(["--task", "Workflow task."], root=root, runner=runner) == expected_exit
    report = json.loads((root / agent_validation.REPORT_PATH).read_bytes())
    assert [check["id"] for check in report["checks"]] == ["ruff", "pytest-workflow", "compileall"]
    result = report["checks"][1]
    command = ["python", *commands[1][1:]]
    assert result["required"] is result["launch_attempted"] is True
    assert result["selected_command"] == command
    assert result["executed_command"] == (None if outcome == "UNAVAILABLE" else command)
    assert result["outcome"] == outcome
    packet = root / agent_finish.PACKET_PATH
    if outcome == "PASS":
        content = packet.read_text(encoding="utf-8")
        assert "pytest-workflow: PASS" in content
        assert " ".join(command) in content
        assert "Fresh independent review and full hosted CI remain required." in content
    else:
        assert not packet.exists()


@pytest.mark.parametrize(
    ("scope", "problem"),
    [
        ("workflow", "full-instead-of-workflow"),
        ("full", "workflow-instead-of-full"),
        ("workflow", "wrong-command"),
        ("workflow", "missing-command"),
        ("workflow", "omitted-check"),
        ("workflow", "wrong-base"),
        ("workflow", "incomplete-scope"),
    ],
)
def test_finish_rejects_incompatible_workflow_evidence(workflow_repository, scope, problem):
    root = workflow_repository
    if scope == "full":
        (root / "unknown.dat").touch()

    def runner(command, cwd):
        assert command[1].endswith("agent_check.py")
        assert agent_check.main(command[2:], runner=lambda check: 0, availability=lambda check: None) == 0
        report = json.loads((root / agent_validation.REPORT_PATH).read_bytes())
        result = report["checks"][1]
        assert result["id"] == ("pytest" if scope == "full" else "pytest-workflow")
        if problem.endswith("instead-of-full") or problem.endswith("instead-of-workflow"):
            replacement = "pytest-workflow" if scope == "full" else "pytest"
            check = next(check for check in agent_check.available_checks() if check.key == replacement)
            result.update(
                id=replacement, selected_command=agent_check.command_identity(check),
                executed_command=agent_check.command_identity(check),
            )
        elif problem == "wrong-command":
            result["executed_command"].pop()
        elif problem == "missing-command":
            del result["executed_command"]
        elif problem == "omitted-check":
            report["checks"].pop(1)
        elif problem == "wrong-base":
            report["base_commit"] = "0" * 40
        else:
            report["changed_files"].clear()
        agent_validation.write_report(root, report)
        return 0

    assert agent_finish.main(["--task", "Workflow task."], root=root, runner=runner) == 2
    assert not (root / agent_finish.PACKET_PATH).exists()


@pytest.mark.parametrize(
    ("runner_exit", "blocked", "outcome", "expected_exit"),
    [(0, False, "PASS", 0), (7, False, "FAIL", 1), (0, True, "INCOMPLETE", 3), (7, True, "FAIL", 1)],
)
def test_report_reflects_actual_execution(repository, runner_exit, blocked, outcome, expected_exit):
    (repository / "action_openapi.yaml").touch()
    (repository / "Dockerfile").touch()
    started = []
    ticks = iter(range(0, 10_000, 100))
    clock = agent_validation.Clock(lambda: datetime(2026, 11, 22, tzinfo=UTC), ticks.__next__)

    def run(check):
        started.append(check.key)
        return runner_exit if check.key == "pytest" else 0

    exit_code = agent_check.main(
        [],
        runner=run,
        availability=lambda check: "unavailable" if blocked and check.needs_docker else None,
        clock=clock,
    )

    report = json.loads((repository / agent_validation.REPORT_PATH).read_bytes())
    assert report["schema_version"] == 3
    assert report["started_at"] == "2026-11-22T00:00:00.000000+00:00"
    assert report["elapsed_ns"] == 1100
    assert report["base_ref"] == "refs/heads/main"
    assert report["base_commit"] == report["source_before"]["head"]
    assert report["merge_base"] == report["source_before"]["head"]
    assert report["source_unchanged"] is True
    assert report["source_after"] == report["source_before"]
    assert report["changed_files"] == [
        {"path": "Dockerfile", "areas": ["docker"]},
        {"path": "action_openapi.yaml", "areas": ["api"]},
    ]
    assert report["manual_requirements"] == [agent_check.ACTION_OPENAPI_MANUAL]
    assert report["overall_outcome"] == outcome
    assert report["exit_code"] == exit_code == expected_exit
    checks = report["checks"]
    assert [check["id"] for check in checks] == ["ruff", "pytest", "compileall", "compose", "docker-build"]
    assert [check["id"] for check in checks if check["executed_command"]] == started
    assert all(check["elapsed_ns"] == 100 and check["required"] for check in checks)
    assert [check["outcome"] for check in checks] == [
        "PASS", "PASS" if runner_exit == 0 else "FAIL", "PASS",
        *(["UNAVAILABLE"] * 2 if blocked else ["PASS"] * 2),
    ]
    assert checks[1]["exit_code"] == runner_exit
    assert checks[1]["executed_command"] == ["python", "-m", "pytest", "-q", "--ignore=tests/e2e"]
    assert checks[-1]["unavailable_stage"] == ("prerequisite" if blocked else None)
    assert checks[-1]["launch_attempted"] is not blocked
    assert report["environment"]["python_version"]


def test_report_omits_sensitive_command_environment_and_error_data(repository, monkeypatch, capsys):
    secret = "private-value-do-not-export"
    monkeypatch.setenv("API_KEY", secret)
    ruff = agent_check.available_checks()[0]
    custom = agent_check.Check("custom", "custom", ("cmd", secret), (("TOKEN", secret),))
    monkeypatch.setattr(agent_check, "select_checks", lambda *args, **kwargs: (ruff, custom))

    def run(check):
        if check == ruff:
            return 0
        raise OSError(f"launch failed: {secret} in {repository}")

    assert agent_check.main(["--changed-file", "scripts/example.py"], runner=run) == 3
    raw = (repository / agent_validation.REPORT_PATH).read_text(encoding="utf-8")
    assert secret not in raw
    assert str(repository).replace("\\", "\\\\") not in raw
    assert secret not in capsys.readouterr().out
    assert json.loads(raw)["checks"][0]["selected_command"][0] == "python"
    assert "API_KEY" not in raw
    custom_result = json.loads(raw)["checks"][1]
    assert custom_result["outcome"] == "UNAVAILABLE"
    assert custom_result["selected_command"] is None
    assert custom_result["unavailable_stage"] == "launch"


def test_atomic_report_preserves_complete_previous_report_on_failed_replace(repository, monkeypatch):
    original = {"run_id": "original"}
    agent_validation.write_report(repository, original)
    path = repository / agent_validation.REPORT_PATH

    def fail_replace(temporary, destination):
        assert json.loads(temporary.read_bytes()) == {"run_id": "replacement"}
        assert json.loads(destination.read_bytes()) == original
        raise PermissionError("replacement denied")

    monkeypatch.setattr(agent_validation.os, "replace", fail_replace)
    with pytest.raises(PermissionError):
        agent_validation.write_report(repository, {"run_id": "replacement"})
    assert json.loads(path.read_bytes()) == original
    assert list(path.parent.iterdir()) == [path]


def test_dry_run_leaves_previous_report_untouched(repository):
    agent_validation.write_report(repository, {"run_id": "original"})
    path = repository / agent_validation.REPORT_PATH
    previous = path.read_bytes()
    assert agent_check.main(["--dry-run"], runner=lambda check: pytest.fail("unexpected check")) == 0
    assert path.read_bytes() == previous


def test_reporting_error_preserves_failed_check_exit_code(repository, monkeypatch, capsys):
    def deny_write(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(agent_validation, "write_report", deny_write)
    assert agent_check.main(
        ["--changed-file", "scripts/example.py"], runner=lambda check: 7,
    ) == 1
    assert "Validation report could not be written" in capsys.readouterr().err


def test_source_identity_changes_for_staged_delta_and_ignores_private_artifacts(repository):
    report_path = repository / agent_validation.REPORT_PATH
    report_path.parent.mkdir()
    baseline = agent_validation.source_identity(repository)
    report_path.write_text("local report", encoding="utf-8")
    assert agent_validation.source_identity(repository) == baseline

    tracked = repository / "base.md"
    tracked.write_bytes(b"staged")
    subprocess.run(
        ["git", "add", tracked.name], cwd=repository, check=True, capture_output=True,
    )
    tracked.write_bytes(b"base\n")
    assert agent_validation.source_identity(repository) != baseline


REFERENCE_FIXTURE = "tests/fixtures/vb151_g_reference.json"


def _commit_reference_fixture(root):
    fixture = root / REFERENCE_FIXTURE
    fixture.parent.mkdir(parents=True)
    lf = (Path(__file__).parent.parent / REFERENCE_FIXTURE).read_bytes()
    assert b"\r" not in lf and b"\n" in lf
    fixture.write_bytes(lf)
    (root / ".gitattributes").write_text(f"{REFERENCE_FIXTURE} text eol=lf\n", encoding="ascii")
    git = agent_validation._git
    git(root, "add", ".gitattributes", REFERENCE_FIXTURE)
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    return fixture, lf, lf.replace(b"\n", b"\r\n")


def test_normalized_tracked_fixture_invalidates_stale_validation(repository):
    fixture, lf, crlf = _commit_reference_fixture(repository)
    relative = REFERENCE_FIXTURE
    git = agent_validation._git
    assert agent_check.main([]) == 0
    report = json.loads((repository / agent_validation.REPORT_PATH).read_bytes())
    assert agent_review.capture_validated_state(repository, report["run_id"])[1] == report

    fixture.write_bytes(crlf)
    # Refresh the index stat cache through Git's clean conversion. The staged
    # blob and the Git-visible worktree both remain unchanged.
    git(repository, "add", "--", relative)
    assert git(repository, "status", "--porcelain") == b""
    assert git(repository, "hash-object", relative) == git(repository, "rev-parse", f"HEAD:{relative}")
    assert hashlib.sha256(lf).digest() != hashlib.sha256(fixture.read_bytes()).digest()

    after = agent_validation.source_identity(repository)
    before = report["source_before"]
    assert after["head"] == before["head"]
    assert after["git_visible_delta_sha256"] == before["git_visible_delta_sha256"]
    assert after["raw_tracked_sha256"] != before["raw_tracked_sha256"]
    with pytest.raises(agent_check.DiscoveryError, match="validation source does not match"):
        agent_review.capture_validated_state(repository, report["run_id"])


@pytest.mark.parametrize("scope", ("clean", "documentation", "workflow", "staged"))
def test_fixture_normalized_before_first_snapshot_requires_full_validation(workflow_repository, scope):
    root = workflow_repository
    fixture, _, crlf = _commit_reference_fixture(root)
    workflow = "scripts/agent_review.py"
    if scope in {"clean", "documentation"}:
        (root / workflow).write_text("# base\n", encoding="utf-8")
    if scope == "documentation":
        (root / "base.md").write_text("changed docs\n", encoding="utf-8")
    fixture.write_bytes(crlf)
    git = agent_validation._git
    if scope == "staged":
        git(root, "add", "--", REFERENCE_FIXTURE)
    oid = git(root, "rev-parse", f"HEAD:{REFERENCE_FIXTURE}")
    assert git(root, "rev-parse", f":{REFERENCE_FIXTURE}") == oid
    assert git(root, "hash-object", REFERENCE_FIXTURE) == oid
    assert git(root, "hash-object", "--no-filters", REFERENCE_FIXTURE) != oid
    assert REFERENCE_FIXTURE.encode() not in git(root, "diff", "--name-only")
    if scope == "staged":
        assert REFERENCE_FIXTURE.encode() not in git(root, "status", "--porcelain")

    assert agent_check.main([], runner=lambda check: 0, availability=lambda check: None) == 0
    report = json.loads((root / agent_validation.REPORT_PATH).read_bytes())
    assert report["source_unchanged"] is True
    assert report["raw_changed_files"] == [REFERENCE_FIXTURE]
    assert [check["id"] for check in report["checks"]] == ["ruff", "pytest", "compileall"]
    assert agent_review.capture_validated_state(root, report["run_id"])[1] == report

    if scope == "workflow":
        # Even a matching source fingerprint and valid focused command cannot
        # certify an earlier report which omitted the raw-only input.
        report["raw_changed_files"].clear()
        report["changed_files"] = [item for item in report["changed_files"] if item["path"] == workflow]
        focused = next(check for check in agent_check.available_checks() if check.key == "pytest-workflow")
        command = agent_check.command_identity(focused)
        report["checks"][1].update(id=focused.key, selected_command=command, executed_command=command)
        agent_validation.write_report(root, report)
        with pytest.raises(agent_check.DiscoveryError, match="validation scope does not match"):
            agent_review.capture_validated_state(root, report["run_id"])


def test_normalization_hidden_source_drift_after_selection_blocks_finish(workflow_repository):
    root = workflow_repository
    fixture, _, crlf = _commit_reference_fixture(root)
    observed = []

    def run(check):
        observed.append(check.key)
        if check.key == "pytest-workflow":
            fixture.write_bytes(crlf)
        return 0

    def runner(command, cwd):
        assert command[1].endswith("agent_check.py")
        return agent_check.main(command[2:], runner=run, availability=lambda check: None)

    assert agent_finish.main(["--task", "Workflow task."], root=root, runner=runner) == 2
    assert observed == ["ruff", "pytest-workflow", "compileall"]
    assert not (root / agent_finish.PACKET_PATH).exists()


def test_ignored_nested_attributes_block_exact_normalization_reproducer_and_managed_handoff(
    workflow_repository, capsys,
):
    root = workflow_repository
    fixture, lf, crlf = _commit_reference_fixture(root)
    override = "tests/fixtures/.gitattributes"
    git = agent_validation._git
    (root / ".git/info/exclude").write_text(f"{override}\n", encoding="ascii")
    assert agent_check.main([], runner=lambda check: 0, availability=lambda check: None) == 0
    report = json.loads((root / agent_validation.REPORT_PATH).read_bytes())
    assert [check["id"] for check in report["checks"]] == ["ruff", "pytest-workflow", "compileall"]

    (root / override).write_text(f"{fixture.name} text eol=crlf\n", encoding="ascii")
    assert git(root, "check-ignore", override).strip() == override.encode()
    assert git(root, "check-attr", "eol", "--", REFERENCE_FIXTURE).strip().endswith(b": eol: crlf")
    assert fixture.read_bytes() == lf
    # Attribute provenance alone invalidates the previous proof while the
    # tracked bytes and the ordinary Git delta still match that report.
    assert agent_validation.source_identity(root) is None
    with pytest.raises(agent_check.DiscoveryError, match="validation source does not match"):
        agent_review.capture_validated_state(root, report["run_id"])

    fixture.write_bytes(crlf)
    git(root, "add", "--", REFERENCE_FIXTURE)
    oid = git(root, "rev-parse", f"HEAD:{REFERENCE_FIXTURE}")
    assert git(root, "rev-parse", f":{REFERENCE_FIXTURE}") == oid
    assert git(root, "hash-object", REFERENCE_FIXTURE) == oid
    assert git(root, "hash-object", "--no-filters", REFERENCE_FIXTURE) != oid
    assert git(root, "status", "--porcelain").splitlines() == [b" M scripts/agent_review.py"]

    def runner(command, cwd):
        assert command[1].endswith("agent_check.py")
        return agent_check.main(command[2:], runner=lambda check: pytest.fail("unexpected check"))

    assert agent_finish.main(["--task", "Workflow task."], root=root, runner=runner) == 2
    assert "unindexed worktree .gitattributes" in capsys.readouterr().err
    assert not (root / agent_finish.PACKET_PATH).exists()


def test_tracked_missing_input_is_stable_and_head_includes_staged_removal(repository):
    git = agent_validation._git
    path = repository / "base.md"
    baseline = agent_validation.source_identity(repository)
    path.unlink()
    missing = agent_validation.source_identity(repository)
    assert missing["raw_tracked_sha256"] != baseline["raw_tracked_sha256"]
    assert agent_validation.source_identity(repository) == missing

    git(repository, "rm", "--cached", "--", "base.md")
    assert b"base.md" not in git(repository, "ls-files", "--cached", "-z").split(b"\0")
    (repository / ".git/info/exclude").write_bytes(b"base.md\n")
    path.write_bytes(b"bytes pytest can still open\n")
    stage = git(repository, "status", "--porcelain")
    retained = agent_validation.source_identity(repository)
    path.write_bytes(b"new retained bytes\n")
    changed = agent_validation.source_identity(repository)
    assert git(repository, "status", "--porcelain") == stage
    assert retained["raw_tracked_sha256"] != changed["raw_tracked_sha256"]


def test_raw_tracked_input_rejects_reparse_or_symlink(repository, tmp_path_factory):
    external = tmp_path_factory.mktemp("redirect")
    outside_file = external / "data.md"
    outside_file.write_bytes(b"outside")
    path = repository / "base.md"
    path.unlink()
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(path), str(external)],
            check=True, capture_output=True,
        )
    else:
        path.symlink_to(outside_file)
    head = agent_validation._git(repository, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(OSError, match="not a regular file"):
        agent_validation._raw_tracked_digest(repository, head)


@pytest.mark.parametrize("problem", ["unreadable", "changed-after-read"])
def test_raw_tracked_identity_is_unavailable_on_read_errors_or_drift(repository, monkeypatch, problem):
    assert agent_validation.source_identity(repository) is not None
    original = agent_validation.repository_reader

    @contextmanager
    def modified(root):
        with original(root) as read:
            @contextmanager
            def open_input(parts):
                target = parts == ("base.md",)
                if target and problem == "unreadable":
                    raise PermissionError("injected failure to read")
                with read(parts) as stream:
                    yield stream
                if target:
                    (root / "base.md").write_bytes(b"changed while still fingerprinting")
            yield open_input

    monkeypatch.setattr(agent_validation, "repository_reader", modified)
    assert agent_validation.source_identity(repository) is None


def test_finish_links_only_its_run_and_rejects_worktree_drift(repository):
    changed = repository / "untracked.md"
    changed.write_text("first", encoding="utf-8")

    def run(command, root):
        if command[1].endswith("agent_check.py"):
            return agent_check.main(command[2:])
        return agent_review.main(command[2:], root=root)

    assert agent_finish.main(["--task", "Exact task."], root=repository, runner=run) == 0
    report = json.loads((repository / agent_validation.REPORT_PATH).read_bytes())
    packet = (repository / agent_finish.PACKET_PATH).read_text(encoding="utf-8")
    assert report["run_id"] in packet
    assert report["source_before"]["git_visible_delta_sha256"] in packet
    assert "No automated checks were selected." in packet
    assert "could not be matched" not in packet

    changed.write_text("other", encoding="utf-8")
    with pytest.raises(agent_check.DiscoveryError, match="does not match"):
        agent_review.capture_validated_state(repository, report["run_id"])
    with pytest.raises(agent_check.DiscoveryError, match="missing, stale or malformed"):
        agent_review.capture_validated_state(repository, "foreign")


@pytest.mark.parametrize("expression", ["HEAD~0", "HEAD@{0}"])
def test_revision_expressions_record_only_resolved_commit(repository, expression, capsys):
    assert agent_check.main(["--base", expression]) == 0
    raw = (repository / agent_validation.REPORT_PATH).read_text(encoding="utf-8")
    report = json.loads(raw)
    assert report["change_discovery"] == "git"
    assert report["base_commit"] == report["merge_base"] == report["source_before"]["head"]
    assert report["base_ref"] in {None, "refs/heads/main"}
    assert expression not in raw
    assert expression not in capsys.readouterr().out


def test_invalid_base_does_not_print_raw_expression(repository, capsys):
    secret = "synthetic-private-revision-expression"
    assert agent_check.main([f"--base=:/not-found-{secret}"]) == 2
    assert secret not in capsys.readouterr().err
    assert not (repository / agent_validation.REPORT_PATH).exists()


@pytest.mark.parametrize("problem", ["missing", "stale", "malformed", "nonfinite", "no-execution", "omitted-check"])
def test_finish_rejects_missing_stale_malformed_or_incomplete_result(repository, capsys, problem):
    scripts = repository / "scripts"
    scripts.mkdir()
    (scripts / "required.py").touch()
    packet = repository / agent_finish.PACKET_PATH
    packet.parent.mkdir()
    packet.write_text("prior successful packet", encoding="utf-8")

    def run(command, root):
        assert command[1].endswith("agent_check.py")
        if problem == "missing":
            return 0
        assert agent_check.main(
            command[2:], runner=lambda check: 0, availability=lambda check: None,
        ) == 0
        path = root / agent_validation.REPORT_PATH
        if problem == "malformed":
            path.write_text("{invalid", encoding="utf-8")
        else:
            report = json.loads(path.read_bytes())
            if problem == "stale":
                report["run_id"] = "a different invocation"
            elif problem == "nonfinite":
                report["extra"] = float("nan")
            elif problem == "no-execution":
                report["checks"][0]["executed_command"] = None
            else:
                report["checks"].pop()
            agent_validation.write_report(root, report)
        return 0

    assert agent_finish.main(["--task", "Exact task."], root=repository, runner=run) == 2
    assert not packet.exists()
    assert "Verification: PASS" not in capsys.readouterr().out


@pytest.mark.parametrize("when", ["during-check", "after-check", "after-render", "at-publish"])
def test_finish_rejects_source_drift_through_publication(repository, monkeypatch, capsys, when):
    scripts = repository / "scripts"
    scripts.mkdir()
    changed = scripts / "required.py"
    changed.write_text("first\n", encoding="utf-8")
    packet = repository / agent_finish.PACKET_PATH

    def pass_check(check):
        if when == "during-check" and check.key == "ruff":
            changed.write_text("during\n", encoding="utf-8")
        return 0

    def run(command, root):
        if command[1].endswith("agent_check.py"):
            result = agent_check.main(command[2:], runner=pass_check, availability=lambda check: None)
            if when == "after-check":
                (repository / "Dockerfile").touch()  # Selects new required container checks.
            return result
        result = agent_review.main(command[2:], root=root)
        if when == "after-render":
            assert result == 0
            changed.write_text("after\n", encoding="utf-8")
        return result

    if when == "at-publish":
        replace = os.replace

        def publish(source, target):
            replace(source, target)
            if target == packet:
                changed.write_text("publishing\n", encoding="utf-8")

        monkeypatch.setattr(agent_finish.os, "replace", publish)

    assert agent_finish.main(["--task", "Exact task."], root=repository, runner=run) == 2
    assert not packet.exists()
    assert "Verification: PASS" not in capsys.readouterr().out


def test_review_checks_diff_digest_before_writing(repository, monkeypatch):
    (repository / "untracked.md").write_text("content", encoding="utf-8")
    collect = agent_review.collect_repository_state

    def run(command, root):
        if command[1].endswith("agent_check.py"):
            return agent_check.main(command[2:])

        def altered_state(*args, **kwargs):
            state = collect(*args, **kwargs)
            return agent_review.replace(state, diff=state.diff + b"different rendered diff")

        monkeypatch.setattr(agent_review, "collect_repository_state", altered_state)
        return agent_review.main(command[2:], root=root)

    assert agent_finish.main(["--task", "Exact task."], root=repository, runner=run) == 2
    assert not (repository / agent_finish.PACKET_PATH).exists()


def test_finish_does_not_clobber_another_managed_run(repository, capsys):
    packet = repository / agent_finish.PACKET_PATH
    packet.parent.mkdir()
    packet.write_text("owned by another run", encoding="utf-8")
    with agent_finish._packet_lock(packet.parent):
        result = agent_finish.main(
            ["--task", "Other run."], root=repository, runner=lambda *args: pytest.fail("entered finish"),
        )
    assert result == 2
    assert "packet lock" in capsys.readouterr().err
    assert packet.read_text(encoding="utf-8") == "owned by another run"


def _focused_record(repository):
    evidence = ".agent/focused-wsl-20261123T120000.json"
    path = repository / evidence
    path.parent.mkdir()
    source = agent_validation.source_identity(repository)
    record = {
        "schema_version": 1,
        "started_at": "2026-11-23T12:00:00+00:00",
        "ended_at": "2026-11-23T12:01:00+00:00",
        "elapsed_ns": 60_000_000_000,
        "exit_code": 0,
        "source_before": source,
        "source_after": source,
        "counts": {"passed": 10, "failed": 0, "skipped": 0},
        "environment": {"system": "Linux", "wsl": True},
        "command": ["python", "-m", "pytest", "-o", "addopts=", "-q", "tests/test_agent_validation.py"],
    }
    path.write_text(json.dumps(record), encoding="utf-8")
    return evidence, path, record


def test_focused_evidence_must_match_source_to_enter_packet(repository):
    evidence, _, _ = _focused_record(repository)

    def run(command, root):
        if command[1].endswith("agent_check.py"):
            return agent_check.main(command[2:])
        return agent_review.main(command[2:], root=root)

    arguments = ["--task", "Exact task.", "--focused-results", evidence]
    packet = repository / agent_finish.PACKET_PATH
    assert agent_finish.main(arguments, root=repository, runner=run) == 0
    assert "10 passed, 0 failed, 0 skipped" in packet.read_text(encoding="utf-8")
    (repository / "untracked.md").touch()
    assert agent_finish.main(arguments, root=repository, runner=run) == 2
    assert not packet.exists()


@pytest.mark.parametrize("when", ["render-input", "staged-packet", "published-packet"])
def test_finish_rejects_focused_substitution_even_when_original_is_restored(
    repository, monkeypatch, capsys, when,
):
    evidence, path, record = _focused_record(repository)
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    packet = repository / agent_finish.PACKET_PATH

    def change_counts(output):
        content = output.read_bytes()
        assert b"10 passed, 0 failed" in content
        output.write_bytes(content.replace(b"10 passed, 0 failed", b"99 passed, 0 failed"))

    def run(command, root):
        if command[1].endswith("agent_check.py"):
            return agent_check.main(command[2:])
        assert f"--focused-sha256={digest}" in command
        if when == "render-input":
            record["counts"]["passed"] = 99
            path.write_text(json.dumps(record), encoding="utf-8")
            assert "99 passed" in agent_validation.focused_records(
                root, [evidence], record["source_before"],
            )[0].summary
        try:
            result = agent_review.main(command[2:], root=root)
        finally:
            path.write_bytes(original)
        if when == "staged-packet":
            assert result == 0
            change_counts(Path(command[command.index("--output") + 1]))
        return result

    if when == "published-packet":
        replace = os.replace

        def corrupt_publication(source, destination):
            replace(source, destination)
            if destination == packet:
                change_counts(packet)

        monkeypatch.setattr(agent_finish.os, "replace", corrupt_publication)

    assert agent_finish.main(
        ["--task", "Exact task.", "--focused-results", evidence], root=repository, runner=run,
    ) == 2
    assert not packet.exists()
    assert path.read_bytes() == original
    assert "Verification: PASS" not in capsys.readouterr().out
