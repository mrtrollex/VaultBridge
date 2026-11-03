"""E6 private composition, exact ownership and lifecycle integration."""

import json
import sqlite3
import sys
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.repositories.semantic_binding import IndexBindingUnavailableError
from app.services.knowledge_query import KnowledgeQuery
from app.services.knowledge_spaces import (
    AuthorizedSpaceBinding,
    Capability,
    Dialect,
    QualifiedPath,
    ReadScope,
    SpaceError,
    SpaceId,
    SpacePolicyResolver,
    SpaceRegistry,
)
from app.services.scoped_budget import CancellationToken, ScopedBudget
from app.services.scoped_reads import QualifiedRelationshipPredicate, ScopedKnowledgeQuery
from app.services.semantic_search import EmbeddingCompatibilityEvidence, SemanticSearchService
from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
from app.services.space_admission import MAX_WAITING_OPERATIONS, ScopedOperationAdmission
from app.services.space_operations import SpaceOperations
from app.services.space_owners import compose_space_registry
from app.services.space_runtime import RuntimeSpaceIndexLifecycle, compose_space_runtime
from app.services.vault import VaultService
from tests.eval.runner import CORPUS_ROOT, DeterministicConceptEmbedder, build_service, load_cases, run_evaluation
from tests.test_semantic_resources import launch
from tests.test_shared_filesystem_watcher import FakeObserver


@pytest.fixture
def backend(monkeypatch):
    state = SimpleNamespace(instances=[], calls=[], cleaned=0, entered=threading.Event(), release=None)

    class Backend:
        def __init__(self, model, cache, **options):
            state.instances.append((model, cache))

        def resolve_embedding_fingerprint(self):
            return "embedding-v1:" + "0" * 64

        def resolve_embedding_compatibility(self):
            return EmbeddingCompatibilityEvidence(self.resolve_embedding_fingerprint(), 2, "e6-test/v1")

        def validate_embedding_backend(self):
            pass

        def embed(self, texts):
            state.calls.append(tuple(texts))
            if state.release is not None:
                state.entered.set()
                assert state.release.wait(20)
            return [np.array([1, 0], dtype=np.float32) for _ in texts]

        def invalidate_embedding_compatibility_cache(self):
            state.cleaned += 1

    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", Backend)
    return state


def configuration(tmp_path, count=2, *, disabled=(), denied=(), watch=False, capabilities=None):
    records = []
    for name in ["default", *[f"space-{i}" for i in range(1, count)]]:
        root = tmp_path / name
        root.mkdir(parents=True)
        (root / "same.md").write_text(f"# {name}\nIndependent {name} notes", encoding="utf-8")
        records.append(dict(
            space_id=name, root=str(root), semantic_data_path=str(tmp_path / (name + "-data")),
            read="deny" if name in denied else "allow", write="deny",
            indexing="disabled" if name in disabled or name in denied else "enabled",
            dialects=[d.value for d in Dialect],
            capabilities=[c.value for c in Capability] if capabilities is None else capabilities,
        ))
    return Settings(vault_path=records[0]["root"], semantic_data_path=records[0]["semantic_data_path"],
                    knowledge_spaces_json=json.dumps(dict(version=1, spaces=records)),
                    semantic_watch_enabled=watch, semantic_watch_debounce_seconds=0.01,
                    semantic_model="e6-test", semantic_index_batch_size=1)


def binding(runtime, name="default"):
    return runtime.registry._binding(SpaceId(name))


def scope(runtime):
    return ReadScope(tuple(runtime.registry._by_id))


def waiters(gate, count):
    with gate._condition:
        assert gate._condition.wait_for(lambda: gate._waiting == count, 10)


@pytest.mark.parametrize("count,disabled", [(1, ()), (8, ()), (1, ("default",))])
def test_inert_composition_zero_one_eight_enabled(tmp_path, backend, count, disabled, monkeypatch):
    issued = []
    original = SpaceRegistry._binding

    def checked(registry, sid):
        assert len(registry._owners) == len(registry._definitions)
        assert all(o._definition is registry._by_id[key] for key, o in registry._owners.items())
        result = original(registry, sid)
        issued.append(result)
        return result

    monkeypatch.setattr(SpaceRegistry, "_binding", checked)
    runtime = compose_space_runtime(configuration(tmp_path, count, disabled=disabled, watch=True))
    assert backend.instances == [] and backend.calls == []
    assert not list(tmp_path.glob("*-data"))
    owner_map = runtime.registry._owners
    if disabled:
        assert runtime.resource is runtime.scheduler is runtime.watcher is None
        assert all(o.scheduler is None for o in owner_map.values())
    else:
        assert runtime.scheduler._executor is None
        assert runtime.watcher._observer is None
        assert len(runtime.scheduler._owners) == count
        assert all(b._owners is owner_map[b.space_id] for b in issued)
        assert all(o.semantic._shared_resource is runtime.resource for o in owner_map.values())
    runtime.shutdown()
    assert runtime.registry._owners is owner_map
    assert backend.instances == []


def test_fully_disabled_never_constructs_resources(tmp_path, monkeypatch):
    settings = configuration(tmp_path, 8, disabled=("default", *[f"space-{i}" for i in range(1, 8)]), watch=True)

    def forbidden(*args, **kwargs):
        pytest.fail("disabled indexing allocated a semantic resource")

    for name in ("SharedEmbeddingResource", "SharedSemanticScheduler", "SharedFilesystemWatcher"):
        monkeypatch.setattr("app.services.space_runtime." + name, forbidden)
    with compose_space_runtime(settings) as runtime:
        assert len(runtime.operations.scoped_list(scope(runtime)).items) == 8
        with pytest.raises(SpaceError, match="indexing_disabled"):
            runtime.operations.scoped_semantic_search("notes", scope(runtime))
    assert not list(tmp_path.glob("*-data"))


@pytest.mark.parametrize("count", [1, 8])
def test_real_independent_indexes_shared_model_and_scoped_queries(tmp_path, backend, count):
    settings = configuration(tmp_path, count)
    with compose_space_runtime(settings) as runtime:
        runtime.scheduler.wait(20)
        assert len(backend.instances) == 1
        assert backend.instances[0][1] == settings.semantic_data_path / "models"
        assert runtime.resource._backend is not None
        result = runtime.operations.scoped_semantic_search("Independent notes", scope(runtime), limit=100)
        assert {item.identity.space_id for item in result.items} == set(runtime.registry._by_id)
        query = ScopedKnowledgeQuery(scope(runtime), KnowledgeQuery(semantic_text="Independent notes"))
        result = runtime.operations.scoped_query(query)
        assert len(result.items) == count and result.state == "complete"
        assert runtime.admission._active_thread is None
        for sid, owners in runtime.registry._owners.items():
            assert owners.semantic.cache_dir == runtime.resource.cache_dir
            assert owners.semantic.db_path.parent == owners._definition.semantic_data_binding
            chunks = owners.semantic.repository.load_chunks()
            assert chunks and all(sid.value in c.content for c in chunks)
            assert owners.scheduler._attachment[0] is runtime
            assert runtime.registry._accepts(owners.scheduler._attachment[1])
    assert runtime.resource._closed and backend.cleaned == 1
    assert runtime.resource._pins == 0 and not runtime.resource._busy
    assert runtime.scheduler._executor._shutdown


def test_exact_owner_grants_and_single_assignment(tmp_path, backend):
    settings = configuration(tmp_path)
    inspection = compose_space_registry(settings)
    runtimes = [compose_space_runtime(settings), compose_space_runtime(settings)]
    first, second = runtimes
    try:
        final = binding(first)
        for foreign in (binding(second), inspection._binding(final.space_id)):
            assert not first.registry._accepts(foreign)
            with pytest.raises(SpaceError, match="invalid_scope"):
                first.operations._owners(foreign)
            with pytest.raises(SpaceError, match="invalid_scope"):
                first.scheduler.status(foreign)
        forged = AuthorizedSpaceBinding._issue(first.registry._by_id[final.space_id],
                                                binding(second)._owners, first.registry._token)
        assert not first.registry._accepts(forged)
        assert first.operations._owners(final) is final._owners
        handle = final._owners.scheduler
        with pytest.raises(RuntimeError, match="already finalized"):
            handle._attach(second, binding(second))
        with pytest.raises(AttributeError):
            final._owners.scheduler = None
        with pytest.raises(AttributeError):
            first.registry = second.registry
        unbound = RuntimeSpaceIndexLifecycle()
        with pytest.raises(SpaceError, match="unavailable_space"):
            unbound.request_full_sync()
        with pytest.raises(SpaceError, match="invalid_scope"):
            unbound._attach(first, final)
        with pytest.raises(SpaceError, match="invalid_scope"):
            handle.enqueue(QualifiedPath(SpaceId("space-1"), "same.md"))
    finally:
        for runtime in runtimes:
            runtime.shutdown()
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        first.start()
    assert not final._owners.scheduler.request_full_sync()


def test_mixed_policies_and_full_authorization_precedes_admission(tmp_path, backend, monkeypatch):
    settings = configuration(tmp_path, 3, disabled=("space-1",), denied=("space-2",))
    runtime = compose_space_runtime(settings)
    touched = []
    monkeypatch.setattr(VaultService, "scoped_session", lambda *a, **k: touched.append(1))
    # Still closed: policy errors must win without entering admission or owners.
    for selected, category in [(scope(runtime), "unknown_or_denied_space"),
                               (ReadScope((SpaceId("space-1"),)), "indexing_disabled"),
                               (ReadScope((SpaceId("missing"),)), "unknown_or_denied_space")]:
        with pytest.raises(SpaceError, match=category):
            runtime.operations.scoped_semantic_search("notes", selected)
    assert touched == [] and backend.instances == []
    assert len(runtime.scheduler._owners) == 1
    assert binding(runtime, "space-1")._owners.scheduler is None
    runtime.shutdown()


@pytest.mark.parametrize("missing", [Capability.RELATIONSHIPS, Capability.SEMANTIC_RETRIEVAL])
def test_supplementary_query_authorization_before_closed_gate(tmp_path, backend, missing):
    caps = [c.value for c in Capability if c is not missing]
    runtime = compose_space_runtime(configuration(tmp_path, capabilities=caps))
    predicates = (() if missing is Capability.SEMANTIC_RETRIEVAL else (
        QualifiedRelationshipPredicate("outgoing", QualifiedPath(SpaceId("default"), "same.md")),
    ))
    request = ScopedKnowledgeQuery(scope(runtime), KnowledgeQuery(semantic_text="notes"), relationships=predicates)
    try:
        with pytest.raises(SpaceError, match="unsupported_capability"):
            runtime.operations.scoped_query(request)
        assert runtime.admission._active_thread is None and backend.instances == []
    finally:
        runtime.shutdown()


def test_semantic_query_enters_admission_once_and_keeps_limits(tmp_path, backend, monkeypatch):
    with compose_space_runtime(configuration(tmp_path)) as runtime:
        runtime.scheduler.wait(20)
        count = 0
        enter = runtime.admission.operation

        @contextmanager
        def counted(cancel=None):
            nonlocal count
            count += 1
            with enter(cancel) as token:
                yield token

        monkeypatch.setattr(runtime.admission, "operation", counted)
        request = ScopedKnowledgeQuery(scope(runtime), KnowledgeQuery(semantic_text="notes"))
        result = runtime.operations.scoped_query(request, limits={"paths": (0, 0)})
        assert not result.items
        assert all("path_limit" in row.reasons for row in result.coverage)
        assert count == 1


def test_operations_closed_before_start_and_after_shutdown(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path))
    with pytest.raises(SpaceError, match="unavailable_space"):
        runtime.operations.scoped_list()
    runtime.shutdown()
    with pytest.raises(SpaceError, match="operation_cancelled"):
        runtime.operations.read_note(QualifiedPath(SpaceId("default"), "same.md"))


def test_gate_serializes_and_cancels_waiting_call_without_owner_work(tmp_path, backend, monkeypatch):
    with compose_space_runtime(configuration(tmp_path)) as runtime:
        runtime.scheduler.wait(20)
        entered, release = threading.Event(), threading.Event()
        read = VaultService.read_note
        calls = []

        def blocked(owner, path):
            calls.append(path)
            entered.set()
            assert release.wait(20)
            return read(owner, path)

        monkeypatch.setattr(VaultService, "read_note", blocked)
        path = QualifiedPath(SpaceId("default"), "same.md")
        first = launch(lambda: runtime.operations.read_note(path))
        assert entered.wait(10)
        token = CancellationToken()
        second = launch(lambda: runtime.operations.read_note(path, cancel=token))
        try:
            waiters(runtime.admission, 1)
            token.cancel()
            with pytest.raises(SpaceError, match="operation_cancelled"):
                second.result(10)
            assert calls == ["same.md"]
        finally:
            release.set()
        assert first.result(10).note.path == "same.md"


def test_gate_bound_and_no_nested_deadlock():
    gate = ScopedOperationAdmission()
    gate.open()
    release, entered = threading.Event(), threading.Event()

    def active():
        with gate.operation():
            with pytest.raises(RuntimeError, match="Nested"):
                with gate.operation():
                    pytest.fail("nested gate admitted")
            entered.set()
            assert release.wait(20)

    first = launch(active)
    assert entered.wait(10)
    queued = [launch(lambda: next_operation(gate)) for _ in range(MAX_WAITING_OPERATIONS)]
    try:
        waiters(gate, MAX_WAITING_OPERATIONS)
        with pytest.raises(SpaceError, match="unavailable_space"):
            next_operation(gate)
        gate.begin_shutdown()
        for task in queued:
            with pytest.raises(SpaceError, match="operation_cancelled"):
                task.result(10)
    finally:
        gate.begin_shutdown()
        release.set()
    with pytest.raises(SpaceError, match="operation_cancelled"):
        first.result(10)
    gate.drain()


def next_operation(gate):
    with gate.operation():
        pass


def test_shutdown_cancels_active_read_and_wakes_waiters(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    entered, release = threading.Event(), threading.Event()
    read = VaultService.read_note

    def blocked(owner, path):
        entered.set()
        assert release.wait(20)
        return read(owner, path)

    monkeypatch.setattr(VaultService, "read_note", blocked)
    path = QualifiedPath(SpaceId("default"), "same.md")
    first = launch(lambda: runtime.operations.read_note(path))
    assert entered.wait(10)
    second = launch(lambda: runtime.operations.scoped_list())
    waiters(runtime.admission, 1)
    closing = launch(runtime.shutdown)
    other_close = launch(runtime.shutdown)
    try:
        with pytest.raises(SpaceError, match="operation_cancelled"):
            second.result(10)
        assert not closing.done() and not other_close.done()
        assert not runtime.resource._closed
    finally:
        release.set()
    with pytest.raises(SpaceError, match="operation_cancelled"):
        first.result(10)
    closing.result(10)
    other_close.result(10)
    assert runtime._state == "stopped"


@pytest.mark.parametrize("scoped", [False, True])
def test_shutdown_drains_backend_boundary_not_eight_syncs(tmp_path, backend, scoped):
    runtime = compose_space_runtime(configuration(tmp_path, 8))
    if scoped:
        runtime.start()
        runtime.scheduler.wait(20)
    backend.release = threading.Event()
    if scoped:
        active = launch(lambda: runtime.operations.scoped_semantic_search("notes", scope(runtime)))
    else:
        active = None
        runtime.start()
    assert backend.entered.wait(10)
    calls = len(backend.calls)
    closing = launch(runtime.shutdown)
    try:
        assert runtime.admission._stopped.wait(10)
        # Every E4 cancellation is signaled before drain, including queued owners.
        with runtime.scheduler._condition:
            assert runtime.scheduler._condition.wait_for(lambda: runtime.scheduler._closed, 10)
            assert all(e.cancel.is_set() for e in runtime.scheduler._owners.values())
        assert not closing.done() and not runtime.resource._closed
    finally:
        backend.release.set()
    if active is not None:
        with pytest.raises(SpaceError, match="operation_cancelled"):
            active.result(20)
    closing.result(20)
    assert len(backend.calls) == calls
    assert runtime.resource._pins == 0 and not runtime.resource._busy and runtime.resource._closed
    assert all(e.service._active_sync is None for e in runtime.scheduler._owners.values())


@pytest.mark.parametrize("failure", [IndexBindingUnavailableError, PermissionError])
def test_constructor_failure_is_fatal_and_rolls_back_shells(tmp_path, backend, monkeypatch, failure):
    settings = configuration(tmp_path)
    close = SemanticSearchService.close_synchronization
    closed = []
    init = SemanticRepository.__init__

    def fail(repository, *args, **kwargs):
        if kwargs.get("_expected_binding") is not None:
            raise failure()
        init(repository, *args, **kwargs)

    def record(service):
        closed.append(service)
        close(service)

    monkeypatch.setattr(SemanticRepository, "__init__", fail)
    monkeypatch.setattr(SemanticSearchService, "close_synchronization", record)
    with pytest.raises(SpaceError, match="^invalid_configuration$"):
        compose_space_runtime(settings)
    assert closed and all(o._sync_closed for o in closed)
    assert backend.instances == [] and not list(tmp_path.glob("*-data"))


def test_index_content_failure_after_construction_is_owner_local_non_destructive(tmp_path, backend):
    settings = configuration(tmp_path)
    data = tmp_path / "space-1-data"
    data.mkdir()
    db = data / "semantic-index.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE private_content(value TEXT)")
        connection.execute("INSERT INTO private_content VALUES('must survive')")
    before = db.read_bytes()
    with compose_space_runtime(settings) as runtime:
        runtime.scheduler.wait(20)
        broken = binding(runtime, "space-1")
        assert runtime.scheduler.status(broken).paused
        assert runtime.scheduler.status(broken).error_type is not None
        assert not runtime.scheduler.status(binding(runtime)).paused
        result = runtime.operations.scoped_semantic_search("notes", scope(runtime))
        assert result.state == "partial"
        assert {item.identity.space_id.value for item in result.items} == {"default"}
        assert db.read_bytes() == before


@pytest.mark.parametrize("failure", ["watcher", "scheduler"])
def test_startup_failure_rolls_back_before_admission(tmp_path, backend, monkeypatch, failure):
    runtime = compose_space_runtime(configuration(tmp_path, watch=True))
    fake = FakeObserver(fail_start=failure == "watcher")
    runtime.watcher._observer_factory = lambda: fake
    if failure == "scheduler":
        def fail(**kwargs):
            raise RuntimeError("scheduler startup injected")
        monkeypatch.setattr(runtime.scheduler, "start", fail)
    with pytest.raises(RuntimeError):
        runtime.start()
    assert runtime._state == "stopped" and runtime.resource._closed
    assert fake.stopped and fake.joined and fake.unscheduled
    assert runtime.watcher._observer is None
    with pytest.raises(SpaceError, match="operation_cancelled"):
        runtime.operations.scoped_list()
    runtime.shutdown()


def test_cleanup_failure_retains_generation_and_retry(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path, watch=True))

    class Observer(FakeObserver):
        fail = True

        def unschedule_all(self):
            if self.fail:
                raise RuntimeError("native disposal injected")
            super().unschedule_all()

    observer = Observer()
    allocations = []
    runtime.watcher._observer_factory = lambda: (allocations.append(observer) or observer)
    runtime.start()
    runtime.scheduler.wait(20)
    token = runtime.watcher._token
    with pytest.raises(RuntimeError, match="native disposal"):
        runtime.shutdown()
    assert runtime.watcher._observer is observer
    assert runtime.scheduler._watcher_token is token
    assert runtime._state == "stopping"
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        runtime.start()
    observer.fail = False
    runtime.shutdown()
    assert runtime.watcher._observer is None and runtime.scheduler._watcher_token is None
    assert len(allocations) == 1 and runtime._state == "stopped"


@pytest.mark.skipif(sys.platform != "win32" and not sys.platform.startswith("linux"), reason="native platform")
def test_native_runtime_readiness_incremental_delivery_and_shutdown(tmp_path, backend, monkeypatch):
    indexed = threading.Event()
    step = SemanticSearchService.sync_step

    def observe(service, *args, **kwargs):
        result = step(service, *args, **kwargs)
        if result.indexed and any(c.path == "native.md" for c in service.repository.load_chunks()):
            indexed.set()
        return result

    monkeypatch.setattr(SemanticSearchService, "sync_step", observe)
    runtime = compose_space_runtime(configuration(tmp_path, watch=True))
    try:
        runtime.start()  # E5 returns only AFTER acknowledged native readiness.
        runtime.scheduler.wait(20)
        assert runtime.watcher._producers and runtime.watcher._observer.is_alive()
        assert all(not runtime.scheduler.status(b).requires_full_sync for b in runtime.watcher._bindings.values())
        root = binding(runtime)._owners.vault.vault_root
        (root / "native.md").write_text("# native\nNative change after runtime readiness", encoding="utf-8")
        assert indexed.wait(20)
    finally:
        runtime.shutdown()
    assert runtime.watcher._observer is None and runtime.watcher._thread is None
    assert runtime.scheduler._watcher_token is None and runtime.resource._closed


def test_inspection_factory_and_legacy_operations_remain_nonstarting(tmp_path, backend):
    registry = compose_space_registry(configuration(tmp_path))
    operations = SpaceOperations(SpacePolicyResolver(registry))
    assert operations.read_note(QualifiedPath(SpaceId("default"), "same.md")).note.path == "same.md"
    assert len(operations.scoped_list().items) == 1
    assert backend.instances == [] and not list(tmp_path.glob("*-data"))


def test_shutdown_interrupts_partial_native_start_and_never_opens_admission(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, watch=True))
    entered = threading.Event()
    original = SharedFilesystemWatcher.start

    def paused(watcher):
        entered.set()
        assert runtime.admission._stopped.wait(20)
        return original(watcher)

    monkeypatch.setattr(SharedFilesystemWatcher, "start", paused)
    starting = launch(runtime.start)
    assert entered.wait(10)
    closing = launch(runtime.shutdown)
    with pytest.raises(RuntimeError):
        starting.result(20)
    closing.result(20)
    assert runtime._state == "stopped" and runtime.watcher._observer is None
    assert backend.instances == []


def test_shutdown_during_scoped_read_releases_session_and_pins(tmp_path, backend, monkeypatch):
    from app.services._scoped_vault import ScopedVaultSession

    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    read, exit_session = ScopedVaultSession.read, ScopedVaultSession.__exit__

    def blocked(session, *args, **kwargs):
        entered.set()
        assert release.wait(20)
        return read(session, *args, **kwargs)

    def closed(session, *args):
        try:
            return exit_session(session, *args)
        finally:
            exited.set()

    monkeypatch.setattr(ScopedVaultSession, "read", blocked)
    monkeypatch.setattr(ScopedVaultSession, "__exit__", closed)
    active = launch(lambda: runtime.operations.scoped_semantic_search("notes", scope(runtime)))
    assert entered.wait(10)
    stopping = launch(runtime.shutdown)
    try:
        assert runtime.admission._stopped.wait(10)
        assert not stopping.done() and not runtime.resource._closed
    finally:
        release.set()
    with pytest.raises(SpaceError, match="operation_cancelled"):
        active.result(20)
    stopping.result(20)
    assert exited.is_set() and runtime.resource._pins == 0


def test_shutdown_after_durable_e3_batch_does_not_dispatch_next_owner(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 8))
    entered, release = threading.Event(), threading.Event()
    step = SemanticSearchService.sync_step
    turns = []

    def barrier(service, *args, **kwargs):
        result = step(service, *args, **kwargs)
        turns.append(service)
        if result.indexed:
            entered.set()
            assert release.wait(20)
        return result

    monkeypatch.setattr(SemanticSearchService, "sync_step", barrier)
    runtime.start()
    assert entered.wait(10)
    durable = binding(runtime)._owners.semantic.repository.load_chunks()
    assert durable
    before = len(turns)
    stopping = launch(runtime.shutdown)
    try:
        with runtime.scheduler._condition:
            assert runtime.scheduler._condition.wait_for(lambda: runtime.scheduler._closed, 10)
        assert not stopping.done()
    finally:
        release.set()
    stopping.result(20)
    assert len(turns) == before
    assert turns[-1] is binding(runtime)._owners.semantic
    assert all(not o.semantic.repository.load_chunks() for sid, o in runtime.registry._owners.items()
               if sid.value != "default")
    assert runtime.resource._closed


def test_native_cleanup_retry_on_same_runtime_generation(tmp_path, backend, monkeypatch):
    """Retain an actual native handle/fd on failed disposal, then retry it."""
    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True)).start()
    runtime.scheduler.wait(20)
    watcher = runtime.watcher
    producer = watcher._producers[0]
    token = watcher._token
    observer = watcher._observer
    try:
        if sys.platform == "win32":
            import ctypes

            prepare = producer._directory_resources._prepare_handle
            retained = producer._whandle

            def denied(value):
                if value == retained:
                    raise ctypes.WinError(5)
                return prepare(value)

            hook = threading.excepthook

            def expected(args):
                if args.thread is producer and isinstance(args.exc_value, OSError):
                    return
                hook(args)

            with monkeypatch.context() as patch:
                patch.setattr(threading, "excepthook", expected)
                # Certified read-only failure before native close dispatch.
                patch.setattr(producer._directory_resources, "_prepare_handle", denied)
                with pytest.raises(OSError):
                    runtime.shutdown()
                assert producer._whandle == retained
        elif sys.platform.startswith("linux"):
            import os

            native = producer._inotify._inotify
            close = native._close_resources

            def denied():
                raise OSError("native cleanup injected")

            retained = native._inotify_fd
            hook = threading.excepthook

            def expected(args):
                if isinstance(args.exc_value, OSError) and str(args.exc_value) == "native cleanup injected":
                    return
                hook(args)

            with monkeypatch.context() as patch:
                patch.setattr(threading, "excepthook", expected)
                patch.setattr(native, "_close_resources", denied)
                # E5 observes resources still owned even if emitter stop catches
                # the original disposal error in its reader thread.
                with pytest.raises((RuntimeError, OSError)):
                    runtime.shutdown()
                os.fstat(retained)  # Kernel fd is still open, not just a Python flag.
            assert close is not None
        else:
            pytest.skip("native platform")
        assert watcher._observer is observer and runtime.scheduler._watcher_token is token
        assert runtime._state == "stopping"
        with pytest.raises(RuntimeError, match="cannot be restarted"):
            runtime.start()
    finally:
        runtime.shutdown()
    assert watcher._observer is None and watcher._thread is None
    assert runtime.scheduler._watcher_token is None and runtime.resource._closed


def test_native_readiness_and_coverage_precede_worker_and_operation_open(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True))
    ready, release = threading.Event(), threading.Event()
    native_start = SharedFilesystemWatcher.start

    def started(watcher):
        native_start(watcher)
        ready.set()  # Acknowledged native coverage, before E4 starts.
        assert release.wait(20)

    monkeypatch.setattr(SharedFilesystemWatcher, "start", started)
    starting = launch(runtime.start)
    try:
        assert ready.wait(10)
        assert runtime.scheduler._executor is None and not runtime.admission._open
        assert runtime.scheduler.status(binding(runtime)).requires_full_sync
        with pytest.raises(SpaceError, match="unavailable_space"):
            runtime.operations.scoped_list()
        root = binding(runtime)._owners.vault.vault_root
        (root / "before-worker.md").write_text("during first attachment", encoding="utf-8")
    finally:
        release.set()
    try:
        starting.result(20)
        runtime.scheduler.wait(20)
        assert {c.path for c in binding(runtime)._owners.semantic.repository.load_chunks()} == {
            "same.md", "before-worker.md",
        }
        assert not runtime.scheduler.status(binding(runtime)).requires_full_sync
    finally:
        runtime.shutdown()


def test_invalid_configuration_is_rejected_before_resource_construction(tmp_path, monkeypatch):
    settings = configuration(tmp_path)
    records = json.loads(settings.knowledge_spaces_json)
    records["spaces"][1]["root"] = records["spaces"][0]["root"]
    settings = Settings(vault_path=settings.vault_path, semantic_data_path=settings.semantic_data_path,
                        knowledge_spaces_json=json.dumps(records))

    def forbidden(*args):
        pytest.fail("structural validation must precede resource allocation")

    monkeypatch.setattr("app.services.space_runtime.SharedEmbeddingResource", forbidden)
    with pytest.raises(SpaceError, match="invalid_configuration"):
        compose_space_runtime(settings)
    assert not list(tmp_path.glob("*-data"))


def test_runtime_all_thirteen_cases_preserve_exact_legacy_scores_and_best_chunks(tmp_path, monkeypatch):
    instances = []

    class Backend(DeterministicConceptEmbedder):
        def resolve_embedding_compatibility(self):
            return EmbeddingCompatibilityEvidence(self.resolve_embedding_fingerprint(),
                                                  len(self.embed(("probe",))[0]), "concepts/v1")

        def validate_embedding_backend(self):
            pass

        def invalidate_embedding_compatibility_cache(self):
            pass

    class SharedBackend(Backend):
        def __init__(self, *args, **kwargs):
            super().__init__()
            instances.append(self)

    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", SharedBackend)
    settings = configuration(tmp_path / "runtime").model_copy(update={
        "semantic_model": "vb022/deterministic-concepts", "semantic_chunk_chars": 250, "semantic_chunk_overlap": 0,
    })
    runtime = compose_space_runtime(settings)
    for owners in runtime.registry._owners.values():
        (owners.vault.vault_root / "same.md").unlink()
        for source in CORPUS_ROOT.rglob("*.md"):
            target = owners.vault.vault_root / source.relative_to(CORPUS_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_text(encoding="utf-8").encode("utf-8"))
    reference, _ = build_service(tmp_path / "reference", embedder=Backend())
    expected = run_evaluation(reference, load_cases())
    reference_id = SpaceId("default")
    eligible = tuple(sorted(p.relative_to(CORPUS_ROOT).as_posix() for p in CORPUS_ROOT.rglob("*.md")))
    # D2/D3 retain full precision; legacy search rounds only its public payload.
    # Use the unchanged D2 primitive on an independent legacy index as the oracle.
    precise = {
        outcome.case.case_id: reference.search_federation_candidates(
            outcome.case.query, eligible, space_id=reference_id, limit=5,
            budget=ScopedBudget((reference_id,)), cancel=CancellationToken(),
        ).candidates[:5]
        for outcome in expected
    }
    with runtime:
        runtime.scheduler.wait(20)
        for sid, owners in runtime.registry._owners.items():
            assert run_evaluation(owners.semantic, load_cases()) == expected
            selected = ReadScope((sid,))
            for outcome in expected:
                result = runtime.operations.scoped_semantic_search(outcome.case.query, selected)
                query = runtime.operations.scoped_query(ScopedKnowledgeQuery(
                    selected, KnowledgeQuery(semantic_text=outcome.case.query, limit=5),
                ))
                for actual in (result, query):
                    assert [(r.identity.canonical_relative_path, r.score, r.semantic_score,
                             r.lexical_score, r.heading, r.snippet) for r in actual.items] == [
                        (r.path, r.score, r.semantic_score, r.lexical_score, r.heading, r.snippet)
                        for r in precise[outcome.case.case_id]
                    ]
        assert len(instances) == 1 and len(expected) == 13
