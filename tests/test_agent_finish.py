from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import agent_check, agent_finish, agent_review


@pytest.fixture
def repository(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    (tmp_path / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
    monkeypatch.setattr(agent_check, "REPOSITORY_ROOT", tmp_path)
    return tmp_path


class FakeRunner:
    def __init__(self, results: Sequence[int]) -> None:
        self.results = iter(results)
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str], root: Path) -> int:
        normalized = tuple(command)
        self.commands.append(normalized)
        result = next(self.results)
        if Path(normalized[1]).name == "agent_check.py" and result == 0:
            return agent_check.main(normalized[2:], runner=lambda check: pytest.fail(check.key))
        if Path(normalized[1]).name == "agent_review.py":
            output = Path(normalized[normalized.index("--output") + 1])
            if result == 0:
                return agent_review.main(normalized[2:], root=root)
            output.write_text("partial packet\n", encoding="utf-8")
        return result


def test_successful_check_generates_packet_and_handoff(repository, capsys):
    runner = FakeRunner([0, 0])

    exit_code = agent_finish.main(["--task", "Implement it."], root=repository, runner=runner)

    assert exit_code == 0
    content = (repository / ".agent/review_packet.md").read_text(encoding="utf-8")
    assert "## Original task\n\n```text\nImplement it.\n```" in content
    assert "## Recorded validation" in content
    output = capsys.readouterr().out
    assert "Verification: PASS" in output
    assert "Review packet: .agent/review_packet.md" in output
    assert "Start a fresh Codex session" in output


def test_failed_check_creates_no_packet(tmp_path):
    runner = FakeRunner([1])

    exit_code = agent_finish.main(["--task", "Implement it."], root=tmp_path, runner=runner)

    assert exit_code == 1
    assert not (tmp_path / ".agent/review_packet.md").exists()
    assert len(runner.commands) == 1
    assert Path(runner.commands[0][1]).name == "agent_check.py"


def test_stale_packet_is_removed_before_failed_verification(tmp_path):
    packet = tmp_path / ".agent/review_packet.md"
    packet.parent.mkdir()
    packet.write_text("stale packet\n", encoding="utf-8")

    exit_code = agent_finish.main(
        ["--task", "Implement it."], root=tmp_path, runner=FakeRunner([3])
    )

    assert exit_code == 3
    assert not packet.exists()


@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("name", ["review_packet.md", "REVIEW_PACKET.MD"])
def test_standalone_review_cannot_publish_during_a_failing_finish(repository, capsys, monkeypatch, absolute, name):
    monkeypatch.chdir(repository)
    packet = repository / agent_finish.PACKET_PATH
    output = packet.with_name(name)
    if not absolute:
        output = output.relative_to(repository)
    packet.parent.mkdir()
    packet.write_bytes(b"old packet")

    def fail_check(command, root):
        assert Path(command[1]).name == "agent_check.py"
        assert not packet.exists()
        assert agent_review.main(
            ["--task", "Overlapping standalone review.", "--output", str(output)], root=root,
        ) == 2
        return 7

    assert agent_finish.main(["--task", "Managed task."], root=repository, runner=fail_check) == 7
    assert not packet.exists() and not output.exists()
    assert "reserved for agent_finish.py" in capsys.readouterr().err


def test_review_generation_failure_is_nonzero_and_leaves_no_packet(repository):
    runner = FakeRunner([0, 7])

    exit_code = agent_finish.main(["--task", "Implement it."], root=repository, runner=runner)

    assert exit_code == 7
    assert not (repository / ".agent/review_packet.md").exists()
    assert not list((repository / ".agent").glob("review-*"))


def test_task_string_is_forwarded(repository):
    runner = FakeRunner([0, 0])

    assert agent_finish.main(["--task", "Exact inline task."], root=repository, runner=runner) == 0

    assert runner.commands[1][2:4] == ("--task", "Exact inline task.")


def test_task_file_is_forwarded(repository):
    runner = FakeRunner([0, 0])
    task_file = repository / "task.md"
    task_file.write_text("Exact file task.\n", encoding="utf-8")

    assert agent_finish.main(["--task-file", str(task_file)], root=repository, runner=runner) == 0

    assert runner.commands[1][2:4] == ("--task-file", str(task_file))


def test_managed_packet_cannot_be_direct_task_file(tmp_path, capsys):
    packet = tmp_path / ".agent/review_packet.md"
    packet.parent.mkdir()
    original = "Preserve this task.\n"
    packet.write_text(original, encoding="utf-8")
    runner = FakeRunner([])

    exit_code = agent_finish.main(
        ["--task-file", str(packet)], root=tmp_path, runner=runner
    )

    assert exit_code == 2
    assert "must not refer to the managed review packet" in capsys.readouterr().err
    assert packet.read_text(encoding="utf-8") == original
    assert runner.commands == []


def test_relative_managed_packet_task_file_is_rejected(
    tmp_path, capsys, monkeypatch
):
    packet = tmp_path / ".agent/review_packet.md"
    packet.parent.mkdir()
    original = "Preserve this relative task.\n"
    packet.write_text(original, encoding="utf-8")
    runner = FakeRunner([])
    monkeypatch.chdir(tmp_path)

    exit_code = agent_finish.main(
        ["--task-file", ".agent/../.agent/review_packet.md"],
        root=tmp_path,
        runner=runner,
    )

    assert exit_code == 2
    assert "must not refer to the managed review packet" in capsys.readouterr().err
    assert packet.read_text(encoding="utf-8") == original
    assert runner.commands == []


def test_hardlink_task_alias_to_managed_packet_is_rejected(tmp_path, capsys):
    packet = tmp_path / ".agent/review_packet.md"
    packet.parent.mkdir()
    original = "Preserve this hardlinked task.\n"
    packet.write_text(original, encoding="utf-8")
    alias = tmp_path / "task.md"
    try:
        alias.hardlink_to(packet)
    except OSError as error:
        pytest.skip(f"hardlinks are not supported: {error}")
    runner = FakeRunner([])

    exit_code = agent_finish.main(
        ["--task-file", str(alias)], root=tmp_path, runner=runner
    )

    assert exit_code == 2
    assert "must not refer to the managed review packet" in capsys.readouterr().err
    assert packet.read_text(encoding="utf-8") == original
    assert alias.read_text(encoding="utf-8") == original
    assert runner.commands == []


def test_symlink_task_alias_to_managed_packet_is_rejected(tmp_path, capsys):
    packet = tmp_path / ".agent/review_packet.md"
    packet.parent.mkdir()
    original = "Preserve this symlinked task.\n"
    packet.write_text(original, encoding="utf-8")
    alias = tmp_path / "task.md"
    try:
        alias.symlink_to(packet)
    except OSError as error:
        pytest.skip(f"symlinks are not supported: {error}")
    runner = FakeRunner([])

    exit_code = agent_finish.main(
        ["--task-file", str(alias)], root=tmp_path, runner=runner
    )

    assert exit_code == 2
    assert "must not refer to the managed review packet" in capsys.readouterr().err
    assert packet.read_text(encoding="utf-8") == original
    assert alias.read_text(encoding="utf-8") == original
    assert runner.commands == []


def test_task_file_identity_error_is_controlled_without_mutation(
    tmp_path, capsys, monkeypatch
):
    task_file = tmp_path / "task.md"
    original_task = "Preserve this task input.\n"
    task_file.write_text(original_task, encoding="utf-8")
    packet = tmp_path / ".agent/review_packet.md"
    packet.parent.mkdir()
    original_packet = "Preserve this stale packet.\n"
    packet.write_text(original_packet, encoding="utf-8")
    runner = FakeRunner([])

    def raise_permission_error(first: Path, second: Path) -> bool:
        raise PermissionError("identity check denied")

    monkeypatch.setattr(
        agent_finish.agent_review,
        "paths_refer_to_same_file",
        raise_permission_error,
    )

    exit_code = agent_finish.main(
        ["--task-file", str(task_file)], root=tmp_path, runner=runner
    )

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "ERROR agent finish:" in error
    assert "identity check denied" in error
    assert runner.commands == []
    assert task_file.read_text(encoding="utf-8") == original_task
    assert packet.read_text(encoding="utf-8") == original_packet


def test_task_sources_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit) as error:
        agent_finish.main(
            ["--task", "Inline.", "--task-file", "task.md"],
            root=tmp_path,
            runner=FakeRunner([]),
        )

    assert error.value.code == 2


def test_output_path_is_fixed_under_agent_directory(repository):
    runner = FakeRunner([0, 0])

    assert agent_finish.main(["--task", "Implement it."], root=repository, runner=runner) == 0

    output_index = runner.commands[1].index("--output")
    staged = Path(runner.commands[1][output_index + 1])
    packet = repository / ".agent/review_packet.md"
    assert staged.parent.parent == packet.parent and staged.name == packet.name
    assert packet.is_file() and not staged.exists()


def test_only_local_check_and_review_scripts_are_invoked(repository):
    runner = FakeRunner([0, 0])

    assert agent_finish.main(["--task", "Implement it."], root=repository, runner=runner) == 0

    assert [Path(command[1]).name for command in runner.commands] == [
        "agent_check.py",
        "agent_review.py",
    ]


def test_default_runner_uses_direct_subprocess(monkeypatch, tmp_path):
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent_finish.subprocess, "run", fake_run)

    assert agent_finish._run(("python", "script.py"), tmp_path) == 0
    assert observed == {
        "command": ("python", "script.py"),
        "kwargs": {"cwd": tmp_path, "check": False},
    }
