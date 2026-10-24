"""E4 deterministic turn, admission, isolation and shutdown regressions."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType, SimpleNamespace

import pytest

from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.repositories.semantic_binding import ExpectedIndexBinding
from app.services.indexer import BackgroundSemanticIndexer
from app.services.knowledge_spaces import (
    Capability,
    Dialect,
    QualifiedPath,
    SpaceError,
    SpaceId,
    SpaceOwners,
    SpacePolicyResolver,
    SpaceRegistry,
    WriteScope,
)
from app.services.semantic_resources import SharedEmbeddingResource
from app.services.semantic_scheduler import SharedSemanticScheduler
from app.services.semantic_search import IndexState, SemanticSearchService, SynchronizationCancelledError
from app.services.semantic_sync import SyncContinuation, SyncStep, SyncStepState
from app.services.vault import VaultService
from tests.eval.runner import CORPUS_ROOT, DeterministicConceptEmbedder, build_service, load_cases, run_evaluation
from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared


class FakeOwner:
    def __init__(self, definition, resource, trace):
        self.vault_root = definition.root_binding
        self.db_path = definition.semantic_data_binding / "semantic-index.sqlite3"
        self.repository = SimpleNamespace(
            db_path=self.db_path,
            _expected_binding=(ExpectedIndexBinding(definition.space_id.value, self.vault_root)
                               if definition.space_id.value != "default" else None),
        )
        self._shared_resource = resource
        self._indexing_enabled = True
        self.sid = definition.space_id.value
        self.trace = trace
        self.begins = []
        self.handles = []
        self.steps = 0
        self.full_steps = 3
        self.target_steps = 1
        self.fail = False
        self.closed = False
        self.cancelled = []
        self.entered = threading.Event()
        self.release = None
        self.hook = None
        self.promote = False

    def begin_sync(self, *, relative_paths, cancel_event):
        assert not self.closed
        if cancel_event.is_set():
            raise SynchronizationCancelledError()
        handle = SyncContinuation(full=relative_paths is None or self.promote)
        self.begins.append(relative_paths)
        self.handles.append(handle)
        self.steps = 0
        return handle

    def sync_step(self, handle, cancel_event):
        assert handle is self.handles[-1] and handle.state is SyncStepState.INCOMPLETE
        self.trace.append(self.sid)
        self.entered.set()
        if self.release is not None:
            assert self.release.wait(15)
        if cancel_event.is_set():
            handle._state = SyncStepState.CANCELLED
            raise SynchronizationCancelledError()
        if self.fail:
            handle._state = SyncStepState.FAILED
            raise RuntimeError("injected owner failure")
        if self.hook is not None:
            self.hook()
        self.steps += 1
        if self.steps >= (self.full_steps if handle.full else self.target_steps):
            handle._state = SyncStepState.COMPLETE
        return SyncStep(handle.state, self.steps, 0, 0)

    def cancel_sync(self, handle):
        self.cancelled.append(handle)
        handle._state = SyncStepState.CANCELLED

    def close_synchronization(self):
        self.closed = True


def setup(tmp_path, count=2, *, disabled=(), denied=(), resource=None, real=False, **service_options):
    names = ["default", *[f"space-{i}" for i in range(1, count)]]
    records = []
    for name in names:
        root = tmp_path / name
        root.mkdir(parents=True)
        records.append(dict(space_id=name, root=str(root), semantic_data_path=str(tmp_path / f"{name}-data"),
                            read="deny" if name in denied else "allow", write="deny",
                            indexing="disabled" if name in disabled or name in denied else "enabled",
                            dialects=[item.value for item in Dialect],
                            capabilities=[item.value for item in Capability]))
    settings = Settings(vault_path=records[0]["root"], semantic_data_path=records[0]["semantic_data_path"],
                        knowledge_spaces_json=json.dumps(dict(version=1, spaces=records)))
    registry = SpaceRegistry.from_settings(settings)
    trace, services, bundles = [], {}, {}
    resource = resource if resource is not None else object()
    for definition in registry._definitions:
        sid = definition.space_id
        if sid.value in disabled or sid.value in denied:
            service = None  # Accessing it would fail: strict disabled no-work.
        elif real:
            service = SemanticSearchService(
                vault_root=definition.root_binding,
                repository=SemanticRepository(
                    definition.semantic_data_binding / "semantic-index.sqlite3",
                    _expected_binding=ExpectedIndexBinding(sid.value, definition.root_binding)
                    if sid.value != "default" else None,
                ), model_name=resource.model_name, shared_resource=resource, index_batch_size=1, **service_options,
            )
        else:
            service = FakeOwner(definition, resource, trace)
        vault = VaultService(vault_root=definition.root_binding, max_note_bytes=100_000)
        services[sid.value] = service
        bundles[sid] = SpaceOwners(definition=definition, root_identity=vault.root_binding_identity(),
                                  vault=vault, semantic=service, scheduler=None, relationships=None,
                                  query=None, duplicates=None, capture=None, promotion=None, hygiene=None)
    object.__setattr__(registry, "_owners", MappingProxyType(bundles))
    bindings = {name: registry._binding(SpaceId(name)) for name in names}
    return registry, bindings, services, trace


def path(binding, name="same.md"):
    return QualifiedPath(binding.space_id, name)


def enqueue(scheduler, binding, name="same.md"):
    return scheduler.enqueue(binding, path(binding, name))


@pytest.mark.parametrize("count", [1, 2, 8])
def test_sorted_round_robin_one_continuation_and_one_executor(tmp_path, monkeypatch, count):
    registry, bindings, owners, trace = setup(tmp_path, count)
    executors = []

    def executor(**kwargs):
        assert kwargs["max_workers"] == 1
        result = ThreadPoolExecutor(**kwargs)
        executors.append(result)
        return result

    monkeypatch.setattr("app.services.semantic_scheduler.ThreadPoolExecutor", executor)
    scheduler = SharedSemanticScheduler(registry, reversed(tuple(bindings.values())))
    assert not executors and not trace
    try:
        scheduler.start(startup=True)
        scheduler.start(startup=True)
        scheduler.wait(5)
        assert trace == sorted(bindings) * 3
        assert len(executors) == 1
        assert all(len(owner.handles) == 1 for owner in owners.values())
        assert all(scheduler.status(binding).progress.state is SyncStepState.COMPLETE for binding in bindings.values())
    finally:
        scheduler.shutdown()
    assert all(owner.closed for owner in owners.values())


def test_large_full_and_short_target_fairness_and_no_parallel_steps(tmp_path):
    registry, bindings, owners, trace = setup(tmp_path)
    a, b = bindings.values()
    owners["default"].full_steps = 99
    owners["default"].release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    scheduler.request_full_sync(a)
    enqueue(scheduler, b)
    try:
        scheduler.start()
        assert owners["default"].entered.wait(3)
        assert not owners["space-1"].entered.is_set()
        owners["default"].release.set()
        scheduler.wait(5)
        assert trace[:3] == ["default", "space-1", "default"]
        assert len(trace) == 100
        assert owners["space-1"].begins == [("same.md",)]
    finally:
        owners["default"].release.set()
        scheduler.shutdown()


def test_equal_paths_are_qualified_deduplicated_and_normalized(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path)
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        for binding in bindings.values():
            assert enqueue(scheduler, binding, "folder\\same.md")
            assert not enqueue(scheduler, binding, " folder/./same.md ")
        assert scheduler.pending_count == 2
        scheduler.start()
        scheduler.wait(5)
        assert all(owner.begins == [("folder/same.md",)] for owner in owners.values())
        assert scheduler.pending_count == 0
    finally:
        scheduler.shutdown()


@pytest.mark.parametrize("name", ["../bad.md", "/bad.md", "C:\\bad.md", "bad.txt", ".git/bad.md"])
def test_unsafe_paths_rejected_before_queueing(tmp_path, name):
    registry, bindings, _, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        with pytest.raises((SpaceError, ValueError)):
            enqueue(scheduler, binding, name)
        assert scheduler.pending_count == 0
    finally:
        scheduler.shutdown()


def test_foreign_stale_wrong_owner_and_wrong_qualified_space_rejected(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path)
    binding, other = bindings.values()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        foreign = SpaceRegistry.from_settings(Settings(vault_path=tmp_path / "default"))
        with pytest.raises(SpaceError, match="invalid_scope"):
            enqueue(scheduler, foreign._binding(SpaceId("default")))
        with pytest.raises(SpaceError, match="invalid_scope"):
            scheduler.enqueue(binding, path(other))
        original = registry._owners
        object.__setattr__(registry, "_owners", MappingProxyType({**original, binding.space_id: other._owners}))
        with pytest.raises(SpaceError, match="invalid_scope"):
            enqueue(scheduler, binding)
        object.__setattr__(registry, "_owners", original)
        assert not any(owner.begins for owner in owners.values())
    finally:
        scheduler.shutdown()


@pytest.mark.parametrize("global_limit", [False, True])
def test_exact_capacity_first_overflow_and_coalescing(tmp_path, global_limit):
    registry, bindings, owners, _ = setup(tmp_path, 8 if global_limit else 1)
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    selected = list(bindings.values())[:5] if global_limit else list(bindings.values())
    try:
        for binding in selected:
            for i in range(2000):
                assert enqueue(scheduler, binding, f"{i}.md")
        assert scheduler.pending_count == (10_000 if global_limit else 2000)
        for binding in selected:
            assert not enqueue(scheduler, binding, "0.md")
            assert not scheduler.status(binding).requires_full_sync
        assert scheduler.pending_count == (10_000 if global_limit else 2000)
        overflow = list(bindings.values())[5] if global_limit else selected[0]
        for i in range(20):
            assert not enqueue(scheduler, overflow, f"overflow-{i}.md")
        status = scheduler.status(overflow)
        assert status.requires_full_sync
        assert len(status.queued_paths) == (0 if global_limit else 2000)
        assert scheduler.pending_count == (10_000 if global_limit else 2000)
        scheduler.start()
        scheduler.wait(10)
        assert owners[overflow.space_id.value].begins == [None]
        assert scheduler.pending_count == 0
        assert not scheduler.status(overflow).requires_full_sync
    finally:
        scheduler.shutdown()


@pytest.mark.parametrize("global_limit", [False, True])
def test_concurrent_admission_at_capacity(tmp_path, global_limit):
    registry, bindings, _, _ = setup(tmp_path, 8 if global_limit else 1)
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    selected = list(bindings.values())
    try:
        for binding in selected[:4] if global_limit else ():
            for i in range(2000):
                enqueue(scheduler, binding, f"{i}.md")
        binding = selected[4] if global_limit else selected[0]
        for i in range(1999):
            enqueue(scheduler, binding, f"{i}.md")
        barrier = threading.Barrier(9)

        def submit(i):
            barrier.wait(5)
            # Use independent spaces to exercise the GLOBAL lock as well.
            target = selected[4 + i % 4] if global_limit else binding
            return enqueue(scheduler, target, f"race-{i}.md")

        workers = [launch(lambda i=i: submit(i)) for i in range(8)]
        barrier.wait(5)
        assert sum(worker.result(10) for worker in workers) == 1
        assert scheduler.pending_count == (10_000 if global_limit else 2000)
        assert any(scheduler.status(b).requires_full_sync for b in selected)
    finally:
        scheduler.shutdown()


def test_full_absorbs_prior_paths_and_preserves_writes_during_full(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    owner = owners["default"]
    owner.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    enqueue(scheduler, binding, "before.md")
    scheduler.request_full_sync(binding)
    try:
        scheduler.start()
        assert owner.entered.wait(3)
        assert scheduler.pending_count == 0
        enqueue(scheduler, binding, "during.md")
        enqueue(scheduler, binding, "before.md")
        owner.release.set()
        scheduler.wait(5)
        assert owner.begins == [None, ("before.md", "during.md")]
        assert scheduler.pending_count == 0
    finally:
        owner.release.set()
        scheduler.shutdown()


def test_overflow_during_full_requires_one_followup_full(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    owner = owners["default"]
    owner.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    scheduler.request_full_sync(binding)
    try:
        scheduler.start()
        assert owner.entered.wait(3)
        for i in range(2003):
            enqueue(scheduler, binding, f"during-{i}.md")
        assert scheduler.pending_count == 2000
        owner.release.set()
        scheduler.wait(5)
        assert owner.begins == [None, None]
        assert not scheduler.status(binding).requires_full_sync
    finally:
        owner.release.set()
        scheduler.shutdown()


def test_inflight_targets_remain_charged_and_rewrites_get_followup(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    owner = owners["default"]
    owner.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        for i in range(2000):
            enqueue(scheduler, binding, f"{i}.md")
        scheduler.start()
        assert owner.entered.wait(3)
        assert scheduler.pending_count == 2000
        assert enqueue(scheduler, binding, "0.md")
        assert not enqueue(scheduler, binding, "extra.md")
        owner.release.set()
        scheduler.wait(5)
        assert owner.begins[1] is None
        assert len(owner.begins) == 2
        assert scheduler.pending_count == 0
    finally:
        owner.release.set()
        scheduler.shutdown()


def test_full_preempts_yielded_target_on_next_rotation(tmp_path):
    registry, bindings, owners, trace = setup(tmp_path)
    a, b = bindings.values()
    owners["default"].target_steps = 20
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    owners["default"].hook = lambda: scheduler.request_full_sync(a)
    owners["space-1"].hook = lambda: setattr(owners["default"], "hook", None)
    enqueue(scheduler, a)
    enqueue(scheduler, b)
    try:
        scheduler.start()
        scheduler.wait(5)
        assert trace[:2] == ["default", "space-1"]
        assert owners["default"].begins == [("same.md",), None]
        assert len(owners["default"].cancelled) == 1
    finally:
        scheduler.shutdown()


def test_failure_isolated_no_hot_retry_and_new_target_cannot_erase_full_debt(tmp_path):
    registry, bindings, owners, trace = setup(tmp_path)
    a, b = bindings.values()
    owners["default"].fail = True
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    scheduler.request_full_sync(a)
    enqueue(scheduler, b)
    try:
        scheduler.start()
        scheduler.wait(5)
        assert trace == ["default", "space-1"]
        status = scheduler.status(a)
        assert status.paused and status.requires_full_sync and status.error_type == "RuntimeError"
        assert scheduler.status(b).error_type is None
        assert scheduler.status(b).progress.state is SyncStepState.COMPLETE
        owners["default"].fail = False
        enqueue(scheduler, a, "recovery.md")
        scheduler.wait(5)
        assert owners["default"].begins == [None, None]
        assert not scheduler.status(a).requires_full_sync
    finally:
        scheduler.shutdown()


def test_disabled_and_denied_are_strict_zero_work(tmp_path, monkeypatch):
    registry, bindings, _, trace = setup(tmp_path, disabled=("default",), denied=("space-1",))

    def forbidden(*args, **kwargs):
        pytest.fail("disabled scheduler performed work")

    monkeypatch.setattr("app.services.semantic_scheduler.ThreadPoolExecutor", forbidden)
    monkeypatch.setattr("app.services.semantic_scheduler.directory_identity", forbidden)
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    scheduler.start(startup=True)
    scheduler.wait(1)
    for binding in bindings.values():
        with pytest.raises(SpaceError, match="indexing_disabled"):
            enqueue(scheduler, binding)
        with pytest.raises(SpaceError):
            SpacePolicyResolver(registry).authorize_index(WriteScope(binding.space_id))
    scheduler.shutdown()
    assert scheduler.pending_count == 0 and trace == []
    assert not any(p.name.endswith("-data") for p in tmp_path.iterdir())


@pytest.mark.parametrize("replacement", [False, True])
def test_root_loss_or_replacement_fails_only_that_owner(tmp_path, replacement):
    registry, bindings, owners, _ = setup(tmp_path)
    a, b = bindings.values()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    scheduler.request_full_sync(a)
    enqueue(scheduler, b)
    root = owners["default"].vault_root
    root.rename(tmp_path / "old-root")
    if replacement:
        root.mkdir()
    try:
        scheduler.start()
        scheduler.wait(5)
        assert scheduler.status(a).paused and scheduler.status(a).requires_full_sync
        assert not owners["default"].begins
        assert scheduler.status(b).progress.state is SyncStepState.COMPLETE
    finally:
        scheduler.shutdown()


def test_cancel_active_discards_queue_preserves_debt_and_explicit_retry(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path)
    a, b = bindings.values()
    owner = owners["default"]
    owner.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    scheduler.request_full_sync(a)
    enqueue(scheduler, b)
    try:
        scheduler.start()
        assert owner.entered.wait(3)
        enqueue(scheduler, a, "during.md")
        scheduler.cancel(a)
        assert scheduler._owners[a.space_id].cancel.is_set()
        owner.release.set()
        scheduler.wait(5)
        assert scheduler.status(a).paused and scheduler.status(a).requires_full_sync
        assert scheduler.status(b).progress.state is SyncStepState.COMPLETE
        assert scheduler.pending_count == 0
        assert scheduler.retry(a)
        scheduler.wait(5)
        assert owner.begins == [None, None]
        assert not scheduler.status(a).requires_full_sync
    finally:
        owner.release.set()
        scheduler.shutdown()


def test_shutdown_signals_all_before_active_boundary_and_racing_enqueue(tmp_path):
    registry, bindings, owners, trace = setup(tmp_path, 8)
    owner = owners["default"]
    owner.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    scheduler.start(startup=True)
    try:
        assert owner.entered.wait(3)
        closing = launch(scheduler.shutdown)
        with scheduler._condition:
            assert scheduler._condition.wait_for(lambda: scheduler._closed, 3)
        assert all(entry.cancel.is_set() for entry in scheduler._owners.values())
        for binding in bindings.values():
            assert not enqueue(scheduler, binding)
        assert not closing.done()
        concurrent_close = launch(scheduler.shutdown)
        owner.release.set()
        closing.result(5)
        concurrent_close.result(5)
        assert trace == ["default"]
        assert all(owner.closed for owner in owners.values())
        assert scheduler.pending_count == 0
    finally:
        owner.release.set()
        scheduler.shutdown()


def test_legacy_external_executor_is_not_closed(tmp_path):
    executor = ThreadPoolExecutor(max_workers=1)
    indexer = BackgroundSemanticIndexer(lambda event: "done", executor=executor)
    try:
        assert indexer.start()
        assert indexer.wait(3) == "done"
        indexer.shutdown()
        assert executor.submit(lambda: "still owned by process").result(3) == "still owned by process"
    finally:
        indexer.shutdown()
        executor.shutdown()


def test_real_e3_owners_share_lazy_e2_and_cleanup_leases(tmp_path, shared):
    resource, backend = shared
    registry, bindings, owners, _ = setup(tmp_path, resource=resource, real=True)
    for service in owners.values():
        (service.vault_root / "same.md").write_text("private owner note", encoding="utf-8")
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    assert not backend.instances
    try:
        scheduler.start(startup=True)
        scheduler.wait(15)
        assert len(backend.instances) == 1 and backend.maximum == 1
        assert resource._pins == 0 and not resource._busy
        for binding in bindings.values():
            service = owners[binding.space_id.value]
            assert service.state is IndexState.READY
            assert service.repository.load_chunks()[0].path == "same.md"
            assert service._active_sync is None and not service.repository._sessions
            assert not service._sync_operations
            assert scheduler.status(binding).progress.state is SyncStepState.COMPLETE
    finally:
        scheduler.shutdown()
    assert resource.is_current(resource.generation)  # Process, never scheduler, owns E2 close.


def test_cancellation_after_successful_commit_retains_discarded_write_debt(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    owner = owners["default"]
    owner.full_steps = 1
    scheduler = SharedSemanticScheduler(registry, bindings.values())

    def late_cancel():
        enqueue(scheduler, binding, "new-write.md")
        scheduler.cancel(binding)

    owner.hook = late_cancel
    try:
        scheduler.start(startup=True)
        scheduler.wait(5)
        assert scheduler.status(binding).requires_full_sync
        assert scheduler.status(binding).paused
        assert scheduler.pending_count == 0
    finally:
        scheduler.shutdown()


@pytest.mark.parametrize("change", ["root", "database", "generation"])
def test_real_resume_revalidates_and_preserves_peer_repository(tmp_path, shared, monkeypatch, change):
    resource, _ = shared
    registry, bindings, owners, _ = setup(tmp_path, resource=resource, real=True)
    a, b = bindings.values()
    for service in owners.values():
        (service.vault_root / "same.md").write_text("independent original", encoding="utf-8")
        service.sync()
    peer_before = owners["space-1"].repository.load_chunks()
    service = owners["default"]
    original = owners["space-1"].sync_step
    changed = False

    def alter_between_turns(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            if change == "root":
                service.vault_root.rename(tmp_path / "old-root")
                service.vault_root.mkdir()
            elif change == "database":
                service.db_path.rename(service.db_path.with_suffix(".old"))
                with sqlite3.connect(service.db_path) as connection:
                    connection.execute("CREATE TABLE unrelated(value)")
            else:
                assert resource._pins == 0 and not resource._busy
                resource.invalidate_embedding_compatibility_cache()
        return result

    monkeypatch.setattr(owners["space-1"], "sync_step", alter_between_turns)
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        scheduler.wait(15)
        assert changed and scheduler.status(a).paused and scheduler.status(a).requires_full_sync
        assert service._active_sync is None and not service.repository._sessions
        if change != "generation":
            assert scheduler.status(b).error_type is None
            assert owners["space-1"].state is IndexState.READY
        # Shared generation invalidation legitimately invalidates both yielded
        # handles. Ordinary owner failures must never invalidate a peer.
        assert owners["space-1"].repository.load_chunks() == peer_before
        assert resource._pins == 0 and not resource._busy
    finally:
        scheduler.shutdown()


def test_named_incompatible_repository_rejected_without_mutation_or_peer_failure(tmp_path, shared):
    resource, _ = shared
    registry, bindings, owners, _ = setup(tmp_path, resource=resource, real=True)
    for service in owners.values():
        (service.vault_root / "same.md").write_text("owner content", encoding="utf-8")
        service.sync()
    bad = owners["space-1"].db_path
    with sqlite3.connect(bad) as connection:
        connection.execute("CREATE UNIQUE INDEX forbidden ON chunks(path)")
    before = bad.read_bytes()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        scheduler.wait(15)
        assert scheduler.status(bindings["space-1"]).paused
        assert scheduler.status(bindings["space-1"]).requires_full_sync
        assert bad.read_bytes() == before
        assert owners["default"].state is IndexState.READY
        assert scheduler.status(bindings["default"]).error_type is None
    finally:
        scheduler.shutdown()
    assert bad.read_bytes() == before


def test_real_full_failure_target_retry_never_restores_ready_prematurely(tmp_path, shared, monkeypatch):
    resource, backend = shared
    registry, bindings, owners, _ = setup(tmp_path, 1, resource=resource, real=True)
    binding = bindings["default"]
    service = owners["default"]
    for i in range(4):
        (service.vault_root / f"{i}.md").write_text(f"note {i}", encoding="utf-8")
    service.sync()
    for i in range(4):
        (service.vault_root / f"{i}.md").write_text(f"changed note {i}", encoding="utf-8")
    backend.fail = True
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        scheduler.wait(15)
        assert service.state is IndexState.ERROR
        assert scheduler.status(binding).requires_full_sync
        assert not service._active_sync
        backend.fail = False
        enqueue(scheduler, binding, "0.md")
        scheduler.wait(15)
        assert service.state is IndexState.READY
        assert scheduler.status(binding).progress.indexed == 4
        assert not scheduler.status(binding).requires_full_sync
    finally:
        scheduler.shutdown()


def assert_real_idle(service, resource):
    assert service._active_sync is None and not service._sync_operations
    assert not service.repository._sessions
    assert resource._pins == 0 and not resource._busy


def test_real_duplicate_write_restarts_failed_full_recovery(tmp_path, shared, monkeypatch):
    resource, backend = shared
    registry, bindings, owners, _ = setup(tmp_path, resource=resource, real=True)
    binding, peer = bindings.values()
    service = owners["default"]
    for owner in owners.values():
        (owner.vault_root / "same.md").write_text("original", encoding="utf-8")
        owner.sync()
        (owner.vault_root / "same.md").write_text("changed content", encoding="utf-8")
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    original = service.sync_step
    begins = []

    def step(handle, cancel_event):
        if handle not in begins:
            begins.append(handle)
            # Queue during discovery, before the later failing inference turn.
            if len(begins) == 1:
                (service.vault_root / "changed.md").write_text("new write", encoding="utf-8")
                assert enqueue(scheduler, binding, "changed.md")
        return original(handle, cancel_event)

    monkeypatch.setattr(service, "sync_step", step)
    backend.fail = True
    try:
        scheduler.request_full_sync(binding)
        scheduler.start()
        scheduler.wait(15)
        status = scheduler.status(binding)
        assert status.paused and status.requires_full_sync and not status.running
        assert service.state is IndexState.ERROR and len(begins) == 1
        assert status.queued_paths == ("changed.md",) and scheduler.pending_count == 1
        assert_real_idle(service, resource)
        backend.fail = False
        # Healthy peer progresses while the failed owner remains paused.
        scheduler.request_full_sync(peer)
        scheduler.wait(15)
        assert owners["space-1"].state is IndexState.READY and len(begins) == 1
        assert not enqueue(scheduler, binding, "changed.md")
        scheduler.wait(15)
        assert service.state is IndexState.READY
        assert len(begins) == 2 and all(handle.full for handle in begins)
        status = scheduler.status(binding)
        assert not status.paused and not status.requires_full_sync and not status.running
        assert not status.queued_paths and scheduler.pending_count == 0
        assert_real_idle(service, resource)
    finally:
        scheduler.shutdown()


def test_real_new_write_restarts_fully_drained_cancellation(tmp_path, shared):
    resource, backend = shared
    registry, bindings, owners, _ = setup(tmp_path, 1, resource=resource, real=True)
    binding = bindings["default"]
    service = owners["default"]
    (service.vault_root / "same.md").write_text("original", encoding="utf-8")
    service.sync()
    (service.vault_root / "same.md").write_text("changed content", encoding="utf-8")
    release = threading.Event()
    backend.block = release
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        assert backend.entered.wait(3)
        scheduler.cancel(binding)
        token = scheduler._owners[binding.space_id].cancel
        assert token.is_set()
        release.set()
        scheduler.wait(15)
        assert_real_idle(service, resource)
        status = scheduler.status(binding)
        assert status.paused and status.requires_full_sync and not status.running
        assert service.state is IndexState.ERROR
        assert enqueue(scheduler, binding, "new.md")
        scheduler.wait(15)
        assert service.state is IndexState.READY
        assert not scheduler.status(binding).requires_full_sync
        assert not scheduler.status(binding).paused and scheduler.pending_count == 0
        assert_real_idle(service, resource)
    finally:
        release.set()
        scheduler.shutdown()


@pytest.mark.parametrize("stage", ["before_pause", "after_pause"])
def test_real_duplicate_racing_failure_publication_is_not_lost(tmp_path, shared, monkeypatch, stage):
    resource, backend = shared
    registry, bindings, owners, _ = setup(tmp_path, 1, resource=resource, real=True)
    binding = bindings["default"]
    service = owners["default"]
    (service.vault_root / "same.md").write_text("needs inference", encoding="utf-8")
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    failed, submitted = threading.Event(), threading.Event()
    original_step = service.sync_step
    original_release = scheduler._release_inflight
    queued = False
    gated = False

    def step(handle, cancel_event):
        nonlocal queued
        if not queued:
            queued = True
            assert enqueue(scheduler, binding, "changed.md")
        try:
            return original_step(handle, cancel_event)
        except Exception:
            if stage == "before_pause":
                failed.set()  # E3 is disposed, scheduler failure is not published yet.
                assert submitted.wait(5)
            raise

    def release(entry):
        nonlocal gated
        original_release(entry)
        if stage == "after_pause" and entry.paused and not gated:
            gated = True
            failed.set()
            # Release the queue lock while the submitter races the failure
            # completion that will clear continuation/running below this hook.
            assert scheduler._condition.wait_for(submitted.is_set, 5)

    monkeypatch.setattr(service, "sync_step", step)
    monkeypatch.setattr(scheduler, "_release_inflight", release)
    backend.fail = True
    try:
        scheduler.start(startup=True)
        assert failed.wait(5)
        assert service.state is IndexState.ERROR
        assert scheduler.status(binding).requires_full_sync
        backend.fail = False
        assert not enqueue(scheduler, binding, "changed.md")
        with scheduler._condition:
            submitted.set()
            scheduler._condition.notify_all()
        scheduler.wait(15)
        assert service.state is IndexState.READY
        assert not scheduler.status(binding).requires_full_sync
        assert scheduler.pending_count == 0
        assert_real_idle(service, resource)
    finally:
        with scheduler._condition:
            submitted.set()
            scheduler._condition.notify_all()
        scheduler.shutdown()


def test_duplicate_during_healthy_full_sync_keeps_one_target_followup(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    owner = owners["default"]
    owner.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        assert owner.entered.wait(3)
        assert enqueue(scheduler, binding, "during.md")
        for _ in range(20):
            assert not enqueue(scheduler, binding, "during.md")
        assert scheduler.pending_count == 1
        assert scheduler.status(binding).queued_paths == ("during.md",)
        assert not scheduler._owners[binding.space_id].full_again
        owner.release.set()
        scheduler.wait(5)
        assert owner.begins == [None, ("during.md",)]
        assert scheduler.pending_count == 0 and not scheduler.status(binding).requires_full_sync
    finally:
        owner.release.set()
        scheduler.shutdown()


def test_duplicate_trigger_retries_once_without_hot_loop_on_repeated_failure(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    owner = owners["default"]
    scheduler = SharedSemanticScheduler(registry, bindings.values())

    def queue_before_failing_turn():
        assert enqueue(scheduler, binding, "changed.md")
        owner.fail = True
        owner.hook = None

    owner.hook = queue_before_failing_turn
    try:
        scheduler.start(startup=True)
        scheduler.wait(5)
        assert owner.begins == [None] and scheduler.pending_count == 1
        assert not enqueue(scheduler, binding, "changed.md")
        scheduler.wait(5)
        for _ in range(3):
            scheduler.wait(1)
        assert owner.begins == [None, None]
        assert scheduler.status(binding).paused and scheduler.status(binding).requires_full_sync
        assert scheduler.pending_count == 0
    finally:
        scheduler.shutdown()


@pytest.mark.parametrize("trigger", ["new", "duplicate", "full"])
def test_real_submission_during_cancelled_inference_waits_for_drain(
    tmp_path, shared, monkeypatch, trigger,
):
    resource, backend = shared
    registry, bindings, owners, _ = setup(tmp_path, 1, resource=resource, real=True)
    binding = bindings["default"]
    service = owners["default"]
    (service.vault_root / "same.md").write_text("original", encoding="utf-8")
    service.sync()
    (service.vault_root / "same.md").write_text("changed inference", encoding="utf-8")
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    release = threading.Event()
    backend.block = release
    original = service.begin_sync
    handles = []

    def begin(**kwargs):
        assert service._active_sync is None and not service._sync_operations
        assert scheduler.status(binding).requires_full_sync
        handle = original(**kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(service, "begin_sync", begin)
    try:
        scheduler.start(startup=True)
        assert backend.entered.wait(3)
        scheduler.cancel(binding)
        entry = scheduler._owners[binding.space_id]
        token = entry.cancel
        handle = entry.continuation
        assert token.is_set() and handle.state is SyncStepState.INCOMPLETE
        if trigger == "full":
            assert scheduler.request_full_sync(binding)
        else:
            assert enqueue(scheduler, binding, "new.md")
            if trigger == "duplicate":
                assert not enqueue(scheduler, binding, "new.md")
        assert not scheduler.retry(binding)
        assert entry.cancel is token and token.is_set()
        assert entry.continuation is handle and len(handles) == 1
        assert scheduler.status(binding).paused and scheduler.status(binding).requires_full_sync
        assert resource._busy  # The non-interruptible inference still owns its lease.
        release.set()
        scheduler.wait(15)
        assert len(handles) == 2 and all(item.full for item in handles)
        assert handle.state is SyncStepState.CANCELLED
        assert token.is_set() and entry.cancel is not token
        assert service.state is IndexState.READY
        assert not scheduler.status(binding).requires_full_sync and scheduler.pending_count == 0
        assert_real_idle(service, resource)
    finally:
        release.set()
        scheduler.shutdown()


def test_submission_to_cancelled_yielded_continuation_defers_token_replacement(tmp_path):
    registry, bindings, owners, _ = setup(tmp_path)
    binding, peer = bindings.values()
    owner, other = owners.values()
    other.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        assert other.entered.wait(3)
        entry = scheduler._owners[binding.space_id]
        assert not entry.running and entry.continuation is not None
        scheduler.cancel(binding)
        token = entry.cancel
        assert enqueue(scheduler, binding, "new.md")
        assert entry.cancel is token and token.is_set()
        other.release.set()
        scheduler.wait(5)
        assert len(owner.cancelled) == 1 and owner.begins == [None, None]
        assert not scheduler.status(binding).requires_full_sync
        assert scheduler.status(peer).progress.state is SyncStepState.COMPLETE
        assert token.is_set() and entry.cancel is not token and scheduler.pending_count == 0
    finally:
        other.release.set()
        scheduler.shutdown()


@pytest.mark.parametrize("order", ["cancel_first", "enqueue_first", "simultaneous"])
def test_cancel_and_enqueue_from_separate_threads_linearize(tmp_path, order):
    registry, bindings, owners, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    owner = owners["default"]
    owner.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    barrier, first_done = threading.Barrier(3), threading.Event()
    actions = []

    def action(name):
        barrier.wait(5)
        first = "cancel" if order == "cancel_first" else "enqueue"
        if order != "simultaneous" and name != first:
            assert first_done.wait(5)
        with scheduler._condition:
            if name == "cancel":
                scheduler.cancel(binding)
            else:
                assert enqueue(scheduler, binding, "new.md")
            actions.append(name)
        if name == first:
            first_done.set()

    try:
        scheduler.start(startup=True)
        assert owner.entered.wait(3)
        token = scheduler._owners[binding.space_id].cancel
        workers = [launch(lambda: action("cancel")), launch(lambda: action("enqueue"))]
        barrier.wait(5)
        for worker in workers:
            worker.result(5)
        assert token.is_set() and len(owner.handles) == 1
        owner.release.set()
        scheduler.wait(5)
        if actions[-1] == "enqueue":
            assert owner.begins == [None, None]
            assert not scheduler.status(binding).requires_full_sync
        else:
            assert owner.begins == [None] and scheduler.status(binding).paused
            assert scheduler.status(binding).requires_full_sync
            assert scheduler.retry(binding)
            scheduler.wait(5)
            assert owner.begins == [None, None]
        assert scheduler.pending_count == 0
    finally:
        first_done.set()
        owner.release.set()
        scheduler.shutdown()


@pytest.mark.parametrize("terminal", ["cancel", "shutdown"])
def test_later_cancellation_or_shutdown_fences_deferred_wakeup(tmp_path, terminal):
    registry, bindings, owners, _ = setup(tmp_path, 1)
    binding = bindings["default"]
    owner = owners["default"]
    owner.release = threading.Event()
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        assert owner.entered.wait(3)
        scheduler.cancel(binding)
        assert enqueue(scheduler, binding, "new.md")
        if terminal == "cancel":
            scheduler.cancel(binding)
        else:
            closing = launch(scheduler.shutdown)
            with scheduler._condition:
                assert scheduler._condition.wait_for(lambda: scheduler._closed, 3)
        owner.release.set()
        if terminal == "shutdown":
            closing.result(5)
            assert not enqueue(scheduler, binding, "new.md")
            assert not scheduler.retry(binding)
            assert not scheduler.request_full_sync(binding)
        else:
            scheduler.wait(5)
        assert owner.begins == [None] and scheduler.status(binding).paused
        assert scheduler.status(binding).requires_full_sync and scheduler.pending_count == 0
    finally:
        owner.release.set()
        scheduler.shutdown()


@pytest.mark.parametrize("blocking", ["owner_lock", "backend_lease", "backend_call"])
def test_real_shutdown_signals_every_owner_without_waiting_for_locks_or_lease(tmp_path, shared, blocking):
    from tests.test_semantic_resources import ObservedLock

    resource, backend = shared
    registry, bindings, owners, _ = setup(tmp_path, resource=resource, real=True)
    for service in owners.values():
        (service.vault_root / "same.md").write_text("initial", encoding="utf-8")
        service.sync()
    first = owners["default"]
    (first.vault_root / "same.md").write_text("requires changed inference", encoding="utf-8")
    release = threading.Event()
    external = None
    observed = None
    if blocking == "owner_lock":
        observed = ObservedLock(first._sync_lock)
        first._sync_lock = observed
        observed.lock.acquire()
    else:
        backend.entered.clear()
        backend.block = release
        if blocking == "backend_lease":
            external = launch(lambda: resource.embed(("unrelated operation",)))
            assert backend.entered.wait(3)
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        if blocking == "owner_lock":
            assert observed.waiting.wait(3)
        elif blocking == "backend_call":
            assert backend.entered.wait(3)
        else:
            # Preparation has pinned but is waiting for the unrelated lease.
            with resource._condition:
                # operation() does not notify on admission; observed through a
                # controlled pipeline callback instead of scheduling sleeps.
                assert resource._busy
        closing = launch(scheduler.shutdown)
        with scheduler._condition:
            assert scheduler._condition.wait_for(lambda: scheduler._closed, 3)
        assert all(entry.cancel.is_set() for entry in scheduler._owners.values())
        if blocking != "backend_call":
            closing.result(5)  # No need to acquire unrelated lock/lease to cancel.
        else:
            assert not closing.done()
            release.set()
            closing.result(5)
        assert all(service._active_sync is None for service in owners.values())
        assert all(not service._sync_operations for service in owners.values())
        if external is None:
            assert resource._pins == 0 and not resource._busy
    finally:
        if observed is not None:
            observed.lock.release()
        release.set()
        if external is not None:
            external.result(5)
        scheduler.shutdown()
    assert resource._pins == 0 and not resource._busy


def test_scheduler_all_thirteen_evaluation_cases_have_exact_legacy_scores(tmp_path, monkeypatch):
    instances = []

    class LocalBackend(DeterministicConceptEmbedder):
        def __init__(self, *args, **kwargs):
            super().__init__()
            instances.append(self)

        def invalidate_embedding_compatibility_cache(self):
            pass

    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", LocalBackend)
    resource = SharedEmbeddingResource(Settings(semantic_model="vb022/deterministic-concepts",
                                               semantic_data_path=tmp_path / "shared-data"))
    reference, _ = build_service(tmp_path / "reference")
    expected = run_evaluation(reference, load_cases())
    registry, bindings, owners, _ = setup(tmp_path / "scheduled", resource=resource, real=True,
                                         chunk_chars=250, chunk_overlap=0)
    for service in owners.values():
        for source in CORPUS_ROOT.rglob("*.md"):
            target = service.vault_root / source.relative_to(CORPUS_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_text(encoding="utf-8").encode("utf-8"))
    scheduler = SharedSemanticScheduler(registry, bindings.values())
    try:
        scheduler.start(startup=True)
        scheduler.wait(20)
        for service in owners.values():
            actual = run_evaluation(service, load_cases())
            assert actual == expected  # Includes every returned score, not just rank.
            assert len(actual) == 13 and all(outcome.rank == 1 for outcome in actual)
        assert len(instances) == 1
    finally:
        scheduler.shutdown()
        resource.close()
