#!/usr/bin/env python3
"""Run verification and prepare VaultBridge's fresh-agent review packet."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

if __package__:
    from . import agent_review
else:
    import agent_review

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = Path(".agent/review_packet.md")

CommandRunner = Callable[[Sequence[str], Path], int]


def _run(command: Sequence[str], root: Path) -> int:
    return subprocess.run(command, cwd=root, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    task_source = parser.add_mutually_exclusive_group(required=True)
    task_source.add_argument("--task-file", type=Path, help="read the task from this file")
    task_source.add_argument("--task", help="use inline task context")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = REPOSITORY_ROOT,
    runner: CommandRunner = _run,
) -> int:
    args = build_parser().parse_args(argv)
    packet = root / PACKET_PATH

    try:
        if args.task_file is not None and agent_review.paths_refer_to_same_file(
            args.task_file, packet
        ):
            print(
                "ERROR agent finish: --task-file must not refer to the managed review packet",
                file=sys.stderr,
            )
            return 2

        packet.parent.mkdir(parents=True, exist_ok=True)
        packet.unlink(missing_ok=True)

        check_command = (sys.executable, str(root / "scripts/agent_check.py"))
        check_result = runner(check_command, root)
        if check_result != 0:
            print(f"Verification: FAIL (exit {check_result})", file=sys.stderr)
            return check_result

        task_arguments = (
            ("--task-file", str(args.task_file))
            if args.task_file is not None
            else ("--task", args.task)
        )
        review_command = (
            sys.executable,
            str(root / "scripts/agent_review.py"),
            *task_arguments,
            "--output",
            str(packet),
        )
        review_result = runner(review_command, root)
        if review_result != 0:
            packet.unlink(missing_ok=True)
            print(f"Review packet generation: FAIL (exit {review_result})", file=sys.stderr)
            return review_result
        if not packet.is_file():
            print("Review packet generation: FAIL (output was not created)", file=sys.stderr)
            return 2
    except OSError as error:
        print(f"ERROR agent finish: {error}", file=sys.stderr)
        return 2

    print(f"Verification: PASS\nReview packet: {PACKET_PATH.as_posix()}")
    print(
        "\nNext step:\n"
        f"Start a fresh Codex session and review `{PACKET_PATH.as_posix()}`.\n"
        "Do not provide the implementer's conversation history."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
