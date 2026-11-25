from __future__ import annotations

import subprocess
from itertools import product
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
    assert "No context files were explicitly requested" in packet
    assert "## Current repository state" in packet
    assert "## Implementation instructions" in packet
    assert packet.count("**Risk and feasibility preflight.**") == 1
    assert sorted(path.name for path in packet_path.parent.iterdir()) == [packet_path.name]
    assert "Task packet: .agent/task_packet.md" in capsys.readouterr().out


def test_task_output_cannot_replace_the_managed_review_packet(tmp_path, capsys):
    root = _repository(tmp_path)
    managed = root / ".agent/review_packet.md"
    managed.parent.mkdir()
    managed.write_bytes(b"managed packet\n")
    assert agent_task.main(
        ["--task", "Prepare context.", "--output", str(managed)], root=root,
    ) == 2
    assert "reserved for agent_finish.py" in capsys.readouterr().err
    assert managed.read_bytes() == b"managed packet\n"


def test_task_file_generates_packet_at_explicit_output(tmp_path):
    root = _repository(tmp_path)
    task_file = tmp_path / "task.md"
    task = "Exact task from file.\n\n  Preserve žluťoučký and ``` fences.\n"
    task_file.write_bytes(task.encode("utf-8"))

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
    assert f"## Original task\n\n````text\n{task}````\n\n## Repository guidance" in packet
    assert packet.count("**Risk and feasibility preflight.**") == 1
    assert not (root / ".agent/task_packet.md").exists()


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


@pytest.mark.parametrize("task", ["x" * 300, "ž" * 300], ids=["ascii", "multibyte"])
def test_context_packet_fits_exact_byte_limit(tmp_path, capsys, task):
    root = _repository(tmp_path)
    relative = "tests/eval/graph_corpus/Drafts/Shared.md"
    context_path = root / relative
    context_path.parent.mkdir(parents=True)
    context_path.write_bytes((agent_task.REPOSITORY_ROOT / relative).read_bytes())
    state = agent_task.collect_repository_state(root)
    contexts = agent_task.load_context_files(root, [Path(relative)])
    expected = agent_task.render_packet(state, task_context=task, context_files=contexts).encode("utf-8")
    maximum = len(expected)
    assert len(agent_task.render_packet(state, task_context=task).encode("utf-8")) > maximum

    arguments = ["--task", task, "--context-file", relative, "--max-packet-bytes"]
    output = root / ".agent/task_packet.md"
    assert agent_task.main([*arguments, str(maximum)], root=root) == 0
    assert output.read_bytes() == expected

    assert agent_task.main([*arguments, str(maximum - 1)], root=root) == 2
    assert "complete task packet exceeds" in capsys.readouterr().err
    assert output.read_bytes() == expected


@pytest.mark.parametrize("reverse_order", [False, True])
def test_mixed_context_packet_fits_review_reproducer(tmp_path, monkeypatch, capsys, reverse_order):
    root = _repository(tmp_path)
    short_path = "tests/eval/graph_corpus/Drafts/Shared.md"
    long_path = "README.md"
    short_file = root / short_path
    short_file.parent.mkdir(parents=True)
    # Use the reviewer's checkout bytes so the exact cap also applies on LF checkouts.
    short_content = (agent_task.REPOSITORY_ROOT / short_path).read_text(encoding="utf-8")
    short_file.write_bytes(short_content.replace("\n", "\r\n").encode("utf-8"))
    (root / long_path).write_bytes((agent_task.REPOSITORY_ROOT / long_path).read_bytes())
    state = agent_task.RepositoryState(
        "chore/workflow-preflight-integration",
        (" M docs/CODEX_PLAYBOOK.md", " M scripts/agent_task.py", " M tests/test_agent_task.py"),
    )
    monkeypatch.setattr(agent_task, "collect_repository_state", lambda _: state)

    task = "x" * 300
    short, long = agent_task.load_context_files(root, [Path(short_path), Path(long_path)])
    all_context = (long, short) if reverse_order else (short, long)
    arguments = ["--task", task]
    for context in all_context:
        arguments.extend(("--context-file", context.path))
    output = root / ".agent/task_packet.md"

    full = agent_task.render_packet(state, task_context=task, context_files=all_context).encode("utf-8")
    assert agent_task.main([*arguments, "--max-packet-bytes", str(len(full))], root=root) == 0
    assert output.read_bytes() == full

    reason = "including it would exceed the 2,199-byte overall packet limit"
    omitted_long = agent_task.ContextFile(long.path, None, reason)
    mixed_context = (omitted_long, short) if reverse_order else (short, omitted_long)
    mixed = agent_task.render_packet(state, task_context=task, context_files=mixed_context).encode("utf-8")
    assert len(mixed) == 2_199
    assert agent_task.main([*arguments, "--max-packet-bytes", "2199"], root=root) == 0
    assert output.read_bytes() == mixed

    assert agent_task.main([*arguments, "--max-packet-bytes", "2198"], root=root) == 2
    assert "complete task packet exceeds" in capsys.readouterr().err
    assert output.read_bytes() == mixed


@pytest.mark.parametrize("short_position", [0, 1, 2], ids=["first", "middle", "last"])
def test_optional_context_priority_at_all_omitted_limit(tmp_path, monkeypatch, capsys, short_position):
    root = _repository(tmp_path)
    contexts = [agent_task.ContextFile("z-first.md", "A" * 240), agent_task.ContextFile("a-second.md", "B" * 240)]
    contexts.insert(short_position, agent_task.ContextFile("short.md", ""))
    arguments = ["--task", "Task."]
    for entry in contexts:
        (root / entry.path).write_bytes(entry.content.encode("utf-8"))
        arguments.extend(("--context-file", entry.path))
    state = agent_task.RepositoryState("main", ())
    monkeypatch.setattr(agent_task, "collect_repository_state", lambda _: state)
    output = root / ".agent/task_packet.md"

    all_omitted = [_omit_at_limit(entry, 1_995) for entry in contexts]
    omitted_packet = agent_task.render_packet(state, task_context="Task.", context_files=all_omitted)
    assert len(omitted_packet.encode("utf-8")) == 1_995
    for limit in (1_994, 1_995, 1_996):
        selected = [entry if entry.path != "a-second.md" else _omit_at_limit(entry, limit) for entry in contexts]
        expected = agent_task.render_packet(state, task_context="Task.", context_files=selected).encode("utf-8")
        assert len(expected) == 1_933
        assert agent_task.main([*arguments, "--max-packet-bytes", str(limit)], root=root) == 0
        assert output.read_bytes() == expected

    # 1,844 bytes is the minimum, with only the empty file retained.
    assert agent_task.main([*arguments, "--max-packet-bytes", "1843"], root=root) == 2
    assert "complete task packet exceeds" in capsys.readouterr().err
    assert output.read_bytes() == expected


def _omit_at_limit(entry: agent_task.ContextFile, limit: int) -> agent_task.ContextFile:
    if entry.content is None:
        return entry
    reason = f"including it would exceed the {limit:,}-byte overall packet limit"
    return agent_task.ContextFile(entry.path, None, reason)


def _render_context_layouts(state, task, contexts, limit):
    # These fixtures have at most five optional files, so there are at most 32 layouts.
    choices = [(False,) if entry.content is None else (False, True) for entry in contexts]
    omitted = [_omit_at_limit(entry, limit) for entry in contexts]
    layouts = {}
    for retained in product(*choices):
        selected = [
            entry if keep else notice
            for entry, notice, keep in zip(contexts, omitted, retained, strict=True)
        ]
        layouts[retained] = agent_task.render_packet(state, task_context=task, context_files=selected).encode("utf-8")
    return layouts


def _assert_context_selection_at_limits(state, task, contexts, limits):
    previous = None
    for limit in limits:
        layouts = _render_context_layouts(state, task, contexts, limit)
        fitting = [retained for retained, packet in layouts.items() if len(packet) <= limit]
        arguments = dict(task_context=task, context_files=contexts, max_packet_bytes=limit)
        if not fitting:
            with pytest.raises(agent_task.agent_check.DiscoveryError, match="complete task packet exceeds"):
                agent_task.render_packet_with_limit(state, **arguments)
            continue
        # The first differing position decides which fitting layout has priority.
        preferred = max(fitting)
        actual = agent_task.render_packet_with_limit(state, **arguments)
        assert actual.encode("utf-8") == layouts[preferred], (limit, preferred)
        assert len(actual.encode("utf-8")) <= limit
        assert agent_task.render_packet_with_limit(state, **arguments) == actual
        # At a digit boundary the same layout can grow due to the notices.
        if previous is not None and len(layouts[previous]) <= limit:
            assert preferred >= previous, (limit, previous, preferred)
        previous = preferred


@pytest.mark.parametrize("reverse_order", [False, True])
def test_context_selection_matches_bounded_layout_oracle(reverse_order):
    state = agent_task.RepositoryState("main", (" M relevant.md",))
    task = "A task with ž and ```` fences."
    contexts = (
        agent_task.ContextFile("z-first.md", "ž" * 200),
        agent_task.ContextFile("short.md", ""),
        agent_task.ContextFile("equal.md", "x" * 151),
        agent_task.ContextFile("a-second.md", "`" * 100),
        agent_task.ContextFile("small.md", "小\n"),
        agent_task.ContextFile("oversized.md", None, "its bytes exceed the per-file limit"),
    )
    tie = _render_context_layouts(state, task, (contexts[2],), 2_000)
    assert len(tie[(True,)]) == len(tie[(False,)])
    if reverse_order:
        contexts = contexts[::-1]
    layouts = _render_context_layouts(state, task, contexts, 2_000)
    limits = sorted({len(packet) + offset for packet in layouts.values() for offset in (-1, 0, 1)})
    _assert_context_selection_at_limits(state, task, contexts, limits)


def test_context_selection_accounts_for_notice_width_change():
    state = agent_task.RepositoryState("main", ())
    contexts = (
        agent_task.ContextFile("first.md", "a" * 2_000),
        agent_task.ContextFile("second.md", "b" * 4_000),
        agent_task.ContextFile("third.md", "c" * 4_000),
    )
    first_only = (True, False, False)
    size = len(_render_context_layouts(state, "x", contexts, 9_999)[first_only])
    task = "x" * (10_000 - size)
    assert len(_render_context_layouts(state, task, contexts, 9_999)[first_only]) == 9_999
    assert len(_render_context_layouts(state, task, contexts, 10_000)[first_only]) == 10_001
    _assert_context_selection_at_limits(state, task, contexts, range(9_998, 10_003))


@pytest.mark.parametrize("context_length", [100, 200])
def test_optional_context_keeps_request_priority(tmp_path, context_length):
    root = _repository(tmp_path)
    (root / "short.md").write_bytes("ž\n".encode("utf-8"))
    (root / "z-first.md").write_bytes(("ž" * context_length).encode("utf-8"))
    (root / "a-second.md").write_bytes(("á" * context_length).encode("utf-8"))
    arguments = ["--task", "Task."]
    paths = [Path(name) for name in ("short.md", "z-first.md", "a-second.md")]
    for path in paths:
        arguments.extend(("--context-file", path.as_posix()))
    state = agent_task.collect_repository_state(root)
    short, first, second = agent_task.load_context_files(root, paths)
    # Measure with a four-digit placeholder for the limit in the omission notice.
    reason = "including it would exceed the 2,000-byte overall packet limit"
    omitted_second = agent_task.ContextFile(second.path, None, reason)
    expected = agent_task.render_packet(state, task_context="Task.", context_files=(short, first, omitted_second))
    maximum = len(expected.encode("utf-8"))
    output = root / ".agent/task_packet.md"

    assert agent_task.main([*arguments, "--max-packet-bytes", str(maximum)], root=root) == 0
    assert output.read_bytes() == expected.replace("2,000-byte", f"{maximum:,}-byte").encode("utf-8")

    assert agent_task.main([*arguments, "--max-packet-bytes", str(maximum - 1)], root=root) == 0
    reason = f"including it would exceed the {maximum - 1:,}-byte overall packet limit"
    omitted_first = agent_task.ContextFile(first.path, None, reason)
    omitted_second = agent_task.ContextFile(second.path, None, reason)
    smaller = agent_task.render_packet(
        state, task_context="Task.", context_files=(short, omitted_first, omitted_second),
    )
    assert output.read_bytes() == smaller.encode("utf-8")


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
    task = "Task."
    state = agent_task.collect_repository_state(root)
    # The omission notice can fit even when the no-context placeholder is too large.
    maximum = len(agent_task.render_packet(state, task_context=task).encode("utf-8")) - 1

    exit_code = agent_task.main(
        [
            "--task",
            task,
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
    assert packet.count("**Risk and feasibility preflight.**") == 1


@pytest.mark.parametrize("with_context", [False, True])
def test_required_framing_with_preflight_exceeds_limit_and_preserves_output(tmp_path, capsys, with_context):
    root = _repository(tmp_path)
    output = root / ".agent/packet.md"
    task = "Task."
    arguments = ["--task", task, "--output", ".agent/packet.md"]
    if with_context:
        arguments.extend(("--context-file", "README.md"))
    assert agent_task.main(arguments, root=root) == 0
    original = output.read_bytes()
    # Set the limit one byte below the mandatory framing with an empty task block.
    maximum = len(original) - len(task.encode("utf-8")) - 1

    exit_code = agent_task.main(
        [*arguments, "--max-packet-bytes", str(maximum)],
        root=root,
    )

    assert exit_code == 2
    assert "complete task packet exceeds" in capsys.readouterr().err
    assert not (root / ".agent/task_packet.md").exists()
    assert output.read_bytes() == original
