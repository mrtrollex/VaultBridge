from __future__ import annotations

import io
import json
import logging
import warnings
from dataclasses import replace

import pytest

from tests.eval.benchmark import (
    EXIT_OPERATIONAL_FAILURE,
    calculate_latency,
    calculate_summary,
    format_json,
    format_markdown,
    main,
    run_benchmark,
)
from tests.eval.runner import CaseResult, DeterministicConceptEmbedder, RelevanceExpectation, load_cases


class StepTimer:
    def __init__(self, step_seconds: float = 0.025) -> None:
        self.value = -step_seconds
        self.step_seconds = step_seconds

    def __call__(self) -> float:
        self.value += self.step_seconds
        return self.value


@pytest.fixture
def deterministic_report(tmp_path):
    return run_benchmark(
        tmp_path,
        embedder=DeterministicConceptEmbedder(),
        timer=StepTimer(),
    )


def test_benchmark_records_ranks_results_scores_and_fake_latencies(deterministic_report):
    report = deterministic_report

    assert report["summary"]["total_cases"] == 13
    assert report["latency"] == {
        "count": 13,
        "mean_ms": 25.0,
        "p50_ms": 25.0,
        "p95_ms": 25.0,
        "min_ms": 25.0,
        "max_ms": 25.0,
    }
    assert report["metadata"]["indexing_duration_ms"] == 25.0
    assert isinstance(report["metadata"]["fastembed_version"], str)

    case = next(item for item in report["cases"] if item["expected_heading"] is not None)
    assert case["actual_expected_rank"] == 1
    assert case["expected_accepted_top_k"] >= 1
    assert case["query_latency_ms"] == 25.0
    assert case["results"][0] == {
        "rank": 1,
        "path": case["expected_path"],
        "heading": case["expected_heading"],
        "final_score": pytest.approx(case["results"][0]["final_score"]),
        "semantic_score": pytest.approx(case["results"][0]["semantic_score"]),
        "lexical_score": pytest.approx(case["results"][0]["lexical_score"]),
    }
    assert all("snippet" not in result for item in report["cases"] for result in item["results"])


def test_summary_calculates_hit_at_1_hit_at_5_and_mrr():
    case = load_cases()[0]
    outcomes = (
        CaseResult(case=case, rank=1, results=()),
        CaseResult(case=case, rank=2, results=()),
        CaseResult(case=case, rank=None, results=()),
    )

    assert calculate_summary(outcomes) == {
        "total_cases": 3,
        "hit_at_1": 0.333333,
        "hit_at_5": 0.666667,
        "mrr": 0.5,
    }


def test_latency_uses_documented_r7_p50_and_p95():
    assert calculate_latency([10.0, 20.0, 30.0, 40.0]) == {
        "count": 4,
        "mean_ms": 25.0,
        "p50_ms": 25.0,
        "p95_ms": 38.5,
        "min_ms": 10.0,
        "max_ms": 40.0,
    }


def test_missing_expected_result_records_null_rank(tmp_path):
    case = load_cases()[0]
    missing_case = replace(
        case,
        expected=RelevanceExpectation(
            path="Missing/Sanitized.md",
            heading=None,
            within_top_k=5,
        ),
    )

    report = run_benchmark(
        tmp_path,
        embedder=DeterministicConceptEmbedder(),
        timer=StepTimer(),
        cases=(missing_case,),
    )

    assert report["cases"][0]["actual_expected_rank"] is None
    assert report["summary"]["hit_at_1"] == 0.0
    assert report["summary"]["hit_at_5"] == 0.0
    assert report["summary"]["mrr"] == 0.0


def test_json_and_markdown_contracts_do_not_leak_temporary_paths(deterministic_report, tmp_path):
    json_output = format_json(deterministic_report)
    decoded = json.loads(json_output)

    assert decoded["schema_version"] == 1
    assert set(decoded) == {"schema_version", "metadata", "summary", "latency", "cases"}
    assert isinstance(decoded["cases"][0]["query_latency_ms"], float)
    assert isinstance(decoded["cases"][0]["results"][0]["final_score"], float)

    markdown = format_markdown(deterministic_report)
    for heading in (
        "# VaultBridge Retrieval Benchmark",
        "## Benchmark metadata",
        "## Aggregate summary",
        "## Latency summary",
        "## Per-case summary",
        "## Ranked result details",
    ):
        assert heading in markdown
    assert "| Cases | Hit@1 | Hit@5 / Recall@5 | MRR |" in markdown
    assert "| Rank | Path | Heading | Final score | Semantic score | Lexical score |" in markdown
    assert str(tmp_path) not in json_output
    assert str(tmp_path) not in markdown


@pytest.mark.parametrize(
    ("arguments", "output_format"),
    ((["--format", "json"], "json"), (["--format", "markdown"], "markdown"), ([], "markdown")),
)
def test_cli_selects_requested_format_without_stdout_noise(
    deterministic_report,
    arguments,
    output_format,
    capsys,
):
    output = io.StringIO()

    def execute():
        print("third-party progress")
        return deterministic_report

    assert main(
        arguments,
        output=output,
        executor=execute,
    ) == 0
    rendered = output.getvalue()
    assert "third-party progress" not in rendered
    assert capsys.readouterr().out == ""
    if output_format == "json":
        assert json.loads(rendered)["summary"]["total_cases"] == 13
    else:
        assert rendered.startswith("# VaultBridge Retrieval Benchmark\n")


def test_cli_rejects_invalid_format_and_arguments_with_exit_two():
    with pytest.raises(SystemExit, match="2"):
        main(["--format", "yaml"])
    with pytest.raises(SystemExit, match="2"):
        main(["--unknown"])


def test_cli_operational_failure_returns_one_without_private_detail(caplog):
    output = io.StringIO()
    error = io.StringIO()

    def fail():
        logging.getLogger("vaultbridge.semantic").critical("private logged detail")
        warnings.warn("private warning detail", UserWarning, stacklevel=1)
        raise RuntimeError("private path and model internals")

    assert main([], output=output, error_output=error, executor=fail) == EXIT_OPERATIONAL_FAILURE
    assert output.getvalue() == ""
    assert error.getvalue() == "Benchmark failed (RuntimeError).\n"
    assert "private path" not in error.getvalue()
    assert "private logged detail" not in caplog.text
