from __future__ import annotations

import io
import json

from tests.eval.graph_benchmark import format_json, format_markdown, main, run_graph_benchmark
from tests.eval.runner import DeterministicConceptEmbedder


class StepTimer:
    def __init__(self, step_seconds: float = 0.025) -> None:
        self.value = -step_seconds
        self.step_seconds = step_seconds

    def __call__(self) -> float:
        self.value += self.step_seconds
        return self.value


def test_graph_benchmark_records_quality_and_incremental_cost(tmp_path):
    report = run_graph_benchmark(
        tmp_path,
        embedder=DeterministicConceptEmbedder(),
        timer=StepTimer(),
    )

    assert report["metadata"]["corpus_notes"] == 17
    assert report["metadata"]["indexing_duration_ms"] == 25.0
    assert report["summary"] == {
        "baseline": {
            "total_cases": 4,
            "hit_at_1": 0.0,
            "hit_at_3": 0.0,
            "mrr": 0.0,
        },
        "graph_aware": {
            "total_cases": 4,
            "hit_at_1": 0.0,
            "hit_at_3": 1.0,
            "mrr": 0.5,
        },
    }
    assert report["latency"]["baseline_query"]["mean_ms"] == 25.0
    assert report["latency"]["graph_expansion"]["mean_ms"] == 25.0
    assert report["latency"]["graph_total"]["mean_ms"] == 50.0
    assert all(case["baseline_rank"] is None for case in report["cases"])
    assert all(case["graph_aware_rank"] == 2 for case in report["cases"])


def test_graph_benchmark_formats_are_path_safe(tmp_path):
    report = run_graph_benchmark(
        tmp_path,
        embedder=DeterministicConceptEmbedder(),
        timer=StepTimer(),
    )
    json_output = format_json(report)
    markdown_output = format_markdown(report)

    assert json.loads(json_output)["metadata"]["benchmark"] == "vaultbridge-graph-retrieval"
    assert markdown_output.startswith("# VaultBridge Graph Retrieval Benchmark\n")
    assert "## Aggregate comparison" in markdown_output
    assert "## Latency comparison" in markdown_output
    assert str(tmp_path) not in json_output
    assert str(tmp_path) not in markdown_output


def test_graph_benchmark_cli_suppresses_executor_stdout():
    output = io.StringIO()
    report = {
        "metadata": {
            "benchmark": "vaultbridge-graph-retrieval",
            "method": "verified-one-hop-interleaving",
        },
        "summary": {},
        "latency": {},
        "cases": [],
    }

    def execute():
        print("third-party progress")
        return report

    assert main(["--format", "json"], output=output, executor=execute) == 0
    assert "third-party progress" not in output.getvalue()
    assert json.loads(output.getvalue())["metadata"]["method"] == (
        "verified-one-hop-interleaving"
    )
