import json

from app.services.semantic_search import (
    LEXICAL_RANK_WEIGHT,
    RELATIVE_RESULT_FLOOR,
    SEMANTIC_RANK_WEIGHT,
)
from tests.eval.graph_runner import (
    GRAPH_EVIDENCE_PATH,
    build_deterministic_graph_services,
    comparison_payload,
    format_comparison_table,
    graph_aware_result,
    load_graph_cases,
    run_graph_comparison,
)
from tests.eval.runner import (
    README_PATH,
    build_service,
    calculate_group_metrics,
    load_baseline,
    load_cases,
    metrics_payload,
    run_evaluation,
)


def test_graph_cases_distinguish_the_candidate_from_the_baseline(tmp_path):
    semantic_service, relationship_service = build_deterministic_graph_services(tmp_path)
    comparisons = run_graph_comparison(semantic_service, relationship_service)

    assert len(comparisons) == 4
    assert {comparison.case.category for comparison in comparisons} == {
        "outgoing-relationship",
        "backlink-relationship",
    }
    assert all(comparison.baseline.rank is None for comparison in comparisons)
    assert all(comparison.graph_aware.rank == 2 for comparison in comparisons)
    assert {
        result.source
        for comparison in comparisons
        for result in comparison.graph_aware.results
    } >= {"baseline", "outgoing", "backlink"}


def test_graph_candidate_uses_only_verified_resolved_relationships(tmp_path):
    semantic_service, relationship_service = build_deterministic_graph_services(tmp_path)
    case = next(
        case for case in load_graph_cases() if case.case_id == "outgoing-service-atlas"
    )
    anchor = semantic_service.search(case.query, limit=1)
    assert [result.path for result in anchor] == ["Maps/Service Atlas.md"]

    relationships = relationship_service.outgoing_relationships("Maps/Service Atlas.md")
    assert [(item.target, item.resolved_path) for item in relationships] == [
        ("Web/FastAPI", "Web/FastAPI.md"),
        ("Shared", None),
        ("Missing Procedure", None),
        ("../../Outside", None),
    ]

    outcome = graph_aware_result(case, anchor, relationship_service)
    graph_paths = [
        result.path for result in outcome.results if result.source != "baseline"
    ]
    assert graph_paths == ["Web/FastAPI.md"]
    assert "Notes/Garden.md" not in graph_paths
    assert all("Shared.md" not in path for path in graph_paths)


def test_graph_comparison_artifact_and_documentation_are_generated_from_runner(tmp_path):
    semantic_service, relationship_service = build_deterministic_graph_services(tmp_path)
    comparisons = run_graph_comparison(semantic_service, relationship_service)

    expected = json.loads(GRAPH_EVIDENCE_PATH.read_text(encoding="utf-8"))
    assert comparison_payload(comparisons) == expected
    assert format_comparison_table(comparisons) in README_PATH.read_text(encoding="utf-8")


def test_graph_results_are_independent_of_repository_iteration_order(tmp_path, monkeypatch):
    semantic_service, relationship_service = build_deterministic_graph_services(tmp_path)
    normal = run_graph_comparison(semantic_service, relationship_service)
    chunks = semantic_service.repository.load_chunks()

    monkeypatch.setattr(
        semantic_service.repository,
        "load_chunks",
        lambda: list(reversed(chunks)),
    )
    reversed_order = run_graph_comparison(semantic_service, relationship_service)

    assert comparison_payload(reversed_order) == comparison_payload(normal)


def test_vb024_baseline_and_production_ranking_contract_remain_unchanged(tmp_path):
    semantic_service, _ = build_service(tmp_path)
    metrics = metrics_payload(
        calculate_group_metrics(run_evaluation(semantic_service, load_cases()))
    )

    assert metrics == load_baseline()
    assert SEMANTIC_RANK_WEIGHT == 1.0
    assert LEXICAL_RANK_WEIGHT == 0.70
    assert RELATIVE_RESULT_FLOOR == 0.78
