from __future__ import annotations

import argparse
import json
import logging
import math
import platform
import shutil
import sys
import tempfile
import time
import warnings
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from importlib.metadata import PackageNotFoundError, version
from io import StringIO
from pathlib import Path
from typing import Any, TextIO

from app.core.config import DEFAULT_SEMANTIC_MODEL, Settings
from app.services.semantic_search import Embedder, SemanticSearchService, semantic_search_service_from_settings
from tests.eval.runner import CORPUS_ROOT, CaseResult, RetrievalCase, load_cases, run_evaluation

RESULT_LIMIT = 5
PERCENTILE_METHOD = "R7 linear interpolation: position=(n-1)*p"
EXIT_SUCCESS = 0
EXIT_OPERATIONAL_FAILURE = 1


def _round_metric(value: float) -> float:
    return round(value, 6)


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unavailable"


def percentile(values: Sequence[float], proportion: float) -> float:
    """Return an R7 linearly interpolated percentile for a non-empty sequence."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= proportion <= 1.0:
        raise ValueError("percentile proportion must be between zero and one")

    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def calculate_summary(outcomes: Sequence[CaseResult]) -> dict[str, int | float]:
    total = len(outcomes)
    if total == 0:
        return {
            "total_cases": 0,
            "hit_at_1": 0.0,
            "hit_at_5": 0.0,
            "mrr": 0.0,
        }
    return {
        "total_cases": total,
        "hit_at_1": _round_metric(sum(outcome.rank == 1 for outcome in outcomes) / total),
        "hit_at_5": _round_metric(
            sum(outcome.rank is not None and outcome.rank <= RESULT_LIMIT for outcome in outcomes) / total
        ),
        "mrr": _round_metric(
            sum(1.0 / outcome.rank for outcome in outcomes if outcome.rank is not None) / total
        ),
    }


def calculate_latency(latencies_ms: Sequence[float]) -> dict[str, int | float]:
    if not latencies_ms:
        return {
            "count": 0,
            "mean_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "count": len(latencies_ms),
        "mean_ms": _round_metric(sum(latencies_ms) / len(latencies_ms)),
        "p50_ms": _round_metric(percentile(latencies_ms, 0.50)),
        "p95_ms": _round_metric(percentile(latencies_ms, 0.95)),
        "min_ms": _round_metric(min(latencies_ms)),
        "max_ms": _round_metric(max(latencies_ms)),
    }


def _case_payload(outcome: CaseResult, latency_ms: float) -> dict[str, Any]:
    case = outcome.case
    return {
        "case_id": case.case_id,
        "language": case.language,
        "category": case.category,
        "query": case.query,
        "expected_path": case.expected.path,
        "expected_heading": case.expected.heading,
        "expected_accepted_top_k": case.expected.within_top_k,
        "actual_expected_rank": outcome.rank,
        "query_latency_ms": _round_metric(latency_ms),
        "results": [
            {
                "rank": rank,
                "path": result.path,
                "heading": result.heading,
                "final_score": result.score,
                "semantic_score": result.semantic_score,
                "lexical_score": result.lexical_score,
            }
            for rank, result in enumerate(outcome.results, start=1)
        ],
    }


def _copy_corpus(vault_root: Path) -> int:
    source_paths = tuple(sorted(CORPUS_ROOT.rglob("*.md")))
    for source_path in source_paths:
        destination = vault_root / source_path.relative_to(CORPUS_ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
    return len(source_paths)


def _build_service(
    work_root: Path,
    *,
    model_name: str,
    embedder: Embedder | None,
) -> tuple[SemanticSearchService, int]:
    vault_root = work_root / "vault"
    note_count = _copy_corpus(vault_root)
    settings = Settings.model_validate(
        {
            "VAULT_PATH": vault_root,
            "SEMANTIC_DATA_PATH": work_root / "data",
            "SEMANTIC_MODEL": model_name,
        }
    )
    return semantic_search_service_from_settings(settings, embedder=embedder), note_count


def run_benchmark(
    work_root: Path,
    *,
    model_name: str = DEFAULT_SEMANTIC_MODEL,
    embedder: Embedder | None = None,
    timer: Callable[[], float] = time.perf_counter,
    cases: tuple[RetrievalCase, ...] | None = None,
) -> dict[str, Any]:
    """Index and query the sanitized fixture, returning path-only benchmark evidence."""
    service, note_count = _build_service(work_root, model_name=model_name, embedder=embedder)

    indexing_started = timer()
    sync_result = service.sync()
    indexing_duration_ms = (timer() - indexing_started) * 1_000
    if sync_result["indexed"] != note_count:
        raise RuntimeError("sanitized benchmark corpus was not fully indexed")

    measured_cases: list[tuple[CaseResult, float]] = []
    benchmark_cases = cases if cases is not None else load_cases()
    for case in benchmark_cases:
        query_started = timer()
        outcome = run_evaluation(service, (case,))[0]
        query_latency_ms = (timer() - query_started) * 1_000
        measured_cases.append((outcome, query_latency_ms))

    outcomes = tuple(outcome for outcome, _ in measured_cases)
    latencies_ms = tuple(latency for _, latency in measured_cases)
    return {
        "schema_version": 1,
        "metadata": {
            "benchmark": "vaultbridge-retrieval",
            "model_identifier": model_name,
            "fastembed_version": _package_version("fastembed"),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.system(),
            "machine_architecture": platform.machine(),
            "corpus_notes": note_count,
            "result_limit": RESULT_LIMIT,
            "timer": "time.perf_counter",
            "latency_unit": "milliseconds",
            "percentile_method": PERCENTILE_METHOD,
            "indexing_duration_ms": _round_metric(indexing_duration_ms),
        },
        "summary": calculate_summary(outcomes),
        "latency": calculate_latency(latencies_ms),
        "cases": [_case_payload(outcome, latency) for outcome, latency in measured_cases],
    }


def execute_benchmark() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="vaultbridge-benchmark-") as temporary_directory:
        return run_benchmark(Path(temporary_directory))


def format_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def _markdown_cell(value: object) -> str:
    if value is None:
        return "null"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def format_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    summary = report["summary"]
    latency = report["latency"]
    cases = report["cases"]
    lines = [
        "# VaultBridge Retrieval Benchmark",
        "",
        "## Benchmark metadata",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Model | {_markdown_cell(metadata['model_identifier'])} |",
        f"| FastEmbed | {_markdown_cell(metadata['fastembed_version'])} |",
        f"| Python | {_markdown_cell(metadata['python_implementation'])} "
        f"{_markdown_cell(metadata['python_version'])} |",
        f"| Platform | {_markdown_cell(metadata['operating_system'])} / "
        f"{_markdown_cell(metadata['machine_architecture'])} |",
        f"| Sanitized corpus notes | {metadata['corpus_notes']} |",
        f"| Returned result limit | {metadata['result_limit']} |",
        f"| Indexing duration | {metadata['indexing_duration_ms']:.3f} ms |",
        f"| Query timer | {_markdown_cell(metadata['timer'])} |",
        f"| Percentiles | {_markdown_cell(metadata['percentile_method'])} |",
        "",
        "## Aggregate summary",
        "",
        "| Cases | Hit@1 | Hit@5 / Recall@5 | MRR |",
        "|---:|---:|---:|---:|",
        f"| {summary['total_cases']} | {summary['hit_at_1']:.2%} | "
        f"{summary['hit_at_5']:.2%} | {summary['mrr']:.2%} |",
        "",
        "## Latency summary",
        "",
        "| Count | Mean | P50 / median | P95 | Min | Max |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {latency['count']} | {latency['mean_ms']:.3f} ms | {latency['p50_ms']:.3f} ms | "
        f"{latency['p95_ms']:.3f} ms | {latency['min_ms']:.3f} ms | {latency['max_ms']:.3f} ms |",
        "",
        "## Per-case summary",
        "",
        "| Case | Language | Category | Expected path | Accepted top-k | Actual rank | Latency |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for case in cases:
        lines.append(
            f"| {_markdown_cell(case['case_id'])} | {_markdown_cell(case['language'])} | "
            f"{_markdown_cell(case['category'])} | {_markdown_cell(case['expected_path'])} | "
            f"{case['expected_accepted_top_k']} | {_markdown_cell(case['actual_expected_rank'])} | "
            f"{case['query_latency_ms']:.3f} ms |"
        )

    lines.extend(["", "## Ranked result details", ""])
    for case in cases:
        lines.extend(
            [
                f"### {_markdown_cell(case['case_id'])}",
                "",
                f"Query: `{_markdown_cell(case['query'])}`",
                "",
                f"Expected: `{_markdown_cell(case['expected_path'])}` / "
                f"`{_markdown_cell(case['expected_heading'])}`; actual rank: "
                f"`{_markdown_cell(case['actual_expected_rank'])}`.",
                "",
                "| Rank | Path | Heading | Final score | Semantic score | Lexical score |",
                "|---:|---|---|---:|---:|---:|",
            ]
        )
        for result in case["results"]:
            lines.append(
                f"| {result['rank']} | {_markdown_cell(result['path'])} | "
                f"{_markdown_cell(result['heading'])} | {result['final_score']:.4f} | "
                f"{result['semantic_score']:.4f} | {result['lexical_score']:.4f} |"
            )
        if not case["results"]:
            lines.append("| - | No results | null | - | - | - |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark production retrieval on the sanitized VB-022 corpus.")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="report format written to stdout (default: markdown)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    executor: Callable[[], dict[str, Any]] = execute_benchmark,
) -> int:
    arguments = build_parser().parse_args(argv)
    output = output or sys.stdout
    error_output = error_output or sys.stderr
    previous_logging_disable = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with redirect_stdout(StringIO()):
                report = executor()
    except Exception as exc:
        print(f"Benchmark failed ({type(exc).__name__}).", file=error_output)
        return EXIT_OPERATIONAL_FAILURE
    finally:
        logging.disable(previous_logging_disable)

    formatter = format_json if arguments.format == "json" else format_markdown
    output.write(formatter(report))
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
