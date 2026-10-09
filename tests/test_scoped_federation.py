"""Slice C end-to-end admission, actual work, isolation and legacy parity."""

import dataclasses
import os
import subprocess
from types import SimpleNamespace

import pytest

from app.services._scoped_vault import ScopedVaultSession
from app.services.knowledge_query import InvalidKnowledgeQueryError, KnowledgeQuery
from app.services.knowledge_spaces import (
    DEFAULT_SPACE_ID,
    Dialect,
    QualifiedPath,
    ReadScope,
    SpaceError,
    SpaceId,
)
from app.services.relationships import RelationshipService, ScopedRelationshipSnapshot
from app.services.scoped_budget import BudgetExhausted, CancellationToken, RelationshipBudgetView, ScopedBudget
from app.services.scoped_reads import QualifiedRelationshipPredicate, ScopedKnowledgeQuery
from app.services.vault import VaultService
from tests.test_space_operations import RESEARCH, note, operations, setup_spaces

ALL = ReadScope((RESEARCH, DEFAULT_SPACE_ID))


def identities(result):
    return [(item.identity.space_id.value, item.identity.canonical_relative_path) for item in result.items]


def scoped(query=None, *, paths=(), relationships=(), scope=ALL):
    return ScopedKnowledgeQuery(scope, query or KnowledgeQuery(), paths, relationships)


def predicate(direction="outgoing", *, sid=DEFAULT_SPACE_ID, path="Target.md", origin=None):
    return QualifiedRelationshipPredicate(direction, QualifiedPath(sid, path), origin)


def capture_budget(monkeypatch):
    import app.services._scoped_federation as federation

    budgets = []

    def create(*args, **kwargs):
        budget = ScopedBudget(*args, **kwargs)
        budgets.append(budget)
        return budget

    monkeypatch.setattr(federation, "ScopedBudget", create)
    return budgets


@pytest.mark.parametrize("kind", ["list", "literal", "query"])
@pytest.mark.parametrize("denied", [False, True])
def test_authorize_entire_selection_before_any_work(tmp_path, monkeypatch, kind, denied):
    _, registry = setup_spaces(tmp_path, **({"read": "deny", "write": "deny", "indexing": "disabled"}
                                         if denied else {}))
    ops = operations(registry)
    selection = ALL if denied else ReadScope((DEFAULT_SPACE_ID, SpaceId("unknown")))
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **kw: pytest.fail("owner work"))
    with pytest.raises(SpaceError, match="unknown_or_denied_space"):
        if kind == "list":
            ops.scoped_list(selection)
        elif kind == "literal":
            ops.scoped_literal_search("hello", selection)
        else:
            ops.scoped_query(scoped(KnowledgeQuery(literal_text="hello"), scope=selection))


@pytest.mark.parametrize("policy,expected", [
    ({"capabilities": []}, "unsupported_capability"),
    ({"dialects": ["obsidian_wikilink"]}, "unsupported_dialect"),
])
def test_all_requirements_authorized_before_work(tmp_path, monkeypatch, policy, expected):
    _, registry = setup_spaces(tmp_path, **policy)
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **kw: pytest.fail("owner work"))
    with pytest.raises(SpaceError, match=expected):
        operations(registry).scoped_query(scoped(relationships=(predicate(),)))


def test_qualified_identity_order_and_markdown_only(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    for sid in ("default", "research"):
        note(tmp_path, sid, "Same.md", "Hello Body")
        note(tmp_path, sid, "Empty.md", "other")
    ops = operations(registry)
    monkeypatch.setattr(type(registry._owners[DEFAULT_SPACE_ID].semantic), "inspect_hygiene_index",
                        lambda *a: pytest.fail("index"))
    # Local owner paths are not an implementation shortcut.
    monkeypatch.setattr(VaultService, "list_notes", lambda *a, **kw: pytest.fail("legacy list"))
    monkeypatch.setattr(VaultService, "search_notes", lambda *a, **kw: pytest.fail("legacy search"))
    monkeypatch.setattr(VaultService, "markdown_path_candidate_snapshot", lambda *a: pytest.fail("unbounded scan"))
    expected = [("default", "Same.md"), ("research", "Same.md")]
    assert identities(ops.scoped_literal_search("hello", ALL)) == expected
    assert identities(ops.scoped_query(scoped(KnowledgeQuery(literal_text="Hello")))) == expected
    assert len(ops.scoped_list(ALL).items) == 4
    assert [row.space_id.value for row in ops.scoped_list(ALL).coverage] == ["default", "research"]
    assert ops.scoped_list(ALL) == ops.scoped_list(ReadScope(tuple(reversed(ALL.space_ids))))
    assert not list(tmp_path.glob("*-data"))


def test_list_full_precision_modification_and_limit(tmp_path):
    _, registry = setup_spaces(tmp_path)
    for path, timestamp in (("A.md", 1_700_000_000_000_000_001), ("Z.md", 1_700_000_000_100_000_000)):
        note(tmp_path, "default", path, "x")
        os.utime(tmp_path / "default" / path, ns=(timestamp, timestamp))
    result = operations(registry).scoped_list(limit=1)
    assert identities(result) == [("default", "Z.md")]
    assert result.result_limited and result.state == "complete"


def test_paths_global_or_and_no_fanout(tmp_path):
    _, registry = setup_spaces(tmp_path)
    for sid in ("default", "research"):
        note(tmp_path, sid, "Same.md", "hello")
    result = operations(registry).scoped_query(scoped(paths=(QualifiedPath(RESEARCH, "Same.md"),)))
    assert identities(result) == [("research", "Same.md")]
    assert result.coverage[0].state == "complete"
    assert result.coverage[0].evaluated_paths == 0


@pytest.mark.parametrize("scoped_request", [
    scoped(KnowledgeQuery()),
    scoped(KnowledgeQuery(paths=("Same.md",))),
    scoped(paths=(QualifiedPath(DEFAULT_SPACE_ID, "Same.md"),) * 65),
    scoped(relationships=(predicate(),) * 17),
])
def test_wrapper_validation_before_work(tmp_path, monkeypatch, scoped_request):
    _, registry = setup_spaces(tmp_path)
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **kw: pytest.fail("owner work"))
    with pytest.raises(InvalidKnowledgeQueryError):
        operations(registry).scoped_query(scoped_request)


def test_outside_scope_and_semantic_are_gated_before_work(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **kw: pytest.fail("owner work"))
    with pytest.raises(SpaceError, match="invalid_scope"):
        operations(registry).scoped_query(scoped(paths=(QualifiedPath(RESEARCH, "Same.md"),), scope=None))
    with pytest.raises(SpaceError, match="unsupported_capability"):
        operations(registry).scoped_query(scoped(KnowledgeQuery(semantic_text="hello")))


def test_candidate_retention_canonical_smallest_after_full_discovery(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    for name in ("z.md", "B.md", "a.md", "C.md"):
        note(tmp_path, "default", name, "x")
    budgets = capture_budget(monkeypatch)
    result = operations(registry).scoped_list(limits={"paths": (2, 2)})
    assert {path for _, path in identities(result)} == {"B.md", "a.md"}
    assert result.coverage[0].reasons == ("path_limit",)
    assert result.state == "partial"
    assert budgets[0].total["paths"] == 2
    assert budgets[0].total["entries"] == 5  # includes the charged EOF step


@pytest.mark.parametrize("resource", ["entries", "directories", "probes"])
def test_actual_discovery_work_exhaustion_discards_no_retry(tmp_path, monkeypatch, resource):
    _, registry = setup_spaces(tmp_path)
    for index in range(4):
        note(tmp_path, "default", f"{index}.md", "x")
    budgets = capture_budget(monkeypatch)
    with pytest.raises(SpaceError, match="unavailable_space"):
        operations(registry).scoped_list(limits={resource: (1, 1)})
    assert budgets[0].total[resource] == 1


def test_bytes_fixed_division_canonical_prefix_and_invalid_utf8(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "A.md", "hit")
    note(tmp_path, "default", "B.md", "hit hit")
    note(tmp_path, "research", "A.md", "hit")
    (tmp_path / "research" / "Z.md").write_bytes(b"\xff")
    budgets = capture_budget(monkeypatch)
    result = operations(registry).scoped_literal_search("hit", ALL, limits={"bytes": (12, 12)})
    assert identities(result) == [("default", "A.md"), ("research", "A.md")]
    assert result.coverage[0].reasons == ("content_limit",)
    assert result.coverage[1].reasons == ("note_unavailable",)
    assert budgets[0].total["bytes"] <= 12
    assert all(used["bytes"] <= 6 for used in budgets[0].used.values())


def test_all_unavailable_vs_available_empty(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "research", "A.md", "[[A]] [[A]]")
    ops = operations(registry)
    # A mixed-ID AND cannot apply to any candidate and must not create global edges.
    result = ops.scoped_query(scoped(relationships=(predicate(), predicate(sid=RESEARCH))))
    assert not result.items and result.state == "complete"
    # Only research needs parsing; default is an executable empty contribution.
    result = ops.scoped_query(scoped(relationships=(predicate(sid=RESEARCH, path="A.md"),),
                                     query=KnowledgeQuery(), scope=ALL),
                              limits={"relationships": (2, 2)})
    assert not result.items and result.state == "partial"
    assert result.coverage[0].state == "complete"
    assert result.coverage[1].state == "unavailable"
    with pytest.raises(SpaceError, match="unavailable_space"):
        ops.scoped_query(scoped(relationships=(predicate(sid=RESEARCH, path="A.md"),),
                               scope=ReadScope((RESEARCH,))), limits={"relationships": (1, 1)})


def test_relationship_source_reuse_and_isolation(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "Source.md", "[[Target]] [x](Target.md)")
    note(tmp_path, "research", "Target.md", "hello")
    note(tmp_path, "research", "Source.md", "[[Target]]")
    ops = operations(registry)
    assert not ops.scoped_query(scoped(relationships=(predicate(),))).items
    note(tmp_path, "default", "Target.md", "hello")
    budgets = capture_budget(monkeypatch)
    result = ops.scoped_query(scoped(relationships=(predicate(), predicate(), predicate(origin=Dialect.MARKDOWN_LINK))))
    assert identities(result) == [("default", "Source.md")]
    assert budgets[0].total["relationships"] == 2
    assert budgets[0].reads == 2
    incoming = ops.scoped_query(scoped(relationships=(predicate("incoming", path="Source.md"),)))
    assert identities(incoming) == [("default", "Target.md")]


def derivation(tmp_path, content, *, quota=50_000, parse_credit=None, cancel=None):
    root = tmp_path / "vault"
    root.mkdir(exist_ok=True)
    (root / "Target.md").write_text("target", encoding="utf-8")
    (root / "Source.md").write_text(content, encoding="utf-8")
    vault = VaultService(vault_root=root, max_note_bytes=10_000_000)
    token = cancel or CancellationToken()
    budget = ScopedBudget((DEFAULT_SPACE_ID,), limits={"relationships": (quota, quota)})
    budget.charge(DEFAULT_SPACE_ID, "bytes", len(content.encode("utf-8")))
    session = vault.scoped_session(space_id=DEFAULT_SPACE_ID, budget=budget, cancel=token,
                                    root_identity=vault.root_binding_identity())
    snapshot = session.discover()
    view = budget.relationships(DEFAULT_SPACE_ID, token)
    if parse_credit is not None:
        original = view.source

        def source(*args):
            original(*args)
            view._parse_remaining = parse_credit
        view.source = source
    # Production occurrence tests isolate parsing/admission from filesystem probes.
    # Complete supplied-universe resolution is separately tested end to end.
    resolutions = []

    def resolve(source, origin, target):
        resolutions.append((origin, target))
        return None, "missing"

    result = RelationshipService(vault).derive_normalized_bounded(
        content, source_path="Source.md", snapshot=SimpleNamespace(resolve=resolve), budget=view, cancel=token,
    )
    return result, budget, resolutions, session, snapshot


@pytest.mark.parametrize("syntax", ["[[x]]", "[x](x.md)"])
def test_production_relationship_ceiling_and_no_overflow_occurrence(tmp_path, syntax):
    result, budget, resolved, _, _ = derivation(tmp_path, syntax * 50_001)
    assert result.state == "limited" and result.reason == "relationship_limit"
    assert len(result.occurrences) == len(resolved) == budget.total["relationships"] == 50_000


def test_mixed_dialects_shared_quota_and_exact_exhaustion(tmp_path):
    result, budget, resolved, _, _ = derivation(tmp_path, "[[x]][x](x.md)" * 10, quota=5)
    assert [origin for origin, _ in resolved] == ["obsidian_wikilink", "markdown_link"] * 2 + ["obsidian_wikilink"]
    assert budget.total["relationships"] == 5
    assert result.state == "limited"
    exact, *_ = derivation(tmp_path, "[[x]]", quota=1)
    assert exact.state == "limited"


def test_shared_aggregate_fixed_allocation_and_no_resets():
    ids = tuple(SpaceId(f"space{i}") for i in range(6))
    budget = ScopedBudget(ids)
    assert all(row["relationships"] == 33_333 for row in budget.allocations.values())
    for sid in ids:
        budget.charge(sid, "relationships", 33_333)
        with pytest.raises(BudgetExhausted):
            budget.relationships(sid, CancellationToken()).try_admit()
    assert budget.total["relationships"] == 199_998


def test_parse_credit_exhaustion_and_cancellation(tmp_path):
    result, budget, resolved, _, _ = derivation(tmp_path, "[" * 1000, parse_credit=20)
    assert result.state == "limited" and not resolved
    assert budget.total["relationships"] == 0 and budget.parse_probes <= 20
    token = CancellationToken()
    token.cancel()
    with pytest.raises(SpaceError, match="operation_cancelled"):
        derivation(tmp_path, "[[x]]" * 20, cancel=token)


@pytest.mark.parametrize("content", [
    "[[Target]] [x](Target.md) [[Missing#h|label]] [[Target]]",
    "```\n[[Target]] [x](Target.md)\n```\n[[Target]] ` [x](Target.md) `",
    "![[Target]] ![image](Target.md) [[a|b|c]] [outer [x](Target.md)",
    "[[Target#Heading|Alias]]\r\n[x](<Target.md#fragment>) [x](../Missing.md)",
    "`[[Target]]` [x](https://example.com/a.md) [[../escape]]",
])
def test_bounded_small_input_legacy_parity(tmp_path, content):
    _, budget, _, session, snapshot = derivation(tmp_path, content)
    budget = ScopedBudget((DEFAULT_SPACE_ID,))
    budget.charge(DEFAULT_SPACE_ID, "bytes", len(content.encode("utf-8")))
    session.budget = budget
    owner = RelationshipService(session.vault)
    view = budget.relationships(DEFAULT_SPACE_ID, session.cancel)
    result = owner.derive_normalized_bounded(content, source_path="Source.md",
                                            snapshot=ScopedRelationshipSnapshot(snapshot, session),
                                            budget=view, cancel=session.cancel)
    eager = owner.normalized_relationships_from_content(content, source_path="Source.md",
                                                        snapshot=owner.normalized_resolution_snapshot(
                                                            session.vault.markdown_path_candidate_snapshot()))
    assert result.state == "complete"
    assert result.occurrences == eager


def test_cancel_whole_federation_no_successful_prefix(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    for sid in ("default", "research"):
        note(tmp_path, sid, "A.md", "hit")
    token = CancellationToken()
    original = ScopedVaultSession.read

    def read(session, *args, **kwargs):
        result = original(session, *args, **kwargs)
        token.cancel()
        return result
    monkeypatch.setattr(ScopedVaultSession, "read", read)
    with pytest.raises(SpaceError, match="operation_cancelled"):
        operations(registry).scoped_literal_search("hit", ALL, cancel=token)


def test_root_loss_drops_buffered_space(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    for sid in ("default", "research"):
        note(tmp_path, sid, "A.md", "hit")
    original = ScopedVaultSession.verify_root

    def verify(session):
        if session.sid == DEFAULT_SPACE_ID:
            raise OSError("private details")
        return original(session)
    monkeypatch.setattr(ScopedVaultSession, "verify_root", verify)
    result = operations(registry).scoped_literal_search("hit", ALL)
    assert identities(result) == [("research", "A.md")]
    assert result.coverage[0].reasons == ("root_unavailable",)
    assert "private" not in repr(result) and str(tmp_path) not in repr(result)


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX symlink suite required")
def test_posix_alias_and_cross_space_escape(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "Target.md", "hit")
    note(tmp_path, "default", "Source.md", "[[Alias]] [x](Alias.md)")
    note(tmp_path, "research", "Other.md", "private")
    (tmp_path / "default" / "Alias.md").symlink_to("Target.md")
    (tmp_path / "default" / "Escape.md").symlink_to(tmp_path / "research" / "Other.md")
    result = operations(registry).scoped_query(scoped(relationships=(predicate(path="Target.md"),)))
    assert identities(result) == [("default", "Source.md")]
    assert ("default", "Escape.md") not in identities(operations(registry).scoped_list(ALL))


def test_result_contract_is_frozen(tmp_path):
    _, registry = setup_spaces(tmp_path)
    result = operations(registry).scoped_list()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.state = "partial"


def test_iterator_charged_before_entry_materialization(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "A.md", "hit")
    budgets = capture_budget(monkeypatch)
    original = os.scandir
    steps = []

    class Iterator:
        def __init__(self, path):
            self.inner = original(path)

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def __next__(self):
            # The real OS iterator may now materialize its next DirEntry.
            charged = budgets[0].total["entries"]
            assert charged > len(steps)
            steps.append(charged)
            return next(self.inner)
    monkeypatch.setattr(os, "scandir", Iterator)
    assert identities(operations(registry).scoped_list()) == [("default", "A.md")]
    assert len(steps) == budgets[0].total["entries"] == 2


def test_real_primitive_calls_are_precharged(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "A.md", "hit")
    budgets = capture_budget(monkeypatch)
    original = ScopedVaultSession.call
    calls = []

    def call(session, function, *args, **kwargs):
        before = session.budget.total["probes"]

        def primitive(*a, **kw):
            assert session.budget.total["probes"] == before + 1
            calls.append(function.__name__)
            return function(*a, **kw)
        return original(session, primitive, *args, **kwargs)
    monkeypatch.setattr(ScopedVaultSession, "call", call)
    assert operations(registry).scoped_literal_search("hit").items
    assert {"open", "fstat"} <= set(calls)
    assert len(calls) <= budgets[0].total["probes"] <= 500_000


def test_authored_read_uses_one_bounded_size_plus_one_request(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "A.md", "12345")
    original = os.fdopen
    sizes = []

    class Stream:
        def __init__(self, fd, mode, **kwargs):
            assert kwargs == {"buffering": 0}
            self.inner = original(fd, mode, **kwargs)

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def fileno(self):
            return self.inner.fileno()

        def read(self, size):
            assert size > 0  # no preliminary unlimited read
            sizes.append(size)
            return self.inner.read(size)
    monkeypatch.setattr(os, "fdopen", Stream)
    result = operations(registry).scoped_literal_search("123", limits={"bytes": (10, 10)})
    assert len(result.items) == 1 and sizes == [6]
    sizes.clear()
    result = operations(registry).scoped_literal_search("123", limits={"bytes": (3, 3)})
    assert not result.items and sizes == [3]
    assert result.coverage[0].reasons == ("content_limit",)


def test_oversized_and_raced_notes_omitted(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "A.md", "hit")
    note(tmp_path, "default", "Large.md", "hit" * 400_000)
    original = ScopedVaultSession.read

    def read(session, fact, **kwargs):
        if fact.path == "A.md":
            (session.root / fact.path).write_text("changed", encoding="utf-8")
        return original(session, fact, **kwargs)
    monkeypatch.setattr(ScopedVaultSession, "read", read)
    result = operations(registry).scoped_literal_search("hit")
    assert not result.items and result.coverage[0].reasons == ("note_unavailable",)


@pytest.mark.parametrize("content", ["[" + "a" * 10_000, "[[" + "a" * 10_000, "`" + "a" * 10_000])
def test_cancellation_inside_long_recognition(tmp_path, monkeypatch, content):
    original = RelationshipBudgetView.probe
    token = CancellationToken()
    counts = []

    def probe(view, count=1):
        original(view, count)
        counts.append(view.owner.parse_probes)
        if view.owner.parse_probes >= 100:
            token.cancel()
    monkeypatch.setattr(RelationshipBudgetView, "probe", probe)
    with pytest.raises(SpaceError, match="operation_cancelled"):
        derivation(tmp_path, content, cancel=token)
    assert counts[-1] <= 1024


def test_shared_view_cannot_reset_source_credit(tmp_path):
    content = "[[x]]"
    _, budget, _, session, snapshot = derivation(tmp_path, content)
    owner = RelationshipService(session.vault)
    with pytest.raises(ValueError, match="already derived"):
        owner.derive_normalized_bounded(content, source_path="Source.md",
                                        snapshot=ScopedRelationshipSnapshot(snapshot, session),
                                        budget=budget.relationships(DEFAULT_SPACE_ID, session.cancel),
                                        cancel=session.cancel)


def test_no_recognition_after_global_relationship_exhaustion(tmp_path, monkeypatch):
    from app.services._scoped_links import Cursor

    content = "[[x]]"
    _, budget, _, session, snapshot = derivation(tmp_path, content, quota=1)
    monkeypatch.setattr(Cursor, "at", lambda *a: pytest.fail("recognition after exhaustion"))
    result = RelationshipService(session.vault).derive_normalized_bounded(
        content, source_path="Later.md", snapshot=ScopedRelationshipSnapshot(snapshot, session),
        budget=budget.relationships(DEFAULT_SPACE_ID, session.cancel), cancel=session.cancel,
    )
    assert result.state == "limited" and not result.occurrences


def test_relationship_path_overflow_is_unavailable_not_prefix_truth(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "A.md", "[[Target]]")
    note(tmp_path, "default", "Target.md", "x")
    with pytest.raises(SpaceError, match="unavailable_space"):
        operations(registry).scoped_query(scoped(relationships=(predicate(),), scope=None),
                                          limits={"paths": (1, 1)})


def test_root_folder_and_legacy_literal_vs_query_semantics(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "Hello.md", "CaseSensitive")
    ops = operations(registry)
    assert len(ops.scoped_literal_search("hello", folder=".").items) == 1
    assert not ops.scoped_query(scoped(KnowledgeQuery(literal_text="hello"), scope=None)).items
    exact = scoped(KnowledgeQuery(literal_text="CaseSensitive", folder="."), scope=None)
    assert len(ops.scoped_query(exact).items) == 1
    assert not ops.scoped_query(scoped(KnowledgeQuery(folder="missing"), scope=None)).items


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX symlink suite required")
def test_posix_directory_alias_retarget_is_not_snapshot_evidence(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "One/Target.md", "one")
    note(tmp_path, "default", "Two/Target.md", "two")
    note(tmp_path, "default", "Source.md", "[x](Alias/Target.md)")
    alias = tmp_path / "default" / "Alias"
    alias.symlink_to("One", target_is_directory=True)
    original = ScopedVaultSession.discover

    def discover(session):
        snapshot = original(session)
        if session.sid == DEFAULT_SPACE_ID:
            alias.unlink()
            alias.symlink_to("Two", target_is_directory=True)
        return snapshot
    monkeypatch.setattr(ScopedVaultSession, "discover", discover)
    result = operations(registry).scoped_query(scoped(relationships=(predicate(path="Two/Target.md"),)))
    assert not result.items
    assert "note_unavailable" in result.coverage[0].reasons


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX symlink suite required")
def test_posix_unsafe_common_folder_rejected_whole_request(tmp_path):
    _, registry = setup_spaces(tmp_path)
    (tmp_path / "default" / "Escape").symlink_to(tmp_path / "research", target_is_directory=True)
    from app.services.knowledge_query import UnsafeKnowledgeQueryScopeError

    with pytest.raises(UnsafeKnowledgeQueryScopeError):
        operations(registry).scoped_list(ALL, folder="Escape")


def test_native_discovery_extension_parity(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "Upper.MD", "hit")
    owners = registry._owners[DEFAULT_SPACE_ID]
    expected = {item.path for item in owners.vault.list_notes()}
    assert {item.identity.canonical_relative_path for item in operations(registry).scoped_list().items} == expected


@pytest.mark.parametrize("space_count", [4, 6])
def test_production_aggregate_shared_by_actual_owner_parsing(tmp_path, space_count):
    ids = tuple(SpaceId(f"space{i}") for i in range(space_count))
    budget = ScopedBudget(ids)
    quota = min(50_000, 200_000 // space_count)
    content = "[[missing]]" * (quota + 1)
    vault = VaultService(vault_root=tmp_path, max_note_bytes=10_000_000)
    owner = RelationshipService(vault)
    token = CancellationToken()
    resolved = 0

    def resolve(*args):
        nonlocal resolved
        # Every resolution observes one already charged admission.
        assert budget.total["relationships"] == resolved + 1
        resolved += 1
        return None, "missing"
    for sid in ids:
        budget.charge(sid, "bytes", len(content))
        result = owner.derive_normalized_bounded(content, source_path="Source.md",
                                                snapshot=SimpleNamespace(resolve=resolve),
                                                budget=budget.relationships(sid, token), cancel=token)
        assert result.state == "limited" and len(result.occurrences) == quota
        assert budget.used[sid]["relationships"] == quota
    assert budget.total["relationships"] == resolved == space_count * quota <= 200_000


def test_no_occurrence_materialization_before_admission(tmp_path, monkeypatch):
    import app.services.relationships as relationships

    original = relationships.RelationshipOccurrence
    materialized = []

    def construct(*args, **kwargs):
        materialized.append(args[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(relationships, "RelationshipOccurrence", construct)
    result, budget, resolved, _, _ = derivation(tmp_path, "[[missing]]" * 10, quota=2)
    assert len(materialized) == len(result.occurrences) == len(resolved) == budget.total["relationships"] == 2


@pytest.mark.parametrize("scope_field", ["folder", "paths", "relationships"])
def test_scoped_query_retains_legacy_whitespace_validation(tmp_path, scope_field):
    _, registry = setup_spaces(tmp_path)
    if scope_field == "folder":
        spec = scoped(KnowledgeQuery(folder=" folder"))
    elif scope_field == "paths":
        spec = scoped(paths=(QualifiedPath(DEFAULT_SPACE_ID, " Target.md"),))
    else:
        spec = scoped(relationships=(predicate(path=" Target.md"),))
    with pytest.raises(InvalidKnowledgeQueryError):
        operations(registry).scoped_query(spec)


def test_depth_ceiling_discards_space_not_os_prefix(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "A.md", "hit")
    note(tmp_path, "research", "A.md", "hit")
    directory = tmp_path / "default"
    for _ in range(65):
        directory /= "d"
        directory.mkdir()
    result = operations(registry).scoped_list(ALL)
    assert identities(result) == [("research", "A.md")]
    assert result.coverage[0].state == "unavailable"
    assert result.coverage[0].reasons == ("discovery_limit",)


def test_cancel_between_admission_and_resolution(tmp_path, monkeypatch):
    token = CancellationToken()
    original = RelationshipBudgetView.try_admit

    def admit(view):
        original(view)
        token.cancel()
    monkeypatch.setattr(RelationshipBudgetView, "try_admit", admit)
    with pytest.raises(SpaceError, match="operation_cancelled"):
        derivation(tmp_path, "[[x]]", cancel=token)


def test_authorized_processing_is_ascii_order(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    original = VaultService.scoped_session
    processed = []

    def session(vault, **kwargs):
        processed.append(kwargs["space_id"].value)
        return original(vault, **kwargs)
    monkeypatch.setattr(VaultService, "scoped_session", session)
    operations(registry).scoped_list(ALL)
    assert processed == ["default", "research"]


@pytest.mark.skipif(os.name != "nt", reason="Windows native junction")
def test_windows_native_junction_containment_and_resolution(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "Canonical/Target.md", "hit")
    note(tmp_path, "default", "Source.md", "[x](Alias/Target.md)")
    alias = tmp_path / "default" / "Alias"
    completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias),
                                str(tmp_path / "default" / "Canonical")], capture_output=True)
    assert completed.returncode == 0
    result = operations(registry).scoped_query(scoped(
        relationships=(predicate(path="Canonical/Target.md"),), scope=None,
    ))
    assert identities(result) == [("default", "Source.md")]
    assert identities(operations(registry).scoped_list(folder="Alias")) == [("default", "Canonical/Target.md")]
    escape = tmp_path / "default" / "Escape"
    completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(escape),
                                str(tmp_path / "research")], capture_output=True)
    assert completed.returncode == 0
    from app.services.knowledge_query import UnsafeKnowledgeQueryScopeError

    with pytest.raises(UnsafeKnowledgeQueryScopeError):
        operations(registry).scoped_list(ALL, folder="Escape")


@pytest.mark.skipif(os.name == "nt", reason="POSIX rename of pinned directory")
def test_posix_final_root_replacement_invalidates_buffer(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    for sid in ("default", "research"):
        note(tmp_path, sid, "A.md", "hit")
    original = ScopedVaultSession.read

    def read(session, fact, **kwargs):
        result = original(session, fact, **kwargs)
        if session.sid == DEFAULT_SPACE_ID:
            session.root.rename(tmp_path / "detached-default")
            session.root.mkdir()
        return result
    monkeypatch.setattr(ScopedVaultSession, "read", read)
    result = operations(registry).scoped_literal_search("hit", ALL)
    assert identities(result) == [("research", "A.md")]
    assert result.coverage[0].state == "unavailable"
    assert result.coverage[0].reasons == ("root_unavailable",)


@pytest.mark.parametrize("phase", [
    "delimiter", "wiki_target", "markdown_destination", "markdown_whitespace", "normalization", "fence_whitespace",
])
def test_cancellation_during_validation_bounds_actual_inspections(monkeypatch, phase):
    from app.services._scoped_links import Cursor, recognize

    token = CancellationToken()
    budget = ScopedBudget((DEFAULT_SPACE_ID,))
    active = phase == "delimiter"
    inspections = 0
    phase_start = 0

    class AuthoredText(str):
        def __getitem__(self, index):
            nonlocal inspections
            if active:
                # No whole-span native validator can hide behind a prepaid
                # probe batch: cancellation must precede copying that span.
                assert not isinstance(index, slice), "uncancellable span before validation"
                inspections += 1
                if inspections == 100:
                    token.cancel()
            return super().__getitem__(index)

    content = AuthoredText({
        "delimiter": "a" * 10_000,
        "wiki_target": "[[" + "a" * 10_000 + "]]",
        "markdown_destination": "[x](<" + "a" * 10_000 + ".md>)",
        "markdown_whitespace": "[x](" + "a" * 10_000 + " .md)",
        "normalization": "[[" + " " * 10_000 + "target]]",
        "fence_whitespace": "```\n```" + " " * 10_000 + "\n",
    }[phase])
    budget.charge(DEFAULT_SPACE_ID, "bytes", len(content))
    view = budget.relationships(DEFAULT_SPACE_ID, token)
    view.source("Source.md", len(content))
    cursor = Cursor(content, view)
    seek = Cursor.seek

    def seek_then_validate(self, *args):
        nonlocal active, phase_start
        result = seek(self, *args)
        active = True
        phase_start = budget.parse_probes
        return result

    trim = Cursor.trim

    def trim_then_validate(self, *args):
        nonlocal active, phase_start
        active = True
        phase_start = budget.parse_probes
        return trim(self, *args)

    if phase in {"wiki_target", "markdown_destination", "markdown_whitespace"}:
        monkeypatch.setattr(Cursor, "seek", seek_then_validate)
    if phase in {"normalization", "fence_whitespace"}:
        monkeypatch.setattr(Cursor, "trim", trim_then_validate)
    with pytest.raises(SpaceError, match="operation_cancelled"):
        if phase == "delimiter":
            cursor.seek(0, "]]")
        elif phase in {"wiki_target", "normalization"}:
            cursor.wiki(0)
        elif phase in {"markdown_destination", "markdown_whitespace"}:
            cursor.markdown(0)
        else:
            tuple(recognize(content, view))
    assert inspections == 100  # zero further inspections after cancellation
    assert budget.parse_probes - phase_start == inspections
    assert budget.total["relationships"] == 0
    assert budget.parse_probes <= 16 * len(content)


@pytest.mark.parametrize("raw", [
    "a" * 5000 + ".md", "a" * 5000 + ":target.md", "a" * 5000 + "!target.md",
    " Target.md ", "Target.md#" + "f" * 5000, "Target.md#bad\x00", "1:Target.md",
    "\\Target.md", "\\\\Target.md", "//server/share.md", "./Target.md", "a\\b.md",
])
def test_bounded_destination_validation_legacy_parity(raw):
    from app.services._scoped_links import Cursor
    from app.services.markdown_links import MarkdownLinkResolver

    budget = ScopedBudget((DEFAULT_SPACE_ID,))
    budget.charge(DEFAULT_SPACE_ID, "bytes", len(raw.encode("utf-8")))
    view = budget.relationships(DEFAULT_SPACE_ID, CancellationToken())
    view.source("Source.md", len(raw.encode("utf-8")))
    assert Cursor(raw, view).note_destination(0, len(raw), angle=True) == MarkdownLinkResolver._note_destination(raw)


@pytest.mark.parametrize("quota", [1, 2])
@pytest.mark.parametrize("destination,recognized", [
    (r"\:Target.md", True),
    ("Target.md", True),
    ("C:/absolute/Note.md", False),
    ("C:Note.md", False),
    ("1:Target.md", False),
    (r"\\host\share\Note.md", False),
])
def test_bounded_markdown_windows_drive_occurrence_parity(tmp_path, destination, recognized, quota):
    from app.services.markdown_links import MarkdownLinkResolver

    content = f"[x]({destination})"
    assert len(MarkdownLinkResolver.parse(content)) == int(recognized)
    _, _, _, session, snapshot = derivation(tmp_path, content)
    budget = ScopedBudget((DEFAULT_SPACE_ID,), limits={"relationships": (quota, quota)})
    budget.charge(DEFAULT_SPACE_ID, "bytes", len(content.encode("utf-8")))
    session.budget = budget
    owner = RelationshipService(session.vault)
    eager = owner.normalized_relationships_from_content(
        content, source_path="Source.md",
        snapshot=owner.normalized_resolution_snapshot(session.vault.markdown_path_candidate_snapshot()),
    )
    attempts = []

    def resolve(*args):
        # Admission has happened exactly once BEFORE the resolution attempt.
        assert budget.total["relationships"] == len(attempts) + 1
        attempts.append(args)
        # Isolate parser/admission/normalization parity from the supplied
        # resolver's independent policy: forward the eager reference outcome.
        return eager[0].resolved_path, eager[0].resolution

    result = owner.derive_normalized_bounded(
        content, source_path="Source.md", snapshot=SimpleNamespace(resolve=resolve),
        budget=budget.relationships(DEFAULT_SPACE_ID, session.cancel), cancel=session.cancel,
    )
    assert result.occurrences == eager
    assert len(result.occurrences) == budget.total["relationships"] == len(attempts) == int(recognized)
    if recognized:
        assert attempts == [("Source.md", "markdown_link", destination)]
    limited = recognized and quota == 1
    assert result.state == ("limited" if limited else "complete")
    assert result.reason == ("relationship_limit" if limited else None)


@pytest.mark.parametrize("healthy", [True, False])
def test_qualified_or_raced_operand_preserves_available_space(tmp_path, monkeypatch, healthy):
    _, registry = setup_spaces(tmp_path)
    for path in ("A.md", "B.md"):
        note(tmp_path, "default", path, "hit")
    discover = ScopedVaultSession.discover

    def race(session):
        snapshot = discover(session)
        (session.root / "A.md").write_text("raced after discovery", encoding="utf-8")
        if not healthy:
            (session.root / "B.md").unlink()
        return snapshot

    monkeypatch.setattr(ScopedVaultSession, "discover", race)
    result = operations(registry).scoped_query(scoped(scope=None, paths=(
        QualifiedPath(DEFAULT_SPACE_ID, "A.md"), QualifiedPath(DEFAULT_SPACE_ID, "B.md"),
    )))
    assert identities(result) == ([("default", "B.md")] if healthy else [])
    assert result.coverage[0].state == "partial"
    assert result.coverage[0].reasons == ("note_unavailable",)


@pytest.mark.parametrize("denied", [False, True])
def test_qualified_or_unauthorized_operand_no_filesystem_work(tmp_path, monkeypatch, denied):
    _, registry = setup_spaces(tmp_path, **({"read": "deny", "write": "deny", "indexing": "disabled"}
                                         if denied else {}))
    other = RESEARCH if denied else SpaceId("unknown")
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **kw: pytest.fail("filesystem work"))
    with pytest.raises(SpaceError, match="unknown_or_denied_space"):
        operations(registry).scoped_query(scoped(scope=ReadScope((DEFAULT_SPACE_ID, other)), paths=(
            QualifiedPath(DEFAULT_SPACE_ID, "A.md"), QualifiedPath(other, "B.md"),
        )))


def test_qualified_or_final_root_loss_still_discards_space(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    for sid in ("default", "research"):
        note(tmp_path, sid, "B.md", "hit")
    original = ScopedVaultSession.verify_root

    def verify(session):
        if session.sid == DEFAULT_SPACE_ID:
            raise OSError("root lost")
        return original(session)

    monkeypatch.setattr(ScopedVaultSession, "verify_root", verify)
    result = operations(registry).scoped_query(scoped(paths=tuple(
        QualifiedPath(sid, "B.md") for sid in (DEFAULT_SPACE_ID, RESEARCH)
    )))
    assert identities(result) == [("research", "B.md")]
    assert result.coverage[0].reasons == ("root_unavailable",)


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX terminal relative directory aliases")
@pytest.mark.parametrize("destination,canonical", [
    (".", "Notes"), ("..", ""), ("Child/..", "Notes"), ("../Notes/Child/..", "Notes"),
])
def test_posix_terminal_directory_alias_metadata_and_scoped_list(tmp_path, monkeypatch, destination, canonical):
    import stat

    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "Notes/A.md", "hit")
    (tmp_path / "default" / "Notes" / "Child").mkdir()
    (tmp_path / "default" / "Notes" / "Alias").symlink_to(destination, target_is_directory=True)
    vault = registry._owners[DEFAULT_SPACE_ID].vault
    budget = ScopedBudget((DEFAULT_SPACE_ID,))
    session = vault.scoped_session(space_id=DEFAULT_SPACE_ID, budget=budget, cancel=CancellationToken(),
                                   root_identity=vault.root_binding_identity())
    calls = []
    original = session.call

    def call(function, *args, **kwargs):
        before = budget.total["probes"]
        result = original(function, *args, **kwargs)
        assert budget.total["probes"] == before + 1
        calls.append(function)
        return result

    monkeypatch.setattr(session, "call", call)
    with session:
        path, info = session.resolve("Notes/Alias")
    expected = (tmp_path / "default" / canonical).stat()
    assert path == canonical and stat.S_ISDIR(info.st_mode)
    assert (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino)
    assert calls.count(os.readlink) == 1
    assert calls[-1] is os.fstat  # final canonical refresh, charged once
    assert budget.total["probes"] == len(calls) <= 500_000
    # Deny the final metadata refresh: no uncharged primitive can sneak past
    # the same fixed probe ceiling used by discovery and later phases.
    limited = ScopedBudget((DEFAULT_SPACE_ID,), limits={"probes": (len(calls) - 1, len(calls) - 1)})
    bounded = vault.scoped_session(space_id=DEFAULT_SPACE_ID, budget=limited, cancel=CancellationToken(),
                                   root_identity=vault.root_binding_identity())
    with pytest.raises(BudgetExhausted, match="probes"), bounded:
        bounded.resolve("Notes/Alias")
    assert limited.total["probes"] == len(calls) - 1
    result = operations(registry).scoped_list(folder="Notes/Alias")
    assert identities(result) == [("default", "Notes/A.md")]
    assert result.state == "complete"
