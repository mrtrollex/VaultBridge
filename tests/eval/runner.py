from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.repositories.semantic import SemanticRepository
from app.services.semantic_search import SemanticResult, SemanticSearchService

FIXTURE_ROOT = Path(__file__).parent
CORPUS_ROOT = FIXTURE_ROOT / "corpus"
CASES_PATH = FIXTURE_ROOT / "retrieval_cases.json"
BASELINE_PATH = FIXTURE_ROOT / "baseline.json"
README_PATH = FIXTURE_ROOT / "README.md"
METRIC_GROUPS = ("all", "english", "slovak", "cross-language", "heading-context")


@dataclass(frozen=True)
class RelevanceExpectation:
    path: str
    heading: str | None
    within_top_k: int


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    language: str
    category: str
    query: str
    expected: RelevanceExpectation
    not_top_1: tuple[str, ...]


@dataclass(frozen=True)
class CaseResult:
    case: RetrievalCase
    rank: int | None
    results: tuple[SemanticResult, ...]

    def diagnostic(self) -> str:
        expected_heading = self.case.expected.heading or "<any heading>"
        actual = "\n".join(
            f"  {index}. {result.path} [{result.heading or '<no heading>'}] "
            f"score={result.score} semantic={result.semantic_score} lexical={result.lexical_score}"
            for index, result in enumerate(self.results, start=1)
        ) or "  <no results>"
        return (
            f"case={self.case.case_id!r}\n"
            f"groups={', '.join(groups_for_case(self.case))}\n"
            f"query={self.case.query!r}\n"
            f"expected={self.case.expected.path} [{expected_heading}] "
            f"actual_rank={self.rank} within_top_k={self.case.expected.within_top_k}\n"
            f"actual:\n{actual}"
        )


@dataclass(frozen=True)
class EvaluationMetrics:
    cases: int
    hit_at_1: float
    hit_at_3: float
    mrr: float


class DeterministicConceptEmbedder:
    """Map EN/SK retrieval concepts to stable vectors without loading an ML model."""

    _CONCEPTS = (
        (("postgresql", "postgres", "relational database", "pg_dump", "wal"), ("databaz",)),
        (("truenas", "zfs", "nas", "network storage"), ("sietov", "ulozisk")),
        (("backup", "retention", "snapshot"), ("zaloh", "snimk")),
        (("restore", "recover", "recovery"), ("obnov",)),
        (("replication", "replica", "standby"), ("replik",)),
        (("storage", "dataset", "pool", "drive", "disk"), ("ulozisk", "disk")),
        (("failure", "failed"), ("zlyhan", "poruch")),
        (("docker", "container", "compose", "deployment"), ("nasaden",)),
        (("fastapi", "asgi", "pydantic"), ()),
        (("authentication", "authorization"), ("overen",)),
        (("bearer", "token", "api key", "credential"), ("pristupov",)),
        (("oracle", "pl/sql", "ords", "apex"), ()),
        (("rest", "endpoint", "json", "api"), ()),
        (("jellyfin", "media", "movie", "poster", "subtitle"), ()),
        (("garden", "tomato", "soil", "watering"), ()),
    )
    _LEXICAL_DIMENSIONS = 64

    def __init__(self, *, multilingual: bool = True) -> None:
        self.multilingual = multilingual
        self.calls: list[list[str]] = []

    @staticmethod
    def _fold(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text.casefold())
        return "".join(character for character in decomposed if not unicodedata.combining(character))

    def embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        self.calls.append(list(texts))
        vectors: list[np.ndarray] = []
        for text in texts:
            folded = self._fold(text)
            values = []
            for english_terms, slovak_terms in self._CONCEPTS:
                terms = english_terms + slovak_terms if self.multilingual else english_terms
                matches = sum(folded.count(term) for term in terms)
                values.append(0.48 * min(matches, 2))

            lexical = [0.0] * self._LEXICAL_DIMENSIONS
            for token in re.findall(r"[\w-]+", folded, flags=re.UNICODE):
                if len(token) <= 2:
                    continue
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=2).digest()
                bucket = int.from_bytes(digest, "big") % self._LEXICAL_DIMENSIONS
                lexical[bucket] += 0.22
            values.extend(lexical)
            values.append(0.10)
            vectors.append(np.asarray(values, dtype=np.float32))
        return vectors


def _require_mapping(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def load_cases() -> tuple[RetrievalCase, ...]:
    raw_cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list):
        raise ValueError("retrieval cases must be a list")

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
                    heading=(
                        str(expected_data["heading"])
                        if expected_data.get("heading") is not None
                        else None
                    ),
                    within_top_k=int(expected_data["within_top_k"]),
                ),
                not_top_1=tuple(str(path) for path in case_data.get("not_top_1", [])),
            )
        )
    return tuple(cases)


def build_service(
    tmp_path: Path,
    *,
    embedder: DeterministicConceptEmbedder | None = None,
) -> tuple[SemanticSearchService, DeterministicConceptEmbedder]:
    vault_root = tmp_path / "vault"
    source_paths = tuple(sorted(CORPUS_ROOT.rglob("*.md")))
    for source_path in source_paths:
        relative_path = source_path.relative_to(CORPUS_ROOT)
        target_path = vault_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_path.read_bytes())

    embedder = embedder or DeterministicConceptEmbedder()
    service = SemanticSearchService(
        vault_root=vault_root,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        model_name="vb022/deterministic-concepts",
        chunk_chars=250,
        chunk_overlap=0,
        embedder=embedder,
    )
    sync_result = service.sync()
    if sync_result["indexed"] != len(source_paths):
        raise AssertionError(f"evaluation corpus was not fully indexed: {sync_result}")
    return service, embedder


def run_evaluation(service: SemanticSearchService, cases: tuple[RetrievalCase, ...]) -> tuple[CaseResult, ...]:
    outcomes: list[CaseResult] = []
    for case in cases:
        results = tuple(service.search(case.query, limit=5))
        rank = next(
            (
                index
                for index, result in enumerate(results, start=1)
                if result.path == case.expected.path
                and (case.expected.heading is None or result.heading == case.expected.heading)
            ),
            None,
        )
        outcomes.append(CaseResult(case=case, rank=rank, results=results))
    return tuple(outcomes)


def calculate_metrics(outcomes: tuple[CaseResult, ...]) -> EvaluationMetrics:
    if not outcomes:
        return EvaluationMetrics(cases=0, hit_at_1=0.0, hit_at_3=0.0, mrr=0.0)
    count = len(outcomes)
    return EvaluationMetrics(
        cases=count,
        hit_at_1=sum(outcome.rank == 1 for outcome in outcomes) / count,
        hit_at_3=sum(outcome.rank is not None and outcome.rank <= 3 for outcome in outcomes) / count,
        mrr=sum(1.0 / outcome.rank for outcome in outcomes if outcome.rank is not None) / count,
    )


def groups_for_case(case: RetrievalCase) -> tuple[str, ...]:
    language_group = {
        "en": "english",
        "sk": "slovak",
        "cross-language": "cross-language",
    }[case.language]
    groups = ["all", language_group]
    if case.category == "heading-context":
        groups.append("heading-context")
    return tuple(groups)


def calculate_group_metrics(outcomes: tuple[CaseResult, ...]) -> dict[str, EvaluationMetrics]:
    return {
        group: calculate_metrics(
            tuple(outcome for outcome in outcomes if group in groups_for_case(outcome.case))
        )
        for group in METRIC_GROUPS
    }


def metrics_payload(metrics: dict[str, EvaluationMetrics]) -> dict[str, dict[str, int | float]]:
    return {
        group: {
            "cases": value.cases,
            "hit_at_1": round(value.hit_at_1, 6),
            "hit_at_3": round(value.hit_at_3, 6),
            "mrr": round(value.mrr, 6),
        }
        for group, value in metrics.items()
    }


def load_baseline() -> dict[str, dict[str, int | float]]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    if not isinstance(baseline, dict):
        raise ValueError("retrieval baseline must be an object")
    return baseline


def format_metrics_table(metrics: dict[str, EvaluationMetrics]) -> str:
    labels = {
        "all": "All",
        "english": "English",
        "slovak": "Slovak",
        "cross-language": "Cross-language",
        "heading-context": "Heading context",
    }
    rows = [
        "| Case group | Cases | Hit@1 | Hit@3 | MRR |",
        "|---|---:|---:|---:|---:|",
    ]
    for group in METRIC_GROUPS:
        value = metrics[group]
        rows.append(
            f"| {labels[group]} | {value.cases} | {value.hit_at_1:.2%} | "
            f"{value.hit_at_3:.2%} | {value.mrr:.2%} |"
        )
    return "\n".join(rows)


def material_score_tie_diagnostics(outcomes: tuple[CaseResult, ...]) -> tuple[str, ...]:
    diagnostics: list[str] = []
    for outcome in outcomes:
        score_groups: dict[float, list[tuple[int, SemanticResult]]] = {}
        for rank, result in enumerate(outcome.results, start=1):
            score_groups.setdefault(result.score, []).append((rank, result))

        for score, tied in score_groups.items():
            if len(tied) < 2:
                continue
            ranks = {rank for rank, _ in tied}
            includes_expected = any(
                result.path == outcome.case.expected.path
                and (
                    outcome.case.expected.heading is None
                    or result.heading == outcome.case.expected.heading
                )
                for _, result in tied
            )
            negative_top_1_statuses = {
                result.path in outcome.case.not_top_1 for _, result in tied
            }
            affects_negative_top_1 = (
                1 in ranks and len(negative_top_1_statuses) > 1
            )
            if not includes_expected and not affects_negative_top_1:
                continue
            candidates = ", ".join(
                f"rank {rank}: {result.path} [{result.heading or '<no heading>'}]"
                for rank, result in tied
            )
            diagnostics.append(
                f"case={outcome.case.case_id!r}\n"
                f"query={outcome.case.query!r}\n"
                f"tied_score={score} candidates={candidates}\n"
                f"expected_rank={outcome.rank} accepted_top_k={outcome.case.expected.within_top_k}"
            )
    return tuple(diagnostics)
