from __future__ import annotations

import argparse
import json
import logging
import platform
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

from app.core.config import DEFAULT_SEMANTIC_MODEL
from app.services.semantic_search import Embedder
from tests.eval.benchmark import calculate_latency
from tests.eval.graph_runner import (
    GRAPH_RESULT_LIMIT,
    GraphComparison,
    baseline_result,
    build_graph_services,
    comparison_metrics,
    graph_aware_result,
    load_graph_cases,
)

EXIT_SUCCESS = 0
EXIT_OPERATIONAL_FAILURE = 1


def _round_metric(value: float) -> float:
    return round(value, 6)


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unavailable"


def _metrics_payload(metrics: Any) -> dict[str, int | float]:
    return {
        "total_cases": metrics.cases,
        "hit_at_1": _round_metric(metrics.hit_at_1),
        "hit_at_3": _round_metric(metrics.hit_at_3),
        "mrr": _round_metric(metrics.mrr),
    }


def run_graph_benchmark(
    work_root: Path,
    *,
    model_name: str = DEFAULT_SEMANTIC_MODEL,
    embedder: Embedder | None = None,
    timer: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Measure the baseline and evaluation-only graph candidate on sanitized notes."""
    semantic_service, relationship_service, note_count = build_graph_services(
        work_root,
        model_name=model_name,
        embedder=embedder,
    )

    indexing_started = timer()
    sync_result = semantic_service.sync()
    indexing_duration_ms = (timer() - indexing_started) * 1_000
    if sync_result["indexed"] != note_count:
        raise RuntimeError("sanitized graph benchmark corpus was not fully indexed")

    measured: list[tuple[GraphComparison, float, float]] = []
    for case in load_graph_cases():
        baseline_started = timer()
        semantic_results = tuple(
            semantic_service.search(case.query, limit=GRAPH_RESULT_LIMIT)
        )
        baseline_latency_ms = (timer() - baseline_started) * 1_000

        graph_started = timer()
        comparison = GraphComparison(
            case=case,
            baseline=baseline_result(case, semantic_results),
            graph_aware=graph_aware_result(
                case,
                semantic_results,
                relationship_service,
            ),
        )
        graph_expansion_latency_ms = (timer() - graph_started) * 1_000
        measured.append((comparison, baseline_latency_ms, graph_expansion_latency_ms))

    comparisons = tuple(comparison for comparison, _, _ in measured)
    baseline_metrics, graph_metrics = comparison_metrics(comparisons)
    baseline_latencies = tuple(baseline for _, baseline, _ in measured)
    expansion_latencies = tuple(expansion for _, _, expansion in measured)
    total_latencies = tuple(
        baseline + expansion for _, baseline, expansion in measured
    )

    return {
        "schema_version": 1,
        "metadata": {
            "benchmark": "vaultbridge-graph-retrieval",
            "method": "verified-one-hop-interleaving",
            "model_identifier": model_name,
            "fastembed_version": _package_version("fastembed"),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.system(),
            "machine_architecture": platform.machine(),
            "corpus_notes": note_count,
            "result_limit": GRAPH_RESULT_LIMIT,
            "timer": "time.perf_counter",
            "latency_unit": "milliseconds",
            "indexing_duration_ms": _round_metric(indexing_duration_ms),
        },
        "summary": {
            "baseline": _metrics_payload(baseline_metrics),
            "graph_aware": _metrics_payload(graph_metrics),
        },
        "latency": {
            "baseline_query": calculate_latency(baseline_latencies),
            "graph_expansion": calculate_latency(expansion_latencies),
            "graph_total": calculate_latency(total_latencies),
        },
        "cases": [
            {
                "case_id": comparison.case.case_id,
                "query": comparison.case.query,
                "expected_path": comparison.case.expected.path,
                "baseline_rank": comparison.baseline.rank,
                "graph_aware_rank": comparison.graph_aware.rank,
                "baseline_query_latency_ms": _round_metric(baseline_latency),
                "graph_expansion_latency_ms": _round_metric(expansion_latency),
                "graph_total_latency_ms": _round_metric(
                    baseline_latency + expansion_latency
                ),
                "graph_aware_results": [
                    {
                        "rank": rank,
                        "path": result.path,
                        "source": result.source,
                        "anchor_path": result.anchor_path,
                    }
                    for rank, result in enumerate(
                        comparison.graph_aware.results,
                        start=1,
                    )
                ],
            }
            for comparison, baseline_latency, expansion_latency in measured
        ],
    }


def execute_graph_benchmark() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="vaultbridge-graph-benchmark-"
    ) as temporary_directory:
        return run_graph_benchmark(Path(temporary_directory))


def format_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def _cell(value: object) -> str:
    if value is None:
        return "null"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def format_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    summary = report["summary"]
    latency = report["latency"]
    lines = [
        "# VaultBridge Graph Retrieval Benchmark",
        "",
        "## Benchmark metadata",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Model | {_cell(metadata['model_identifier'])} |",
        f"| FastEmbed | {_cell(metadata['fastembed_version'])} |",
        f"| Platform | {_cell(metadata['operating_system'])} / "
        f"{_cell(metadata['machine_architecture'])} |",
        f"| Sanitized corpus notes | {metadata['corpus_notes']} |",
        f"| Indexing duration | {metadata['indexing_duration_ms']:.3f} ms |",
        "",
        "## Aggregate comparison",
        "",
        "| Variant | Cases | Hit@1 | Hit@3 | MRR |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, key in (("Baseline", "baseline"), ("Graph-aware", "graph_aware")):
        metrics = summary[key]
        lines.append(
            f"| {label} | {metrics['total_cases']} | {metrics['hit_at_1']:.2%} | "
            f"{metrics['hit_at_3']:.2%} | {metrics['mrr']:.2%} |"
        )

    lines.extend(
        [
            "",
            "## Latency comparison",
            "",
            "| Stage | Mean | P50 | P95 | Min | Max |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, key in (
        ("Baseline query", "baseline_query"),
        ("Graph expansion", "graph_expansion"),
        ("Graph total", "graph_total"),
    ):
        values = latency[key]
        lines.append(
            f"| {label} | {values['mean_ms']:.3f} ms | {values['p50_ms']:.3f} ms | "
            f"{values['p95_ms']:.3f} ms | {values['min_ms']:.3f} ms | "
            f"{values['max_ms']:.3f} ms |"
        )

    lines.extend(
        [
            "",
            "## Per-case comparison",
            "",
            "| Case | Expected path | Baseline rank | Graph-aware rank | Baseline | Expansion | Total |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for case in report["cases"]:
        lines.append(
            f"| {_cell(case['case_id'])} | {_cell(case['expected_path'])} | "
            f"{_cell(case['baseline_rank'])} | {_cell(case['graph_aware_rank'])} | "
            f"{case['baseline_query_latency_ms']:.3f} ms | "
            f"{case['graph_expansion_latency_ms']:.3f} ms | "
            f"{case['graph_total_latency_ms']:.3f} ms |"
        )
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark an evaluation-only graph signal on sanitized notes."
    )
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
    executor: Callable[[], dict[str, Any]] = execute_graph_benchmark,
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
        print(f"Graph benchmark failed ({type(exc).__name__}).", file=error_output)
        return EXIT_OPERATIONAL_FAILURE
    finally:
        logging.disable(previous_logging_disable)

    formatter = format_json if arguments.format == "json" else format_markdown
    output.write(formatter(report))
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
