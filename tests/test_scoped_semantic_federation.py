"""D3a internal federation with trusted executable owners and synthetic indexes."""

import json
import os
from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType

import pytest

from app.core.config import Settings
from app.services._scoped_vault import ScopedVaultSession
from app.services.knowledge_query import InvalidKnowledgeQueryError, UnsafeKnowledgeQueryScopeError
from app.services.knowledge_spaces import (
    DEFAULT_SPACE_ID,
    Capability,
    Dialect,
    ReadScope,
    SpaceError,
    SpaceId,
    SpaceOwners,
)
from app.services.scoped_budget import CEILINGS, BudgetExhausted, CancellationToken
from app.services.semantic_search import SemanticSearchService
from app.services.space_owners import compose_space_registry
from app.services.vault import VaultService
from tests.test_scoped_federation import capture_budget, identities
from tests.test_semantic_candidates import chunk, service_fixture
from tests.test_space_operations import RESEARCH, note, operations, setup_spaces

ALL = ReadScope((RESEARCH, DEFAULT_SPACE_ID))


def inject(registry, sid, service):
    old = registry._owners[sid]
    owners = SpaceOwners(**{
        name: service if name == "semantic" else getattr(old, "_" + name if name in {
            "definition", "root_identity",
        } else name)
        for name in ("definition", "root_identity", "vault", "relationships", "query", "duplicates",
                     "capture", "promotion", "hygiene", "semantic", "scheduler")
    })
    object.__setattr__(registry, "_owners", MappingProxyType(dict(registry._owners) | {sid: owners}))


def executable(tmp_path, *, chunks=None, **policies):
    _, registry = setup_spaces(tmp_path, **policies)
    services, backends = {}, {}
    for sid in ALL.space_ids:
        rows = (chunks or {}).get(sid.value, [chunk("Same.md")])
        for row in rows:
            note(tmp_path, sid.value, row.path, "live Markdown")
        directory = tmp_path / f"{sid.value}-data"
        directory.mkdir()
        services[sid], backends[sid] = service_fixture(directory, rows)
        inject(registry, sid, services[sid])
    return registry, services, backends


def track_sessions(monkeypatch):
    active, closed = set(), []
    enter, leave = ScopedVaultSession.__enter__, ScopedVaultSession.__exit__

    def start(self):
        result = enter(self)
        active.add(self.sid)
        return result

    def end(self, *args):
        leave(self, *args)
        active.discard(self.sid)
        closed.append(self.sid)
        assert self._root_handle is None

    monkeypatch.setattr(ScopedVaultSession, "__enter__", start)
    monkeypatch.setattr(ScopedVaultSession, "__exit__", end)
    return active, closed


@pytest.mark.parametrize("policy,scope,error", [
    ({}, ReadScope((DEFAULT_SPACE_ID, SpaceId("unknown"))), "unknown_or_denied_space"),
    ({"read": "deny", "write": "deny", "indexing": "disabled"}, ALL, "unknown_or_denied_space"),
    ({"capabilities": []}, ALL, "unsupported_capability"),
    ({"indexing": "disabled"}, ALL, "indexing_disabled"),
])
def test_whole_authorization_before_owner_access(tmp_path, monkeypatch, policy, scope, error):
    _, registry = setup_spaces(tmp_path, **policy)
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **k: pytest.fail("owner access"))
    with pytest.raises(SpaceError, match=error):
        operations(registry).scoped_semantic_search("query", scope)


@pytest.mark.parametrize("text,kwargs", [
    ("", {}), (" \n", {}), ("x" * 4097, {}), ("\ud800", {}), (None, {}),
    ("x", {"limit": True}), ("x", {"limit": 0}), ("x", {"limit": 101}),
    ("x", {"min_score": True}), ("x", {"min_score": "0.2"}),
    ("x", {"min_score": float("nan")}), ("x", {"min_score": float("inf")}),
    ("x", {"min_score": -0.1}), ("x", {"min_score": 1.1}),
])
def test_validation_before_owner_work(tmp_path, monkeypatch, text, kwargs):
    _, registry = setup_spaces(tmp_path)
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **k: pytest.fail("owner access"))
    with pytest.raises(InvalidKnowledgeQueryError):
        operations(registry).scoped_semantic_search(text, ALL, **kwargs)


@pytest.mark.parametrize("folder", ["", "../other", "C:\\private", "/private", "\\\\host\\share", "\ud800"])
def test_folder_validation_before_work(tmp_path, monkeypatch, folder):
    _, registry = setup_spaces(tmp_path)
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **k: pytest.fail("owner access"))
    with pytest.raises(UnsafeKnowledgeQueryScopeError):
        operations(registry).scoped_semantic_search("query", ALL, folder=folder)


@pytest.mark.parametrize("field,value", [
    ("model", "other"), ("embedding_fingerprint", "embedding-v1:" + "1" * 64),
    ("index_signature", "other"), ("dimensions", 3), ("backend_contract", "other"),
    ("min_score", 0.29),
])
def test_descriptor_mismatch_before_every_embedding(tmp_path, monkeypatch, field, value):
    registry, _, backends = executable(tmp_path)
    active, closed = track_sessions(monkeypatch)
    prepare = SemanticSearchService.prepare_federation_candidates

    def changed(self, *a, **kw):
        result = prepare(self, *a, **kw)
        if kw["space_id"] == RESEARCH:
            return replace(result, descriptor=replace(result.descriptor, **{field: value}))
        return result

    monkeypatch.setattr(SemanticSearchService, "prepare_federation_candidates", changed)
    with pytest.raises(SpaceError, match="^incompatible_semantic_configuration$"):
        operations(registry).scoped_semantic_search("query", ALL)
    assert all(not backend.calls for backend in backends.values())
    assert not active and len(closed) == 2


def test_precise_merge_qualified_identity_and_all_sessions_retained(tmp_path, monkeypatch):
    registry, _, _ = executable(tmp_path, chunks={
        "default": [chunk("Same.md", 0.80001), chunk("a.md", 0.8), chunk("B.md", 0.8)],
        "research": [chunk("Same.md", 0.80002)],
    })
    active, closed = track_sessions(monkeypatch)
    original = SemanticSearchService.search_federation_candidates

    def scoring(self, *a, **kw):
        assert active == set(ALL.space_ids)
        assert len(a) == 1 and "eligible_paths" not in kw
        assert kw["limit"] == 3
        return original(self, *a, **kw)

    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", scoring)
    result = operations(registry).scoped_semantic_search("query", ALL, limit=3)
    assert identities(result) == [("research", "Same.md"), ("default", "Same.md"), ("default", "a.md")]
    assert result.items[0].score > result.items[1].score
    assert round(result.items[0].score, 4) == round(result.items[1].score, 4)
    assert result.ordering == "semantic" and result.result_limited and result.state == "complete"
    assert [row.returned_candidates for row in result.coverage] == [3, 1]
    with pytest.raises(FrozenInstanceError):
        result.items[0].score = 1.0
    assert not active and len(closed) == 2


def test_local_floor_and_window_owned_by_d2(tmp_path):
    registry, _, _ = executable(tmp_path, chunks={
        "default": [chunk("winner.md", 0.99), chunk("below.md", 0.70)],
        "research": [chunk(f"{i:02}.md", 0.6) for i in range(15)],
    })
    result = operations(registry).scoped_semantic_search("query", ALL, limit=2)
    assert identities(result) == [("default", "winner.md"), ("research", "00.md")]
    assert [row.returned_candidates for row in result.coverage] == [1, 10]
    assert result.result_limited  # no global floor removes the weaker local winner


def test_isolation_folder_and_missing_live_paths(tmp_path):
    registry, services, _ = executable(tmp_path, chunks={
        "default": [chunk("Inside/A.md"), chunk("B.md")],
        "research": [chunk("Inside/A.md"), chunk("Ghost.md")],
    })
    (tmp_path / "research" / "Ghost.md").unlink()
    assert identities(operations(registry).scoped_semantic_search("query", ALL, folder="Inside")) == [
        ("default", "Inside/A.md"), ("research", "Inside/A.md"),
    ]
    assert identities(operations(registry).scoped_semantic_search("query")) == [
        ("default", "B.md"), ("default", "Inside/A.md"),
    ]
    assert not operations(registry).scoped_semantic_search("query", ALL, folder="missing").items
    assert operations(registry).scoped_semantic_search("query", ALL, folder=".").state == "complete"
    assert services[DEFAULT_SPACE_ID].repository.db_path != services[RESEARCH].repository.db_path


def test_empty_partial_and_all_unavailable(tmp_path):
    registry, services, _ = executable(tmp_path, chunks={"default": [], "research": []})
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert not result.items and result.state == "complete"
    services[RESEARCH].repository.db_path.write_bytes(b"corrupt database")
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert not result.items and result.state == "partial"
    assert result.coverage[1].reasons == ("index_unavailable",)
    services[DEFAULT_SPACE_ID].repository.db_path.unlink()
    with pytest.raises(SpaceError, match="^semantic_unavailable$"):
        operations(registry).scoped_semantic_search("query", ALL)


def test_normal_composition_stays_nonexecutable(tmp_path):
    _, registry = setup_spaces(tmp_path)
    with pytest.raises(SpaceError, match="^semantic_unavailable$"):
        operations(registry).scoped_semantic_search("query", ALL)
    assert not list(tmp_path.glob("*-data"))


@pytest.mark.parametrize("resource", ["semantic_rows", "semantic_bytes", "sqlite_steps"])
def test_semantic_exhaustion_discards_contribution(tmp_path, monkeypatch, resource):
    registry, _, backends = executable(tmp_path, chunks={
        "default": [], "research": [chunk("Same.md"), chunk("Other.md")],
    })
    budgets = capture_budget(monkeypatch)
    # An empty contribution still requires bounded metadata work. Use row quota
    # for partial availability; the other zero quotas make all indexes unavailable.
    ceiling = 1 if resource == "semantic_rows" else 0
    if ceiling:
        result = operations(registry).scoped_semantic_search("query", ALL, limits={resource: (2, 1)})
        assert result.coverage[0].state == "complete"
        assert result.coverage[1].reasons == ("semantic_limit",)
        assert not backends[RESEARCH].calls
    else:
        with pytest.raises(SpaceError, match="^semantic_unavailable$"):
            operations(registry).scoped_semantic_search("query", ALL, limits={resource: (0, 0)})
        assert all(not backend.calls for backend in backends.values())
    assert budgets[0].total[resource] <= (2 if ceiling else 0)


def test_content_path_discovery_and_unreadable_coverage(tmp_path):
    registry, _, _ = executable(tmp_path, chunks={
        "default": [chunk("A.md"), chunk("B.md")], "research": [],
    })
    result = operations(registry).scoped_semantic_search("query", ALL, limits={"bytes": (30, 30)})
    assert identities(result) == [("default", "A.md")]
    assert result.coverage[0].reasons == ("content_limit",)
    result = operations(registry).scoped_semantic_search("query", ALL, limits={"paths": (2, 1)})
    assert result.coverage[0].reasons == ("path_limit",)
    (tmp_path / "default" / "A.md").write_bytes(b"\xff")
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert identities(result) == [("default", "B.md")]
    assert result.coverage[0].reasons == ("note_unavailable",)
    with pytest.raises(SpaceError, match="^semantic_unavailable$"):
        operations(registry).scoped_semantic_search("query", ALL, limits={"entries": (0, 0)})


@pytest.mark.parametrize("count", [1, 2, 5, 8])
@pytest.mark.parametrize("limited", [False, True])
def test_fixed_full_operation_quotas(tmp_path, monkeypatch, count, limited):
    records = []
    ids = ["default", *(f"s{i}" for i in range(1, count))]
    for name in ids:
        root = tmp_path / name
        root.mkdir()
        records.append(dict(space_id=name, root=str(root), semantic_data_path=str(tmp_path / f"{name}-data"),
                            read="allow", write="allow", indexing="enabled",
                            dialects=[d.value for d in Dialect], capabilities=[c.value for c in Capability]))
    registry = compose_space_registry(Settings(
        vault_path=records[0]["root"], semantic_data_path=records[0]["semantic_data_path"],
        knowledge_spaces_json=json.dumps(dict(version=1, spaces=records)),
    ))
    for name in ids:
        directory = tmp_path / f"{name}-data"
        directory.mkdir()
        rows = [chunk("A.md"), chunk("B.md")] if name == "default" else [chunk("A.md")]
        for row in rows:
            note(tmp_path, name, row.path, "live")
        service, _ = service_fixture(directory, rows)
        inject(registry, SpaceId(name), service)
    budgets = capture_budget(monkeypatch)
    scope = ReadScope(tuple(SpaceId(s) for s in ids))
    limits = {"semantic_rows": (count, 1)} if limited else None
    if limited and count == 1:
        with pytest.raises(SpaceError, match="^semantic_unavailable$"):
            operations(registry).scoped_semantic_search("query", scope, limits=limits)
    else:
        result = operations(registry).scoped_semantic_search("query", scope, limits=limits)
        if limited:
            assert result.coverage[0].reasons == ("semantic_limit",)
            assert all(row.state == "complete" for row in result.coverage[1:])
            assert all(row.returned_candidates == 1 for row in result.coverage[1:])
    budget = budgets[0]
    for resource, (total, local) in CEILINGS.items():
        if limited and resource == "semantic_rows":
            total, local = count, 1
        assert all(allocation[resource] == min(local, total // count) for allocation in budget.allocations.values())
        assert budget.total[resource] <= total
        assert all(used[resource] <= min(local, total // count) for used in budget.used.values())


@pytest.mark.parametrize("phase", ["discovery", "prepare", "score", "final"])
def test_cancellation_fails_whole_request_and_closes_sessions(tmp_path, monkeypatch, phase):
    registry, _, _ = executable(tmp_path)
    active, closed = track_sessions(monkeypatch)
    cancel = CancellationToken()
    owner, method = {
        "discovery": (ScopedVaultSession, "discover"),
        "prepare": (SemanticSearchService, "prepare_federation_candidates"),
        "score": (SemanticSearchService, "search_federation_candidates"),
        "final": (ScopedVaultSession, "verify_root"),
    }[phase]
    original = getattr(owner, method)
    calls = []

    def interrupt(self, *a, **kw):
        result = original(self, *a, **kw)
        calls.append(self)
        if phase != "final" or len(calls) == 3:
            cancel.cancel()
        return result

    monkeypatch.setattr(owner, method, interrupt)
    with pytest.raises(SpaceError, match="^operation_cancelled$"):
        operations(registry).scoped_semantic_search("query", ALL, cancel=cancel)
    assert not active and closed


def test_unexpected_failure_releases_every_session(tmp_path, monkeypatch):
    registry, _, _ = executable(tmp_path)
    active, closed = track_sessions(monkeypatch)

    def broken(*a, **kw):
        raise RuntimeError("programming failure")

    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", broken)
    with pytest.raises(RuntimeError, match="programming failure"):
        operations(registry).scoped_semantic_search("query", ALL)
    assert not active and len(closed) == 2


@pytest.mark.parametrize("phase", ["before", "after_score"])
def test_root_loss_coverage_and_cleanup(tmp_path, monkeypatch, phase):
    registry, _, _ = executable(tmp_path)
    active, closed = track_sessions(monkeypatch)
    if phase == "before":
        (tmp_path / "research" / "Same.md").unlink()
        (tmp_path / "research").rmdir()
    else:
        original = SemanticSearchService.search_federation_candidates

        def lose(self, *a, **kw):
            result = original(self, *a, **kw)
            if kw["space_id"] == RESEARCH:
                # Portable fault injection: Windows pins the directory against
                # removal; POSIX permits unlinking a root with an open descriptor.
                object.__setattr__(registry._owners[RESEARCH].vault, "vault_root", tmp_path / "gone")
                for instance in sessions:
                    if instance.sid == RESEARCH:
                        instance.root = tmp_path / "gone"
            return result

        sessions = []
        enter = ScopedVaultSession.__enter__

        def remember(self):
            result = enter(self)
            sessions.append(self)
            return result

        monkeypatch.setattr(ScopedVaultSession, "__enter__", remember)
        monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", lose)
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert identities(result) == [("default", "Same.md")]
    assert result.coverage[1].state == "unavailable"
    assert "root_unavailable" in result.coverage[1].reasons
    assert not active and len(closed) == (1 if phase == "before" else 2)


def test_final_verification_once_no_refill_and_no_rediscovery(tmp_path, monkeypatch):
    registry, _, _ = executable(tmp_path, chunks={
        "default": [chunk(f"{i:02}.md") for i in range(10)], "research": [],
    })
    scored = False
    final, discovered, searched = [], [], []
    score = SemanticSearchService.search_federation_candidates
    verify = ScopedVaultSession.verify
    discover = ScopedVaultSession.discover

    def score_once(self, *a, **kw):
        nonlocal scored
        result = score(self, *a, **kw)
        searched.append(kw["space_id"])
        scored = True
        return result

    def verify_once(self, fact):
        if scored:
            final.append((self.sid, fact.path))
            raise OSError("private host path must not escape")
        return verify(self, fact)

    def discover_once(self):
        discovered.append(self.sid)
        return discover(self)

    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", score_once)
    monkeypatch.setattr(ScopedVaultSession, "verify", verify_once)
    monkeypatch.setattr(ScopedVaultSession, "discover", discover_once)
    result = operations(registry).scoped_semantic_search("query", ALL, limit=1)
    assert not result.items and not result.result_limited
    assert result.coverage[0].reasons == ("note_unavailable",)
    assert result.coverage[0].returned_candidates == 0
    assert final == [(DEFAULT_SPACE_ID, f"{i:02}.md") for i in range(5)]
    assert discovered == searched == list(ALL.space_ids)
    assert "private host path" not in repr(result)


def test_merge_score_tiebreak_chain(tmp_path, monkeypatch):
    # Windows cannot store two files differing only in case. POSIX additionally
    # exercises the final exact-spelling key after casefold equivalence.
    second = "c.md" if os.name == "nt" else "B.md"
    registry, _, _ = executable(tmp_path, chunks={
        "default": [chunk("a.md"), chunk("Z.md"), chunk("b.md"), chunk(second)],
        "research": [chunk("a.md")],
    })
    original = SemanticSearchService.search_federation_candidates

    def tie(self, *a, **kw):
        result = original(self, *a, **kw)
        # Deliberately shuffled candidates to test all merge keys independently.
        signals = {"a.md": (0.8, 0.3), "Z.md": (0.9, 0.1), "b.md": (0.8, 0.4), second: (0.8, 0.4)}
        return replace(result, candidates=tuple(replace(
            item, score=0.5, semantic_score=signals[item.path][0], lexical_score=signals[item.path][1],
        ) for item in reversed(result.candidates)))

    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", tie)
    result = operations(registry).scoped_semantic_search("query", ALL)
    ties = ["b.md", second] if os.name == "nt" else [second, "b.md"]
    assert identities(result) == [("default", "Z.md"), *(("default", path) for path in ties),
                                  ("default", "a.md"), ("research", "a.md")]


def test_escaping_symlink_never_reaches_index_or_merge(tmp_path):
    registry, services, _ = executable(tmp_path)
    external = tmp_path / "outside.md"
    external.write_text("private", encoding="utf-8")
    try:
        (tmp_path / "default" / "Escape.md").symlink_to(external)
    except OSError:
        pytest.skip("symlink creation not permitted")
    from app.repositories.semantic import StoredNote

    with services[DEFAULT_SPACE_ID].repository.transaction() as session:
        session.replace_note(StoredNote("Escape.md", 0, 1, "hash", "now"), [chunk("Escape.md", 0.99)])
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert all(item.identity.canonical_relative_path != "Escape.md" for item in result.items)
    with pytest.raises(UnsafeKnowledgeQueryScopeError):
        operations(registry).scoped_semantic_search("query", ALL, folder="Escape.md")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction containment")
def test_windows_junction_escape(tmp_path):
    import subprocess

    registry, _, _ = executable(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Secret.md").write_text("private", encoding="utf-8")
    junction = tmp_path / "default" / "Escape"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert all("Secret" not in item.identity.canonical_relative_path for item in result.items)
    with pytest.raises(UnsafeKnowledgeQueryScopeError):
        operations(registry).scoped_semantic_search("query", ALL, folder="Escape")


def test_final_budget_failure_discards_already_verified_space(tmp_path, monkeypatch):
    registry, _, _ = executable(tmp_path, chunks={
        "default": [chunk("A.md"), chunk("B.md")], "research": [chunk("Same.md")],
    })
    original = ScopedVaultSession.verify
    score = SemanticSearchService.search_federation_candidates
    scoring_done = False

    def scored(self, *a, **kw):
        nonlocal scoring_done
        result = score(self, *a, **kw)
        if kw["space_id"] == RESEARCH:
            scoring_done = True
        return result

    def exhausted(self, fact):
        if scoring_done and self.sid == DEFAULT_SPACE_ID and fact.path == "B.md":
            raise BudgetExhausted("probes")
        return original(self, fact)

    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", scored)
    monkeypatch.setattr(ScopedVaultSession, "verify", exhausted)
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert identities(result) == [("research", "Same.md")]
    assert result.coverage[0].reasons == ("discovery_limit",)
    assert result.coverage[0].state == "unavailable"
    assert result.coverage[0].returned_candidates == 0


def test_embedding_failure_is_safe_partial_availability(tmp_path, monkeypatch):
    registry, _, backends = executable(tmp_path)
    active, closed = track_sessions(monkeypatch)

    def unavailable(*a):
        raise OSError("private path / secret backend metadata")

    monkeypatch.setattr(backends[RESEARCH], "embed", unavailable)
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert identities(result) == [("default", "Same.md")]
    assert result.coverage[1].reasons == ("index_unavailable",)
    assert "secret" not in repr(result)
    assert not active and len(closed) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX open-root replacement")
def test_posix_root_replacement_during_scoring(tmp_path, monkeypatch):
    registry, _, _ = executable(tmp_path)
    original = SemanticSearchService.search_federation_candidates

    def replace_root(self, *a, **kw):
        result = original(self, *a, **kw)
        if kw["space_id"] == RESEARCH:
            root = tmp_path / "research"
            root.rename(tmp_path / "previous-root")
            root.mkdir()
            (root / "Same.md").write_text("replacement", encoding="utf-8")
        return result

    monkeypatch.setattr(SemanticSearchService, "search_federation_candidates", replace_root)
    result = operations(registry).scoped_semantic_search("query", ALL)
    assert identities(result) == [("default", "Same.md")]
    assert result.coverage[1].state == "unavailable"
    assert "root_unavailable" in result.coverage[1].reasons


@pytest.mark.parametrize("cause", [
    "missing_index", "corrupt_index", "semantic_rows", "semantic_bytes", "sqlite_steps", "root_loss",
])
def test_all_unavailable_semantic_category(tmp_path, monkeypatch, cause):
    registry, services, _ = executable(tmp_path)
    active, closed = track_sessions(monkeypatch)
    limits = None
    if cause in {"semantic_rows", "semantic_bytes", "sqlite_steps"}:
        limits = {cause: (0, 0)}
    for sid in ALL.space_ids:
        if cause == "missing_index":
            services[sid].repository.db_path.unlink()
        elif cause == "corrupt_index":
            services[sid].repository.db_path.write_bytes(b"corrupt database")
        elif cause == "root_loss":
            (tmp_path / sid.value / "Same.md").unlink()
            (tmp_path / sid.value).rmdir()
    with pytest.raises(SpaceError, match="^semantic_unavailable$") as caught:
        operations(registry).scoped_semantic_search("query", ALL, limits=limits)
    assert caught.value.category == "semantic_unavailable"
    assert not active
    assert len(closed) == (0 if cause == "root_loss" else 2)


@pytest.mark.parametrize("mode", ["list", "literal"])
def test_all_roots_unavailable_preserves_nonsemantic_category(tmp_path, mode):
    _, registry = setup_spaces(tmp_path)
    for sid in ALL.space_ids:
        (tmp_path / sid.value).rmdir()
    ops = operations(registry)
    with pytest.raises(SpaceError, match="^unavailable_space$"):
        if mode == "list":
            ops.scoped_list(ALL)
        else:
            ops.scoped_literal_search("query", ALL)
