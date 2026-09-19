#!/usr/bin/env python3
"""Prepare compact implementation context for a VaultBridge Codex session."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from . import agent_check, agent_review
else:
    import agent_check
    import agent_review

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = Path(".agent/task_packet.md")
MAX_CONTEXT_FILE_BYTES = 20_000
MAX_CONTEXT_BYTES = 50_000
MAX_PACKET_BYTES = 100_000
MAX_STATUS_PATHS = 50


@dataclass(frozen=True)
class RepositoryState:
    branch: str
    status_lines: tuple[str, ...]


@dataclass(frozen=True)
class ContextFile:
    path: str
    content: str | None
    omission_reason: str | None = None


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise agent_check.DiscoveryError(detail or f"git {' '.join(args)} failed")
    return completed.stdout


def collect_repository_state(root: Path) -> RepositoryState:
    agent_review.ensure_repository_root(root)
    status = _git(
        root,
        "-c",
        "core.quotepath=false",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).decode("utf-8", "replace")
    return RepositoryState(
        branch=agent_review.current_branch(root),
        status_lines=tuple(sorted(line for line in status.splitlines() if line)),
    )


def _normalized_real_path(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _is_within_repository(root: Path, path: Path) -> bool:
    repository = _normalized_real_path(root)
    candidate = _normalized_real_path(path)
    try:
        return os.path.commonpath((repository, candidate)) == repository
    except ValueError:
        return False


def _repository_path(
    root: Path,
    supplied: Path,
    *,
    label: str,
    must_exist: bool,
) -> tuple[Path, str]:
    candidate = supplied if supplied.is_absolute() else root / supplied
    resolved = candidate.resolve(strict=must_exist)
    if not _is_within_repository(root, resolved):
        raise agent_check.DiscoveryError(f"{label} is outside the repository: {supplied}")
    if must_exist and not resolved.is_file():
        kind = "a directory" if resolved.is_dir() else "not a regular file"
        raise agent_check.DiscoveryError(f"{label} is {kind}: {supplied}")
    if not must_exist and candidate.exists() and candidate.is_dir():
        raise agent_check.DiscoveryError(f"{label} is a directory: {supplied}")
    relative = resolved.relative_to(root.resolve()).as_posix()
    return candidate, relative


def load_context_files(root: Path, supplied_files: Sequence[Path]) -> tuple[ContextFile, ...]:
    files: list[ContextFile] = []
    seen: set[str] = set()
    total_bytes = 0
    for supplied in supplied_files:
        candidate, relative = _repository_path(
            root,
            supplied,
            label="context file",
            must_exist=True,
        )
        identity = _normalized_real_path(candidate)
        if identity in seen:
            continue
        seen.add(identity)

        raw = candidate.read_bytes()
        if len(raw) > MAX_CONTEXT_FILE_BYTES:
            files.append(
                ContextFile(
                    relative,
                    None,
                    f"its {len(raw):,} bytes exceed the {MAX_CONTEXT_FILE_BYTES:,}-byte "
                    "per-file limit",
                )
            )
            continue
        if total_bytes + len(raw) > MAX_CONTEXT_BYTES:
            files.append(
                ContextFile(
                    relative,
                    None,
                    f"including it would exceed the {MAX_CONTEXT_BYTES:,}-byte total context limit",
                )
            )
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise agent_check.DiscoveryError(
                f"context file is not valid UTF-8: {relative}"
            ) from error
        files.append(ContextFile(relative, content))
        total_bytes += len(raw)
    return tuple(files)


def _fenced(content: str, language: str = "") -> str:
    longest_run = max((len(run) for run in re.findall(r"`+", content)), default=0)
    fence = "`" * max(3, longest_run + 1)
    separator = "" if content.endswith("\n") else "\n"
    return f"{fence}{language}\n{content}{separator}{fence}"


def _changed_files_summary(status_lines: Sequence[str]) -> str:
    if not status_lines:
        return "- (none)"

    shown = status_lines[:MAX_STATUS_PATHS]
    listing = "\n".join(f"- `{line}`" for line in shown)
    omitted = len(status_lines) - len(shown)
    if not omitted:
        return listing
    return (
        f"Pre-existing changed files: {len(status_lines)}\n\n"
        f"Showing first {len(shown)}:\n\n{listing}\n\n"
        f"{omitted} additional paths omitted from the packet.\n"
        "Inspect `git status` directly before editing."
    )


def render_packet(
    state: RepositoryState,
    *,
    task_context: str,
    context_files: Sequence[ContextFile] = (),
) -> str:
    status = "clean" if not state.status_lines else "dirty"
    changed = _changed_files_summary(state.status_lines)

    if context_files:
        context_sections: list[str] = []
        for context_file in context_files:
            heading = f"### `{context_file.path}`"
            if context_file.content is None:
                body = (
                    f"Omitted because {context_file.omission_reason}. Inspect this file directly "
                    "if the task requires it; no partial content was included."
                )
            else:
                body = _fenced(context_file.content, "text")
            context_sections.extend((heading, body))
        relevant_context = "\n\n".join(context_sections)
    else:
        relevant_context = (
            "No context files were explicitly requested, so no repository content was embedded. "
            "Inspect the task-relevant implementation and tests directly before editing. The tool "
            "does not infer requirements from branch names or Git history."
        )

    sections = (
        "# VaultBridge Implementation Task Packet",
        "## Original task",
        _fenced(task_context, "text"),
        "## Repository guidance",
        "`AGENTS.md` is the authoritative repository rule set. "
        "`docs/CODEX_PLAYBOOK.md` is the authoritative Codex workflow guide. Read the exact "
        "task-relevant backlog section, architecture/status material, implementation, and tests "
        "when the task requires them; this packet intentionally does not copy those documents.",
        "## Relevant context",
        relevant_context,
        "## Current repository state",
        f"- Current branch: `{state.branch}`\n"
        f"- Working tree before implementation: **{status}**\n\n"
        f"Changed files present before implementation:\n\n{changed}",
        "## Implementation instructions",
        "- Inspect the relevant code and tests before editing.\n"
        "- Preserve unrelated and pre-existing changes.\n"
        "- Make the smallest coherent change that follows existing architecture and conventions.\n"
        "- Add or update focused tests for changed behavior.\n"
        "- Do not commit unless explicitly requested.\n"
        "- Finish with `python scripts/agent_finish.py` using the same task source.",
    )
    return "\n\n".join(sections) + "\n"


def render_packet_with_limit(
    state: RepositoryState,
    *,
    task_context: str,
    context_files: Sequence[ContextFile],
    max_packet_bytes: int,
) -> str:
    packet = render_packet(
        state,
        task_context=task_context,
        context_files=context_files,
    )
    if len(packet.encode("utf-8")) <= max_packet_bytes:
        return packet

    bounded_context = [
        ContextFile(
            context_file.path,
            None,
            f"including it would exceed the {max_packet_bytes:,}-byte overall packet limit",
        )
        if context_file.content is not None
        else context_file
        for context_file in context_files
    ]
    packet = render_packet(
        state,
        task_context=task_context,
        context_files=bounded_context,
    )
    if len(packet.encode("utf-8")) > max_packet_bytes:
        raise agent_check.DiscoveryError(
            f"complete task packet exceeds the {max_packet_bytes:,}-byte limit even without "
            "optional embedded context"
        )

    for index, context_file in enumerate(context_files):
        if context_file.content is None:
            continue
        candidate_context = list(bounded_context)
        candidate_context[index] = context_file
        candidate_packet = render_packet(
            state,
            task_context=task_context,
            context_files=candidate_context,
        )
        if len(candidate_packet.encode("utf-8")) <= max_packet_bytes:
            bounded_context = candidate_context
            packet = candidate_packet
    return packet


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    task_source = parser.add_mutually_exclusive_group(required=True)
    task_source.add_argument("--task-file", type=Path, help="read the exact task from this file")
    task_source.add_argument("--task", help="use exact inline task context")
    parser.add_argument(
        "--context-file",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="embed an explicit repository file; repeat for multiple files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=f"write inside the repository instead of {PACKET_PATH.as_posix()}",
    )
    parser.add_argument(
        "--max-packet-bytes",
        type=_positive_int,
        default=MAX_PACKET_BYTES,
        help=f"maximum encoded packet size (default: {MAX_PACKET_BYTES})",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = REPOSITORY_ROOT) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        agent_review.ensure_repository_root(root)
        output_supplied = args.output or PACKET_PATH
        output, _ = _repository_path(
            root,
            output_supplied,
            label="output",
            must_exist=False,
        )
        if args.task_file is not None and agent_review.paths_refer_to_same_file(
            args.task_file, output
        ):
            raise agent_check.DiscoveryError(
                "--task-file and --output must refer to different files; choose a separate output"
            )
        for supplied_context in args.context_file:
            context_path, _ = _repository_path(
                root,
                supplied_context,
                label="context file",
                must_exist=True,
            )
            if agent_review.paths_refer_to_same_file(context_path, output):
                raise agent_check.DiscoveryError(
                    "--context-file and --output must refer to different files; "
                    "choose a separate output"
                )

        task_context = (
            args.task_file.read_text(encoding="utf-8")
            if args.task_file is not None
            else args.task
        )
        if not task_context.strip():
            parser.error("task context must not be empty")
        if len(task_context.encode("utf-8")) > args.max_packet_bytes:
            raise agent_check.DiscoveryError(
                f"task exceeds the supported size for the {args.max_packet_bytes:,}-byte "
                "packet limit"
            )

        state = collect_repository_state(root)
        task_packet = render_packet(state, task_context=task_context)
        if len(task_packet.encode("utf-8")) > args.max_packet_bytes:
            empty_task_packet = render_packet(state, task_context="")
            if len(empty_task_packet.encode("utf-8")) <= args.max_packet_bytes:
                raise agent_check.DiscoveryError(
                    f"task exceeds the supported size for the {args.max_packet_bytes:,}-byte "
                    "packet limit"
                )
        context_files = load_context_files(root, args.context_file)
        packet = render_packet_with_limit(
            state,
            task_context=task_context,
            context_files=context_files,
            max_packet_bytes=args.max_packet_bytes,
        )
        packet_bytes = packet.encode("utf-8")
        if len(packet_bytes) > args.max_packet_bytes:
            raise agent_check.DiscoveryError(
                f"complete task packet exceeds the {args.max_packet_bytes:,}-byte limit"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(packet, encoding="utf-8", newline="\n")
    except (OSError, UnicodeError, agent_check.DiscoveryError) as error:
        print(f"ERROR task packet generation: {error}", file=sys.stderr)
        return 2

    relative_output = output.resolve().relative_to(root.resolve()).as_posix()
    print(f"Task packet: {relative_output}")
    print(
        "\nNext step:\n"
        f"Start the implementation Codex session with `{relative_output}`.\n"
        "Inspect task-relevant code directly as needed; the packet is a compact starting context."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
