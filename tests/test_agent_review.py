from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import agent_check, agent_review


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
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(
        root,
        "-c",
        "user.name=Agent Review",
        "-c",
        "user.email=agent@example.invalid",
        "commit",
        "-m",
        "base",
    )
    return root


def _changed_repository(tmp_path: Path) -> Path:
    root = _repository(tmp_path)
    _git(root, "checkout", "-b", "feature/review")
    (root / "scripts").mkdir()
    (root / "scripts" / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "scripts/tracked.py")
    _git(
        root,
        "-c",
        "user.name=Agent Review",
        "-c",
        "user.email=agent@example.invalid",
        "commit",
        "-m",
        "tracked change",
    )
    (root / "README.md").write_text("base\nworking tree\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "untracked.md").write_text("untracked\n", encoding="utf-8")
    return root


def test_repository_state_discovers_branch_base_and_all_changed_files(tmp_path):
    root = _changed_repository(tmp_path)

    state = agent_review.collect_repository_state(root, base_ref="main")

    assert state.branch == "feature/review"
    assert state.discovery.base_ref == "main"
    assert len(state.discovery.merge_base or "") == 40
    assert state.discovery.files == (
        "README.md",
        "docs/untracked.md",
        "scripts/tracked.py",
    )
    diff = state.diff.decode("utf-8")
    assert "+working tree" in diff
    assert "+VALUE = 1" in diff
    assert "+untracked" in diff
    assert "3 files changed" not in state.diff_statistics  # Per-untracked stats stay explicit.


def test_packet_reuses_agent_check_classification_and_verification(tmp_path):
    root = _changed_repository(tmp_path)
    state = agent_review.collect_repository_state(root, base_ref="main")

    packet = agent_review.render_packet(state, task_context="Review the change.")

    assert agent_check.classify_files(state.discovery.files) == {"docs", "python"}
    assert "## Affected areas\n\n- docs\n- python" in packet
    assert "- ruff" in packet
    assert "- pytest (unit/integration, includes tests/eval)" in packet
    assert "- compileall" in packet
    assert "## Original task\n\n```text\nReview the change.\n```" in packet


def test_small_diff_is_included_with_additions_and_removals(tmp_path):
    root = _repository(tmp_path)
    (root / "README.md").write_text("replacement\n", encoding="utf-8")

    packet = agent_review.render_packet(
        agent_review.collect_repository_state(root, base_ref="main"),
        task_context="Replace the README content.",
    )

    assert "```diff" in packet
    assert "-base" in packet
    assert "+replacement" in packet


def test_oversized_diff_is_omitted_without_partial_content(tmp_path):
    root = _repository(tmp_path)
    marker = "UNIQUE-LARGE-DIFF-MARKER"
    (root / "README.md").write_text(marker * 20, encoding="utf-8")
    state = agent_review.collect_repository_state(root, base_ref="main")

    packet = agent_review.render_packet(
        state,
        task_context="Review a large README change.",
        max_diff_bytes=20,
    )

    assert "Full diff omitted" in packet
    assert "exceeding the 20-byte packet limit" in packet
    assert "not a truncation" in packet
    assert marker not in packet
    assert "README.md" in packet


def test_task_file_and_output_file_are_supported_and_output_is_excluded(
    tmp_path, monkeypatch
):
    root = _changed_repository(tmp_path)
    task_file = tmp_path / "task.md"
    task_file.write_text("Review this exact task.\n", encoding="utf-8")
    output = root / "review_packet.md"
    output.write_text("old generated packet\n", encoding="utf-8")
    monkeypatch.chdir(root)

    exit_code = agent_review.main(
        ["--base", "main", "--task-file", str(task_file), "--output", str(output)],
        root=root,
    )

    assert exit_code == 0
    packet = output.read_text(encoding="utf-8")
    assert "## Original task" in packet
    assert "Review this exact task." in packet
    assert "`review_packet.md`" not in packet


@pytest.mark.parametrize("use_relative_task_path", [False, True])
def test_task_file_cannot_also_be_output(
    tmp_path, capsys, monkeypatch, use_relative_task_path
):
    root = _repository(tmp_path)
    task_file = root / "task.md"
    original = "Preserve this task.\n"
    task_file.write_text(original, encoding="utf-8")
    monkeypatch.chdir(root)
    task_argument = Path("task.md") if use_relative_task_path else task_file

    exit_code = agent_review.main(
        [
            "--base",
            "main",
            "--task-file",
            str(task_argument),
            "--output",
            str(task_file.resolve()),
        ],
        root=root,
    )

    assert exit_code == 2
    assert "--task-file and --output must refer to different files" in capsys.readouterr().err
    assert task_file.read_text(encoding="utf-8") == original


def test_task_file_cannot_use_hardlink_as_output(tmp_path, capsys, monkeypatch):
    root = _repository(tmp_path)
    task_file = root / "task.md"
    output = root / "review_packet.md"
    original = "Preserve this hardlinked task.\n"
    task_file.write_text(original, encoding="utf-8")
    try:
        output.hardlink_to(task_file)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"hardlink creation is unsupported on this test filesystem: {error}")
    monkeypatch.chdir(root)

    exit_code = agent_review.main(
        [
            "--base",
            "main",
            "--task-file",
            str(task_file),
            "--output",
            str(output),
        ],
        root=root,
    )

    assert exit_code == 2
    assert "--task-file and --output must refer to different files" in capsys.readouterr().err
    assert task_file.read_text(encoding="utf-8") == original


def test_inline_task_is_supported(tmp_path, capsys):
    root = _repository(tmp_path)

    exit_code = agent_review.main(
        ["--base", "main", "--task", "Review the inline task."],
        root=root,
    )

    assert exit_code == 0
    packet = capsys.readouterr().out
    assert "## Original task" in packet
    assert "Review the inline task." in packet


def test_missing_task_context_is_rejected(capsys):
    with pytest.raises(SystemExit) as error:
        agent_review.main([])

    assert error.value.code == 2
    assert "one of the arguments --task-file --task is required" in capsys.readouterr().err


def test_task_sources_are_mutually_exclusive(tmp_path, capsys):
    task_file = tmp_path / "task.md"
    task_file.write_text("Task from file.\n", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        agent_review.main(["--task-file", str(task_file), "--task", "Inline task."])

    assert error.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_clean_repository_packet_is_explicit(tmp_path):
    root = _repository(tmp_path)

    state = agent_review.collect_repository_state(root, base_ref="main")
    packet = agent_review.render_packet(state, task_context="Review a clean repository.")

    assert state.discovery.files == ()
    assert state.diff == b""
    assert "- (none)" in packet
    assert "No changed files were discovered." in packet


def test_nested_repository_directory_is_rejected(tmp_path, capsys):
    root = _repository(tmp_path)
    nested = root / "nested"
    nested.mkdir()

    exit_code = agent_review.main(
        ["--base", "main", "--task", "Review from the wrong directory."],
        root=nested,
    )

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "ERROR review packet generation:" in error
    assert "must run against the repository root" in error


def test_linked_worktree_root_is_accepted(tmp_path):
    root = _repository(tmp_path)
    worktree = tmp_path / "linked-worktree"
    _git(root, "worktree", "add", "-b", "review-worktree", str(worktree))

    state = agent_review.collect_repository_state(worktree, base_ref="main")

    assert state.branch == "review-worktree"
    assert state.discovery.files == ()


def test_non_git_directory_is_rejected(tmp_path, capsys, monkeypatch):
    root = tmp_path / "not-a-repository"
    root.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(root))

    exit_code = agent_review.main(
        ["--base", "main", "--task", "Review a non-Git directory."],
        root=root,
    )

    assert exit_code == 2
    assert "ERROR review packet generation:" in capsys.readouterr().err


def test_invalid_diff_limit_is_rejected():
    with pytest.raises(SystemExit) as error:
        agent_review.build_parser().parse_args(
            ["--task", "Review the diff limit.", "--max-diff-bytes", "0"]
        )

    assert error.value.code == 2
