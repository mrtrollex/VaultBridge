"""Slice G: connect accepted owners through the complete private runtime."""

import json
import threading
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.knowledge_query import KnowledgeQuery
from app.services.knowledge_spaces import QualifiedPath, ReadScope, SpaceError, SpaceId
from app.services.scoped_reads import ScopedKnowledgeQuery
from app.services.semantic_search import SemanticSearchService
from app.services.space_operations import SpaceOperations
from app.services.space_runtime import compose_space_runtime
from tests.space_integration_evidence import evidence_bytes
from tests.test_scoped_federation import capture_budget, identities
from tests.test_scoped_promotion import approve, capture
from tests.test_space_runtime import backend as backend_fixture
from tests.test_space_runtime import binding, configuration, scope


@pytest.fixture(name="backend")
def local_backend(monkeypatch):
    return backend_fixture.__wrapped__(monkeypatch)


def writable_settings(tmp_path, count=2, *, watch=False, disabled=()):
    settings = configuration(tmp_path, count, watch=watch, disabled=disabled)
    document = json.loads(settings.knowledge_spaces_json)
    for record in document["spaces"]:
        record["write"] = "allow"
        (tmp_path / record["space_id"] / "Notes").mkdir()
    return Settings(**{**settings.model_dump(), "knowledge_spaces_json": json.dumps(document)})


def test_all_semantic_scores_and_v1_bytes_equal_accepted_baseline():
    assert evidence_bytes() == (Path(__file__).parent / "fixtures/vb151_g_reference.json").read_bytes()


@pytest.mark.parametrize("count", range(1, 9))
def test_capture_promotion_to_real_index_and_federation(tmp_path, backend, count):
    """No mocked enqueue: F -> E4/E3/E1/E2 -> D/C, including equal paths."""
    runtime = compose_space_runtime(writable_settings(tmp_path, count))
    assert backend.instances == []
    with runtime:
        runtime.scheduler.wait(20)
        expected = []
        for sid in sorted(runtime.registry._by_id):
            result = capture(runtime, sid.value)
            assert result.committed and result.index_state == "index_pending"
            approved = approve(runtime, sid.value)
            result = runtime.operations.scoped_promotion_apply(approved).result
            assert result.committed and result.index_state == "index_pending"
            runtime.scheduler.wait(20)
            chunks = binding(runtime, sid.value)._owners.semantic.repository.load_chunks()
            assert any(c.path == "Notes/Target.md" and "Approved body" in c.content for c in chunks)
            assert capture(runtime, sid.value).category == "already_applied"
            assert runtime.operations.scoped_promotion_apply(approved).result.category == "already_applied"
            expected.append((sid.value, "Notes/Target.md"))

        selected = ReadScope(tuple(reversed(tuple(runtime.registry._by_id))))
        # An identical semantic/lexical score must retain the qualified ID tie order.
        results = (
            runtime.operations.scoped_literal_search("Approved body", selected, limit=100),
            runtime.operations.scoped_query(ScopedKnowledgeQuery(
                selected, KnowledgeQuery(literal_text="Approved body", limit=100))),
            runtime.operations.scoped_semantic_search("Approved body", selected, folder="Notes", limit=100),
            runtime.operations.scoped_query(ScopedKnowledgeQuery(
                selected, KnowledgeQuery(semantic_text="Approved body", folder="Notes", limit=100))),
        )
        for result in results:
            assert identities(result) == expected
            assert result.state == "complete" and not result.result_limited
            assert [row.space_id.value for row in result.coverage] == [sid for sid, _ in expected]
            assert all(row.state == "complete" for row in result.coverage)
        assert {identity for identity in identities(runtime.operations.scoped_list(selected, limit=100))
                if identity[1] == "Notes/Target.md"} == set(expected)
        assert len(backend.instances) == 1
        assert runtime.scheduler._executor._max_workers == 1
        # Only the selected repository acquired that namespace's private binding.
        for sid in runtime.registry._by_id:
            repository = binding(runtime, sid.value)._owners.semantic.repository
            assert repository.db_path.parent == tmp_path / (sid.value + "-data")
            if sid.value != "default":
                assert repository.get_metadata("knowledge_space_id") == sid.value
    assert runtime._state == "stopped" and runtime.resource._closed
    assert runtime.scheduler._shutdown_complete and runtime.scheduler._executor._shutdown
    assert runtime.admission._active_thread is None and runtime.resource._pins == 0
    with pytest.raises(SpaceError, match="operation_cancelled"):
        runtime.operations.scoped_list()


def test_native_watcher_updates_equal_paths_only_in_own_repository(tmp_path, backend, monkeypatch):
    """Real ReadDirectoryChangesW/inotify, not a fabricated watchdog event."""
    runtime = compose_space_runtime(writable_settings(tmp_path, 3, watch=True, disabled=("space-2",)))
    indexed = threading.Event()
    original = SemanticSearchService.sync_step

    def observed(service, *args, **kwargs):
        result = original(service, *args, **kwargs)
        if result.indexed and service.vault_root == tmp_path / "space-1":
            if any(c.path == "same.md" and "native revision" in c.content
                   for c in service.repository.load_chunks()):
                indexed.set()
        return result

    monkeypatch.setattr(SemanticSearchService, "sync_step", observed)
    with runtime:
        runtime.scheduler.wait(20)
        default = binding(runtime)._owners.semantic.repository
        before = default.load_chunks()
        assert runtime.watcher._observer.is_alive()
        assert len(runtime.watcher._bindings) == 2
        # Both roots get the same relative update. The disabled one has no watcher/job/storage.
        for sid in ("space-1", "space-2"):
            (tmp_path / sid / "same.md").write_bytes(b"# native revision\nOnly this authorized owner")
        assert indexed.wait(20), "native watcher did not deliver the selected owner update"
        runtime.scheduler.wait(20)
        assert default.load_chunks() == before
        result = runtime.operations.scoped_query(ScopedKnowledgeQuery(
            ReadScope((SpaceId("space-1"), SpaceId("default"))),
            KnowledgeQuery(semantic_text="native revision", literal_text="native revision")))
        assert identities(result) == [("space-1", "same.md")]
        assert binding(runtime, "space-2")._owners.scheduler is None
        assert not (tmp_path / "space-2-data").exists()
    assert runtime._state == "stopped"
    assert runtime.watcher._observer is None and runtime.watcher._thread is None
    assert runtime.scheduler._watcher_token is None and runtime.resource._closed


@pytest.mark.parametrize("mode", ["list", "literal", "query", "semantic"])
@pytest.mark.parametrize("bad_id", ["space-1", "unknown"])
def test_whole_scope_denial_precedes_all_operational_owners(tmp_path, backend, monkeypatch, mode, bad_id, caplog):
    runtime = compose_space_runtime(configuration(tmp_path, denied=("space-1",)))

    def forbidden(*args, **kwargs):
        pytest.fail("operational work before whole-selection authorization")

    monkeypatch.setattr(SpaceOperations, "_owners", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", forbidden)
    selected = ReadScope((SpaceId("default"), SpaceId(bad_id)))
    try:
        with pytest.raises(SpaceError) as error:
            if mode == "list":
                runtime.operations.scoped_list(selected)
            elif mode == "literal":
                runtime.operations.scoped_literal_search("PRIVATE_QUERY", selected)
            elif mode == "query":
                runtime.operations.scoped_query(ScopedKnowledgeQuery(
                    selected, KnowledgeQuery(semantic_text="PRIVATE_QUERY")))
            else:
                runtime.operations.scoped_semantic_search("PRIVATE_QUERY", selected)
        assert str(error.value) == "unknown_or_denied_space"
        assert backend.instances == [] and not list(tmp_path.glob("*-data"))
        private = (str(tmp_path), "PRIVATE_QUERY", "space-1", "unknown")
        assert not any(value in repr(runtime) + caplog.text for value in private)
    finally:
        runtime.shutdown()


def test_runtime_written_relationships_and_work_limits_remain_local(tmp_path, backend, monkeypatch):
    from app.services.scoped_reads import QualifiedRelationshipPredicate

    with compose_space_runtime(writable_settings(tmp_path)) as runtime:
        runtime.scheduler.wait(20)
        for sid in ("default", "space-1"):
            capture(runtime, sid, content="[[same]] [same](same.md) [[missing]]")
        runtime.scheduler.wait(20)
        budgets = capture_budget(monkeypatch)
        target = QualifiedPath(SpaceId("space-1"), "same.md")
        request = ScopedKnowledgeQuery(scope(runtime), KnowledgeQuery(), relationships=(
            QualifiedRelationshipPredicate("outgoing", target),))
        result = runtime.operations.scoped_query(request)
        assert identities(result) == [("space-1", "Inbox/Captures/12345678-1234-4123-8123-123456789abc.md")]
        assert budgets[-1].used[SpaceId("default")]["relationships"] == 0
        assert budgets[-1].used[SpaceId("space-1")]["relationships"] == 3
        # Exhaustion discards the affected contribution; the available empty default survives.
        result = runtime.operations.scoped_query(request, limits={"relationships": (2, 2)})
        assert identities(result) == [] and result.state == "partial"
        assert result.coverage[0].state == "complete"
        assert result.coverage[1].state == "unavailable"
        assert result.coverage[1].reasons == ("relationship_limit",)
        assert budgets[-1].total["relationships"] == 1  # fixed allocation floor(2/2)
        for resource, total in budgets[-1].total.items():
            assert total <= budgets[-1].ceilings[resource][0]
        assert not any(budgets[-1].reserved.values())
