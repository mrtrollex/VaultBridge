from __future__ import annotations

from collections.abc import Sequence
from math import sqrt

import numpy as np
import pytest

from app.repositories.semantic import SemanticRepository, StoredChunk
from app.services.semantic_search import SemanticSearchService


class QueryVectorEmbedder:
    def __init__(self, vector: Sequence[float] = (1.0, 0.0)) -> None:
        self.vector = np.asarray(vector, dtype=np.float32)

    def embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        return [self.vector.copy() for _ in texts]


def _vector_with_similarity(similarity: float) -> bytes:
    vector = np.asarray(
        [similarity, sqrt(max(0.0, 1.0 - similarity**2))],
        dtype=np.float32,
    )
    return vector.tobytes()


def _chunk(
    path: str,
    *,
    similarity: float,
    content: str = "unrelated body",
    heading: str | None = None,
    chunk_index: int = 0,
) -> StoredChunk:
    return StoredChunk(
        path=path,
        chunk_index=chunk_index,
        heading=heading,
        content=content,
        embedding=_vector_with_similarity(similarity),
        dimensions=2,
    )


def _ranking_service(tmp_path, monkeypatch, chunks: Sequence[StoredChunk]) -> SemanticSearchService:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    repository = SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3")
    service = SemanticSearchService(
        vault_root=vault_root,
        repository=repository,
        model_name="ranking/test",
        embedder=QueryVectorEmbedder(),
    )
    assert service.sync() == {"indexed": 0, "unchanged": 0, "removed": 0}
    monkeypatch.setattr(repository, "load_chunks", lambda: list(chunks))
    return service


def test_semantic_relevance_remains_primary_over_a_lexical_distractor(tmp_path, monkeypatch):
    chunks = (
        _chunk("Notes/Relevant.md", similarity=0.99),
        _chunk("Zebra Lunar.md", similarity=0.30),
    )
    service = _ranking_service(tmp_path, monkeypatch, chunks)

    results = service.search("zebra lunar", min_score=0.0)

    assert [result.path for result in results] == ["Notes/Relevant.md", "Zebra Lunar.md"]
    assert results[0].semantic_score == pytest.approx(0.99)
    assert results[0].lexical_score == 0.0
    assert results[1].lexical_score == pytest.approx(0.75)


def test_lexical_exact_match_can_resolve_a_semantically_broad_distractor(tmp_path, monkeypatch):
    chunks = (
        _chunk("Notes/Broad.md", similarity=0.80),
        _chunk("Zebra Lunar.md", similarity=0.60),
    )
    service = _ranking_service(tmp_path, monkeypatch, chunks)

    results = service.search("zebra lunar", min_score=0.0)

    assert [result.path for result in results] == ["Zebra Lunar.md"]
    assert results[0].lexical_score == pytest.approx(0.75)


def test_title_and_heading_relevance_use_separate_explainable_components():
    title_match = SemanticSearchService._lexical_score_components(
        "zebra lunar",
        path="Zebra Lunar.md",
        heading=None,
        content="unrelated body",
    )
    heading_match = SemanticSearchService._lexical_score_components(
        "zebra lunar",
        path="Notes/Runbook.md",
        heading="Zebra Lunar",
        content="unrelated body",
    )

    assert title_match.title == pytest.approx(0.40)
    assert title_match.path == pytest.approx(0.25)
    assert title_match.heading == 0.0
    assert title_match.exact_match == pytest.approx(0.10)
    assert heading_match.title == 0.0
    assert heading_match.path == 0.0
    assert heading_match.heading == pytest.approx(0.10)
    assert heading_match.exact_match == 0.0
    assert title_match.score > heading_match.score


def test_equal_scores_have_stable_semantic_then_path_tie_breaks(tmp_path, monkeypatch):
    semantic_tie_winner = _chunk("Z-last.md", similarity=0.80)
    lexical_tie_loser = _chunk("A-first.md", similarity=0.625, content="zebra lunar")
    exact_a = _chunk("Alpha.md", similarity=0.70)
    exact_b = _chunk("Beta.md", similarity=0.70)
    chunks = (lexical_tie_loser, semantic_tie_winner, exact_b, exact_a)
    service = _ranking_service(tmp_path, monkeypatch, chunks)

    signal_tie = service.search("zebra lunar", min_score=0.0)
    monkeypatch.setattr(service.repository, "load_chunks", lambda: list(reversed(chunks)))
    reversed_signal_tie = service.search("zebra lunar", min_score=0.0)

    assert signal_tie[0].path == "Z-last.md"
    assert [result.path for result in reversed_signal_tie] == [
        result.path for result in signal_tie
    ]
    exact_ties = [
        result for result in signal_tie if result.path in {"Alpha.md", "Beta.md"}
    ]
    assert [result.path for result in exact_ties] == [
        "Alpha.md",
        "Beta.md",
    ]
    assert exact_ties[0].score == exact_ties[1].score


def test_normalized_hybrid_score_preserves_high_score_separation(tmp_path, monkeypatch):
    chunks = (
        _chunk("More Relevant.md", similarity=1.0),
        _chunk("Less Relevant.md", similarity=0.90),
    )
    service = _ranking_service(tmp_path, monkeypatch, chunks)

    results = service.search("relevant", min_score=0.0)

    assert [result.path for result in results] == ["More Relevant.md", "Less Relevant.md"]
    assert results[0].score < 1.0
    assert results[0].score > results[1].score
    assert results[0].score - results[1].score == pytest.approx(0.0589, abs=0.0001)
    assert SemanticSearchService._hybrid_score(1.0, 1.0) == 1.0
    assert SemanticSearchService._hybrid_score(0.90, 1.0) < 1.0


def test_note_aggregation_selects_one_stable_best_chunk(tmp_path, monkeypatch):
    chunks = (
        _chunk(
            "Runbook.md",
            similarity=0.75,
            heading="Later",
            chunk_index=1,
        ),
        _chunk(
            "Runbook.md",
            similarity=0.75,
            heading="Earlier",
            chunk_index=0,
        ),
        _chunk("Other.md", similarity=0.40),
    )
    service = _ranking_service(tmp_path, monkeypatch, chunks)

    results = service.search("unmatched", min_score=0.0)
    monkeypatch.setattr(service.repository, "load_chunks", lambda: list(reversed(chunks)))
    reversed_results = service.search("unmatched", min_score=0.0)

    assert [result.path for result in results].count("Runbook.md") == 1
    assert results[0].heading == "Earlier"
    assert [(result.path, result.heading) for result in reversed_results] == [
        (result.path, result.heading) for result in results
    ]


def test_slovak_unicode_lexical_matching_is_diacritic_insensitive():
    accented = SemanticSearchService._lexical_score(
        "Nočné zálohy",
        path="Poznámky/Zálohy.md",
        heading="Nočné zálohy",
        content="Plán nočnej zálohy.",
    )
    folded = SemanticSearchService._lexical_score(
        "Nocne zalohy",
        path="Poznamky/Zalohy.md",
        heading="Nocne zalohy",
        content="Plan nocnej zalohy.",
    )

    assert accented == pytest.approx(folded)
    assert accented > 0.0


def test_weak_stopword_query_receives_no_artificial_lexical_boost(tmp_path, monkeypatch):
    service = _ranking_service(
        tmp_path,
        monkeypatch,
        (_chunk("How The.md", similarity=0.10, content="how the"),),
    )

    assert service.search("how the") == []
    permissive = service.search("how the", min_score=0.0)
    assert len(permissive) == 1
    assert permissive[0].lexical_score == 0.0
    assert permissive[0].score == pytest.approx(0.0588, abs=0.0001)
