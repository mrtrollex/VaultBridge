"""Independent E6 cleanup reproductions and same-owner retry regressions."""

import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.services.knowledge_spaces import SpaceError
from app.services.semantic_search import (
    SemanticIndexOperationError,
    SemanticSearchService,
    SemanticSearchUnavailableError,
)
from app.services.space_runtime import compose_space_runtime
from tests.test_semantic_resources import launch
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration


@pytest.mark.parametrize("failures", [1, 3])
def test_runtime_model_release_failure_retains_resource_for_retry(tmp_path, backend, monkeypatch, failures):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    resource = runtime.resource
    model = resource._backend
    release = model.invalidate_embedding_compatibility_cache
    generation = resource.generation
    attempts = []

    def failing():
        attempts.append(model)
        if len(attempts) <= failures:
            raise PermissionError("private backend path")
        release()

    monkeypatch.setattr(model, "invalidate_embedding_compatibility_cache", failing)
    try:
        for count in range(1, failures + 1):
            with pytest.raises(SemanticIndexOperationError) as raised:
                runtime.shutdown()
            assert "private" not in str(raised.value)
            assert raised.value.__context__ is None
            assert len(attempts) == count
            assert runtime._state == "stopping" and not resource._closed
            assert resource._closing and resource._backend is model
            assert resource.generation == generation and resource._pins == 0
            with pytest.raises(SemanticSearchUnavailableError):
                resource.embed(("new work",))
            with pytest.raises(SpaceError, match="operation_cancelled"):
                runtime.operations.scoped_list()
        runtime.shutdown()
        runtime.shutdown()
        assert runtime._state == "stopped" and resource._closed
        assert not resource._cleanup_failed and len(attempts) == failures + 1
        assert len(backend.instances) == 1 and backend.cleaned == 1
    finally:
        monkeypatch.setattr(model, "invalidate_embedding_compatibility_cache", release)
        runtime.shutdown()


@pytest.mark.parametrize("failures", [1, 3])
def test_runtime_workspace_failure_retains_actual_e4_work(tmp_path, backend, monkeypatch, failures):
    runtime = compose_space_runtime(configuration(tmp_path, 2))
    service = binding(runtime)._owners.semantic
    other = binding(runtime, "space-1")._owners.semantic
    entered, resume = threading.Event(), threading.Event()
    step = SemanticSearchService.sync_step
    turns = []

    def paused(owner, *args, **kwargs):
        result = step(owner, *args, **kwargs)
        turns.append(owner)
        if owner is service and result.indexed:
            entered.set()
            assert resume.wait(20)
        return result

    monkeypatch.setattr(SemanticSearchService, "sync_step", paused)
    runtime.start()
    assert entered.wait(10)
    work, other_work = service._active_sync, other._active_sync
    workspace, other_workspace = Path(work.temporary.name), Path(other_work.temporary.name)
    chunks = service.repository.load_chunks()
    assert chunks and workspace.is_dir() and other_workspace.is_dir()
    remove = work.temporary._rmtree
    attempts = []

    def failing(*args, **kwargs):
        assert not work.turn_lock.locked()
        assert not service._sync_lock.locked() and not service._configuration_lock.locked()
        assert not service._sync_admin_lock.locked()
        assert not runtime.scheduler._condition._is_owned()
        assert not runtime.resource._condition._is_owned()
        attempts.append(args[0])
        if len(attempts) <= failures:
            raise PermissionError("private workspace path")
        return remove(*args, **kwargs)

    monkeypatch.setattr(work.temporary, "_rmtree", failing)
    closing = launch(runtime.shutdown)
    try:
        with runtime.scheduler._condition:
            assert runtime.scheduler._condition.wait_for(lambda: runtime.scheduler._closed, 10)
        resume.set()
        with pytest.raises((RuntimeError, SemanticIndexOperationError)):
            closing.result(20)
        for count in range(1, failures + 1):
            assert len(attempts) == count
            assert runtime._state == "stopping" and workspace.is_dir()
            assert service._active_sync is work
            assert not work.temporary._finalizer.alive
            assert other._active_sync is None and not other_workspace.exists()
            assert not runtime.resource._closed
            assert runtime.resource._pins == 0
            if count < failures:
                with pytest.raises((RuntimeError, SemanticIndexOperationError)):
                    runtime.shutdown()
        before = len(turns)
        runtime.shutdown()
        runtime.shutdown()
        assert runtime._state == "stopped" and not workspace.exists()
        assert service._active_sync is None
        assert len(attempts) == failures + 1 and len(turns) == before
        assert service.repository.load_chunks() == chunks
        assert runtime.resource._closed
    finally:
        resume.set()
        monkeypatch.setattr(work.temporary, "_rmtree", remove)
        runtime.shutdown()


@pytest.mark.parametrize("phase", ["active_turn", "finish"])
def test_workspace_failure_inside_real_sync_step_requires_explicit_retry(tmp_path, backend, monkeypatch, phase):
    runtime = compose_space_runtime(configuration(tmp_path, 2))
    service = binding(runtime)._owners.semantic
    other = binding(runtime, "space-1")._owners.semantic
    entered, resume = threading.Event(), threading.Event()
    operation = service._embedding_operation
    step = SemanticSearchService.sync_step
    works, attempts = [], []

    def install(work):
        if works:
            return
        works.append(work)
        remove = work.temporary._rmtree

        def failing(*args, **kwargs):
            assert not service._sync_lock.locked() and not service._configuration_lock.locked()
            assert not service._sync_admin_lock.locked() and not work.turn_lock.locked()
            assert not runtime.scheduler._condition._is_owned()
            assert not runtime.resource._condition._is_owned()
            attempts.append(args[0])
            if len(attempts) == 1:
                if phase == "finish":
                    entered.set()
                    assert resume.wait(20)
                raise PermissionError("private workspace path")
            return remove(*args, **kwargs)

        monkeypatch.setattr(work.temporary, "_rmtree", failing)

    @contextmanager
    def paused_operation(*args, **kwargs):
        with operation(*args, **kwargs) as generation:
            work = service._active_sync
            if phase == "active_turn" and work is not None and work.phase == "process":
                install(work)
                entered.set()
                assert resume.wait(20)
            yield generation

    def observed_step(owner, *args, **kwargs):
        work = owner._active_sync
        if phase == "finish" and owner is service and work is not None and work.phase == "stale":
            install(work)
        return step(owner, *args, **kwargs)

    monkeypatch.setattr(service, "_embedding_operation", paused_operation)
    monkeypatch.setattr(SemanticSearchService, "sync_step", observed_step)
    runtime.start()
    assert entered.wait(10)
    work = works[0]
    workspace = Path(work.temporary.name)
    closing = launch(runtime.shutdown)
    try:
        with runtime.scheduler._condition:
            assert runtime.scheduler._condition.wait_for(lambda: runtime.scheduler._closed, 10)
        resume.set()
        with pytest.raises(RuntimeError, match="cleanup is incomplete"):
            closing.result(20)
        assert attempts == [str(workspace)]
        assert runtime._state == "stopping" and workspace.is_dir()
        assert service._active_sync is work and other._active_sync is None
        assert service._sync_closed and not runtime.resource._closed
        chunks = service.repository.load_chunks()

        def forbidden(*args, **kwargs):
            pytest.fail("cleanup retry repeated synchronization or a SQLite write transaction")

        with monkeypatch.context() as patch:
            patch.setattr(service.repository, "transaction", forbidden)
            patch.setattr(SemanticSearchService, "sync_step", forbidden)
            runtime.shutdown()
        assert runtime._state == "stopped" and not workspace.exists()
        assert len(attempts) == 2 and service.repository.load_chunks() == chunks
    finally:
        resume.set()
        runtime.shutdown()


def test_runtime_concurrent_model_shutdown_retries_only_once(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    model = runtime.resource._backend
    release = model.invalidate_embedding_compatibility_cache
    entered, resume = threading.Event(), threading.Event()
    attempts = []

    def failing_then_blocked():
        attempts.append(model)
        if len(attempts) == 1:
            raise PermissionError("private backend path")
        entered.set()
        assert resume.wait(20)
        release()

    monkeypatch.setattr(model, "invalidate_embedding_compatibility_cache", failing_then_blocked)
    callers = []
    try:
        with pytest.raises(SemanticIndexOperationError):
            runtime.shutdown()
        ready = threading.Barrier(5)

        def shutdown():
            ready.wait(10)
            runtime.shutdown()

        callers = [launch(shutdown) for _ in range(4)]
        ready.wait(10)
        assert entered.wait(10)
        assert len(attempts) == 2 and runtime.resource._cleaning
        assert not runtime.resource._closed and runtime._state == "stopping"
        with pytest.raises(SemanticSearchUnavailableError):
            runtime.resource.resolve_embedding_fingerprint()
        resume.set()
        for caller in callers:
            caller.result(20)
        assert runtime._state == "stopped" and len(attempts) == 2
        assert backend.cleaned == 1 and len(backend.instances) == 1
        runtime.shutdown()
        assert len(attempts) == 2
    finally:
        resume.set()
        monkeypatch.setattr(model, "invalidate_embedding_compatibility_cache", release)
        runtime.shutdown()


def test_resource_concurrent_closes_share_failed_release_attempt(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    runtime.scheduler.shutdown()
    resource = runtime.resource
    model = resource._backend
    release = model.invalidate_embedding_compatibility_cache
    entered, resume, waiting = threading.Event(), threading.Event(), threading.Event()
    attempts, waits = [], set()
    wait = resource._condition.wait

    def observed_wait(*args, **kwargs):
        if resource._cleaning:
            waits.add(threading.get_ident())
            if len(waits) == 3:
                waiting.set()
        return wait(*args, **kwargs)

    def failing():
        attempts.append(model)
        entered.set()
        assert resume.wait(20)
        raise PermissionError("private backend path")

    monkeypatch.setattr(resource._condition, "wait", observed_wait)
    monkeypatch.setattr(model, "invalidate_embedding_compatibility_cache", failing)
    callers = [launch(resource.close)]
    try:
        assert entered.wait(10)
        callers.extend(launch(resource.close) for _ in range(3))
        assert waiting.wait(10)
        resume.set()
        for caller in callers:
            with pytest.raises(SemanticIndexOperationError) as raised:
                caller.result(20)
            assert raised.value.__context__ is None and "private" not in str(raised.value)
        assert attempts == [model] and not resource._closed and resource._closing
        assert resource._cleanup_failed and resource._pins == 0
    finally:
        resume.set()
        monkeypatch.setattr(model, "invalidate_embedding_compatibility_cache", release)
        runtime.shutdown()
    assert runtime._state == "stopped" and backend.cleaned == 1


@pytest.mark.parametrize("shutdown_layer", ["runtime", "scheduler"])
def test_runtime_concurrent_workspace_cleanup_retries_same_work(tmp_path, backend, monkeypatch, shutdown_layer):
    runtime = compose_space_runtime(configuration(tmp_path, 2))
    service = binding(runtime)._owners.semantic
    entered, resume, deleting, dispose = (threading.Event() for _ in range(4))
    step = SemanticSearchService.sync_step
    turns = []

    def paused(owner, *args, **kwargs):
        result = step(owner, *args, **kwargs)
        turns.append(owner)
        if owner is service and result.indexed:
            entered.set()
            assert resume.wait(20)
        return result

    monkeypatch.setattr(SemanticSearchService, "sync_step", paused)
    runtime.start()
    assert entered.wait(10)
    work = service._active_sync
    workspace = Path(work.temporary.name)
    remove = work.temporary._rmtree
    attempts = []

    def failing_then_blocked(*args, **kwargs):
        assert not work.turn_lock.locked()
        assert not service._sync_lock.locked() and not service._configuration_lock.locked()
        attempts.append(args[0])
        if len(attempts) == 1:
            raise PermissionError("private workspace path")
        deleting.set()
        assert dispose.wait(20)
        return remove(*args, **kwargs)

    monkeypatch.setattr(work.temporary, "_rmtree", failing_then_blocked)
    runtime.admission.begin_shutdown()
    shutdown_runtime = runtime.shutdown if shutdown_layer == "runtime" else runtime.scheduler.shutdown
    closing = launch(shutdown_runtime)
    callers = []
    try:
        with runtime.scheduler._condition:
            assert runtime.scheduler._condition.wait_for(lambda: runtime.scheduler._closed, 10)
        resume.set()
        with pytest.raises(RuntimeError, match="cleanup is incomplete"):
            closing.result(20)
        before = len(turns)
        ready = threading.Barrier(5)

        def shutdown():
            ready.wait(10)
            shutdown_runtime()

        callers = [launch(shutdown) for _ in range(4)]
        ready.wait(10)
        assert deleting.wait(10)
        assert service._active_sync is work and workspace.is_dir()
        assert runtime._state != "stopped" and not runtime.resource._closed
        # Cleanup holds no scheduler/owner/resource lock, even while blocked.
        runtime.scheduler.status(binding(runtime))
        assert service._sync_admin_lock.acquire(blocking=False)
        service._sync_admin_lock.release()
        assert runtime.resource._pins == 0
        dispose.set()
        for caller in callers:
            caller.result(20)
        runtime.shutdown()
        assert len(attempts) == 2 and set(attempts) == {str(workspace)}
        assert len(turns) == before and not workspace.exists()
        assert service._active_sync is None and runtime._state == "stopped"
    finally:
        resume.set()
        dispose.set()
        monkeypatch.setattr(work.temporary, "_rmtree", remove)
        runtime.shutdown()
