"""D3b live eligibility composes with the existing semantic federation pipeline."""

import os
import subprocess
from dataclasses import replace

import pytest

from app.services._scoped_vault import ScopedVaultSession
from app.services.knowledge_query import KnowledgeQuery, MetadataPredicate, UnsafeKnowledgeQueryScopeError
from app.services.knowledge_spaces import DEFAULT_SPACE_ID, Capability, Dialect, QualifiedPath, SpaceError
from app.services.relationships import RelationshipService
from app.services.scoped_budget import CancellationToken
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService
from tests.test_scoped_federation import capture_budget, identities, predicate, scoped
from tests.test_scoped_semantic_federation import ALL, executable, track_sessions
from tests.test_semantic_candidates import chunk
from tests.test_space_operations import RESEARCH, note, operations, setup_spaces


def semantic(query=None, **kwargs):
    return scoped(query or KnowledgeQuery(semantic_text="query"), **kwargs)


def test_all_live_filters_restrict_paths_before_preparation(tmp_path, monkeypatch):
    paths = ["Projects/A.md", "Projects/B.md", "Projects/C.md", "Projects/D.md",
             "Projects/E.md", "Projects-old/F.md"]
    registry, _, backends = executable(tmp_path, chunks={sid: [chunk(p) for p in paths]
                                                        for sid in ("default", "research")})
    good = "---\ntags: [ exact ]\nkind: 2\n---\nNeedle"
    for sid in ("default", "research"):
        for path in paths:
            note(tmp_path, sid, path, good)
        note(tmp_path, sid, paths[1], good.replace("Needle", "needle"))
        note(tmp_path, sid, paths[2], good.replace("exact", "Exact"))
        note(tmp_path, sid, paths[3], good.replace("kind: 2", "kind: true"))
    prepared = []
    prepare = SemanticSearchService.prepare_federation_candidates

    def capture(self, eligible_paths, **kwargs):
        prepared.append((kwargs["space_id"], eligible_paths))
        return prepare(self, eligible_paths, **kwargs)

    monkeypatch.setattr(SemanticSearchService, "prepare_federation_candidates", capture)
    request = semantic(KnowledgeQuery(semantic_text="query", literal_text="Needle", tags=(" exact ",),
                                     metadata=(MetadataPredicate("kind", "equals", 2),), folder="Projects"),
                       paths=tuple(QualifiedPath(sid, path) for sid in ALL.space_ids
                                   for path in paths if path != "Projects/E.md"))
    result = operations(registry).scoped_query(request)
    assert identities(result) == [("default", "Projects/A.md"), ("research", "Projects/A.md")]
    assert prepared == [(DEFAULT_SPACE_ID, ("Projects/A.md",)), (RESEARCH, ("Projects/A.md",))]
    assert all(backend.calls == [["query"]] for backend in backends.values())
    assert result.ordering == "semantic" and result.state == "complete"


def test_qualified_path_or_projection_and_query_whitespace(tmp_path):
    registry, _, backends = executable(tmp_path, chunks={
        "default": [chunk("A.md"), chunk("B.md")], "research": [chunk("A.md"), chunk("B.md")],
    })
    request = semantic(KnowledgeQuery(semantic_text=" \n"), paths=(
        QualifiedPath(DEFAULT_SPACE_ID, "A.md"), QualifiedPath(RESEARCH, "B.md"),
        QualifiedPath(RESEARCH, "B.md"),
    ))
    assert identities(operations(registry).scoped_query(request)) == [("default", "A.md"), ("research", "B.md")]
    assert all(b.calls == [[" \n"]] for b in backends.values())


@pytest.mark.parametrize("folder", [None, ".", "missing"])
def test_empty_eligible_universe_is_executable(tmp_path, folder):
    registry, _, _ = executable(tmp_path, chunks={"default": [], "research": []})
    result = operations(registry).scoped_query(semantic(KnowledgeQuery(semantic_text="query", folder=folder)))
    assert not result.items and result.state == "complete"
    assert all(row.semantic_index_basis == "compatible_ready" for row in result.coverage)


@pytest.mark.parametrize("origin", [None, Dialect.OBSIDIAN_WIKILINK, Dialect.MARKDOWN_LINK])
def test_incoming_outgoing_source_read_and_derivation_reused(tmp_path, monkeypatch, origin):
    registry, _, _ = executable(tmp_path, chunks={"default": [chunk("Source.md"), chunk("Target.md")],
                                                "research": [chunk("Source.md"), chunk("Target.md")]})
    for sid in ("default", "research"):
        note(tmp_path, sid, "Source.md", "[[Target]] [target](Target.md)")
        note(tmp_path, sid, "Target.md", "[[Source]] [source](Source.md)")
    reads, derivations = [], []
    read, derive = ScopedVaultSession.read, RelationshipService.derive_normalized_bounded

    def read_once(self, fact, **kwargs):
        reads.append((self.sid, fact.path))
        return read(self, fact, **kwargs)

    def derive_once(self, content, **kwargs):
        derivations.append(kwargs["source_path"])
        return derive(self, content, **kwargs)

    monkeypatch.setattr(ScopedVaultSession, "read", read_once)
    monkeypatch.setattr(RelationshipService, "derive_normalized_bounded", derive_once)
    budgets = capture_budget(monkeypatch)
    p = predicate("incoming", path="Source.md", origin=origin)
    request = semantic(relationships=(p, p, predicate(path="Source.md", origin=origin)))
    result = operations(registry).scoped_query(request)
    assert identities(result) == [("default", "Target.md")]
    assert reads == [(DEFAULT_SPACE_ID, "Source.md"), (DEFAULT_SPACE_ID, "Target.md")]
    assert derivations == ["Source.md", "Target.md"]
    assert budgets[0].reads == 2
    assert result.coverage[1].state == "complete"  # endpoints in another space cannot form edges
    assert not operations(registry).scoped_query(semantic(relationships=(
        p, predicate(sid=RESEARCH, path="Source.md"),
    ))).items


def test_incoming_source_outside_qualified_paths_reuses_shared_budget(tmp_path, monkeypatch):
    registry, _, _ = executable(tmp_path, chunks={"default": [chunk("Target.md")], "research": []})
    note(tmp_path, "default", "Source.md", "[[Target]]")
    budgets = capture_budget(monkeypatch)
    result = operations(registry).scoped_query(semantic(
        paths=(QualifiedPath(DEFAULT_SPACE_ID, "Target.md"),),
        relationships=(predicate("incoming", path="Source.md"),),
    ))
    assert identities(result) == [("default", "Target.md")]
    assert budgets[0].incoming_reads == {(DEFAULT_SPACE_ID, "Source.md")}
    assert budgets[0].reads == 2 and budgets[0].total["semantic_rows"] == 1


@pytest.mark.parametrize("policy,error,relationships", [
    ({"read": "deny", "write": "deny", "indexing": "disabled"}, "unknown_or_denied_space", ()),
    ({"indexing": "disabled"}, "indexing_disabled", ()),
    ({"capabilities": [c.value for c in Capability if c != Capability.KNOWLEDGE_QUERY]},
     "unsupported_capability", ()),
    ({"capabilities": [c.value for c in Capability if c != Capability.SEMANTIC_RETRIEVAL]},
     "unsupported_capability", ()),
    ({"capabilities": [c.value for c in Capability if c != Capability.RELATIONSHIPS]},
     "unsupported_capability", (predicate(),)),
    ({"dialects": [Dialect.OBSIDIAN_WIKILINK.value]}, "unsupported_dialect", (predicate(),)),
])
def test_whole_selection_authorization_before_owner_work(tmp_path, monkeypatch, policy, error, relationships):
    _, registry = setup_spaces(tmp_path, **policy)
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **k: pytest.fail("owner access"))
    with pytest.raises(SpaceError, match=error):
        operations(registry).scoped_query(semantic(relationships=relationships))


@pytest.mark.parametrize("empty", [False, True])
def test_empty_contribution_descriptors_compared_before_embedding(tmp_path, monkeypatch, empty):
    registry, _, backends = executable(tmp_path)
    prepare = SemanticSearchService.prepare_federation_candidates

    def different(self, *args, **kwargs):
        result = prepare(self, *args, **kwargs)
        return (replace(result, descriptor=replace(result.descriptor, model="other"))
                if kwargs["space_id"] == RESEARCH else result)

    monkeypatch.setattr(SemanticSearchService, "prepare_federation_candidates", different)
    request = semantic(paths=(QualifiedPath(DEFAULT_SPACE_ID, "Same.md"),)) if empty else semantic()
    with pytest.raises(SpaceError, match="^incompatible_semantic_configuration$"):
        operations(registry).scoped_query(request)
    assert all(not b.calls for b in backends.values())


def test_precise_ranking_local_floor_and_qualified_ties(tmp_path):
    registry, _, _ = executable(tmp_path, chunks={
        "default": [chunk("Same.md", 0.800001), chunk("a.md", 0.800001), chunk("low.md", 0.6)],
        "research": [chunk("Same.md", 0.800002), chunk("weak.md", 0.65)],
    })
    result = operations(registry).scoped_query(semantic(KnowledgeQuery(semantic_text="query", limit=3)))
    assert identities(result) == [("research", "Same.md"), ("default", "a.md"), ("default", "Same.md")]
    assert result.items[0].score > result.items[1].score
    assert round(result.items[0].score, 4) == round(result.items[1].score, 4)
    assert result.result_limited  # research weak survives its local floor; default low does not
    assert [row.returned_candidates for row in result.coverage] == [2, 2]


@pytest.mark.parametrize("limit", [20, 100])
def test_global_query_limit_and_d2_window(tmp_path, monkeypatch, limit):
    registry, _, _ = executable(tmp_path, chunks={sid: [chunk(f"{i:03}.md") for i in range(105)]
                                                for sid in ("default", "research")})
    score = SemanticSearchService.search_federation_candidates
    seen = []

    def capture(self, *args, **kwargs):
        seen.append(kwargs["limit"])
        return score(self, *args, **kwargs)

    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", capture)
    query = KnowledgeQuery(semantic_text="query") if limit == 20 else KnowledgeQuery(semantic_text="query", limit=limit)
    result = operations(registry).scoped_query(semantic(query))
    assert len(result.items) == limit and result.result_limited
    assert seen == [limit, limit]
    assert [row.returned_candidates for row in result.coverage] == [min(105, limit * 5)] * 2


@pytest.mark.parametrize("resource", ["relationships", "comparisons", "paths"])
def test_incomplete_relationship_evidence_discards_space(tmp_path, resource):
    registry, _, backends = executable(tmp_path, chunks={"default": [chunk("A.md"), chunk("Target.md")],
                                                       "research": []})
    note(tmp_path, "default", "A.md", "[[Target]] [[Target]]")
    result = operations(registry).scoped_query(semantic(relationships=(predicate(),)),
                                             limits={resource: (2, 1)})
    assert not result.items and result.state == "partial"
    assert result.coverage[0].state == "unavailable"
    assert "relationship_limit" in result.coverage[0].reasons
    assert not backends[DEFAULT_SPACE_ID].calls


@pytest.mark.parametrize("resource", ["bytes", "entries", "semantic_rows"])
def test_budget_exhaustion_and_fixed_allocations(tmp_path, monkeypatch, resource):
    registry, _, _ = executable(tmp_path, chunks={"default": [chunk("A.md"), chunk("B.md")], "research": []})
    budgets = capture_budget(monkeypatch)
    limits = {resource: (30, 30) if resource == "bytes" else (2, 1)}
    result = operations(registry).scoped_query(semantic(), limits=limits)
    assert result.state == "partial"
    assert result.coverage[0].reasons == ({"bytes": ("content_limit",), "entries": ("discovery_limit",),
                                         "semantic_rows": ("semantic_limit",)}[resource])
    budget = budgets[0]
    assert budget.allocations[DEFAULT_SPACE_ID][resource] == limits[resource][0] // 2
    assert budget.used[DEFAULT_SPACE_ID][resource] <= budget.allocations[DEFAULT_SPACE_ID][resource]


@pytest.mark.parametrize("phase", ["read", "prepare", "score", "final"])
def test_cancellation_fails_whole_request_and_closes_sessions(tmp_path, monkeypatch, phase):
    registry, _, _ = executable(tmp_path)
    active, closed = track_sessions(monkeypatch)
    cancel = CancellationToken()
    owner, method = {"read": (ScopedVaultSession, "read"),
                     "prepare": (SemanticSearchService, "prepare_federation_candidates"),
                     "score": (SemanticSearchService, "search_federation_candidates"),
                     "final": (ScopedVaultSession, "verify_root")}[phase]
    original = getattr(owner, method)
    calls = []

    def interrupt(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        calls.append(self)
        if phase != "final" or len(calls) == 3:
            cancel.cancel()
        return result

    monkeypatch.setattr(owner, method, interrupt)
    with pytest.raises(SpaceError, match="^operation_cancelled$"):
        operations(registry).scoped_query(semantic(), cancel=cancel)
    assert not active and closed


@pytest.mark.parametrize("state,basis", [("indexing", "compatible_previous_refresh"),
                                       ("error", "compatible_previous_error")])
def test_searchable_empty_partial_all_unavailable_and_basis(tmp_path, state, basis):
    registry, services, _ = executable(tmp_path, chunks={"default": [chunk("Same.md")], "research": []})
    assert services[DEFAULT_SPACE_ID].is_search_available()
    services[DEFAULT_SPACE_ID].repository.set_metadata("index_state", state)
    request = semantic(KnowledgeQuery(semantic_text="query", literal_text="absent"))
    result = operations(registry).scoped_query(request)
    assert not result.items and result.state == "complete"
    assert [row.semantic_index_basis for row in result.coverage] == [basis, "compatible_ready"]
    services[RESEARCH].repository.db_path.write_bytes(b"corrupt database")
    result = operations(registry).scoped_query(request)
    assert not result.items and result.state == "partial"
    assert result.coverage[1].reasons == ("index_unavailable",)
    services[DEFAULT_SPACE_ID].repository.db_path.unlink()
    with pytest.raises(SpaceError, match="^semantic_unavailable$"):
        operations(registry).scoped_query(request)


def test_final_verification_root_loss_no_retry_or_reread(tmp_path, monkeypatch):
    registry, _, _ = executable(tmp_path)
    active, closed = track_sessions(monkeypatch)
    score = SemanticSearchService.search_federation_candidates
    read, discover, verify = ScopedVaultSession.read, ScopedVaultSession.discover, ScopedVaultSession.verify
    reads, discoveries, final = [], [], []
    sessions = []
    scored = False

    def read_once(self, fact, **kwargs):
        reads.append((self.sid, fact.path))
        return read(self, fact, **kwargs)

    def discover_once(self):
        sessions.append(self)
        discoveries.append(self.sid)
        return discover(self)

    def score_once(self, *args, **kwargs):
        nonlocal scored
        result = score(self, *args, **kwargs)
        scored = True
        if kwargs["space_id"] == RESEARCH:
            for session in sessions:
                if session.sid == RESEARCH:
                    session.root = tmp_path / "gone"
        return result

    def verify_once(self, fact):
        if scored:
            final.append((self.sid, fact.path))
            if self.sid == DEFAULT_SPACE_ID:
                raise OSError("private detail")
        return verify(self, fact)

    monkeypatch.setattr(ScopedVaultSession, "read", read_once)
    monkeypatch.setattr(ScopedVaultSession, "discover", discover_once)
    monkeypatch.setattr(ScopedVaultSession, "verify", verify_once)
    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", score_once)
    result = operations(registry).scoped_query(semantic())
    assert not result.items and result.state == "partial"
    assert reads == final == [(DEFAULT_SPACE_ID, "Same.md"), (RESEARCH, "Same.md")]
    assert discoveries == list(ALL.space_ids)
    assert result.coverage[0].reasons == ("note_unavailable",)
    assert "root_unavailable" in result.coverage[1].reasons
    assert not active and len(closed) == 2


def test_explicit_origin_uses_only_applicable_dialect(tmp_path):
    registry, _, _ = executable(tmp_path, dialects=[Dialect.OBSIDIAN_WIKILINK.value])
    note(tmp_path, "research", "Same.md", "[[Target]]")
    note(tmp_path, "research", "Target.md", "target")
    result = operations(registry).scoped_query(semantic(relationships=(
        predicate(sid=RESEARCH, origin=Dialect.OBSIDIAN_WIKILINK),
    )))
    assert identities(result) == [("research", "Same.md")]


@pytest.mark.parametrize("endpoint", ["path", "relationship", "folder"])
def test_containment_rejects_external_directory_alias_before_scoring(tmp_path, monkeypatch, endpoint):
    registry, _, backends = executable(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Secret.md").write_text("private", encoding="utf-8")
    alias = tmp_path / "default" / "Escape"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(outside)], check=True, capture_output=True)
    else:
        alias.symlink_to(outside, target_is_directory=True)
    request = {
        "path": semantic(paths=(QualifiedPath(DEFAULT_SPACE_ID, "Escape/Secret.md"),)),
        "relationship": semantic(relationships=(predicate(path="Escape/Secret.md"),)),
        "folder": semantic(KnowledgeQuery(semantic_text="query", folder="Escape")),
    }[endpoint]
    active, closed = track_sessions(monkeypatch)
    with pytest.raises(UnsafeKnowledgeQueryScopeError):
        operations(registry).scoped_query(request)
    assert all(not b.calls for b in backends.values())
    assert not active and closed


@pytest.mark.skipif(os.name == "nt", reason="POSIX open-root replacement")
def test_posix_root_replacement_after_query_scoring(tmp_path, monkeypatch):
    registry, _, _ = executable(tmp_path)
    score = SemanticSearchService.search_federation_candidates

    def replace_root(self, *args, **kwargs):
        result = score(self, *args, **kwargs)
        if kwargs["space_id"] == RESEARCH:
            root = tmp_path / "research"
            root.rename(tmp_path / "previous-root")
            root.mkdir()
            (root / "Same.md").write_text("replacement", encoding="utf-8")
        return result

    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", replace_root)
    result = operations(registry).scoped_query(semantic())
    assert identities(result) == [("default", "Same.md")]
    assert result.coverage[1].state == "unavailable"
    assert "root_unavailable" in result.coverage[1].reasons
