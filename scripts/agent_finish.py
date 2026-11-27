#!/usr/bin/env python3
"""Run verification and prepare VaultBridge's fresh-agent review packet."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

if __package__:
    from . import agent_check, agent_evidence, agent_review, agent_validation
else:
    import agent_check
    import agent_evidence
    import agent_review
    import agent_validation

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = agent_review.PACKET_PATH

CommandRunner = Callable[[Sequence[str], Path], int]


def _run(command: Sequence[str], root: Path) -> int:
    return subprocess.run(command, cwd=root, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    task_source = parser.add_mutually_exclusive_group(required=True)
    task_source.add_argument("--task-file", type=Path, help="read the task from this file")
    task_source.add_argument("--task", help="use inline task context")
    parser.add_argument("--evidence-manifest", help="require the declared repository evidence")
    parser.add_argument("--focused-results", action="append", default=[], help="include a matching focused run record")
    parser.add_argument("--parallel-pytest", action="store_true", help="run the selected pytest suite in parallel")
    return parser


@contextmanager
def _packet_lock(directory: Path):
    # Atomic mkdir also coordinates native Windows and WSL on a shared checkout.
    lock = directory / "finish.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise agent_check.DiscoveryError("could not acquire the managed review packet lock") from error
    try:
        yield
    finally:
        lock.rmdir()


def _finish(args: argparse.Namespace, root: Path, packet: Path, runner: CommandRunner) -> int:
    packet.unlink(missing_ok=True)
    evidence_arguments: tuple[str, ...] = ()
    evidence = None
    if args.evidence_manifest is not None:
        try:
            evidence = agent_evidence.validate(root, args.evidence_manifest)
        except agent_evidence.EvidenceError as error:
            print(f"Evidence preflight: FAIL ({error})", file=sys.stderr)
            return 1
        print(f"Evidence preflight: PASS ({len(evidence.artifacts)} required artifacts)")
        evidence_arguments = (
            f"--evidence-manifest={evidence.manifest}", "--evidence-sha256", evidence.sha256,
        )

    run_id = str(uuid4())
    check_command = (sys.executable, str(root / "scripts/agent_check.py"), "--run-id", run_id)
    if args.parallel_pytest:
        check_command += ("--parallel-pytest",)
    check_result = runner(check_command, root)
    if check_result != 0:
        print(f"Verification: FAIL (exit {check_result})", file=sys.stderr)
        return check_result

    if evidence is not None:
        agent_evidence.validate(root, evidence.manifest, expected_sha256=evidence.sha256)
    snapshot = agent_review.capture_validated_state(root, run_id, parallel_pytest=args.parallel_pytest)
    state, report = snapshot
    source = report["source_before"]
    focused = agent_validation.focused_records(root, args.focused_results, source)
    focused_args = tuple(
        argument
        for record in focused
        for argument in (f"--focused-results={record.name}", f"--focused-sha256={record.sha256}")
    )
    task_arguments = (
        ("--task-file", str(args.task_file))
        if args.task_file is not None
        else ("--task", args.task)
    )

    def require_same_snapshot() -> None:
        if evidence is not None:
            agent_evidence.validate(root, evidence.manifest, expected_sha256=evidence.sha256)
        if focused != agent_validation.focused_records(root, args.focused_results, source):
            raise agent_check.DiscoveryError("focused records changed during packet generation")
        if snapshot != agent_review.capture_validated_state(root, run_id, parallel_pytest=args.parallel_pytest):
            raise agent_check.DiscoveryError("packet scope, diff or validation report changed before handoff")

    published = False
    try:
        with tempfile.TemporaryDirectory(prefix="review-", dir=packet.parent) as directory:
            staged = Path(directory) / packet.name
            review_command = (
                sys.executable, str(root / "scripts/agent_review.py"), *task_arguments,
                "--output", str(staged), "--run-id", run_id,
                "--diff-sha256", hashlib.sha256(state.diff).hexdigest(),
                *evidence_arguments, *focused_args,
            )
            review_result = runner(review_command, root)
            if review_result != 0:
                print(f"Review packet generation: FAIL (exit {review_result})", file=sys.stderr)
                return review_result
            if not staged.is_file() or staged.is_symlink():
                raise agent_check.DiscoveryError("review packet generation did not create a regular output file")
            task_context = (
                (root / args.task_file).read_text(encoding="utf-8") if args.task_file is not None else args.task
            )
            expected_packet = agent_review.render_packet(
                state, task_context=task_context, validation=agent_review.verification_summary(report),
                evidence=evidence, focused=tuple(record.summary for record in focused),
            ).encode("utf-8")
            require_same_snapshot()
            if staged.read_bytes() != expected_packet:
                raise agent_check.DiscoveryError("staged packet differs from the approved source and evidence")
            os.replace(staged, packet)
        require_same_snapshot()
        if packet.read_bytes() != expected_packet:
            raise agent_check.DiscoveryError("published packet differs from the approved source and evidence")
        published = True
    finally:
        if not published:
            packet.unlink(missing_ok=True)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = REPOSITORY_ROOT,
    runner: CommandRunner = _run,
) -> int:
    args = build_parser().parse_args(argv)
    packet = root / PACKET_PATH
    verified = False

    try:
        if args.task_file is not None and agent_review.paths_refer_to_same_file(
            args.task_file, packet
        ):
            print(
                "ERROR agent finish: --task-file must not refer to the managed review packet",
                file=sys.stderr,
            )
            return 2

        directory = agent_validation.report_path(root).parent
        directory.mkdir(parents=True, exist_ok=True)
        with _packet_lock(directory):
            result = _finish(args, root, packet, runner)
            if result:
                return result
            verified = True
    except (OSError, RuntimeError, ValueError) as error:
        if verified:
            packet.unlink(missing_ok=True)
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
