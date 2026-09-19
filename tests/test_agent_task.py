from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import agent_task


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    (root / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "README.md")
    _git(
        root,
        "-c",
        "user.name=Agent Task",
        "-c",
        "user.email=agent@example.invalid",
        "commit",
        "-m",
        "base",
    )
    return root


def test_inline_task_generates_default_packet(tmp_path, capsys):
    root = _repository(tmp_path)

    exit_code = agent_task.main(["--task", "Implement the exact change."], root=root)

    assert exit_code == 0
    packet_path = root / ".agent/task_packet.md"
    assert packet_path.is_file()
    packet = packet_path.read_text(encoding="utf-8")
    assert "## Original task\n\n```text\nImplement the exact change.\n```" in packet
    assert "## Repository guidance" in packet
    assert "## Relevant context" in packet
    assert "## Current repository state" in packet
    assert "## Implementation instructions" in packet
    assert "Task packet: .agent/task_packet.md" in capsys.readouterr().out


def test_task_file_generates_packet_at_explicit_output(tmp_path):
    root = _repository(tmp_path)
    task_file = tmp_path / "task.md"
    task_file.write_text("Exact task from file.\nSecond line.\n", encoding="utf-8")

    exit_code = agent_task.main(
        [
            "--task-file",
            str(task_file),
            "--output",
            "artifacts/implementation.md",
        ],
        root=root,
    )

    assert exit_code == 0
    packet = (root / "artifacts/implementation.md").read_text(encoding="utf-8")
    assert "Exact task from file.\nSecond line.\n```" in packet


def test_missing_task_is_rejected(capsys):
    with pytest.raises(SystemExit) as error:
        agent_task.main([])

    assert error.value.code == 2
    assert "one of the arguments --task-file --task is required" in capsys.readouterr().err


def test_conflicting_task_sources_are_rejected(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("Task.\n", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        agent_task.main(["--task", "Inline.", "--task-file", str(task_file)])

    assert error.value.code == 2


def test_nested_repository_root_is_rejected(tmp_path, capsys):
    root = _repository(tmp_path)
    nested = root / "nested"
    nested.mkdir()

    exit_code = agent_task.main(["--task", "Task."], root=nested)

    assert exit_code == 2
    assert "must run against the repository root" in capsys.readouterr().err


def test_context_file_is_included_and_labeled(tmp_path):
    root = _repository(tmp_path)
    context = root / "src/example.py"
    context.parent.mkdir()
    context.write_text("VALUE = 'žluťoučký'\n", encoding="utf-8")

    exit_code = agent_task.main(
        ["--task", "Use the example.", "--context-file", "src/example.py"],
        root=root,
    )

    assert exit_code == 0
    packet = (root / ".agent/task_packet.md").read_text(encoding="utf-8")
    assert "### `src/example.py`" in packet
    assert "VALUE = 'žluťoučký'" in packet


def test_context_file_outside_repository_is_rejected(tmp_path, capsys):
    root = _repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")

    exit_code = agent_task.main(
        ["--task", "Task.", "--context-file", str(outside)], root=root
    )

    assert exit_code == 2
    assert "context file is outside the repository" in capsys.readouterr().err
    assert not (root / ".agent/task_packet.md").exists()


def test_context_file_symlink_escape_is_rejected_where_supported(tmp_path, capsys):
    root = _repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks are unsupported: {error}")

    exit_code = agent_task.main(
        ["--task", "Task.", "--context-file", "linked.txt"], root=root
    )

    assert exit_code == 2
    assert "context file is outside the repository" in capsys.readouterr().err


def test_oversized_context_is_omitted_without_truncation(tmp_path):
    root = _repository(tmp_path)
    marker = "UNIQUE-OVERSIZED-CONTEXT"
    context = root / "large.txt"
    context.write_text(marker * 1_000, encoding="utf-8")

    exit_code = agent_task.main(
        ["--task", "Task.", "--context-file", "large.txt"], root=root
    )

    assert exit_code == 0
    packet = (root / ".agent/task_packet.md").read_text(encoding="utf-8")
    assert "exceed the 20,000-byte per-file limit" in packet
    assert "no partial content was included" in packet
    assert marker not in packet


@pytest.mark.parametrize("relative", [False, True])
def test_task_file_cannot_be_output(tmp_path, capsys, monkeypatch, relative):
    root = _repository(tmp_path)
    task_file = root / "task.md"
    original = "Preserve this task.\n"
    task_file.write_text(original, encoding="utf-8")
    monkeypatch.chdir(root)
    task_argument = Path("task.md") if relative else task_file

    exit_code = agent_task.main(
        [
            "--task-file",
            str(task_argument),
            "--output",
            str(task_file.resolve()),
        ],
        root=root,
    )

    assert exit_code == 2
    assert "must refer to different files" in capsys.readouterr().err
    assert task_file.read_text(encoding="utf-8") == original


def test_hardlink_task_alias_cannot_be_output(tmp_path, capsys):
    root = _repository(tmp_path)
    task_file = root / "task.md"
    output = root / "packet.md"
    original = "Preserve hardlinked task.\n"
    task_file.write_text(original, encoding="utf-8")
    try:
        output.hardlink_to(task_file)
    except OSError as error:
        pytest.skip(f"hardlinks are unsupported: {error}")

    exit_code = agent_task.main(
        ["--task-file", str(task_file), "--output", str(output)], root=root
    )

    assert exit_code == 2
    assert "must refer to different files" in capsys.readouterr().err
    assert task_file.read_text(encoding="utf-8") == original


def test_dirty_working_tree_is_reported(tmp_path):
    root = _repository(tmp_path)
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    (root / "new.txt").write_text("new\n", encoding="utf-8")

    assert agent_task.main(["--task", "Task."], root=root) == 0

    packet = (root / ".agent/task_packet.md").read_text(encoding="utf-8")
    assert "Working tree before implementation: **dirty**" in packet
    assert "M README.md" in packet
    assert "?? new.txt" in packet


def test_clean_working_tree_is_reported(tmp_path):
    root = _repository(tmp_path)

    assert agent_task.main(["--task", "Task."], root=root) == 0

    packet = (root / ".agent/task_packet.md").read_text(encoding="utf-8")
    assert "Working tree before implementation: **clean**" in packet
    assert "Changed files present before implementation:\n\n- (none)" in packet


def test_git_state_is_not_mutated(tmp_path):
    root = _repository(tmp_path)
    (root / "README.md").write_text("unstaged\n", encoding="utf-8")
    head_before = _git(root, "rev-parse", "HEAD").stdout
    branch_before = _git(root, "branch", "--show-current").stdout
    index_before = _git(root, "diff", "--cached", "--binary").stdout
    status_before = _git(root, "status", "--porcelain=v1").stdout

    assert agent_task.main(["--task", "Task."], root=root) == 0

    assert _git(root, "rev-parse", "HEAD").stdout == head_before
    assert _git(root, "branch", "--show-current").stdout == branch_before
    assert _git(root, "diff", "--cached", "--binary").stdout == index_before
    assert _git(root, "status", "--porcelain=v1").stdout == status_before


def test_output_outside_repository_is_rejected(tmp_path, capsys):
    root = _repository(tmp_path)
    outside = tmp_path / "packet.md"

    exit_code = agent_task.main(
        ["--task", "Task.", "--output", str(outside)], root=root
    )

    assert exit_code == 2
    assert "output is outside the repository" in capsys.readouterr().err
    assert not outside.exists()


def test_context_directory_is_rejected(tmp_path, capsys):
    root = _repository(tmp_path)
    directory = root / "docs"
    directory.mkdir()

    exit_code = agent_task.main(
        ["--task", "Task.", "--context-file", "docs"], root=root
    )

    assert exit_code == 2
    assert "context file is a directory" in capsys.readouterr().err


def test_context_file_cannot_be_output(tmp_path, capsys):
    root = _repository(tmp_path)
    context = root / "context.md"
    original = "Preserve this context.\n"
    context.write_text(original, encoding="utf-8")

    exit_code = agent_task.main(
        ["--task", "Task.", "--context-file", "context.md", "--output", "context.md"],
        root=root,
    )

    assert exit_code == 2
    assert "--context-file and --output must refer to different files" in capsys.readouterr().err
    assert context.read_text(encoding="utf-8") == original


def test_relative_context_cannot_alias_absolute_output(tmp_path, capsys):
    root = _repository(tmp_path)
    context = root / "context.md"
    original = "Preserve relative context.\n"
    context.write_text(original, encoding="utf-8")

    exit_code = agent_task.main(
        [
            "--task",
            "Task.",
            "--context-file",
            "context.md",
            "--output",
            str(context.resolve()),
        ],
        root=root,
    )

    assert exit_code == 2
    assert context.read_text(encoding="utf-8") == original


def test_hardlink_context_alias_cannot_be_output(tmp_path, capsys):
    root = _repository(tmp_path)
    context = root / "context.md"
    output = root / "packet.md"
    original = "Preserve hardlinked context.\n"
    context.write_text(original, encoding="utf-8")
    try:
        output.hardlink_to(context)
    except OSError as error:
        pytest.skip(f"hardlinks are unsupported: {error}")

    exit_code = agent_task.main(
        ["--task", "Task.", "--context-file", "context.md", "--output", "packet.md"],
        root=root,
    )

    assert exit_code == 2
    assert context.read_text(encoding="utf-8") == original


def test_symlink_context_alias_cannot_be_output_where_supported(tmp_path, capsys):
    root = _repository(tmp_path)
    context = root / "context.md"
    output = root / "packet.md"
    original = "Preserve symlinked context.\n"
    context.write_text(original, encoding="utf-8")
    try:
        output.symlink_to(context)
    except OSError as error:
        pytest.skip(f"symlinks are unsupported: {error}")

    exit_code = agent_task.main(
        ["--task", "Task.", "--context-file", "context.md", "--output", "packet.md"],
        root=root,
    )

    assert exit_code == 2
    assert context.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("task_source", ["inline", "file"])
def test_oversized_task_is_rejected_without_output(tmp_path, capsys, task_source):
    root = _repository(tmp_path)
    arguments = ["--max-packet-bytes", "2000"]
    if task_source == "inline":
        arguments.extend(("--task", "x" * 2001))
    else:
        task_file = tmp_path / "task.md"
        task_file.write_text("x" * 2001, encoding="utf-8")
        arguments.extend(("--task-file", str(task_file)))

    exit_code = agent_task.main(arguments, root=root)

    assert exit_code == 2
    assert "task exceeds the supported size" in capsys.readouterr().err
    assert not (root / ".agent/task_packet.md").exists()


def test_task_that_cannot_fit_with_packet_framing_is_rejected(tmp_path, capsys):
    root = _repository(tmp_path)

    exit_code = agent_task.main(
        ["--task", "x" * 1_000, "--max-packet-bytes", "2000"], root=root
    )

    assert exit_code == 2
    assert "task exceeds the supported size" in capsys.readouterr().err
    assert not (root / ".agent/task_packet.md").exists()


def test_large_repository_status_is_bounded_and_reports_omissions(tmp_path):
    root = _repository(tmp_path)
    for index in range(70):
        (root / f"changed-{index:02}.txt").write_text("changed\n", encoding="utf-8")

    assert agent_task.main(["--task", "Task."], root=root) == 0

    packet = (root / ".agent/task_packet.md").read_text(encoding="utf-8")
    assert "Pre-existing changed files: 70" in packet
    assert "Showing first 50:" in packet
    assert "20 additional paths omitted from the packet." in packet
    assert "Inspect `git status` directly before editing." in packet
    assert "changed-49.txt" in packet
    assert "changed-50.txt" not in packet


def test_optional_context_is_omitted_to_keep_packet_within_limit(tmp_path):
    root = _repository(tmp_path)
    context = root / "context.md"
    context.write_text("optional context\n" * 500, encoding="utf-8")
    maximum = 3_000

    exit_code = agent_task.main(
        [
            "--task",
            "Task.",
            "--context-file",
            "context.md",
            "--max-packet-bytes",
            str(maximum),
        ],
        root=root,
    )

    assert exit_code == 0
    packet_path = root / ".agent/task_packet.md"
    packet = packet_path.read_text(encoding="utf-8")
    assert packet_path.stat().st_size <= maximum
    assert "overall packet limit" in packet
    assert "optional context" not in packet


def test_final_size_failure_leaves_no_partial_output(tmp_path, capsys):
    root = _repository(tmp_path)
    output = root / "packet.md"
    original = "Existing packet remains intact.\n"
    output.write_text(original, encoding="utf-8")

    exit_code = agent_task.main(
        [
            "--task",
            "Task.",
            "--max-packet-bytes",
            "100",
            "--output",
            "packet.md",
        ],
        root=root,
    )

    assert exit_code == 2
    assert "complete task packet exceeds" in capsys.readouterr().err
    assert not (root / ".agent/task_packet.md").exists()
    assert output.read_text(encoding="utf-8") == original
