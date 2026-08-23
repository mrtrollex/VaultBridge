import json
from dataclasses import replace

from app.services.semantic_search import SemanticSearchService
from tests.eval.runner import (
    README_PATH,
    DeterministicConceptEmbedder,
    build_service,
    calculate_group_metrics,
    format_metrics_table,
    load_baseline,
    load_cases,
    material_score_tie_diagnostics,
    metrics_payload,
    run_evaluation,
)


def test_fixture_shape_is_multilingual_and_heading_aware():
    cases = load_cases()

    assert len(cases) == 13
    assert {case.language for case in cases} == {"en", "sk", "cross-language"}
    assert sum(case.language == "sk" for case in cases) >= 3
    assert sum(case.expected.heading is not None for case in cases) >= 6
    assert sum(case.category == "heading-context" for case in cases) >= 2
    assert len({case.case_id for case in cases}) == len(cases)


def test_retrieval_cases_meet_relevance_expectations(tmp_path):
    service, embedder = build_service(tmp_path)
    cases = load_cases()
    outcomes = run_evaluation(service, cases)
    failures: list[str] = []

    for outcome in outcomes:
        accepted = outcome.rank is not None and outcome.rank <= outcome.case.expected.within_top_k
        negative_confusion = (
            outcome.results
            and outcome.results[0].path in outcome.case.not_top_1
        )
        if not accepted or negative_confusion:
            failures.append(outcome.diagnostic())

    assert embedder.calls
    assert len(outcomes) == len(cases)
    assert not material_score_tie_diagnostics(outcomes), "\n\n".join(
        material_score_tie_diagnostics(outcomes)
    )
    assert not failures, "\n\n".join(failures)


def test_vb022_retrieval_baseline_and_documentation(tmp_path):
    service, _ = build_service(tmp_path)
    outcomes = run_evaluation(service, load_cases())
    metrics = calculate_group_metrics(outcomes)
    actual = metrics_payload(metrics)
    expected = load_baseline()

    assert actual == expected, (
        "VB-022 baseline changed:\n"
        f"expected={json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"actual={json.dumps(actual, indent=2, sort_keys=True)}"
    )
    assert format_metrics_table(metrics) in README_PATH.read_text(encoding="utf-8")


def test_evaluation_ranks_are_independent_of_repository_iteration_order(tmp_path, monkeypatch):
    service, _ = build_service(tmp_path)
    cases = load_cases()
    normal = run_evaluation(service, cases)
    chunks = service.repository.load_chunks()

    monkeypatch.setattr(service.repository, "load_chunks", lambda: list(reversed(chunks)))
    reversed_order = run_evaluation(service, cases)

    normal_ranks = {outcome.case.case_id: outcome.rank for outcome in normal}
    reversed_ranks = {outcome.case.case_id: outcome.rank for outcome in reversed_order}
    assert reversed_ranks == normal_ranks, (
        "VB-022 ranks depend on repository iteration order:\n"
        f"normal={normal_ranks}\nreversed={reversed_ranks}"
    )
    assert metrics_payload(calculate_group_metrics(reversed_order)) == metrics_payload(
        calculate_group_metrics(normal)
    )
    ties = (*material_score_tie_diagnostics(normal), *material_score_tie_diagnostics(reversed_order))
    assert not ties, "\n\n".join(ties)


def test_material_tie_diagnostic_identifies_the_affected_case(tmp_path):
    service, _ = build_service(tmp_path)
    case = next(case for case in load_cases() if case.case_id == "hierarchy-database-recovery")
    outcome = run_evaluation(service, (case,))[0]
    tied_results = tuple(replace(result, score=0.5) for result in outcome.results[:2])
    diagnostic = material_score_tie_diagnostics((replace(outcome, results=tied_results),))

    assert len(diagnostic) == 1
    assert "hierarchy-database-recovery" in diagnostic[0]
    assert case.query in diagnostic[0]
    assert "Runbooks/Recovery Alpha.md" in diagnostic[0]
    assert "Runbooks/Recovery Beta.md" in diagnostic[0]
    assert "tied_score=0.5" in diagnostic[0]
    assert "expected_rank=1 accepted_top_k=1" in diagnostic[0]

    irrelevant_tie = replace(
        outcome,
        results=(
            outcome.results[0],
            replace(outcome.results[1], score=0.4),
            replace(outcome.results[2], score=0.4),
        ),
    )
    assert not material_score_tie_diagnostics((irrelevant_tie,))


def test_heading_context_cases_depend_on_vb021_embedding_input(tmp_path, monkeypatch):
    cases = tuple(case for case in load_cases() if case.category == "heading-context")
    normal_service, _ = build_service(tmp_path / "normal")
    recovery_chunks = {
        chunk.path: chunk
        for chunk in normal_service.repository.load_chunks()
        if chunk.heading in {"PostgreSQL > Recovery Procedure", "TrueNAS > Recovery Procedure"}
    }
    assert set(recovery_chunks) == {
        "Runbooks/Recovery Alpha.md",
        "Runbooks/Recovery Beta.md",
    }
    assert all(chunk.content.startswith("## Recovery Procedure\n") for chunk in recovery_chunks.values())
    assert "PostgreSQL" not in recovery_chunks["Runbooks/Recovery Alpha.md"].content
    assert "TrueNAS" not in recovery_chunks["Runbooks/Recovery Beta.md"].content

    normal = {outcome.case.case_id: outcome for outcome in run_evaluation(normal_service, cases)}
    assert all(outcome.rank == 1 for outcome in normal.values())

    monkeypatch.setattr(
        SemanticSearchService,
        "_build_embedding_text",
        staticmethod(lambda title, heading, content: f"{title}\n{content}"),
    )
    suppressed_service, _ = build_service(tmp_path / "heading-suppressed")
    suppressed = {
        outcome.case.case_id: outcome
        for outcome in run_evaluation(suppressed_service, cases)
    }

    assert suppressed["hierarchy-database-recovery"].rank != 1, suppressed[
        "hierarchy-database-recovery"
    ].diagnostic()
    assert suppressed["hierarchy-storage-recovery"].rank != 1, suppressed[
        "hierarchy-storage-recovery"
    ].diagnostic()


def test_cross_language_case_depends_on_multilingual_concept_equivalence(tmp_path):
    cases = tuple(case for case in load_cases() if case.language == "cross-language")
    normal_service, _ = build_service(tmp_path / "normal")
    normal = run_evaluation(normal_service, cases)
    assert len(normal) == 1
    assert normal[0].rank == 1, normal[0].diagnostic()

    monolingual_service, _ = build_service(
        tmp_path / "monolingual",
        embedder=DeterministicConceptEmbedder(multilingual=False),
    )
    monolingual = run_evaluation(monolingual_service, cases)
    assert monolingual[0].rank is None, monolingual[0].diagnostic()
