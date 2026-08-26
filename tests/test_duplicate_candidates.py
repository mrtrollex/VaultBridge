from __future__ import annotations

from pathlib import Path

import pytest

from app.services.duplicate_candidates import DuplicateCandidateService, normalize_note_title
from app.services.semantic_search import SemanticResult, SemanticSearchUnavailableError
from app.services.vault import VaultService
from tests.test_api import RecordingIndexer, auth, client_for, replace_with_symlink_or_skip


class StubSemanticSearch:
    def __init__(self, results=(), *, error: Exception | None = None) -> None:
        self.results = list(results)
        self.error = error
        self.calls: list[tuple[str, str, int, float]] = []

    def search(self, text, *, folder, limit, min_score):
        self.calls.append((text, folder, limit, min_score))
        if self.error is not None:
            raise self.error
        return self.results[:limit]


def service_for(tmp_path: Path, semantic_search: StubSemanticSearch) -> DuplicateCandidateService:
    return DuplicateCandidateService(
        vault_service=VaultService(vault_root=tmp_path, max_note_bytes=1_000_000),
        semantic_search_service=semantic_search,
    )


def semantic_result(path: str, score: float) -> SemanticResult:
    return SemanticResult(
        path=path,
        title="untrusted indexed title",
        score=score,
        semantic_score=score - 0.01,
        lexical_score=score - 0.02,
        snippet=f"snippet for {path}",
        heading="Indexed heading",
    )


def test_title_normalization_is_unicode_casefolded_whitespace_equivalence_without_substrings(
    tmp_path,
):
    (tmp_path / "Résumé   Plan.md").write_text("first", encoding="utf-8")
    (tmp_path / "résumé plan.md").write_text("second", encoding="utf-8")
    (tmp_path / "Résumé Planning.md").write_text("partial", encoding="utf-8")
    semantic = StubSemanticSearch()
    service = service_for(tmp_path, semantic)

    results = service.find_candidates(title="  Re\u0301sume\u0301 plan  ")

    assert normalize_note_title("  Re\u0301sume\u0301 plan  ") == "résumé plan"
    assert [candidate.path for candidate in results] == ["Résumé   Plan.md", "résumé plan.md"]
    assert all(candidate.match_type == "exact_title" for candidate in results)
    assert all(candidate.score is None and candidate.snippet is None for candidate in results)
    assert semantic.calls == [("  Re\u0301sume\u0301 plan  ", "", 15, 0.28)]


def test_exact_candidates_lead_semantic_order_and_merge_semantic_evidence_once(tmp_path):
    for path in ("Alpha.md", "Different B.md", "Different C.md"):
        (tmp_path / path).write_text(path, encoding="utf-8")
    semantic = StubSemanticSearch(
        [
            semantic_result("Different B.md", 0.90),
            semantic_result("Alpha.md", 0.80),
            semantic_result("Different C.md", 0.70),
        ]
    )
    service = service_for(tmp_path, semantic)

    results = service.find_candidates(
        title="Alpha",
        text="Concise topic",
        limit=3,
        min_score=0.2,
    )

    assert [candidate.path for candidate in results] == [
        "Alpha.md",
        "Different B.md",
        "Different C.md",
    ]
    assert [candidate.match_type for candidate in results] == [
        "exact_title",
        "semantic",
        "semantic",
    ]
    assert results[0].score == 0.80
    assert results[0].snippet == "snippet for Alpha.md"
    assert semantic.calls == [("Alpha\n\nConcise topic", "", 9, 0.2)]


def test_limit_applies_after_exact_merge_deduplication_and_stale_filtering(tmp_path):
    (tmp_path / "One").mkdir()
    (tmp_path / "Two").mkdir()
    for path in ("One/Same.md", "Two/Same.md", "Live B.md", "Live C.md"):
        (tmp_path / path).write_text(path, encoding="utf-8")
    semantic = StubSemanticSearch(
        [
            semantic_result("Missing.md", 0.99),
            semantic_result("One/Same.md", 0.95),
            semantic_result("Live B.md", 0.90),
            semantic_result("Live C.md", 0.80),
        ]
    )
    service = service_for(tmp_path, semantic)

    results = service.find_candidates(title="Same", limit=3)

    assert [candidate.path for candidate in results] == [
        "One/Same.md",
        "Two/Same.md",
        "Live B.md",
    ]
    assert semantic.calls[0][2] == 9


def test_folder_scope_applies_to_live_and_untrusted_semantic_candidates(tmp_path):
    for folder in ("Scoped", "Other"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "Same.md").write_text(folder, encoding="utf-8")
    semantic = StubSemanticSearch(
        [semantic_result("Other/Same.md", 0.9), semantic_result("Scoped/Same.md", 0.8)]
    )
    service = service_for(tmp_path, semantic)

    results = service.find_candidates(title="Same", folder="Scoped")

    assert [candidate.path for candidate in results] == ["Scoped/Same.md"]
    assert results[0].score == 0.8
    assert semantic.calls == [("Same", "Scoped", 15, 0.28)]
    assert service.find_candidates(title="Same", folder="Missing") == []
    assert len(semantic.calls) == 1


def test_semantic_candidates_require_live_markdown_verification(tmp_path):
    (tmp_path / "Live.md").write_text("live", encoding="utf-8")
    (tmp_path / "Directory.md").mkdir()
    (tmp_path / "Other.txt").write_text("not markdown", encoding="utf-8")
    semantic = StubSemanticSearch(
        [
            semantic_result("../escape.md", 0.99),
            semantic_result("Missing.md", 0.98),
            semantic_result("Directory.md", 0.97),
            semantic_result("Other.txt", 0.96),
            semantic_result("Live.md", 0.90),
        ]
    )

    results = service_for(tmp_path, semantic).find_candidates(title="Prospective")

    assert [candidate.path for candidate in results] == ["Live.md"]


def test_live_title_scan_is_filename_only_and_excludes_service_directories(tmp_path, monkeypatch):
    (tmp_path / "Target.md").write_text("private body", encoding="utf-8")
    excluded = tmp_path / ".trash"
    excluded.mkdir()
    (excluded / "Target.md").write_text("discarded", encoding="utf-8")
    semantic = StubSemanticSearch()
    service = service_for(tmp_path, semantic)

    def fail_read(*_args, **_kwargs):
        raise AssertionError("exact-title discovery read note content")

    monkeypatch.setattr(Path, "read_text", fail_read)

    assert [candidate.path for candidate in service.find_candidates(title="Target")] == ["Target.md"]


def test_broken_and_external_symlinks_are_excluded_from_title_and_semantic_candidates(tmp_path):
    (tmp_path / "Broken.md").write_text("replace", encoding="utf-8")
    (tmp_path / "External.md").write_text("replace", encoding="utf-8")
    external = tmp_path.parent / f"{tmp_path.name}-outside.md"
    external.write_text("outside", encoding="utf-8")
    replace_with_symlink_or_skip(tmp_path / "Broken.md", tmp_path / "missing.md")
    replace_with_symlink_or_skip(tmp_path / "External.md", external)
    semantic = StubSemanticSearch(
        [semantic_result("Broken.md", 0.9), semantic_result("External.md", 0.8)]
    )

    try:
        results = service_for(tmp_path, semantic).find_candidates(title="External")
    finally:
        external.unlink(missing_ok=True)

    assert results == []


def test_semantic_unavailable_falls_back_only_when_exact_title_evidence_exists(tmp_path):
    (tmp_path / "Existing.md").write_text("body", encoding="utf-8")
    unavailable = SemanticSearchUnavailableError("Semantic index is unavailable")
    service = service_for(tmp_path, StubSemanticSearch(error=unavailable))

    exact = service.find_candidates(title="Existing")

    assert [candidate.path for candidate in exact] == ["Existing.md"]
    with pytest.raises(SemanticSearchUnavailableError, match="Semantic index is unavailable"):
        service.find_candidates(title="Absent")


def test_unexpected_semantic_programming_error_is_not_hidden(tmp_path):
    service = service_for(tmp_path, StubSemanticSearch(error=RuntimeError("unexpected defect")))

    with pytest.raises(RuntimeError, match="unexpected defect"):
        service.find_candidates(title="Prospective")


def test_legacy_and_v1_duplicate_routes_are_identical_trim_title_and_remain_read_only(tmp_path):
    note = tmp_path / "Existing.md"
    note.write_text("Existing semantic body.", encoding="utf-8")
    client = client_for(tmp_path)

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        before_content = note.read_bytes()
        before_chunks = client.app.state.semantic_search_service.repository.load_chunks()
        payload = {"title": "  Existing  ", "text": "semantic body", "min_score": 0.0}
        legacy = client.post("/notes/duplicates", headers=auth(), json=payload)
        versioned = client.post("/api/v1/notes/duplicates", headers=auth(), json=payload)

    assert legacy.status_code == versioned.status_code == 200
    assert legacy.json() == versioned.json()
    assert legacy.json()["title"] == "Existing"
    assert legacy.json()["results"][0]["match_type"] == "exact_title"
    assert note.read_bytes() == before_content
    assert client.app.state.semantic_search_service.repository.load_chunks() == before_chunks


@pytest.mark.parametrize("path", ["/notes/duplicates", "/api/v1/notes/duplicates"])
def test_duplicate_routes_require_auth_and_validate_bounded_request(path, tmp_path):
    client = client_for(tmp_path, semantic_indexer=RecordingIndexer())

    assert client.post(path, json={"title": "Candidate"}).status_code == 401
    assert client.post(path, headers=auth(), json={"title": "   "}).status_code == 422
    assert client.post(path, headers=auth(), json={"title": "x" * 181}).status_code == 422
    assert client.post(path, headers=auth(), json={"title": "x", "text": "y" * 4001}).status_code == 422
    assert client.post(path, headers=auth(), json={"title": "x", "limit": 21}).status_code == 422
    assert client.post(path, headers=auth(), json={"title": "x", "min_score": 1.01}).status_code == 422


def test_duplicate_route_semantic_unavailable_contract_and_unexpected_errors(tmp_path, monkeypatch):
    client = client_for(tmp_path, semantic_indexer=RecordingIndexer())
    service = client.app.state.duplicate_candidate_service.semantic_search_service

    def unavailable(*_args, **_kwargs):
        raise SemanticSearchUnavailableError("Semantic index is unavailable")

    monkeypatch.setattr(service, "search", unavailable)
    response = client.post("/api/v1/notes/duplicates", headers=auth(), json={"title": "Absent"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Semantic search unavailable: Semantic index is unavailable"
    }

    def unexpected(*_args, **_kwargs):
        raise RuntimeError("unexpected duplicate defect")

    monkeypatch.setattr(service, "search", unexpected)
    with pytest.raises(RuntimeError, match="unexpected duplicate defect"):
        client.post("/notes/duplicates", headers=auth(), json={"title": "Absent"})
