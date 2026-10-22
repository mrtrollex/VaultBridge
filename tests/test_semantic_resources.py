"""E2 shared backend lifecycle, concurrency and independent owner integration."""

import threading
import time
import weakref
from collections import Counter
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import replace

import numpy as np
import pytest

from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.repositories.semantic_binding import ExpectedIndexBinding, IndexBindingUnavailableError
from app.services.knowledge_spaces import SpaceError, SpaceId
from app.services.scoped_budget import CancellationToken, ScopedBudget
from app.services.semantic_resources import SharedEmbeddingResource
from app.services.semantic_search import (
    EmbeddingCompatibilityEvidence,
    FastEmbedder,
    SemanticIndexOperationError,
    SemanticSearchService,
    SemanticSearchUnavailableError,
    SynchronizationCancelledError,
)
from tests.eval.runner import (
    DeterministicConceptEmbedder,
    build_service,
    calculate_group_metrics,
    load_baseline,
    load_cases,
    metrics_payload,
    run_evaluation,
)


def launch(call):
    future = Future()

    def run():
        try:
            future.set_result(call())
        except BaseException as exc:
            future.set_exception(exc)

    threading.Thread(target=run, daemon=True).start()
    return future


class BackendState:
    def __init__(self):
        self.lock = threading.Lock()
        self.depths = Counter()
        self.maximum = 0
        self.instances = []
        self.calls = Counter()
        self.fingerprint = "embedding-v1:" + "0" * 64
        self.block = None
        self.entered = threading.Event()
        self.fail = False
        self.fail_stage = None

    @contextmanager
    def operation(self, name):
        tid = threading.get_ident()
        with self.lock:
            self.depths[tid] += 1
            self.maximum = max(self.maximum, len(self.depths))
            self.calls[name] += 1
        try:
            time.sleep(0.002)
            if name == self.fail_stage:
                raise SemanticIndexOperationError("fake stage failure")
            yield
        finally:
            with self.lock:
                self.depths[tid] -= 1
                if not self.depths[tid]:
                    del self.depths[tid]


@pytest.fixture
def shared(tmp_path, monkeypatch):
    state = BackendState()

    class Backend:
        def __init__(self, model_name, cache_dir, **kwargs):
            with state.operation("init"):
                state.instances.append((self, model_name, cache_dir, kwargs))

        def resolve_embedding_fingerprint(self):
            with state.operation("fingerprint"):
                return state.fingerprint

        def resolve_embedding_compatibility(self):
            with state.operation("compatibility"):
                return EmbeddingCompatibilityEvidence(state.fingerprint, 2, "test/v1")

        def validate_embedding_backend(self):
            with state.operation("validate"):
                self.embed(("probe",))  # Nested backend inference must not reacquire lease.

        def embed(self, texts):
            with state.operation("embed"):
                if state.block is not None:
                    state.entered.set()
                    assert state.block.wait(5)
                if state.fail:
                    raise SemanticIndexOperationError("fake failure")
                return [np.array([1, 0], dtype=np.float32) for _ in texts]

        def invalidate_embedding_compatibility_cache(self):
            with state.operation("invalidate"):
                pass

    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", Backend)
    settings = Settings(
        api_key="test", vault_path=tmp_path / "default-root",
        semantic_data_path=tmp_path / "default-data", semantic_model="test/model",
        semantic_embed_batch_size=3, semantic_onnx_cpu_mem_arena=False,
    )
    resource = SharedEmbeddingResource(settings)
    yield resource, state
    resource.close()


def owner(tmp_path, resource, sid="research", **kwargs):
    root = tmp_path / sid
    root.mkdir(exist_ok=True)
    (root / "a.md").write_text(f"{sid} independent text", encoding="utf-8")
    return SemanticSearchService(
        vault_root=root,
        repository=SemanticRepository(
            tmp_path / f"{sid}-data" / "index.sqlite3",
            _expected_binding=ExpectedIndexBinding(sid, root),
        ),
        model_name=resource.model_name, shared_resource=resource, **kwargs,
    )


def prepare(service, sid="research", cancel=None):
    sid = SpaceId(sid)
    cancel = cancel or CancellationToken()
    budget = ScopedBudget((sid,))
    prepared = service.prepare_federation_candidates(
        ("a.md",), space_id=sid, budget=budget, cancel=cancel,
    )
    return prepared, dict(space_id=sid, budget=budget, cancel=cancel)


def test_lazy_shared_owners_keep_storage_state_and_settings_independent(shared, tmp_path):
    resource, state = shared
    first = owner(tmp_path, resource)
    second = owner(tmp_path, resource, "work", chunk_chars=300)
    assert not state.instances
    assert not resource.cache_dir.exists()
    assert first._sync_lock is not second._sync_lock
    assert first._configuration_lock is not second._configuration_lock
    first.sync()
    assert not second.db_path.exists()
    second.sync()
    assert len(state.instances) == 1
    _, model, cache, options = state.instances[0]
    assert (model, cache) == (resource.model_name, tmp_path / "default-data" / "models")
    assert options == {"batch_size": 3, "enable_cpu_mem_arena": False}
    assert not (tmp_path / "default-data" / "semantic-index.sqlite3").exists()
    assert first.index_signature != second.index_signature
    assert first.repository.load_chunks()[0].content != second.repository.load_chunks()[0].content
    first.rebuild()
    first.sync_paths(("a.md",))
    assert first.search("independent")[0].path == "a.md"
    assert second.search("independent")[0].path == "a.md"
    assert second.repository.get_metadata("index_state") == "ready"


def test_concurrent_initialization_fingerprint_validation_and_inference(shared):
    resource, state = shared
    barrier = threading.Barrier(17)
    methods = [
        resource.resolve_embedding_fingerprint, resource.resolve_embedding_compatibility,
        resource.validate_embedding_backend, lambda: resource.embed(("query",)),
    ]

    def call(index):
        barrier.wait(3)
        return methods[index % len(methods)]()

    futures = [launch(lambda i=i: call(i)) for i in range(16)]
    barrier.wait(3)
    for future in futures:
        future.result(5)
    assert len(state.instances) == 1
    assert state.maximum == 1
    assert state.calls["validate"] == 4


def test_concurrent_independent_owner_sync_and_search(shared, tmp_path):
    resource, state = shared
    services = [owner(tmp_path, resource, sid) for sid in ("research", "work", "notes")]
    for future in [launch(service.sync) for service in services]:
        future.result(5)
    for future in [launch(lambda s=s: s.search("text")) for s in services]:
        assert future.result(5)[0].path == "a.md"
    assert len(state.instances) == state.maximum == 1


def test_generation_rejects_prepared_and_revalidates_all_owners(shared, tmp_path):
    resource, state = shared
    first, second = [owner(tmp_path, resource, sid) for sid in ("research", "work")]
    first.sync()
    second.sync()
    old, args = prepare(first)
    prepare(second, "work")
    generation = resource.generation
    resource.invalidate_embedding_compatibility_cache()
    assert resource.generation == generation + 1
    assert not first.probe_search_availability()
    assert not second.probe_search_availability()
    before = state.calls["embed"]
    with pytest.raises(SemanticSearchUnavailableError, match="stale"):
        first.search_federation_candidates("query", prepared=old, **args)
    assert state.calls["embed"] == before
    for service, sid in ((first, "research"), (second, "work")):
        fresh, args = prepare(service, sid)
        assert fresh.resource_generation == resource.generation
        assert service.search_federation_candidates("query", prepared=fresh, **args).candidates
    state.fingerprint = "embedding-v1:" + "1" * 64
    resource.invalidate_embedding_compatibility_cache()
    for service, sid in ((first, "research"), (second, "work")):
        with pytest.raises(SemanticSearchUnavailableError):
            prepare(service, sid)
    assert len(state.instances) == 1


def test_invalidation_drains_generation_without_holding_backend_lease(shared):
    resource, state = shared
    pinned = threading.Event()
    release = threading.Event()

    def pipeline():
        with resource.operation():
            resource.resolve_embedding_fingerprint()
            pinned.set()
            assert release.wait(5)

    active = launch(pipeline)
    assert pinned.wait(3)
    # Another owner can enter the backend while the first does non-backend work.
    launch(lambda: resource.embed(("other",))).result(3)
    invalidation = launch(resource.invalidate_embedding_compatibility_cache)
    try:
        time.sleep(0.06)
        assert not invalidation.done()
        assert not state.calls["invalidate"]
    finally:
        release.set()
    active.result(3)
    invalidation.result(3)
    assert resource.generation == 1
    assert state.maximum == 1


@pytest.mark.parametrize("phase", ["scoring", "preparation"])
def test_owner_pipeline_pins_generation_through_sensitive_boundaries(shared, tmp_path, monkeypatch, phase):
    resource, state = shared
    service = owner(tmp_path, resource)
    entered, release = threading.Event(), threading.Event()
    if phase == "scoring":
        service.sync()
        target, name, call = service, "_rank_chunks", lambda: service.search("query")
    else:
        target, name, call = service.repository, "prepare_index", service.sync
    original = getattr(target, name)

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, blocked)
    active = launch(call)
    assert entered.wait(3)
    generation = resource.generation
    invalidation = launch(resource.invalidate_embedding_compatibility_cache)
    try:
        time.sleep(0.06)
        assert not invalidation.done()
        assert resource.generation == generation
        assert not state.calls["invalidate"]
    finally:
        release.set()
    active.result(3)
    invalidation.result(3)
    assert resource.generation == generation + 1


@pytest.mark.parametrize("scoped", [False, True])
def test_owner_waiting_cancellation_does_not_release_active_lease(shared, tmp_path, scoped):
    resource, state = shared
    service = owner(tmp_path, resource)
    service.sync()
    prepared, args = prepare(service)
    state.block = threading.Event()
    active = launch(lambda: resource.embed(("active owner",)))
    assert state.entered.wait(3)
    if scoped:
        waiting = launch(lambda: service.search_federation_candidates("query", prepared=prepared, **args))
        cancel = args["cancel"].cancel
        error = SpaceError
    else:
        event = threading.Event()
        (service.vault_root / "a.md").write_text("changed note requiring inference", encoding="utf-8")
        waiting = launch(lambda: service.sync(event))
        cancel = event.set
        error = SynchronizationCancelledError
    try:
        time.sleep(0.06)
        cancel()
        with pytest.raises(error):
            waiting.result(3)
        assert not active.done()
        assert state.maximum == 1
    finally:
        state.block.set()
    active.result(3)
    state.block = None
    assert resource.embed(("after cancellation",))


def test_failure_retry_and_validation_before_destructive_prepare(shared, tmp_path):
    resource, state = shared
    service = owner(tmp_path, resource)
    service.sync()
    old_signature = service.repository.get_metadata("index_signature")
    old_chunks = service.repository.load_chunks()
    state.fingerprint = "embedding-v1:" + "1" * 64
    resource.invalidate_embedding_compatibility_cache()
    state.fail = True
    with pytest.raises(SemanticIndexOperationError):
        service.sync()
    assert service.repository.get_metadata("index_signature") == old_signature
    assert service.repository.load_chunks() == old_chunks
    state.fail = False
    service.sync()
    assert service.index_signature != old_signature
    assert service.search("text")
    assert len(state.instances) == state.maximum == 1


def test_close_rejects_waiters_drains_active_and_is_idempotent(shared):
    resource, state = shared
    state.block = threading.Event()
    active = launch(lambda: resource.embed(("active",)))
    assert state.entered.wait(3)
    waiting = launch(resource.resolve_embedding_fingerprint)
    closing = launch(resource.close)
    try:
        with pytest.raises(SemanticSearchUnavailableError):
            waiting.result(3)
        assert not closing.done()
        assert not state.calls["invalidate"]
        with pytest.raises(SemanticSearchUnavailableError):
            resource.embed(("new",))
    finally:
        state.block.set()
    active.result(3)
    closing.result(3)
    releases = state.calls["invalidate"]
    resource.close()
    assert state.calls["invalidate"] == releases == 1
    assert state.maximum == 1


@pytest.mark.parametrize("method,stage", [
    ("resolve_embedding_fingerprint", "fingerprint"),
    ("resolve_embedding_compatibility", "compatibility"),
    ("validate_embedding_backend", "validate"),
])
def test_backend_stage_failure_invalidates_and_retries_one_instance(shared, method, stage):
    resource, state = shared
    state.fail_stage = stage
    with pytest.raises(SemanticIndexOperationError):
        getattr(resource, method)()
    assert resource.generation == 1
    assert state.calls["invalidate"] == 1
    state.fail_stage = None
    getattr(resource, method)()
    assert len(state.instances) == state.maximum == 1


def test_cache_permission_failure_is_lazy_and_retryable(shared, monkeypatch):
    resource, state = shared

    def denied(**kwargs):
        raise PermissionError("private location")

    with monkeypatch.context() as patch:
        patch.setattr("app.services.semantic_resources.tempfile.TemporaryFile", denied)
        with pytest.raises(SemanticIndexOperationError, match="cache"):
            resource.embed(("query",))
    assert not state.instances
    assert resource.embed(("query",))


def test_already_cancelled_and_unbound_source_do_no_model_work(shared, tmp_path):
    resource, state = shared
    service = owner(tmp_path, resource)
    token = CancellationToken()
    token.cancel()
    with pytest.raises(SpaceError, match="operation_cancelled"):
        prepare(service, cancel=token)
    event = threading.Event()
    event.set()
    with pytest.raises(SynchronizationCancelledError):
        service.sync(event)
    service.vault_root = tmp_path
    with pytest.raises(IndexBindingUnavailableError):
        service.sync()
    assert not state.instances
    assert not service.db_path.exists()


def test_disabled_and_inaccessible_cache_are_no_fallback_paths(shared, tmp_path):
    resource, state = shared
    service = owner(tmp_path, resource, indexing_enabled=False)
    for call in (service.sync, service.rebuild, lambda: service.sync_paths(("a.md",)),
                 lambda: service.search("text"), lambda: prepare(service)):
        with pytest.raises(SemanticSearchUnavailableError):
            call()
    assert not state.instances
    assert not service.db_path.exists()
    resource.cache_dir.parent.mkdir()
    resource.cache_dir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(SemanticIndexOperationError, match="cache"):
        resource.resolve_embedding_fingerprint()
    assert not state.instances
    resource.cache_dir.unlink()
    resource.resolve_embedding_fingerprint()
    assert len(state.instances) == 1


def test_legacy_and_custom_construction_and_shared_misconfiguration(shared, tmp_path):
    resource, state = shared
    legacy = SemanticSearchService(vault_root=tmp_path, repository=SemanticRepository(tmp_path / "legacy.db"))
    assert isinstance(legacy.embedder, FastEmbedder)
    custom = object()
    service = SemanticSearchService(
        vault_root=tmp_path, repository=SemanticRepository(tmp_path / "custom.db"), embedder=custom,
    )
    assert service.embedder is custom
    injected = SemanticSearchService(
        vault_root=tmp_path, repository=service.repository,
        model_name=resource.model_name, embedder=resource,
    )
    assert injected._shared_resource is resource
    for options in ({"embedder": custom}, {"embedding_fingerprint": state.fingerprint},
                    {"model_name": "wrong/model"}):
        with pytest.raises(ValueError):
            SemanticSearchService(
                vault_root=tmp_path, repository=service.repository, shared_resource=resource, **options,
            )
    assert not state.instances


def test_shared_owners_preserve_all_thirteen_evaluation_ranks(tmp_path, monkeypatch):
    instances = []

    class LocalBackend(DeterministicConceptEmbedder):
        def __init__(self, *args, **kwargs):
            super().__init__()
            instances.append(self)

        def invalidate_embedding_compatibility_cache(self):
            pass

    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", LocalBackend)
    resource = SharedEmbeddingResource(Settings(
        semantic_model="vb022/deterministic-concepts", semantic_data_path=tmp_path / "shared-data",
    ))
    try:
        services = [build_service(tmp_path / sid, embedder=resource)[0] for sid in ("one", "two")]
        for service in services:
            outcomes = run_evaluation(service, load_cases())
            assert len(outcomes) == 13
            assert all(outcome.rank == 1 for outcome in outcomes)
            assert metrics_payload(calculate_group_metrics(outcomes)) == load_baseline()
        assert len(instances) == 1
    finally:
        resource.close()


@pytest.mark.parametrize("finish", ["retry", "close", "close_during_retry"])
def test_retained_failed_future_releases_underlying_model(tmp_path, monkeypatch, finish):
    models = []
    fail = True
    retry_entered, retry_release = threading.Event(), threading.Event()

    class Model:
        def __init__(self):
            # Also require cleanup to collect released cyclic model state.
            self.cycle = self

        def embed(self, texts, **kwargs):
            if fail:
                try:
                    raise ValueError("private backend details")
                except ValueError as exc:
                    raise RuntimeError("private inference failure") from exc
            if finish == "close_during_retry":
                retry_entered.set()
                assert retry_release.wait(5)
            return [np.array([1, 0], dtype=np.float32) for _ in texts]

    def get_model(backend):
        if backend._model is None:
            assert not any(ref() is not None for ref in models), "live model generations overlap"
            backend._model = Model()
            models.append(weakref.ref(backend._model))
        return backend._model

    monkeypatch.setattr(FastEmbedder, "_get_model", get_model)
    resource = SharedEmbeddingResource(Settings(semantic_data_path=tmp_path / "data"))
    failed = launch(lambda: resource.embed(("private query",)))
    try:
        retained = failed.exception(3)
        assert retained is not None
        assert models[0]() is None, "retained failure keeps the old model alive"
        assert isinstance(retained, SemanticIndexOperationError)
        assert retained.__context__ is None and retained.__cause__ is None
        assert "private" not in str(retained)
        fail = False
        if finish == "retry":
            resource.embed(("retry",))
            assert len(models) == 2
        elif finish == "close_during_retry":
            retry = launch(lambda: resource.embed(("retry",)))
            assert retry_entered.wait(3)
            closing = launch(resource.close)
            try:
                with resource._condition:
                    assert resource._condition.wait_for(lambda: resource._closing, timeout=1)
                with pytest.raises(SemanticSearchUnavailableError):
                    resource.embed(("new",))
                assert not closing.done()
            finally:
                retry_release.set()
            retry.result(3)
            closing.result(3)
            assert len(models) == 2
        else:
            resource.close()
        resource.close()
        assert all(ref() is None for ref in models)
        assert failed.exception() is retained
    finally:
        resource.close()


class ObservedLock:
    """Signal attempted contention, without replacing the lock's semantics."""

    def __init__(self, lock):
        self.lock = lock
        self.waiting = threading.Event()
        self.attempts = []
        self.releases = 0

    def acquire(self, *args, **kwargs):
        self.attempts.append((args, kwargs))
        if self.lock.locked():
            self.waiting.set()
        return self.lock.acquire(*args, **kwargs)

    def release(self):
        self.lock.release()
        self.releases += 1

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


@pytest.mark.parametrize("scoped", [False, True])
def test_same_owner_fingerprint_wait_is_cancellable(shared, tmp_path, monkeypatch, scoped):
    resource, _ = shared
    service = owner(tmp_path, resource)
    entered, release = threading.Event(), threading.Event()
    resolve = resource.resolve_embedding_fingerprint

    def blocked():
        entered.set()
        assert release.wait(5)
        return resolve()

    monkeypatch.setattr(resource, "resolve_embedding_fingerprint", blocked)
    observed = ObservedLock(service._fingerprint_lock)
    service._fingerprint_lock = observed
    active = launch(lambda: service.index_signature)
    assert entered.wait(3)
    if scoped:
        token = CancellationToken()
        waiting = launch(lambda: prepare(service, cancel=token))
        cancel, error = token.cancel, SpaceError
    else:
        event = threading.Event()
        waiting = launch(lambda: service.sync(event))
        cancel, error = event.set, SynchronizationCancelledError
    try:
        assert observed.waiting.wait(3)
        cancel()
        with pytest.raises(error):
            waiting.result(1)
        assert not active.done()
    finally:
        release.set()
        active.result(3)
        try:
            waiting.result(3)
        except (SpaceError, SynchronizationCancelledError):
            pass


@pytest.mark.parametrize("action", ["cancel", "is_current", "close"])
def test_blocked_cleanup_keeps_condition_available(shared, monkeypatch, action):
    resource, state = shared
    resource.resolve_embedding_fingerprint()
    backend = state.instances[0][0]
    cleanup = backend.invalidate_embedding_compatibility_cache
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(5)
        cleanup()

    monkeypatch.setattr(backend, "invalidate_embedding_compatibility_cache", blocked)
    invalidation = launch(resource.invalidate_embedding_compatibility_cache)
    assert entered.wait(3)
    closing = None
    try:
        if action == "cancel":
            token = CancellationToken()
            checked = threading.Event()

            def check():
                checked.set()
                token.check()

            def acquire():
                with resource.operation(check):
                    resource.embed(("waiting",))

            waiting = launch(acquire)
            assert checked.wait(3)
            token.cancel()
            with pytest.raises(SpaceError, match="operation_cancelled"):
                waiting.result(1)
        elif action == "is_current":
            assert not launch(lambda: resource.is_current(0)).result(1)
        else:
            closing = launch(resource.close)

            def shutdown_admitted():
                with resource._condition:
                    return resource._condition.wait_for(lambda: resource._closing, timeout=1)

            assert launch(shutdown_admitted).result(1.5)
            with pytest.raises(SemanticSearchUnavailableError):
                launch(lambda: resource.embed(("new",))).result(1)
            assert not closing.done()
        assert resource.generation == 0
        assert state.calls["init"] == 1
    finally:
        release.set()
        invalidation.result(3)
        if closing is not None:
            closing.result(3)
    assert state.calls["invalidate"] == 1


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_cleanup_failure_notifies_waiters_and_fails_closed(shared, monkeypatch, failure):
    resource, state = shared
    resource.resolve_embedding_fingerprint()
    backend = state.instances[0][0]
    cleanup = backend.invalidate_embedding_compatibility_cache
    entered, release, waiter_checked = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def blocked_failure():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        raise failure("private cleanup details")

    monkeypatch.setattr(backend, "invalidate_embedding_compatibility_cache", blocked_failure)
    invalidation = launch(resource.invalidate_embedding_compatibility_cache)
    assert entered.wait(3)

    def waiting_operation():
        with resource.operation(waiter_checked.set):
            resource.embed(("waiting",))

    waiting = launch(waiting_operation)
    try:
        assert waiter_checked.wait(3)
        assert not launch(lambda: resource.is_current(0)).result(1)
    finally:
        release.set()
    expected = KeyboardInterrupt if failure is KeyboardInterrupt else SemanticIndexOperationError
    retained = invalidation.exception(3)
    assert isinstance(retained, expected)
    assert "private" not in str(retained)
    with pytest.raises(SemanticSearchUnavailableError, match="cleanup failed"):
        waiting.result(3)
    with pytest.raises(SemanticSearchUnavailableError):
        resource.embed(("no replacement",))
    assert resource.generation == 0
    assert not resource._cleaning and not resource._invalidating
    assert len(calls) == 1
    # A terminal cleanup error prohibits replacement, but close can still drain
    # the resource; it never strands exclusive cleanup ownership.
    monkeypatch.setattr(backend, "invalidate_embedding_compatibility_cache", cleanup)
    resource.close()
    resource.close()


@pytest.mark.parametrize("finish", ["invalidate", "close"])
def test_sync_embedding_lock_cancellation_releases_only_its_pin(shared, tmp_path, monkeypatch, finish):
    resource, state = shared
    first = owner(tmp_path, resource, "work")
    second = owner(tmp_path, resource)
    first.sync()
    second.sync()
    old_chunks = second.repository.load_chunks()
    (second.vault_root / "a.md").write_text("changed note requiring inference", encoding="utf-8")
    observed = ObservedLock(second._embed_lock)
    second._embed_lock = observed
    search_waiting, maintenance_requested = threading.Event(), threading.Event()
    embed = resource.embed
    reserve = resource._reserve_cleanup

    def observed_embed(texts):
        if texts == ["second search"]:
            # _embed() already owns the per-owner lock at this boundary.
            assert observed.lock.locked()
            search_waiting.set()
        return embed(texts)

    def observed_reserve():
        if resource._invalidating:
            maintenance_requested.set()
        return reserve()

    monkeypatch.setattr(resource, "embed", observed_embed)
    monkeypatch.setattr(resource, "_reserve_cleanup", observed_reserve)
    state.block = threading.Event()
    active = launch(lambda: first.search("first search"))
    assert state.entered.wait(3)
    searching = launch(lambda: second.search("second search"))
    assert search_waiting.wait(3)
    event = threading.Event()
    syncing = launch(lambda: second.sync(event))
    maintenance = None
    try:
        assert observed.waiting.wait(3)
        with resource._condition:
            assert resource._pins == 3 and resource._busy
        generation = resource.generation
        event.set()
        with pytest.raises(SynchronizationCancelledError):
            syncing.result(1)
        with resource._condition:
            assert resource._pins == 2 and resource._busy
        assert not second._sync_lock.locked()
        assert observed.lock.locked()  # Cancellation did not release search's lock.
        assert not active.done() and not searching.done()
        assert second.repository.load_chunks() == old_chunks  # Cancelled batch rolled back.
        maintenance = launch(
            resource.close if finish == "close" else resource.invalidate_embedding_compatibility_cache
        )
        assert maintenance_requested.wait(3)
        assert resource.generation == generation
        assert not maintenance.done() and not active.done()
        if finish == "close":
            with pytest.raises(SemanticSearchUnavailableError):
                searching.result(1)
            with resource._condition:
                assert resource._pins == 1 and resource._busy
    finally:
        state.block.set()
        active.result(3)
        if maintenance is not None and finish == "close":
            with pytest.raises(SemanticSearchUnavailableError):
                searching.result(3)
        else:
            searching.result(3)
        try:
            syncing.result(3)
        except SynchronizationCancelledError:
            pass
        if maintenance is not None:
            maintenance.result(3)
    assert resource.generation == generation + 1
    with resource._condition:
        assert resource._pins == 0 and not resource._busy and not resource._cleaning
    assert not observed.lock.locked() and not second._sync_lock.locked()
    assert state.maximum == 1
    state.block = None
    if finish == "invalidate":
        assert second.sync()["indexed"] == 1
        assert second.search("changed")[0].path == "a.md"
    resource.close()
    resource.close()


def test_legacy_sync_embedding_lock_remains_blocking(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    note = root / "a.md"
    note.write_text("initial note", encoding="utf-8")
    service = SemanticSearchService(
        vault_root=root, repository=SemanticRepository(tmp_path / "legacy.db"),
        embedder=DeterministicConceptEmbedder(),
    )
    service.sync()
    note.write_text("changed note requiring inference", encoding="utf-8")
    observed = ObservedLock(service._embed_lock)
    service._embed_lock = observed
    event = threading.Event()
    observed.lock.acquire()
    syncing = launch(lambda: service.sync(event))
    try:
        assert observed.waiting.wait(3)
        assert observed.attempts == [((), {})]  # Legacy acquisition has no polling timeout.
        event.set()
        assert not syncing.done()
    finally:
        observed.lock.release()
    # Legacy cancellation remains at batch boundaries; the accepted batch finishes.
    assert syncing.result(3)["indexed"] == 1
    assert not observed.lock.locked() and not service._sync_lock.locked()


def run_sync_entry(service, entry, event=None):
    if entry == "sync_paths":
        return service.sync_paths(("a.md",), cancel_event=event)
    return getattr(service, entry)(cancel_event=event)


@pytest.mark.parametrize("entry", ["sync", "rebuild", "sync_paths"])
@pytest.mark.parametrize("finish", ["invalidate", "close"])
def test_sync_lock_wait_cancellation_preserves_active_owner(shared, tmp_path, monkeypatch, entry, finish):
    resource, state = shared
    service = owner(tmp_path, resource)
    observed = ObservedLock(service._sync_lock)
    service._sync_lock = observed
    marker = threading.local()
    writes = []
    for name in ("prepare_index", "reset_index", "set_metadata", "transaction", "finalize_full_sync_success"):
        original = getattr(service.repository, name)

        def record(*args, _name=name, _original=original, **kwargs):
            if getattr(marker, "waiting", False):
                writes.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(service.repository, name, record)
    maintenance_requested = threading.Event()
    reserve = resource._reserve_cleanup

    def observed_reserve():
        if resource._invalidating:
            maintenance_requested.set()
        return reserve()

    monkeypatch.setattr(resource, "_reserve_cleanup", observed_reserve)
    state.block = threading.Event()
    active = launch(service.sync)
    assert state.entered.wait(3)
    fields = ("_state_initialized", "_search_available", "_fingerprint_resolution_failed",
              "_resolved_index_signature", "_resource_generation")
    owner_state = tuple(getattr(service, name) for name in fields)
    event = threading.Event()

    def queued():
        marker.waiting = True
        return run_sync_entry(service, entry, event)

    waiting = launch(queued)
    maintenance = None
    try:
        assert observed.waiting.wait(3)
        with resource._condition:
            assert resource._pins == 2 and resource._busy
        generation = resource.generation
        event.set()
        with pytest.raises(SynchronizationCancelledError):
            waiting.result(1)
        with resource._condition:
            assert resource._pins == 1 and resource._busy
        assert observed.lock.locked() and observed.releases == 0
        assert not active.done()
        assert not writes
        assert tuple(getattr(service, name) for name in fields) == owner_state
        maintenance = launch(
            resource.close if finish == "close" else resource.invalidate_embedding_compatibility_cache
        )
        assert maintenance_requested.wait(3)
        assert resource.generation == generation
        assert not active.done() and not maintenance.done()
    finally:
        state.block.set()
        assert active.result(3)["indexed"] == 1
        try:
            waiting.result(3)
        except SynchronizationCancelledError:
            pass
        if maintenance is not None:
            maintenance.result(3)
    assert not writes
    assert not observed.lock.locked() and observed.releases == 1
    with resource._condition:
        assert resource._pins == 0 and not resource._busy and not resource._cleaning
    assert resource.generation == generation + 1
    assert state.maximum == 1
    state.block = None
    if finish == "invalidate":
        result = run_sync_entry(service, entry)
        assert result["indexed"] + result["unchanged"] == 1
        assert service.search("text")
    resource.close()
    resource.close()


@pytest.mark.parametrize("entry", ["sync", "rebuild", "sync_paths"])
@pytest.mark.parametrize("cancel_at", ["before_acquire", "after_acquire"])
def test_sync_lock_acquisition_cancellation_race_releases_once(shared, tmp_path, entry, cancel_at):
    resource, state = shared
    service = owner(tmp_path, resource)
    service.sync()
    event = threading.Event()

    class CancellingLock(ObservedLock):
        def acquire(self, *args, **kwargs):
            if cancel_at == "before_acquire":
                event.set()
            acquired = super().acquire(*args, **kwargs)
            if acquired and cancel_at == "after_acquire":
                event.set()
            return acquired

    observed = CancellingLock(service._sync_lock)
    service._sync_lock = observed
    before = service.db_path.read_bytes()
    calls = state.calls.copy()
    with pytest.raises(SynchronizationCancelledError):
        run_sync_entry(service, entry, event)
    assert observed.releases == 1 and not observed.lock.locked()
    assert resource._pins == 0
    assert service.db_path.read_bytes() == before
    assert state.calls == calls
    # Retry after the acquisition race proves no leaked or double-released lock.
    service._sync_lock = ObservedLock(observed.lock)
    result = run_sync_entry(service, entry)
    assert result["indexed"] + result["unchanged"] == 1


@pytest.mark.parametrize("entry", ["sync", "rebuild", "sync_paths"])
def test_legacy_sync_lock_wait_remains_blocking(tmp_path, entry):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "a.md").write_text("legacy note", encoding="utf-8")
    service = SemanticSearchService(
        vault_root=root, repository=SemanticRepository(tmp_path / "legacy.db"),
        embedder=DeterministicConceptEmbedder(),
    )
    service.sync()
    observed = ObservedLock(service._sync_lock)
    service._sync_lock = observed
    event = threading.Event()
    observed.lock.acquire()
    waiting = launch(lambda: run_sync_entry(service, entry, event))
    try:
        assert observed.waiting.wait(3)
        event.set()
        assert observed.attempts == [((), {})]
        assert not waiting.done() and observed.releases == 0
    finally:
        observed.lock.release()
    with pytest.raises(SynchronizationCancelledError):
        waiting.result(3)
    assert not observed.lock.locked() and observed.releases == 1


@pytest.mark.parametrize("entry", ["sync", "rebuild", "sync_paths"])
@pytest.mark.parametrize("held", ["state", "configuration"])
def test_state_and_configuration_wait_cancellation(shared, tmp_path, monkeypatch, entry, held):
    resource, state = shared
    service = owner(tmp_path, resource)
    service.sync()
    marker = threading.local()
    entered, release = threading.Event(), threading.Event()
    writes = []
    for name in ("prepare_index", "reset_index", "set_metadata", "transaction", "finalize_full_sync_success"):
        original = getattr(service.repository, name)

        def record(*args, _name=name, _original=original, **kwargs):
            if getattr(marker, "waiting", False):
                writes.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(service.repository, name, record)
    if held == "state":
        service._state_initialized = False  # The persisted index has not been adopted by this lifecycle.
        read_status = service.repository.read_status

        def blocked_status():
            result = read_status()
            if getattr(marker, "holder", False):
                entered.set()
                assert release.wait(5)
            return result

        monkeypatch.setattr(service.repository, "read_status", blocked_status)
        name = "_state_init_lock"
    else:
        validate = service._validate_source_root

        def blocked_validation(root):
            if getattr(marker, "holder", False):
                entered.set()
                assert release.wait(5)
            validate(root)

        monkeypatch.setattr(service, "_validate_source_root", blocked_validation)
        name = "_configuration_lock"
    observed = ObservedLock(getattr(service, name))
    setattr(service, name, observed)

    def hold():
        marker.holder = True
        return service.state if held == "state" else service.reconfigure(max_note_bytes=service.max_note_bytes)

    holder = launch(hold)
    assert entered.wait(3)
    event = threading.Event()

    def queued():
        marker.waiting = True
        return run_sync_entry(service, entry, event)

    before = service.db_path.read_bytes()
    waiting = launch(queued)
    try:
        assert observed.waiting.wait(3)
        event.set()
        with pytest.raises(SynchronizationCancelledError):
            waiting.result(1)
        assert observed.lock.locked() and observed.releases == 0
        assert not holder.done() and not service._sync_lock.locked()
        assert resource._pins == (1 if held == "state" else 0)
        assert not writes and service.db_path.read_bytes() == before
        if held == "state":
            assert not service._state_initialized
    finally:
        release.set()
        holder.result(3)
        try:
            waiting.result(3)
        except SynchronizationCancelledError:
            pass
    assert resource._pins == 0 and not observed.lock.locked()
    result = run_sync_entry(service, entry)
    assert result["indexed"] + result["unchanged"] == 1
    assert state.maximum == 1


@pytest.mark.parametrize("validation", ["backend", "validator"])
def test_cancelled_failure_cleanup_fences_active_compatibility(shared, tmp_path, monkeypatch, validation):
    resource, state = shared
    service = owner(tmp_path, resource)
    service.sync()
    old, old_args = prepare(service)
    service._federation_evidence = None
    marker = threading.local()
    snapshot_ready, evidence_allowed = threading.Event(), threading.Event()
    compatibility_entered, release = threading.Event(), threading.Event()
    snapshot = service.repository.read_eligible_snapshot
    status = service.repository.read_status
    invalidate = service._invalidate_dynamic_compatibility_cache
    backend = state.instances[0][0]
    compatibility = backend.resolve_embedding_compatibility

    def blocked_snapshot(*args, **kwargs):
        result = snapshot(*args, **kwargs)
        snapshot_ready.set()
        assert evidence_allowed.wait(5)
        return result

    def mismatched_status():
        result = status()
        return replace(result, index_signature="old") if getattr(marker, "sync", False) else result

    def cleanup():
        marker.cleanup = True
        return invalidate()

    def blocked_compatibility():
        compatibility_entered.set()
        assert release.wait(5)
        return compatibility()

    class CleanupFingerprintLock(ObservedLock):
        def acquire(self, *args, **kwargs):
            acquired = super().acquire(*args, **kwargs)
            if acquired and getattr(marker, "cleanup", False):
                evidence_allowed.set()
                assert compatibility_entered.wait(3)
            return acquired

    fingerprint = CleanupFingerprintLock(service._fingerprint_lock)
    evidence = ObservedLock(service._federation_evidence_lock)
    service._fingerprint_lock = fingerprint
    service._federation_evidence_lock = evidence
    monkeypatch.setattr(service.repository, "read_eligible_snapshot", blocked_snapshot)
    monkeypatch.setattr(service.repository, "read_status", mismatched_status)
    monkeypatch.setattr(service, "_invalidate_dynamic_compatibility_cache", cleanup)
    monkeypatch.setattr(backend, "resolve_embedding_compatibility", blocked_compatibility)
    state.fail_stage = "validate"
    if validation == "validator":
        def fail_validation():
            raise SemanticIndexOperationError("local validator failure")

        monkeypatch.setattr(resource, "validate_embedding_backend", fail_validation)
    preparing = launch(lambda: prepare(service))
    assert snapshot_ready.wait(3)
    event = threading.Event()

    def synchronize():
        marker.sync = True
        return service.sync(event)

    syncing = launch(synchronize)
    scoring = None
    try:
        assert evidence.waiting.wait(3)
        event.set()
        with pytest.raises(SynchronizationCancelledError):
            syncing.result(1)
        assert not fingerprint.lock.locked() and not service._sync_lock.locked()
        assert evidence.lock.locked() and not preparing.done()
        assert resource._pins == 1 and resource._busy and resource._invalidating
        assert service._compatibility_invalidation_pending
        embeds = state.calls["embed"]
        scoring = launch(lambda: service.search_federation_candidates("query", prepared=old, **old_args))
    finally:
        release.set()
        evidence_allowed.set()
        try:
            preparing.result(3)
        except SemanticSearchUnavailableError:
            pass
        try:
            syncing.result(3)
        except (SynchronizationCancelledError, SemanticIndexOperationError):
            pass
    assert scoring is not None
    with pytest.raises(SemanticSearchUnavailableError):
        scoring.result(3)
    assert state.calls["embed"] == embeds
    assert resource._pins == 0 and resource.generation > old.resource_generation
    assert not evidence.lock.locked() and not fingerprint.lock.locked()
    state.fail_stage = None
    monkeypatch.setattr(resource, "validate_embedding_backend", backend.validate_embedding_backend)
    fresh, args = prepare(service)
    assert service.search_federation_candidates("query", prepared=fresh, **args).candidates


@pytest.mark.parametrize("entry", ["sync", "rebuild", "sync_paths"])
@pytest.mark.parametrize("name", ["_state_init_lock", "_configuration_lock"])
@pytest.mark.parametrize("cancel_at", ["before_acquire", "after_acquire"])
def test_state_configuration_acquisition_race(shared, tmp_path, entry, name, cancel_at):
    resource, state = shared
    service = owner(tmp_path, resource)
    service.sync()
    if name == "_state_init_lock":
        service._state_initialized = False
    event = threading.Event()

    class CancellingLock(ObservedLock):
        def acquire(self, *args, **kwargs):
            if cancel_at == "before_acquire":
                event.set()
            acquired = super().acquire(*args, **kwargs)
            if acquired and cancel_at == "after_acquire":
                event.set()
            return acquired

    observed = CancellingLock(getattr(service, name))
    setattr(service, name, observed)
    before = service.db_path.read_bytes()
    calls = state.calls.copy()
    with pytest.raises(SynchronizationCancelledError):
        run_sync_entry(service, entry, event)
    assert observed.releases == 1 and not observed.lock.locked()
    assert not service._sync_lock.locked() and resource._pins == 0
    assert service.db_path.read_bytes() == before and state.calls == calls
    setattr(service, name, observed.lock)
    result = run_sync_entry(service, entry)
    assert result["indexed"] + result["unchanged"] == 1


@pytest.mark.parametrize("cancel_at", ["before_acquire", "after_acquire"])
def test_evidence_cleanup_acquisition_race_still_invalidates(shared, tmp_path, cancel_at):
    resource, _ = shared
    service = owner(tmp_path, resource)
    service.sync()
    old, args = prepare(service)
    event = threading.Event()

    class CancellingLock(ObservedLock):
        def acquire(self, *args, **kwargs):
            if cancel_at == "before_acquire":
                event.set()
            acquired = super().acquire(*args, **kwargs)
            if acquired and cancel_at == "after_acquire":
                event.set()
            return acquired

    observed = CancellingLock(service._federation_evidence_lock)
    service._federation_evidence_lock = observed
    with pytest.raises(SynchronizationCancelledError):
        with resource.operation(lambda: service._raise_if_cancelled(event)):
            service._invalidate_dynamic_compatibility_cache()
    assert observed.releases == 1 and not observed.lock.locked()
    assert not service._fingerprint_lock.locked() and resource._pins == 0
    assert service._compatibility_invalidation_pending
    assert resource.generation > old.resource_generation
    with pytest.raises(SemanticSearchUnavailableError, match="stale"):
        service.search_federation_candidates("query", prepared=old, **args)
    service._federation_evidence_lock = observed.lock
    fresh, args = prepare(service)
    assert not service._compatibility_invalidation_pending
    assert service.search_federation_candidates("query", prepared=fresh, **args).candidates


def test_availability_wait_cancellation_drops_only_waiter_pin(shared, tmp_path):
    resource, state = shared
    service = owner(tmp_path, resource)
    service.sync()
    observed = ObservedLock(service._availability_lock)
    service._availability_lock = observed
    before = service.db_path.read_bytes()
    calls = state.calls.copy()
    event = threading.Event()
    observed.lock.acquire()
    waiting = launch(lambda: service.sync(event))
    try:
        assert observed.waiting.wait(3)
        event.set()
        with pytest.raises(SynchronizationCancelledError):
            waiting.result(1)
        assert observed.lock.locked() and observed.releases == 0
        assert not service._sync_lock.locked() and resource._pins == 0
        assert service.db_path.read_bytes() == before and state.calls == calls
    finally:
        observed.lock.release()
    assert service.sync()["unchanged"] == 1


@pytest.mark.parametrize("entry", ["sync", "rebuild", "sync_paths"])
@pytest.mark.parametrize("name", ["_state_init_lock", "_configuration_lock"])
def test_legacy_state_configuration_locks_remain_blocking(tmp_path, entry, name):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "a.md").write_text("legacy note", encoding="utf-8")
    service = SemanticSearchService(
        vault_root=root, repository=SemanticRepository(tmp_path / "legacy.db"),
        embedder=DeterministicConceptEmbedder(),
    )
    service.sync()
    if name == "_state_init_lock":
        service._state_initialized = False
    observed = ObservedLock(getattr(service, name))
    setattr(service, name, observed)
    event = threading.Event()
    observed.lock.acquire()
    waiting = launch(lambda: run_sync_entry(service, entry, event))
    try:
        assert observed.waiting.wait(3)
        event.set()
        assert observed.attempts == [((), {})]
        assert not waiting.done() and observed.releases == 0
    finally:
        observed.lock.release()
    with pytest.raises(SynchronizationCancelledError):
        waiting.result(3)
    assert not observed.lock.locked()
