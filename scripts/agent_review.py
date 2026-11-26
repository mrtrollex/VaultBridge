#!/usr/bin/env python3
"""Prepare a compact packet for an independent review of the current change."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import UUID

if __package__:
    from . import agent_check, agent_evidence, agent_validation
else:
    import agent_check
    import agent_evidence
    import agent_validation

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = Path(".agent/review_packet.md")
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
        [*agent_validation.GIT_READ_COMMAND, *args],
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
    args = ["diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-renames"]
    if stat:
        args.append("--stat")
    args.extend(("--", "/dev/null", path))
    return _git(root, *args, allowed_return_codes=(0, 1))


def collect_repository_state(
    root: Path,
    *,
    base_ref: str | None = None,
    excluded_files: Sequence[str] = (),
    managed: bool = False,
) -> RepositoryState:
    ensure_repository_root(root)
    discovery = agent_check.discover_changed_files(root, base_ref=base_ref, managed=managed)
    if discovery.merge_base is None:
        raise agent_check.DiscoveryError("Git discovery did not provide a merge base")

    excluded = {PurePosixPath(path.replace("\\", "/")).as_posix() for path in excluded_files}
    files = tuple(path for path in discovery.files if path not in excluded)
    discovery = replace(
        discovery, files=files, workflow_only=discovery.workflow_only and files == discovery.files,
        raw_changed_files=tuple(path for path in discovery.raw_changed_files if path not in excluded),
    )

    tracked_diff = _git(
        root,
        "--literal-pathspecs",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--binary",
        discovery.merge_base,
        "--",
        *files,
    ) if files else b""
    tracked_stat = _git(
        root,
        "--literal-pathspecs",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
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


def capture_validated_state(root: Path, run_id: str) -> tuple[RepositoryState, dict]:
    report = agent_validation.read_report(root, run_id)
    if report is None:
        raise agent_check.DiscoveryError("missing, stale or malformed validation report for this run")
    source = agent_validation.source_identity(root)
    if (
        source is None
        or report.get("source_unchanged") is not True
        or report.get("source_before") != source
        or report.get("source_after") != source
    ):
        raise agent_check.DiscoveryError("validation source does not match the current checkout")
    state = collect_repository_state(root, managed=True)
    discovery = state.discovery
    areas = agent_check.classify_files(discovery.files)
    manual = agent_check.select_manual_requirements(discovery.files, areas)
    expected = agent_check.scope_record(discovery, areas, manual)
    if any(field not in report or report[field] != value for field, value in expected.items()):
        raise agent_check.DiscoveryError("validation scope does not match the current change scope")
    selected = agent_check.select_checks(areas, discovery.files, workflow_only=discovery.workflow_only)
    checks = report.get("checks")
    if (
        not agent_validation.valid_timing(report)
        or report.get("overall_outcome") != "PASS"
        or type(report.get("exit_code")) is not int
        or report["exit_code"] != 0
        or report.get("environment") != agent_validation.environment_identity()
        or not isinstance(checks, list)
        or len(checks) != len(selected)
    ):
        raise agent_check.DiscoveryError("validation report does not prove a complete successful run")
    for result, check in zip(checks, selected, strict=True):
        command = agent_check.command_identity(check)
        if (
            not isinstance(result, dict)
            or not agent_validation.valid_timing(result)
            or result.get("id") != check.key
            or command is None
            or result.get("selected_command") != command
            or result.get("executed_command") != command
            or result.get("required") is not True
            or result.get("launch_attempted") is not True
            or result.get("outcome") != "PASS"
            or type(result.get("exit_code")) is not int
            or result["exit_code"] != 0
            or "unavailable_stage" not in result
            or result["unavailable_stage"] is not None
        ):
            raise agent_check.DiscoveryError("validation report does not prove execution of every required check")
    if sum(result["elapsed_ns"] for result in checks) > report["elapsed_ns"]:
        raise agent_check.DiscoveryError("validation report has inconsistent timing")
    if source != agent_validation.source_identity(root):
        raise agent_check.DiscoveryError("source changed while collecting the packet scope and diff")
    return state, report


def verification_summary(report: dict) -> str:
    source = report["source_before"]
    checks = report["checks"]
    lines = [
        f"- Local result: `{report['run_id']}` ({report['started_at']})",
        f"- Automated outcome: {report['overall_outcome']} ({report['elapsed_ns'] / 1e9:.3f}s)",
        f"- Tested HEAD: `{source['head']}`",
        f"- Git-visible delta SHA-256: `{source['git_visible_delta_sha256']}`",
        f"- Raw tracked inputs SHA-256: `{source['raw_tracked_sha256']}`",
        *(
            f"- {check['id']}: {check['outcome']} ({check['elapsed_ns'] / 1e9:.3f}s)"
            for check in checks
        ),
    ]
    if not checks:
        lines.append("- No automated checks were selected.")
    for check in checks:
        if check["id"] == "pytest-workflow":
            lines.append(f"- Required workflow pytest command: `{' '.join(check['executed_command'])}`")
            lines.append(
                "- The changed workflow code selected and verified this local run. "
                "Fresh independent review and full hosted CI remain required."
            )
    lines.extend(
        f"- Manual verification remains: {requirement}" for requirement in report["manual_requirements"]
    )
    lines.append("- Local results are supplementary. Inspect the source and tests directly.")
    return "\n".join(lines)


def render_packet(
    state: RepositoryState,
    *,
    task_context: str,
    max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES,
    validation: str | None = None,
    evidence: agent_evidence.Evidence | None = None,
    focused: tuple[str, ...] = (),
) -> str:
    files = state.discovery.files
    areas = agent_check.classify_files(files)
    checks = agent_check.select_checks(areas, files, workflow_only=state.discovery.workflow_only)
    manual = agent_check.select_manual_requirements(files, areas)

    sections = [
        "# VaultBridge Fresh-Agent Review Packet",
        "## Original task",
        _fenced(task_context, "text"),
    ]

    changed_files = "\n".join(f"- `{path}`" for path in files) or "- (none)"
    if state.discovery.raw_changed_files:
        changed_files += "\n\nCheckout-byte differences outside the Git diff:\n\n" + "\n".join(
            f"- `{path}`" for path in state.discovery.raw_changed_files
        )
    sections.extend(
        (
            "## Repository state",
            f"- Current branch: `{state.branch}`\n"
            f"- Selected comparison base: `{state.discovery.base_ref or state.discovery.base_commit}`\n"
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

    if validation is not None:
        sections.extend(("## Recorded validation", validation))
    if focused:
        sections.extend((
            "## Source-matched focused regression results",
            "Local focused records share the tested Git and raw tracked identities above.\n\n" + "\n".join(focused),
        ))
    if evidence is not None:
        references = "\n".join((evidence.manifest, *evidence.artifacts))
        sections.extend((
            "## Declared qualification evidence",
            f"Evidence preflight passed. Manifest SHA-256: `{evidence.sha256}`\n\n"
            f"{_fenced(references, 'text')}\n\n"
            "Review the evidence against the task's qualification requirements.",
        ))

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
            "and diff. Inspect the actual source and tests directly.\n\n"
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
    normalized = os.path.normcase(os.path.realpath(path))
    if os.name == "nt":
        if normalized.startswith("\\\\?\\unc\\"):
            return "\\\\" + normalized[len("\\\\?\\unc\\"):]
        return normalized.removeprefix("\\\\?\\")
    return normalized


def paths_refer_to_same_file(first: Path, second: Path) -> bool:
    if _normalized_real_path(first) == _normalized_real_path(second):
        return True
    try:
        return first.samefile(second)
    except FileNotFoundError:
        return False


def _is_managed_packet(root: Path, output: Path) -> bool:
    managed = root / PACKET_PATH
    if paths_refer_to_same_file(output, managed):
        return True
    # POSIX paths can live on a case-insensitive mount. Compare the parent
    # independently so the reservation also applies while the leaf is absent.
    return output.name.casefold() == managed.name.casefold() and paths_refer_to_same_file(
        output.parent.resolve(), managed.parent.resolve(),
    )


def packet_destination(root: Path, output: Path) -> Path:
    if os.name == "nt" and any(
        part != ".." and (part.endswith((".", " ")) or ":" in part)
        for part in output.parts[(1 if output.anchor else 0):]
    ):
        raise agent_check.DiscoveryError("ambiguous Windows review output path")
    if _is_managed_packet(root, output):
        raise agent_check.DiscoveryError("the managed review packet path is reserved for agent_finish.py")
    destination = output.resolve()
    if _is_managed_packet(root, destination):
        raise agent_check.DiscoveryError("the managed review packet path is reserved for agent_finish.py")
    return destination


def write_packet(root: Path, destination: Path, packet: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".review-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(packet.encode("utf-8"))
        if packet_destination(root, destination) != destination:
            raise agent_check.DiscoveryError("review packet output was redirected")
        # Replacing the entry avoids following a newly substituted leaf alias.
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="override the Git base reference")
    parser.add_argument("--run-id", type=UUID, help=argparse.SUPPRESS)
    parser.add_argument("--diff-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--evidence-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--focused-results", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--focused-sha256", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--evidence-manifest", help="require the declared repository evidence")
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
        output = packet_destination(root, args.output) if args.output else None
        if args.task_file and args.output:
            if paths_refer_to_same_file(args.task_file, args.output):
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
        if output:
            try:
                excluded_files = (output.relative_to(root.resolve()).as_posix(),)
            except ValueError:
                pass
        if args.run_id:
            if args.base is not None:
                raise agent_check.DiscoveryError("managed review requires the trusted comparison base")
            if (args.evidence_manifest is None) != (args.evidence_sha256 is None):
                raise agent_check.DiscoveryError("managed evidence requires the preflight manifest digest")
        evidence = (
            agent_evidence.validate(root, args.evidence_manifest, expected_sha256=args.evidence_sha256)
            if args.evidence_manifest is not None else None
        )
        report = None
        if args.run_id:
            state, report = capture_validated_state(root, str(args.run_id))
            if args.diff_sha256 != hashlib.sha256(state.diff).hexdigest():
                raise agent_check.DiscoveryError("packet diff does not match the verified handoff diff")
        else:
            state = collect_repository_state(root, base_ref=args.base, excluded_files=excluded_files)
        focused = agent_validation.focused_records(
            root, args.focused_results, report["source_before"], expected_digests=args.focused_sha256,
        ) if report is not None else ()
        packet = render_packet(
            state,
            task_context=task_context,
            max_diff_bytes=args.max_diff_bytes,
            validation=verification_summary(report) if report is not None else None,
            evidence=evidence,
            focused=tuple(record.summary for record in focused),
        )
        if output:
            write_packet(root, output, packet)
        else:
            sys.stdout.write(packet)
    except (OSError, ValueError, agent_check.DiscoveryError) as error:
        print(f"ERROR review packet generation: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
