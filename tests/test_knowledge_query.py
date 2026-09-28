from __future__ import annotations

import os
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.services.knowledge_query import (
    MAX_INT64,
    MAX_LITERAL_TEXT_BYTES,
    MAX_METADATA_KEY_BYTES,
    MAX_PATH_BYTES,
    MAX_SEMANTIC_TEXT_BYTES,
    MAX_VALUE_BYTES,
    OMITTED,
    InvalidKnowledgeQueryError,
    KnowledgeQuery,
    KnowledgeQueryMatch,
    KnowledgeQueryResult,
    KnowledgeQuerySemanticUnavailableError,
    KnowledgeQueryService,
    MetadataPredicate,
    RelationshipPredicate,
    UnsafeKnowledgeQueryScopeError,
)
from app.services.relationships import RelationshipService
from app.services.semantic_search import SemanticResult
from app.services.vault import VaultService


class FakeSemanticSearch:
    def __init__(self, *, basis="compatible_ready", results=()):
        self.basis = basis
        self.results = list(results)
        self.calls = []

    def query_basis(self):
        return self.basis

    def search(self, text, **kwargs):
        self.calls.append((text, kwargs))
        eligible = kwargs["eligible_paths"]
        return [result for result in self.results if result.path in eligible][
            : kwargs["limit"]
        ]


def write(vault: Path, relative: str, content: str) -> None:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def service_for(vault: Path, *, semantic=None, max_note_bytes=1_000_000):
    vault_service = VaultService(vault_root=vault, max_note_bytes=max_note_bytes)
    relationship_service = RelationshipService(vault_service)
    semantic_service = semantic or FakeSemanticSearch()
    return (
        KnowledgeQueryService(
            vault_service=vault_service,
            relationship_service=relationship_service,
            semantic_search_service=semantic_service,
        ),
        vault_service,
        relationship_service,
        semantic_service,
    )


def paths(result: KnowledgeQueryResult) -> list[str]:
    return [match.canonical_path for match in result.matches]


def semantic_result(path: str, score: float) -> SemanticResult:
    return SemanticResult(
        path=path,
        title=Path(path).stem,
        score=score,
        semantic_score=score - 0.1,
        lexical_score=0.5,
        snippet="private",
        heading="Private",
    )


def test_domain_values_are_frozen_and_omitted_differs_from_explicit_null():
    omitted = MetadataPredicate("field", "exists")
    explicit_null = MetadataPredicate("field", "equals", None)

    assert omitted.value is OMITTED
    assert explicit_null.value is None
    with pytest.raises(FrozenInstanceError):
        omitted.key = "other"
    with pytest.raises(FrozenInstanceError):
        KnowledgeQueryMatch("Note.md").canonical_path = "Other.md"
    with pytest.raises(FrozenInstanceError):
        KnowledgeQueryResult((), "canonical_path", "none").matches = ()


@pytest.mark.parametrize(
    "query",
    [
        KnowledgeQuery(),
        KnowledgeQuery(semantic_text=""),
        KnowledgeQuery(literal_text=""),
        KnowledgeQuery(folder=""),
        KnowledgeQuery(folder="   "),
        KnowledgeQuery(paths=("",)),
        KnowledgeQuery(tags=("  ",)),
        KnowledgeQuery(literal_text="x", limit=0),
        KnowledgeQuery(literal_text="x", limit=101),
        KnowledgeQuery(literal_text="x", limit=True),
        KnowledgeQuery(metadata=(MetadataPredicate("x", "exists", None),)),
        KnowledgeQuery(metadata=(MetadataPredicate("x", "equals"),)),
        KnowledgeQuery(metadata=(MetadataPredicate("x", "unknown"),)),
        KnowledgeQuery(metadata=(MetadataPredicate("x", "equals", ("x",)),)),
        KnowledgeQuery(relationships=(RelationshipPredicate("sideways", "Note.md"),)),
        KnowledgeQuery(
            relationships=(RelationshipPredicate("outgoing", "Note.md", "html"),)
        ),
    ],
)
def test_invalid_request_shapes_fail_before_vault_work(tmp_path, monkeypatch, query):
    service, vault, _, _ = service_for(tmp_path)
    monkeypatch.setattr(
        vault,
        "markdown_path_candidate_snapshot",
        lambda: pytest.fail("validation must happen before enumeration"),
    )

    with pytest.raises(InvalidKnowledgeQueryError) as failure:
        service.query(query)
    assert failure.value.reason == "invalid_request"


@pytest.mark.parametrize("whitespace", ["leading", "trailing", "both"])
@pytest.mark.parametrize(
    ("kind", "unspaced"),
    [
        ("folder", "Projects"),
        ("path", "Projects/Note.md"),
        ("outgoing", "Target.md"),
        ("incoming", "Target.md"),
    ],
)
def test_scope_paths_reject_surrounding_whitespace_before_expensive_work(
    tmp_path,
    monkeypatch,
    whitespace,
    kind,
    unspaced,
):
    write(tmp_path, "Projects/Note.md", "[[Target]]")
    write(tmp_path, "Target.md", "target")
    service, vault, relationships, semantic = service_for(tmp_path)
    supplied = {
        "leading": f" {unspaced}",
        "trailing": f"{unspaced} ",
        "both": f" {unspaced} ",
    }[whitespace]
    query = {
        "folder": KnowledgeQuery(folder=supplied),
        "path": KnowledgeQuery(paths=(supplied,)),
        "outgoing": KnowledgeQuery(
            relationships=(RelationshipPredicate("outgoing", supplied),)
        ),
        "incoming": KnowledgeQuery(
            relationships=(RelationshipPredicate("incoming", supplied),)
        ),
    }[kind]

    def unexpected(*_args, **_kwargs):
        pytest.fail("scope validation must precede expensive work")

    monkeypatch.setattr(vault, "verify_existing_folder_scope_result", unexpected)
    monkeypatch.setattr(vault, "verify_existing_markdown_path_result", unexpected)
    monkeypatch.setattr(vault, "markdown_path_candidate_snapshot", unexpected)
    monkeypatch.setattr(vault, "read_note", unexpected)
    monkeypatch.setattr(relationships, "normalized_resolution_snapshot", unexpected)
    monkeypatch.setattr(relationships, "normalized_relationships_from_content", unexpected)
    monkeypatch.setattr(semantic, "query_basis", unexpected)
    monkeypatch.setattr(semantic, "search", unexpected)

    with pytest.raises(InvalidKnowledgeQueryError) as failure:
        service.query(query)
    assert failure.value.reason == "invalid_request"
    assert supplied not in repr(failure.value)
    assert str(tmp_path) not in repr(failure.value)


@pytest.mark.parametrize(
    ("query", "valid_query"),
    [
        (
            KnowledgeQuery(semantic_text="é" * (MAX_SEMANTIC_TEXT_BYTES // 2 + 1)),
            KnowledgeQuery(semantic_text="é" * (MAX_SEMANTIC_TEXT_BYTES // 2)),
        ),
        (
            KnowledgeQuery(literal_text="é" * (MAX_LITERAL_TEXT_BYTES // 2 + 1)),
            KnowledgeQuery(literal_text="é" * (MAX_LITERAL_TEXT_BYTES // 2)),
        ),
        (
            KnowledgeQuery(folder="é" * (MAX_PATH_BYTES // 2 + 1)),
            KnowledgeQuery(folder="missing"),
        ),
        (
            KnowledgeQuery(paths=("é" * (MAX_PATH_BYTES // 2 + 1),)),
            KnowledgeQuery(paths=("missing.md",)),
        ),
        (
            KnowledgeQuery(tags=("é" * (MAX_VALUE_BYTES // 2 + 1),)),
            KnowledgeQuery(tags=("é" * (MAX_VALUE_BYTES // 2),)),
        ),
        (
            KnowledgeQuery(
                metadata=(MetadataPredicate("é" * (MAX_METADATA_KEY_BYTES // 2 + 1), "exists"),)
            ),
            KnowledgeQuery(metadata=(MetadataPredicate("é" * (MAX_METADATA_KEY_BYTES // 2), "exists"),)),
        ),
        (
            KnowledgeQuery(metadata=(MetadataPredicate("x", "equals", "é" * 513),)),
            KnowledgeQuery(metadata=(MetadataPredicate("x", "equals", "é" * 512),)),
        ),
        (
            KnowledgeQuery(
                relationships=(RelationshipPredicate("outgoing", "é" * 513),)
            ),
            KnowledgeQuery(
                relationships=(RelationshipPredicate("outgoing", "missing.md"),)
            ),
        ),
    ],
)
def test_utf8_byte_bounds(tmp_path, query, valid_query):
    service, _, _, _ = service_for(tmp_path)
    with pytest.raises(InvalidKnowledgeQueryError):
        service.query(query)
    service.query(valid_query)


@pytest.mark.parametrize(
    "query",
    [
        KnowledgeQuery(paths=("missing.md",) * 65),
        KnowledgeQuery(tags=("tag",) * 17),
        KnowledgeQuery(metadata=(MetadataPredicate("x", "exists"),) * 17),
        KnowledgeQuery(
            relationships=(RelationshipPredicate("outgoing", "missing.md"),) * 17
        ),
    ],
)
def test_cardinality_is_checked_before_deduplication(tmp_path, query):
    service, _, _, _ = service_for(tmp_path)
    with pytest.raises(InvalidKnowledgeQueryError):
        service.query(query)


@pytest.mark.parametrize(
    "value",
    [-(2**63), MAX_INT64, None, True, False, 1.0, sys.float_info.max, "text"],
)
def test_supported_scalar_request_boundaries(tmp_path, value):
    service, _, _, _ = service_for(tmp_path)
    service.query(KnowledgeQuery(metadata=(MetadataPredicate("x", "equals", value),)))


@pytest.mark.parametrize("value", [-(2**63) - 1, MAX_INT64 + 1, float("nan"), float("inf")])
def test_invalid_numeric_request_values(tmp_path, value):
    service, _, _, _ = service_for(tmp_path)
    with pytest.raises(InvalidKnowledgeQueryError):
        service.query(KnowledgeQuery(metadata=(MetadataPredicate("x", "equals", value),)))


def test_scope_is_recursive_segment_aware_exact_and_deterministic(tmp_path):
    write(tmp_path, "Projects/B.md", "needle")
    write(tmp_path, "Projects/deep/a.md", "needle")
    write(tmp_path, "Projects-old/C.md", "needle")
    write(tmp_path, "Root.md", "needle")
    service, _, _, _ = service_for(tmp_path)

    assert paths(service.query(KnowledgeQuery(folder="Projects"))) == [
        "Projects/B.md",
        "Projects/deep/a.md",
    ]
    assert paths(
        service.query(
            KnowledgeQuery(
                folder="Projects",
                paths=("Projects/deep/a.md", "Root.md", "missing.md"),
            )
        )
    ) == ["Projects/deep/a.md"]
    assert paths(service.query(KnowledgeQuery(folder="missing"))) == []
    assert paths(service.query(KnowledgeQuery(folder="."))) == [
        "Projects-old/C.md",
        "Projects/B.md",
        "Projects/deep/a.md",
        "Root.md",
    ]
    assert paths(service.query(KnowledgeQuery(paths=("Root.md", "Root.md")))) == [
        "Root.md"
    ]
    assert paths(service.query(KnowledgeQuery(folder="projects"))) == []


def test_internal_folder_symlink_uses_canonical_identity_and_external_or_broken_are_unsafe(
    tmp_path,
):
    canonical = tmp_path / "Canonical"
    canonical.mkdir()
    write(tmp_path, "Canonical/Note.md", "body")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    try:
        os.symlink(canonical, tmp_path / "Internal", target_is_directory=True)
        os.symlink(outside, tmp_path / "External", target_is_directory=True)
        os.symlink(tmp_path / "Missing", tmp_path / "Broken", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {type(exc).__name__}")
    service, _, _, _ = service_for(tmp_path)

    assert paths(service.query(KnowledgeQuery(folder="Internal"))) == ["Canonical/Note.md"]
    for folder in ("External", "Broken"):
        with pytest.raises(UnsafeKnowledgeQueryScopeError):
            service.query(KnowledgeQuery(folder=folder))


@pytest.mark.parametrize("scope", ["../escape", "C:/absolute", "/absolute"])
def test_unsafe_folder_and_path_scope_fail_privately(tmp_path, scope):
    service, _, _, _ = service_for(tmp_path)
    for query in (KnowledgeQuery(folder=scope), KnowledgeQuery(paths=(scope,))):
        with pytest.raises(UnsafeKnowledgeQueryScopeError) as failure:
            service.query(query)
        assert str(tmp_path) not in repr(failure.value)
        assert scope not in repr(failure.value)


def test_literal_tags_and_metadata_use_one_content_snapshot(tmp_path, monkeypatch):
    write(
        tmp_path,
        "Match.md",
        "---\n"
        "tags: [ work , '#literal', Život]\n"
        "nullish: null\nflag: true\ncount: 1\nratio: 1.0\n"
        "seq: [1, true, inner]\nnested: [[inner]]\na.b: exact\n"
        "---\nExact Život text",
    )
    write(tmp_path, "Wrong.md", "---\ntags: [work]\ncount: 2\n---\nexact život text")
    service, vault, _, _ = service_for(tmp_path)
    original = vault.read_note
    reads = []

    def recorded(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(vault, "read_note", recorded)
    result = service.query(
        KnowledgeQuery(
            literal_text="Exact Život",
            tags=("work", " #literal ", "Život", "work"),
            metadata=(
                MetadataPredicate("nullish", "equals", None),
                MetadataPredicate("flag", "equals", True),
                MetadataPredicate("count", "equals", 1),
                MetadataPredicate("count", "not_equals", 2),
                MetadataPredicate("ratio", "equals", 1.0),
                MetadataPredicate("seq", "sequence_contains", True),
                MetadataPredicate("a.b", "equals", "exact"),
                MetadataPredicate("nested", "exists"),
            ),
        )
    )

    assert paths(result) == ["Match.md"]
    assert reads.count("Match.md") == 1
    assert result.ordering == "canonical_path"
    assert result.semantic_index_basis == "none"
    assert result.matches[0].final_score is None


@pytest.mark.parametrize(
    "predicate",
    [
        MetadataPredicate("count", "equals", True),
        MetadataPredicate("count", "equals", 1.0),
        MetadataPredicate("flag", "equals", 1),
        MetadataPredicate("missing", "not_equals", "x"),
        MetadataPredicate("seq", "sequence_contains", 1.0),
        MetadataPredicate("nested", "sequence_contains", "inner"),
        MetadataPredicate("container", "not_equals", "x"),
    ],
)
def test_metadata_type_mismatch_absence_and_containers_do_not_match(tmp_path, predicate):
    write(
        tmp_path,
        "Note.md",
        "---\ncount: 1\nflag: true\nseq: [1]\nnested: [[inner]]\ncontainer: {x: y}\n---\nbody",
    )
    service, _, _, _ = service_for(tmp_path)
    assert paths(service.query(KnowledgeQuery(metadata=(predicate,)))) == []


def test_invalid_frontmatter_satisfies_no_tag_or_metadata_predicate(tmp_path):
    write(tmp_path, "Broken.md", "---\ntags: [work\n---\nbody")
    service, _, _, _ = service_for(tmp_path)
    assert paths(service.query(KnowledgeQuery(tags=("work",)))) == []
    assert paths(
        service.query(KnowledgeQuery(metadata=(MetadataPredicate("tags", "exists"),)))
    ) == []


def test_outgoing_and_incoming_relationships_reuse_snapshot_without_backlink_scans(
    tmp_path, monkeypatch
):
    write(tmp_path, "Source.md", "[[Target|label]] and [target](Target.md#fragment)")
    write(tmp_path, "Target.md", "target")
    write(tmp_path, "Other.md", "other")
    service, _, relationships, _ = service_for(tmp_path)
    monkeypatch.setattr(
        relationships,
        "normalized_backlinks",
        lambda *_: pytest.fail("incoming must not scan backlinks per candidate"),
    )
    original = relationships.normalized_resolution_snapshot
    snapshots = 0

    def counted(candidate_snapshot):
        nonlocal snapshots
        snapshots += 1
        return original(candidate_snapshot)

    monkeypatch.setattr(relationships, "normalized_resolution_snapshot", counted)

    assert paths(
        service.query(
            KnowledgeQuery(
                relationships=(
                    RelationshipPredicate("outgoing", "Target.md", "obsidian_wikilink"),
                    RelationshipPredicate("outgoing", "Target.md", "markdown_link"),
                )
            )
        )
    ) == ["Source.md"]
    assert paths(
        service.query(
            KnowledgeQuery(
                relationships=(RelationshipPredicate("incoming", "Source.md"),)
            )
        )
    ) == ["Target.md"]
    assert snapshots == 2


def test_missing_relationship_target_matches_nothing_and_unsafe_fails(tmp_path):
    write(tmp_path, "Note.md", "[[Missing]]")
    service, _, _, _ = service_for(tmp_path)
    assert paths(
        service.query(
            KnowledgeQuery(
                relationships=(RelationshipPredicate("outgoing", "Missing.md"),)
            )
        )
    ) == []
    with pytest.raises(UnsafeKnowledgeQueryScopeError):
        service.query(
            KnowledgeQuery(
                relationships=(RelationshipPredicate("outgoing", "../Outside.md"),)
            )
        )


def test_relationship_origin_is_exact_for_incoming_queries(tmp_path):
    write(tmp_path, "Source.md", "[[WikiTarget]] and [md](MarkdownTarget.md)")
    write(tmp_path, "WikiTarget.md", "wiki")
    write(tmp_path, "MarkdownTarget.md", "markdown")
    service, _, _, _ = service_for(tmp_path)

    assert paths(
        service.query(
            KnowledgeQuery(
                relationships=(
                    RelationshipPredicate("incoming", "Source.md", "markdown_link"),
                )
            )
        )
    ) == ["MarkdownTarget.md"]


@pytest.mark.parametrize(
    "basis",
    [
        "compatible_ready",
        "compatible_previous_refresh",
        "compatible_previous_error",
    ],
)
def test_semantic_filters_live_eligibility_before_ranking_and_reports_basis(tmp_path, basis):
    write(tmp_path, "Eligible.md", "---\ntags: [keep]\n---\ncurrent literal")
    write(tmp_path, "Distractor.md", "---\ntags: [drop]\n---\ncurrent literal")
    semantic = FakeSemanticSearch(
        basis=basis,
        results=(semantic_result("Distractor.md", 0.99), semantic_result("Eligible.md", 0.8)),
    )
    service, _, _, _ = service_for(tmp_path, semantic=semantic)

    result = service.query(
        KnowledgeQuery(
            semantic_text="query text",
            literal_text="current literal",
            tags=("keep",),
            limit=1,
        )
    )

    assert paths(result) == ["Eligible.md"]
    assert result.ordering == "semantic"
    assert result.semantic_index_basis == basis
    assert result.matches[0].final_score == 0.8
    assert semantic.calls == [
        ("query text", {"limit": 5, "eligible_paths": frozenset({"Eligible.md"})})
    ]


def test_semantic_candidate_window_is_bounded_at_500(tmp_path):
    write(tmp_path, "Note.md", "body")
    semantic = FakeSemanticSearch(results=(semantic_result("Note.md", 0.8),))
    service, _, _, _ = service_for(tmp_path, semantic=semantic)
    service.query(KnowledgeQuery(semantic_text="query", limit=100))
    assert semantic.calls[0][1]["limit"] == 500


def test_semantic_unavailable_is_explicit_and_nonsemantic_is_independent(tmp_path):
    write(tmp_path, "Note.md", "literal")
    semantic = FakeSemanticSearch(basis=None)
    service, _, _, _ = service_for(tmp_path, semantic=semantic)
    with pytest.raises(KnowledgeQuerySemanticUnavailableError):
        service.query(KnowledgeQuery(semantic_text="query"))
    assert paths(service.query(KnowledgeQuery(literal_text="literal"))) == ["Note.md"]


def test_stale_semantic_path_and_disappearing_candidate_are_omitted(tmp_path, monkeypatch):
    write(tmp_path, "Live.md", "literal")
    semantic = FakeSemanticSearch(
        results=(semantic_result("Deleted.md", 0.9), semantic_result("Live.md", 0.8))
    )
    service, vault, _, _ = service_for(tmp_path, semantic=semantic)
    original = vault.verify_existing_markdown_path_result
    verifications = 0

    def disappears(raw, **kwargs):
        nonlocal verifications
        result = original(raw, **kwargs)
        if raw == "Live.md":
            verifications += 1
            if verifications >= 3:
                return type(result)("missing")
        return result

    monkeypatch.setattr(vault, "verify_existing_markdown_path_result", disappears)
    assert paths(service.query(KnowledgeQuery(semantic_text="query"))) == []


def test_expected_candidate_read_failures_are_omitted_without_private_diagnostics(tmp_path):
    write(tmp_path, "Large.md", "too large")
    service, _, _, _ = service_for(tmp_path, max_note_bytes=2)
    assert paths(service.query(KnowledgeQuery(literal_text="too"))) == []


def test_invalid_utf8_candidate_is_omitted(tmp_path):
    (tmp_path / "Invalid.md").write_bytes(b"literal\xff")
    service, _, _, _ = service_for(tmp_path)
    assert paths(service.query(KnowledgeQuery(literal_text="literal"))) == []


def test_visible_limit_defaults_to_twenty_and_caps_results(tmp_path):
    for index in range(25):
        write(tmp_path, f"Note-{index:02}.md", "literal")
    service, _, _, _ = service_for(tmp_path)
    result = service.query(KnowledgeQuery(literal_text="literal"))
    assert len(result.matches) == 20
    assert paths(result) == sorted(paths(result), key=lambda path: (path.casefold(), path))
