from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.core.config import Settings
from app.services.relationships import RelationshipService
from app.services.semantic_search import (
    Embedder,
    SemanticResult,
    SemanticSearchService,
    semantic_search_service_from_settings,
)
from app.services.vault import VaultService
from tests.eval.runner import (
    CORPUS_ROOT,
    DeterministicConceptEmbedder,
    EvaluationMetrics,
    RelevanceExpectation,
    RetrievalCase,
    calculate_metrics,
)

FIXTURE_ROOT = Path(__file__).parent
GRAPH_CORPUS_ROOT = FIXTURE_ROOT / "graph_corpus"
GRAPH_CASES_PATH = FIXTURE_ROOT / "graph_cases.json"
GRAPH_EVIDENCE_PATH = FIXTURE_ROOT / "graph_comparison.json"
GRAPH_RESULT_LIMIT = 5


@dataclass(frozen=True)
class GraphRankedResult:
    path: str
    source: Literal["baseline", "outgoing", "backlink"]
    anchor_path: str | None


@dataclass(frozen=True)
class GraphCaseResult:
    case: RetrievalCase
    rank: int | None
    results: tuple[GraphRankedResult, ...]


@dataclass(frozen=True)
class GraphComparison:
    case: RetrievalCase
    baseline: GraphCaseResult
    graph_aware: GraphCaseResult


def _require_mapping(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def load_graph_cases() -> tuple[RetrievalCase, ...]:
    raw_cases = json.loads(GRAPH_CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list):
        raise ValueError("graph retrieval cases must be a list")

    cases: list[RetrievalCase] = []
    for index, raw_case in enumerate(raw_cases):
        case_data = _require_mapping(raw_case, context=f"case {index}")
        expected_data = _require_mapping(
            case_data.get("expected"),
            context=f"case {index}.expected",
        )
        cases.append(
            RetrievalCase(
                case_id=str(case_data["id"]),
                language=str(case_data["language"]),
                category=str(case_data["category"]),
                query=str(case_data["query"]),
                expected=RelevanceExpectation(
                    path=str(expected_data["path"]),
                    heading=None,
                    within_top_k=int(expected_data["within_top_k"]),
                ),
                not_top_1=(),
            )
        )
    return tuple(cases)


def copy_graph_evaluation_corpus(vault_root: Path) -> int:
    copied = 0
    for corpus_root in (CORPUS_ROOT, GRAPH_CORPUS_ROOT):
        for source_path in sorted(corpus_root.rglob("*.md")):
            destination = vault_root / source_path.relative_to(corpus_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            copied += 1
    return copied


def build_graph_services(
    work_root: Path,
    *,
    model_name: str = "vb105/deterministic-concepts",
    embedder: Embedder | None = None,
) -> tuple[SemanticSearchService, RelationshipService, int]:
    vault_root = work_root / "vault"
    note_count = copy_graph_evaluation_corpus(vault_root)
    settings = Settings.model_validate(
        {
            "VAULT_PATH": vault_root,
            "SEMANTIC_DATA_PATH": work_root / "data",
            "SEMANTIC_MODEL": model_name,
        }
    )
    semantic_service = semantic_search_service_from_settings(settings, embedder=embedder)
    vault_service = VaultService(
        vault_root=vault_root,
        max_note_bytes=settings.max_note_bytes,
    )
    return semantic_service, RelationshipService(vault_service), note_count


def build_deterministic_graph_services(
    work_root: Path,
) -> tuple[SemanticSearchService, RelationshipService]:
    semantic_service, relationship_service, note_count = build_graph_services(
        work_root,
        embedder=DeterministicConceptEmbedder(),
    )
    sync_result = semantic_service.sync()
    if sync_result["indexed"] != note_count:
        raise AssertionError(f"graph evaluation corpus was not fully indexed: {sync_result}")
    return semantic_service, relationship_service


def baseline_result(
    case: RetrievalCase,
    semantic_results: Sequence[SemanticResult],
) -> GraphCaseResult:
    ranked = tuple(
        GraphRankedResult(path=result.path, source="baseline", anchor_path=None)
        for result in semantic_results
    )
    return GraphCaseResult(
        case=case,
        rank=_expected_rank(case, ranked),
        results=ranked,
    )


def graph_aware_result(
    case: RetrievalCase,
    semantic_results: Sequence[SemanticResult],
    relationship_service: RelationshipService,
    *,
    limit: int = GRAPH_RESULT_LIMIT,
) -> GraphCaseResult:
    """Interleave verified one-hop neighbors after each unchanged semantic anchor."""
    ranked: list[GraphRankedResult] = []
    seen: set[str] = set()

    def append(path: str, source: Literal["baseline", "outgoing", "backlink"], anchor: str | None) -> None:
        if len(ranked) >= limit or path in seen:
            return
        seen.add(path)
        ranked.append(GraphRankedResult(path=path, source=source, anchor_path=anchor))

    for semantic_result in semantic_results:
        if len(ranked) >= limit:
            break
        anchor_path = semantic_result.path
        append(anchor_path, "baseline", None)
        for relationship in relationship_service.outgoing_relationships(anchor_path):
            if relationship.resolved_path is not None:
                append(relationship.resolved_path, "outgoing", anchor_path)
        for backlink in relationship_service.backlinks(anchor_path):
            append(backlink.source_path.replace("\\", "/"), "backlink", anchor_path)

    outcome = tuple(ranked)
    return GraphCaseResult(
        case=case,
        rank=_expected_rank(case, outcome),
        results=outcome,
    )


def _expected_rank(
    case: RetrievalCase,
    results: Sequence[GraphRankedResult],
) -> int | None:
    return next(
        (
            rank
            for rank, result in enumerate(results, start=1)
            if result.path == case.expected.path
        ),
        None,
    )


def run_graph_comparison(
    semantic_service: SemanticSearchService,
    relationship_service: RelationshipService,
    cases: tuple[RetrievalCase, ...] | None = None,
) -> tuple[GraphComparison, ...]:
    comparisons: list[GraphComparison] = []
    for case in cases or load_graph_cases():
        semantic_results = tuple(semantic_service.search(case.query, limit=GRAPH_RESULT_LIMIT))
        comparisons.append(
            GraphComparison(
                case=case,
                baseline=baseline_result(case, semantic_results),
                graph_aware=graph_aware_result(
                    case,
                    semantic_results,
                    relationship_service,
                ),
            )
        )
    return tuple(comparisons)


def comparison_metrics(
    comparisons: Sequence[GraphComparison],
) -> tuple[EvaluationMetrics, EvaluationMetrics]:
    baseline = calculate_metrics(tuple(comparison.baseline for comparison in comparisons))
    graph_aware = calculate_metrics(tuple(comparison.graph_aware for comparison in comparisons))
    return baseline, graph_aware


def comparison_payload(comparisons: Sequence[GraphComparison]) -> dict[str, Any]:
    baseline_metrics, graph_metrics = comparison_metrics(comparisons)

    def metric_payload(metrics: EvaluationMetrics) -> dict[str, int | float]:
        return {
            "cases": metrics.cases,
            "hit_at_1": round(metrics.hit_at_1, 6),
            "hit_at_3": round(metrics.hit_at_3, 6),
            "mrr": round(metrics.mrr, 6),
        }

    return {
        "schema_version": 1,
        "method": "verified-one-hop-interleaving",
        "result_limit": GRAPH_RESULT_LIMIT,
        "summary": {
            "baseline": metric_payload(baseline_metrics),
            "graph_aware": metric_payload(graph_metrics),
        },
        "cases": [
            {
                "case_id": comparison.case.case_id,
                "expected_path": comparison.case.expected.path,
                "baseline_rank": comparison.baseline.rank,
                "graph_aware_rank": comparison.graph_aware.rank,
                "graph_aware_results": [
                    {
                        "rank": rank,
                        "path": result.path,
                        "source": result.source,
                        "anchor_path": result.anchor_path,
                    }
                    for rank, result in enumerate(comparison.graph_aware.results, start=1)
                ],
            }
            for comparison in comparisons
        ],
    }


def format_comparison_table(comparisons: Sequence[GraphComparison]) -> str:
    rows = [
        "| Case | Expected path | Baseline rank | Graph-aware rank |",
        "|---|---|---:|---:|",
    ]
    for comparison in comparisons:
        baseline_rank = comparison.baseline.rank if comparison.baseline.rank is not None else "null"
        graph_rank = comparison.graph_aware.rank if comparison.graph_aware.rank is not None else "null"
        rows.append(
            f"| {comparison.case.case_id} | {comparison.case.expected.path} | "
            f"{baseline_rank} | {graph_rank} |"
        )
    return "\n".join(rows)
