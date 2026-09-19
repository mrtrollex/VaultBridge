#!/usr/bin/env python3
"""Prepare a compact packet for an independent review of the current change."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

if __package__:
    from . import agent_check
else:
    import agent_check

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_DIFF_BYTES = 100_000


@dataclass(frozen=True)
class RepositoryState:
    branch: str
    discovery: agent_check.Discovery
    diff_statistics: str
    diff: bytes


def _git(
    root: Path,
    *args: str,
    allowed_return_codes: tuple[int, ...] = (0,),
) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode not in allowed_return_codes:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise agent_check.DiscoveryError(detail or f"git {' '.join(args)} failed")
    return completed.stdout


def current_branch(root: Path) -> str:
    branch = _git(root, "branch", "--show-current").decode("utf-8", "replace").strip()
    return branch or "(detached HEAD)"


def ensure_repository_root(root: Path) -> None:
    top_level_output = _git(root, "rev-parse", "--show-toplevel").rstrip(b"\r\n")
    if not top_level_output:
        raise agent_check.DiscoveryError("Git did not report a repository top-level directory")

    supplied_root = os.path.normcase(os.path.realpath(root))
    top_level = os.path.normcase(os.path.realpath(os.fsdecode(top_level_output)))
    if supplied_root != top_level:
        raise agent_check.DiscoveryError(
            "review generation must run against the repository root: "
            f"received {root.resolve()}, repository root is {Path(os.fsdecode(top_level_output))}"
        )


def _untracked_files(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                os.fsdecode(item).replace("\\", "/")
                for item in _git(
                    root, "ls-files", "--others", "--exclude-standard", "-z"
                ).split(b"\0")
                if item
            }
        )
    )


def _untracked_diff(root: Path, path: str, *, stat: bool) -> bytes:
    args = ["diff", "--no-index", "--no-ext-diff", "--no-renames"]
    if stat:
        args.append("--stat")
    args.extend(("--", "/dev/null", path))
    return _git(root, *args, allowed_return_codes=(0, 1))


def collect_repository_state(
    root: Path,
    *,
    base_ref: str | None = None,
    excluded_files: Sequence[str] = (),
) -> RepositoryState:
    ensure_repository_root(root)
    discovery = agent_check.discover_changed_files(root, base_ref=base_ref)
    if discovery.merge_base is None:
        raise agent_check.DiscoveryError("Git discovery did not provide a merge base")

    excluded = {PurePosixPath(path.replace("\\", "/")).as_posix() for path in excluded_files}
    files = tuple(path for path in discovery.files if path not in excluded)
    discovery = agent_check.Discovery(
        files=files,
        base=discovery.base,
        base_ref=discovery.base_ref,
        merge_base=discovery.merge_base,
    )

    tracked_diff = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-renames",
        "--binary",
        discovery.merge_base,
        "--",
        *files,
    ) if files else b""
    tracked_stat = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-renames",
        "--stat",
        discovery.merge_base,
        "--",
        *files,
    ) if files else b""

    untracked = tuple(path for path in _untracked_files(root) if path in files)
    untracked_diffs = tuple(_untracked_diff(root, path, stat=False) for path in untracked)
    untracked_stats = tuple(_untracked_diff(root, path, stat=True) for path in untracked)
    diff = tracked_diff + b"".join(untracked_diffs)
    statistics = (tracked_stat + b"".join(untracked_stats)).decode("utf-8", "replace").strip()
    return RepositoryState(
        branch=current_branch(root),
        discovery=discovery,
        diff_statistics=statistics or "(no changes)",
        diff=diff,
    )


def _fenced(content: str, language: str = "") -> str:
    longest_run = max((len(run) for run in re.findall(r"`+", content)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{content.rstrip()}\n{fence}"


def render_packet(
    state: RepositoryState,
    *,
    task_context: str,
    max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES,
) -> str:
    files = state.discovery.files
    areas = agent_check.classify_files(files)
    checks = agent_check.select_checks(areas, files)
    manual = agent_check.select_manual_requirements(files, areas)

    sections = [
        "# VaultBridge Fresh-Agent Review Packet",
        "## Original task",
        _fenced(task_context, "text"),
    ]

    changed_files = "\n".join(f"- `{path}`" for path in files) or "- (none)"
    sections.extend(
        (
            "## Repository state",
            f"- Current branch: `{state.branch}`\n"
            f"- Selected comparison base: `{state.discovery.base_ref}`\n"
            f"- Merge base: `{state.discovery.merge_base}`\n\n"
            f"Changed files:\n\n{changed_files}\n\n"
            f"Diff statistics:\n\n{_fenced(state.diff_statistics, 'text')}",
        )
    )

    detected = [area for area in agent_check.AREAS if area in areas]
    area_lines = "\n".join(f"- {area}" for area in detected) or "- (none)"
    sections.extend(("## Affected areas", area_lines))

    check_lines = "\n".join(f"- {check.label}" for check in checks) or "- (none)"
    verification = f"Expected automated verification:\n\n{check_lines}"
    if manual:
        verification += "\n\nAdditional agent verification:\n\n" + "\n".join(
            f"- {requirement}" for requirement in manual
        )
    sections.extend(("## Verification context", verification))

    if not files:
        diff_section = "No changed files were discovered."
    elif len(state.diff) > max_diff_bytes:
        diff_section = (
            f"Full diff omitted: its raw size is {len(state.diff):,} bytes, exceeding the "
            f"{max_diff_bytes:,}-byte packet limit. The omission is intentional, not a "
            "truncation. Inspect the branch and working-tree diff directly before reviewing."
        )
    else:
        diff_section = _fenced(state.diff.decode("utf-8", "replace"), "diff")
    sections.extend(("## Diff", diff_section))

    sections.extend(
        (
            "## Review instructions",
            "Assume the implementation may be wrong. Do not modify files and do not commit. "
            "Review the task context, repository state, affected areas, verification expectations, "
            "and diff. Inspect the repository directly when the packet says the diff was omitted.\n\n"
            "Check for correctness, regressions, failure and error paths, relevant security issues, "
            "relevant lifecycle or concurrency issues, missing or weak tests, unnecessary complexity, "
            "and scope violations. Report only concrete, actionable findings supported by code; avoid "
            "speculative or theoretical noise. Prioritize each finding as `BLOCKER`, `HIGH`, `MEDIUM`, "
            "or `LOW`.\n\n"
            "Finish with exactly one recommendation on its own line: `APPROVE` or `FIXES REQUIRED`.",
        )
    )
    return "\n\n".join(sections) + "\n"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _normalized_real_path(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _paths_refer_to_same_file(first: Path, second: Path) -> bool:
    if _normalized_real_path(first) == _normalized_real_path(second):
        return True
    try:
        return first.samefile(second)
    except FileNotFoundError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="override the Git base reference")
    task_source = parser.add_mutually_exclusive_group(required=True)
    task_source.add_argument(
        "--task-file", type=Path, help="include the original task from this file"
    )
    task_source.add_argument("--task", help="include the original task as inline text")
    parser.add_argument("--output", type=Path, help="write the packet to this file instead of stdout")
    parser.add_argument(
        "--max-diff-bytes",
        type=_positive_int,
        default=DEFAULT_MAX_DIFF_BYTES,
        help=f"omit a larger diff (default: {DEFAULT_MAX_DIFF_BYTES})",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = REPOSITORY_ROOT) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.task_file and args.output:
            if _paths_refer_to_same_file(args.task_file, args.output):
                raise agent_check.DiscoveryError(
                    "--task-file and --output must refer to different files; "
                    "choose a separate output path"
                )
        task_context = (
            args.task_file.read_text(encoding="utf-8") if args.task_file else args.task
        )
        if not task_context.strip():
            parser.error("task context must not be empty")
        excluded_files: tuple[str, ...] = ()
        if args.output:
            output = args.output if args.output.is_absolute() else Path.cwd() / args.output
            try:
                excluded_files = (output.resolve().relative_to(root.resolve()).as_posix(),)
            except ValueError:
                pass
        state = collect_repository_state(root, base_ref=args.base, excluded_files=excluded_files)
        packet = render_packet(
            state,
            task_context=task_context,
            max_diff_bytes=args.max_diff_bytes,
        )
        if args.output:
            args.output.write_text(packet, encoding="utf-8", newline="\n")
        else:
            sys.stdout.write(packet)
    except (OSError, UnicodeError, agent_check.DiscoveryError) as error:
        print(f"ERROR review packet generation: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
