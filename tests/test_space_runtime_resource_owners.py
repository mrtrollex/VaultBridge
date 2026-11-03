"""Real E6 reader/spool/filesystem/native construction ownership failures."""

import errno
import os
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.knowledge_spaces import SpaceError
from app.services.space_runtime import compose_space_runtime
from tests.test_semantic_resources import launch
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration


def exclusive(path):
    with closing(sqlite3.connect(path, timeout=0)) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        connection.rollback()


def closed_descriptor(descriptor):
    with pytest.raises(OSError) as error:
        os.fstat(descriptor)
    assert error.value.errno == errno.EBADF


def live(connection):
    try:
        connection.in_transaction
    except sqlite3.ProgrammingError:
        return False
    return True


def shutdown(runtime):
    for _ in range(2):
        try:
            runtime.shutdown()
        except (OSError, RuntimeError, SpaceError):
            pass


def test_reviewer_failed_reader_close_is_reachable_at_shutdown(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime, "space-1")._owners.semantic.repository
    state = SimpleNamespace(fail=True, target=None)
    connect, read = sqlite3.connect, repository._read_status_connection

    class Connection(sqlite3.Connection):
        def close(self):
            if self is state.target and state.fail:
                raise OSError("reader close failed")
            return super().close()

    def tracked(*args, **kwargs):
        return connect(*args, factory=Connection, **kwargs)

    def selected(connection):
        state.target = connection
        return read(connection)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    monkeypatch.setattr(repository, "_read_status_connection", selected)
    try:
        try:
            repository.read_status()
        except (OSError, RuntimeError):
            pass
        assert state.target is not None and live(state.target)
        state.fail = False
        shutdown(runtime)
        locked = False
        with closing(connect(repository.db_path, timeout=0)) as probe:
            try:
                probe.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError:
                locked = True
            else:
                probe.rollback()
        print(dict(runtime=runtime._state, reader_live=live(state.target), exclusive_locked=locked))
        assert runtime._state == "stopped" and not live(state.target) and not locked
    finally:
        state.fail = False
        if state.target is not None:
            sqlite3.Connection.close(state.target)
        shutdown(runtime)


def test_reviewer_spool_close_failure_retains_connection_and_workspace(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    service = binding(runtime)._owners.semantic
    state = SimpleNamespace(fail=True, target=None, path=None)
    connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def close(self):
            if self is state.target and state.fail:
                raise OSError("spool close failed")
            return super().close()

    def tracked(path, *args, **kwargs):
        if Path(str(path)).name == "scan.sqlite3":
            result = connect(path, *args, factory=Connection, **kwargs)
            state.target, state.path = result, Path(path).parent
            return result
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    try:
        with pytest.raises((OSError, RuntimeError)):
            service.begin_sync()
        assert state.target is not None and live(state.target)
        owned = service._active_sync is not None and state.path.exists()
        state.fail = False
        shutdown(runtime)
        print(dict(workspace_retained_after_failure=owned, spool_live=live(state.target), state=runtime._state))
        assert owned and runtime._state == "stopped" and not live(state.target) and not state.path.exists()
    finally:
        state.fail = False
        if state.target is not None:
            sqlite3.Connection.close(state.target)
        shutdown(runtime)


@pytest.mark.skipif(os.name == "nt", reason="POSIX scoped root descriptor; Windows uses native path handles")
def test_reviewer_scoped_descriptor_survives_consumed_exitstack(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    root = binding(runtime)._owners.vault.root_binding_identity()
    state = SimpleNamespace(descriptor=None, fail=True)
    opened, close = os.open, os.close

    def tracked(*args, **kwargs):
        descriptor = opened(*args, **kwargs)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == root and state.descriptor is None:
            state.descriptor = descriptor
        return descriptor

    def failed(descriptor):
        if descriptor == state.descriptor and state.fail:
            raise OSError("scoped root close failed")
        return close(descriptor)

    monkeypatch.setattr(os, "open", tracked)
    monkeypatch.setattr(os, "close", failed)
    try:
        try:
            runtime.operations.scoped_list()
        except (OSError, RuntimeError):
            pass
        assert state.descriptor is not None
        os.fstat(state.descriptor)
        state.fail = False
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping"
            os.fstat(state.descriptor)
        close(state.descriptor)  # Fixture-only proof: failed() did not release it.
        shutdown(runtime)
        released = False
        try:
            os.fstat(state.descriptor)
        except OSError as error:
            assert error.errno == errno.EBADF
            released = True
        print(dict(state=runtime._state, root_descriptor_released=released))
        assert runtime._state == "stopped" and released
    finally:
        state.fail = False
        try:
            info = os.fstat(state.descriptor)
        except OSError:
            pass
        else:
            if (info.st_dev, info.st_ino) == root:
                close(state.descriptor)
        shutdown(runtime)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows native event allocation")
def test_reviewer_interrupted_native_event_construction_is_owned(tmp_path, backend, monkeypatch):
    import ctypes

    from app.services import watcher_ingress_windows as module

    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True))
    create, overlapped = module.winapi.CreateEvent, module._Overlapped
    captured = []

    def allocated(*args):
        handle = create(*args)
        captured.append(handle)
        return handle

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(module.winapi, "CreateEvent", allocated)
    monkeypatch.setattr(module, "_Overlapped", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.start()
        monkeypatch.setattr(module, "_Overlapped", overlapped)
        assert len(captured) == 1
        shutdown(runtime)
        flags = ctypes.c_uint32()
        get = ctypes.WinDLL("kernel32", use_last_error=True).GetHandleInformation
        get.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        handle_live = bool(get(captured[0], ctypes.byref(flags)))
        print(dict(state=runtime._state, native_event_live=handle_live))
        assert runtime._state == "stopped" and not handle_live
    finally:
        monkeypatch.setattr(module, "_Overlapped", overlapped)
        for handle in captured:
            flags = ctypes.c_uint32()
            if get(handle, ctypes.byref(flags)):
                module.winapi.CloseHandle(handle)
        shutdown(runtime)

@pytest.mark.parametrize("reader", ["status", "preflight", "bounded"])
@pytest.mark.parametrize("failure", ["before", "noop", "after"])
def test_reader_disposal_is_verified_and_retryable(tmp_path, backend, monkeypatch, reader, failure):
    from app.services.scoped_budget import CancellationToken, ScopedBudget

    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    peer = binding(runtime)._owners.semantic.repository
    repository = binding(runtime, "space-1")._owners.semantic.repository
    connect = sqlite3.connect
    state = SimpleNamespace(target=None, fail=True, attempts=0)
    peer_rows = peer.load_chunks()
    # DELETE mode makes native reader locking observable independently of WAL.
    with closing(connect(repository.db_path)) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")

    class Connection(sqlite3.Connection):
        def close(self):
            if self is state.target:
                state.attempts += 1
                if state.fail and failure == "before":
                    raise OSError("reader close failed")
                if state.fail and failure == "noop":
                    return
            super().close()
            if self is state.target and state.fail and failure == "after":
                raise OSError("reader close raised after release")

    def tracked(*args, **kwargs):
        connection = connect(*args, factory=Connection, **kwargs)
        if state.target is None and "mode=ro" in str(args[0]):
            state.target = connection
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked)
    try:
        def read():
            if reader == "status":
                return repository.read_status()
            if reader == "preflight":
                return repository.validate_expected_binding()
            return repository.read_eligible_snapshot(
                ("same.md",), space_id=binding(runtime, "space-1").space_id,
                budget=ScopedBudget((binding(runtime, "space-1").space_id,)), cancel=CancellationToken(),
            )

        try:
            read()
        except (OSError, RuntimeError):
            pass
        assert state.target is not None
        assert live(state.target) == (failure != "after")
        assert repository._reader_connections.pending == (failure != "after")
        if failure != "after":
            for _ in range(2):
                with pytest.raises((OSError, RuntimeError)):
                    runtime.shutdown()
                assert runtime._state == "stopping" and live(state.target)
                assert repository._reader_connections.pending
            with pytest.raises((RuntimeError, SpaceError)):
                runtime.operations.scoped_list()
        state.fail = False
        runtime.shutdown()
        assert runtime._state == "stopped" and not live(state.target)
        assert not repository._reader_connections.pending
        attempts = state.attempts
        runtime.shutdown()
        assert state.attempts == attempts
        exclusive(repository.db_path)
        assert peer.load_chunks() == peer_rows
        repository.db_path.unlink()  # Real Windows sharing/handle assertion.
    finally:
        state.fail = False
        shutdown(runtime)


def test_reader_concurrent_retry_closes_original_once(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime, "space-1")._owners.semantic.repository
    state = SimpleNamespace(target=None, fail=True, calls=0)
    connect, read = sqlite3.connect, repository._read_status_connection
    entered, release, other_started = threading.Event(), threading.Event(), threading.Event()

    class Connection(sqlite3.Connection):
        def close(self):
            if self is state.target:
                state.calls += 1
                if state.fail:
                    raise OSError("reader close failed")
                entered.set()
                assert release.wait(20)
            super().close()

    def selected(connection):
        state.target = connection
        return read(connection)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
    monkeypatch.setattr(repository, "_read_status_connection", selected)
    try:
        assert repository.read_status().storage_error
        with pytest.raises(RuntimeError):
            runtime.shutdown()
        state.fail = False
        first = launch(runtime.shutdown)
        assert entered.wait(20)

        def second():
            other_started.set()
            runtime.shutdown()

        other = launch(second)
        assert other_started.wait(20)
        release.set()
        first.result(20)
        other.result(20)
        assert state.calls == 2 and not live(state.target)
        assert runtime._state == "stopped"
        exclusive(repository.db_path)
    finally:
        state.fail = False
        release.set()
        shutdown(runtime)


def test_reader_cursor_failure_is_disposed_before_parent_connection(tmp_path, backend, monkeypatch):
    from app.services.scoped_budget import CancellationToken, ScopedBudget

    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime, "space-1")._owners.semantic.repository
    sid = binding(runtime, "space-1").space_id
    connect = sqlite3.connect
    state = SimpleNamespace(cursor=None, parent=None, fail=True, calls=0, parent_closes=0)

    class Cursor(sqlite3.Cursor):
        def close(self):
            if self is state.cursor:
                state.calls += 1
                if state.fail:
                    raise OSError("cursor close failed")
            super().close()

    class Connection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            cursor = super().cursor(*args, factory=Cursor, **kwargs)
            if state.cursor is None:
                state.cursor, state.parent = cursor, self
            return cursor

        def close(self):
            if self is state.parent:
                state.parent_closes += 1
            super().close()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
    try:
        with pytest.raises((OSError, RuntimeError)):
            repository.read_eligible_snapshot(
                ("same.md",), space_id=sid, budget=ScopedBudget((sid,)), cancel=CancellationToken(),
            )
        assert state.cursor is not None and live(state.parent)
        assert state.parent_closes == 0
        with pytest.raises(RuntimeError):
            runtime.shutdown()
        assert runtime._state == "stopping" and live(state.parent)
        state.fail = False
        runtime.shutdown()
        assert not live(state.parent) and state.parent_closes == 1
        with pytest.raises(sqlite3.ProgrammingError):
            state.cursor.fetchone()
        exclusive(repository.db_path)
    finally:
        state.fail = False
        shutdown(runtime)


@pytest.mark.parametrize("initialization_fails", [False, True])
def test_spool_persistent_failure_keeps_original_workspace_until_connection_closes(
    tmp_path, backend, monkeypatch, initialization_fails,
):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    service = binding(runtime)._owners.semantic
    peer = binding(runtime, "space-1")._owners.semantic.repository
    before = peer.load_chunks()
    state = SimpleNamespace(target=None, path=None, fail=True, scripts=0, calls=0)
    entered, release, other_started = threading.Event(), threading.Event(), threading.Event()
    connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def executescript(self, script):
            state.scripts += 1
            if initialization_fails:
                raise KeyboardInterrupt()
            return super().executescript(script)

        def close(self):
            if self is state.target:
                state.calls += 1
                assert state.path.exists()  # No workspace disposal before SQLite.
                if state.fail:
                    raise OSError("spool close failed")
                entered.set()
                assert release.wait(20)
            super().close()

    def tracked(path, *args, **kwargs):
        if Path(str(path)).name == "scan.sqlite3":
            assert state.target is None  # Retry must never recreate work.
            result = connect(path, *args, factory=Connection, **kwargs)
            state.target, state.path = result, Path(path).parent
            return result
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    try:
        with pytest.raises(KeyboardInterrupt if initialization_fails else OSError):
            service.begin_sync()
        work = service._active_sync
        assert work is not None and work.temporary.name == str(state.path)
        assert live(state.target) and state.target.execute("SELECT 1").fetchone() == (1,)
        for _ in range(2):
            with pytest.raises((RuntimeError, OSError)):
                runtime.shutdown()
            assert runtime._state == "stopping" and service._active_sync is work
            assert state.path.exists() and live(state.target)
        assert binding(runtime, "space-1")._owners.semantic._sync_closed
        state.fail = False
        calls = state.calls
        first = launch(runtime.shutdown)
        assert entered.wait(20)

        def second():
            other_started.set()
            runtime.shutdown()

        other = launch(second)
        assert other_started.wait(20)
        release.set()
        first.result(20)
        other.result(20)
        assert state.calls == calls + 1
        assert runtime._state == "stopped" and not live(state.target)
        assert not state.path.exists() and service._active_sync is None
        assert state.scripts == 1 and peer.load_chunks() == before
        calls = state.calls
        runtime.shutdown()
        assert state.calls == calls
    finally:
        state.fail = False
        release.set()
        shutdown(runtime)


@pytest.mark.skipif(os.name == "nt", reason="POSIX scoped root descriptor")
def test_scoped_descriptor_persistent_failure_and_concurrent_shutdown(tmp_path, backend, monkeypatch):
    from app.core.resource_cleanup import DescriptorDisposal

    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    vault = binding(runtime)._owners.vault
    root = vault.root_binding_identity()
    state = SimpleNamespace(descriptor=None, fail=True, calls=0)
    opened, prepare = os.open, DescriptorDisposal._prepare_descriptor
    entered, release, other_started = threading.Event(), threading.Event(), threading.Event()

    def tracked(*args, **kwargs):
        descriptor = opened(*args, **kwargs)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == root and state.descriptor is None:
            state.descriptor = descriptor
        return descriptor

    def failed(descriptor):
        if descriptor == state.descriptor:
            state.calls += 1
            if state.fail:
                raise OSError("scoped root read-only preflight failed")
            entered.set()
            assert release.wait(20)
        return prepare(descriptor)

    monkeypatch.setattr(os, "open", tracked)
    monkeypatch.setattr(DescriptorDisposal, "_prepare_descriptor", staticmethod(failed))
    try:
        with pytest.raises((OSError, RuntimeError, SpaceError)):
            runtime.operations.scoped_list()
        assert vault._scoped_resources.pending
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping"
            os.fstat(state.descriptor)
        state.fail = False
        first = launch(runtime.shutdown)
        assert entered.wait(20)

        def second():
            other_started.set()
            runtime.shutdown()

        other = launch(second)
        assert other_started.wait(20)
        release.set()
        first.result(20)
        other.result(20)
        assert runtime._state == "stopped" and not vault._scoped_resources.pending
        closed_descriptor(state.descriptor)
        calls = state.calls
        runtime.shutdown()
        assert state.calls == calls
    finally:
        state.fail = False
        release.set()
        shutdown(runtime)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows native event allocation")
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_partial_native_event_failed_close_remains_owned_by_generation(tmp_path, backend, monkeypatch, interruption):
    from app.core.resource_cleanup import NativeHandles
    from app.services import watcher_ingress_windows as module

    runtime = compose_space_runtime(configuration(tmp_path, watch=True))
    create, close, overlapped = module.winapi.CreateEvent, module.winapi.CloseHandle, module._Overlapped
    state = SimpleNamespace(handle=None, fail=True, calls=0, allocations=0)

    def allocated(*args):
        state.allocations += 1
        assert state.handle is None
        state.handle = create(*args)
        return state.handle

    def interrupted(*args, **kwargs):
        raise interruption()

    def failed(handle):
        if handle == state.handle:
            state.calls += 1
            if state.fail:
                return 0
        return close(handle)

    monkeypatch.setattr(module.winapi, "CreateEvent", allocated)
    monkeypatch.setattr(module.winapi, "CloseHandle", failed)
    monkeypatch.setattr(module, "_Overlapped", interrupted)
    try:
        with pytest.raises(interruption):
            runtime.start()
        observer = runtime.watcher._observer
        producers = runtime.watcher._producers
        assert observer is not None and producers
        assert NativeHandles.valid(state.handle) and runtime._state == "stopping"
        monkeypatch.setattr(module, "_Overlapped", overlapped)
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping" and NativeHandles.valid(state.handle)
            assert runtime.watcher._observer is observer and runtime.watcher._producers == producers
        state.fail = False
        with pytest.raises((OSError, RuntimeError)):
            runtime.shutdown()
        assert NativeHandles.valid(state.handle)
        assert state.calls == 1  # Opaque failure is never numerically retried.
        assert close(state.handle)  # Test-only knowledge: failed() returned before OS release.
        runtime.shutdown()
        assert runtime._state == "stopped" and not NativeHandles.valid(state.handle)
        assert runtime.watcher._observer is None and state.allocations == 1
        calls = state.calls
        runtime.shutdown()
        assert state.calls == calls
    finally:
        state.fail = False
        monkeypatch.setattr(module, "_Overlapped", overlapped)
        shutdown(runtime)

@pytest.mark.skipif(os.name == "nt", reason="POSIX numeric descriptor reuse")
@pytest.mark.parametrize("recycle", [False, True])
def test_scoped_close_after_release_does_not_close_reused_root_descriptor(tmp_path, backend, monkeypatch, recycle):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    vault = binding(runtime)._owners.vault
    root = vault.root_binding_identity()
    state = SimpleNamespace(descriptor=None, calls=0, reused=None)
    opened, close = os.open, os.close

    def tracked(*args, **kwargs):
        descriptor = opened(*args, **kwargs)
        info = os.fstat(descriptor)
        if state.descriptor is None and (info.st_dev, info.st_ino) == root:
            state.descriptor = descriptor
        return descriptor

    def failed(descriptor):
        if descriptor == state.descriptor and state.calls == 0:
            state.calls += 1
            close(descriptor)
            if recycle:
                # Even reopening the SAME directory is a different file description.
                other = opened(vault.vault_root, os.O_RDONLY | os.O_DIRECTORY)
                state.reused = os.dup2(other, descriptor)
                if other != descriptor:
                    close(other)
            raise OSError("close raised after releasing descriptor")
        return close(descriptor)

    monkeypatch.setattr(os, "open", tracked)
    monkeypatch.setattr(os, "close", failed)
    try:
        with pytest.raises((OSError, RuntimeError, SpaceError)):
            runtime.operations.scoped_list()
        if recycle:
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping"
        else:
            runtime.shutdown()
            assert runtime._state == "stopped"
        assert state.calls == 1
        if recycle:
            info = os.fstat(state.reused)
            assert (info.st_dev, info.st_ino) == root
        else:
            closed_descriptor(state.descriptor)
    finally:
        monkeypatch.setattr(os, "close", close)
        if state.reused is not None:
            close(state.reused)
        shutdown(runtime)


@pytest.mark.parametrize("resource", ["stream", "iterator"])
def test_scoped_child_resource_failed_exit_remains_reachable(tmp_path, backend, monkeypatch, resource):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    vault = binding(runtime)._owners.vault
    state = SimpleNamespace(target=None, fail=True, calls=0, descriptor=None)
    factory = os.fdopen if resource == "stream" else os.scandir

    class Resource:
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            return self.inner.__enter__()

        def __exit__(self, *args):
            state.calls += 1
            if state.fail:
                raise OSError("scoped child exit failed")
            return self.inner.__exit__(*args)

        def fileno(self):
            return self.inner.fileno()

    def tracked(*args, **kwargs):
        result = factory(*args, **kwargs)
        if state.target is None:
            state.target = result
            if resource == "stream":
                state.descriptor = result.fileno()
            return Resource(result)
        return result

    monkeypatch.setattr(os, "fdopen" if resource == "stream" else "scandir", tracked)
    try:
        def operation():
            if resource == "iterator":
                return runtime.operations.scoped_list()
            return runtime.operations.scoped_literal_search("Independent")

        try:
            operation()
        except (OSError, RuntimeError, SpaceError):
            pass
        assert state.target is not None and vault._scoped_resources.pending
        if resource == "stream":
            assert not state.target.closed
            os.fstat(state.descriptor)
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping"
        state.fail = False
        runtime.shutdown()
        assert runtime._state == "stopped" and not vault._scoped_resources.pending
        if resource == "stream":
            assert state.target.closed
            closed_descriptor(state.descriptor)
        else:
            assert list(state.target) == []  # Closed real scandir, not just bookkeeping.
    finally:
        state.fail = False
        shutdown(runtime)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native path pin")
def test_scoped_windows_pin_failed_native_close_retries_exact_handle(tmp_path, backend, monkeypatch):
    from app.core.resource_cleanup import NativeHandles
    from app.services import _vault_writes as module

    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    vault = binding(runtime)._owners.vault
    dll = module.ctypes.WinDLL
    kernel = dll("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = module.ctypes.c_void_p
    kernel.CloseHandle.argtypes = (module.ctypes.c_void_p,)
    create, close = kernel.CreateFileW, kernel.CloseHandle
    state = SimpleNamespace(handle=None, fail=True, calls=0)

    def allocated(*args):
        handle = create(*args)
        if state.handle is None:
            state.handle = handle
        return handle

    def failed(handle):
        if handle == state.handle:
            state.calls += 1
            if state.fail:
                return 0
        return close(handle)

    monkeypatch.setattr(kernel, "CreateFileW", allocated)
    monkeypatch.setattr(kernel, "CloseHandle", failed)
    monkeypatch.setattr(module.ctypes, "WinDLL",
                        lambda name, *a, **kw: kernel if name == "kernel32" else dll(name, *a, **kw))
    try:
        try:
            runtime.operations.scoped_list()
        except (OSError, RuntimeError, SpaceError):
            pass
        assert state.handle is not None and NativeHandles.valid(state.handle)
        assert vault._scoped_resources.pending
        with pytest.raises((OSError, RuntimeError)):
            runtime.shutdown()
        assert runtime._state == "stopping"
        state.fail = False
        with pytest.raises((OSError, RuntimeError)):
            runtime.shutdown()
        assert NativeHandles.valid(state.handle) and state.calls == 1
        assert close(state.handle)
        runtime.shutdown()
        assert runtime._state == "stopped" and not NativeHandles.valid(state.handle)
        assert not vault._scoped_resources.pending
    finally:
        state.fail = False
        shutdown(runtime)

@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_reader_primary_interruption_survives_failed_close(tmp_path, backend, monkeypatch, interruption):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime, "space-1")._owners.semantic.repository
    connect = sqlite3.connect
    state = SimpleNamespace(target=None, fail=True)

    class Connection(sqlite3.Connection):
        def close(self):
            if self is state.target and state.fail:
                raise OSError("reader cleanup failed")
            super().close()

    def interrupted(connection):
        state.target = connection
        if not connection.in_transaction:
            connection.execute("BEGIN")
        connection.execute("SELECT * FROM notes").fetchall()
        raise interruption()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
    monkeypatch.setattr(repository, "_read_status_connection", interrupted)
    try:
        with pytest.raises(interruption):
            repository.read_status()
        assert live(state.target) and repository._reader_connections.pending
        with pytest.raises(RuntimeError):
            runtime.shutdown()
        assert runtime._state == "stopping"
        state.fail = False
        runtime.shutdown()
        assert runtime._state == "stopped" and not live(state.target)
        exclusive(repository.db_path)
    finally:
        state.fail = False
        shutdown(runtime)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native event ownership")
def test_native_event_concurrent_shutdown_serializes_original_handle(tmp_path, backend, monkeypatch):
    from app.core.resource_cleanup import NativeHandles
    from app.services import watcher_ingress_windows as module

    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True))
    create, prepare, overlapped = module.winapi.CreateEvent, NativeHandles._prepare_handle, module._Overlapped
    state = SimpleNamespace(handle=None, fail=True, calls=0)
    entered, release, other_started = threading.Event(), threading.Event(), threading.Event()

    def allocated(*args):
        assert state.handle is None
        state.handle = create(*args)
        return state.handle

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    def failed(owner, handle):
        if handle == state.handle:
            state.calls += 1
            if state.fail:
                raise OSError("certified read-only preflight failure")
            entered.set()
            assert release.wait(20)
        return prepare(owner, handle)

    monkeypatch.setattr(module.winapi, "CreateEvent", allocated)
    monkeypatch.setattr(NativeHandles, "_prepare_handle", failed)
    monkeypatch.setattr(module, "_Overlapped", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.start()
        assert NativeHandles.valid(state.handle) and runtime._state == "stopping"
        monkeypatch.setattr(module, "_Overlapped", overlapped)
        state.fail = False
        calls = state.calls
        first = launch(runtime.shutdown)
        assert entered.wait(20)

        def second():
            other_started.set()
            runtime.shutdown()

        other = launch(second)
        assert other_started.wait(20)
        release.set()
        first.result(20)
        other.result(20)
        assert runtime._state == "stopped" and not NativeHandles.valid(state.handle)
        assert state.calls == calls + 1
    finally:
        state.fail = False
        release.set()
        monkeypatch.setattr(module, "_Overlapped", overlapped)
        shutdown(runtime)

@pytest.mark.parametrize("interruption", [OSError, KeyboardInterrupt])
def test_scoped_fdopen_constructor_failure_releases_descriptor(tmp_path, backend, monkeypatch, interruption):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    state = SimpleNamespace(descriptor=None)

    def interrupted(descriptor, *args, **kwargs):
        state.descriptor = descriptor
        raise interruption()

    monkeypatch.setattr(os, "fdopen", interrupted)
    try:
        if interruption is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                runtime.operations.scoped_literal_search("Independent")
        else:
            runtime.operations.scoped_literal_search("Independent")
        assert state.descriptor is not None
        closed_descriptor(state.descriptor)
        runtime.shutdown()
        assert runtime._state == "stopped"
        assert not binding(runtime)._owners.vault._scoped_resources.pending
    finally:
        shutdown(runtime)


def test_failed_semantic_cleanup_does_not_skip_independent_scoped_cleanup(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    vault = binding(runtime)._owners.vault
    repository = binding(runtime, "space-1")._owners.semantic.repository
    connect, scandir, read = sqlite3.connect, os.scandir, repository._read_status_connection
    state = SimpleNamespace(reader=None, iterator=None, fail_iterator=True, fail_reader=True)

    class Iterator:
        def __enter__(self):
            return state.iterator.__enter__()

        def __exit__(self, *args):
            if state.fail_iterator:
                raise OSError("scoped iterator failed")
            return state.iterator.__exit__(*args)

    def tracked(*args):
        if state.iterator is None:
            state.iterator = scandir(*args)
            return Iterator()
        return scandir(*args)

    class Connection(sqlite3.Connection):
        def close(self):
            if self is state.reader and state.fail_reader:
                raise OSError("reader cleanup failed")
            super().close()

    def selected(connection):
        state.reader = connection
        return read(connection)

    monkeypatch.setattr(os, "scandir", tracked)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
    monkeypatch.setattr(repository, "_read_status_connection", selected)
    try:
        try:
            runtime.operations.scoped_list()
        except (OSError, RuntimeError, SpaceError):
            pass
        assert vault._scoped_resources.pending
        assert repository.read_status().storage_error
        state.fail_iterator = False
        with pytest.raises(RuntimeError):
            runtime.shutdown()
        assert runtime._state == "stopping" and live(state.reader)
        assert not vault._scoped_resources.pending and list(state.iterator) == []
        state.fail_reader = False
        runtime.shutdown()
        assert runtime._state == "stopped" and not live(state.reader)
    finally:
        state.fail_iterator = state.fail_reader = False
        shutdown(runtime)

@pytest.mark.parametrize("resource", [
    "stream", pytest.param("native_event", marks=pytest.mark.skipif(
        sys.platform != "win32", reason="Windows native event guard; stream variant covers POSIX descriptors",
    )),
])
def test_successful_guard_close_does_not_mistake_recycled_slot_for_live_ownership(
    tmp_path, backend, monkeypatch, resource,
):
    # Stream guard reuse is exercised on both platforms; native handles on Windows.
    from app.core.resource_cleanup import NativeHandles

    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=resource == "native_event")).start()
    runtime.scheduler.wait(20)
    state = SimpleNamespace(descriptor=None, guard=None, reused=None)
    events = []
    opened = os.fdopen
    duplicate, close = os.dup, os.close
    unrelated = None
    native_close = None
    if resource == "stream":
        other_path = tmp_path / "unrelated.bin"
        other_path.write_bytes(b"unrelated caller-owned file")
        unrelated = os.open(other_path, os.O_RDONLY)
        identity = os.fstat(unrelated)

        def tracked(descriptor, *args, **kwargs):
            if state.descriptor is None:
                state.descriptor = descriptor
            return opened(descriptor, *args, **kwargs)

        def guarded(descriptor):
            result = duplicate(descriptor)
            if descriptor == state.descriptor and state.guard is None:
                state.guard = result
            return result

        def recycled(descriptor):
            result = close(descriptor)
            if descriptor == state.guard and state.reused is None:
                state.reused = os.dup2(unrelated, descriptor)
            return result

        monkeypatch.setattr(os, "fdopen", tracked)
        monkeypatch.setattr(os, "dup", guarded)
        monkeypatch.setattr(os, "close", recycled)
    else:
        from app.services import watcher_ingress_windows as module

        native = runtime.watcher._producers[0]._native_read
        native_close = module.winapi.CloseHandle
        native_errors = []
        monkeypatch.setattr(threading, "excepthook", lambda args: native_errors.append(args.exc_type))

        def recycled(handle):
            leases = tuple(native._events._leases)
            is_guard = any(lease.resource.guard is not None and lease.resource.guard.value == handle
                           for lease in leases)
            result = native_close(handle)
            if is_guard and state.reused is None:
                state.guard = handle
                for _ in range(16_384):
                    events.append(module.winapi.CreateEvent(None, True, False, None))
                    if events[-1] == handle:
                        state.reused = handle
                        break
                assert state.reused == handle  # Observe actual native slot reuse.
            return result

        monkeypatch.setattr(module.winapi, "CloseHandle", recycled)
    try:
        if resource == "stream":
            with pytest.raises((OSError, RuntimeError, SpaceError)):
                runtime.operations.scoped_literal_search("Independent")
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping"
        assert state.reused == state.guard and state.reused is not None
        if resource == "stream":
            info = os.fstat(state.reused)
            assert (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino)
        else:
            assert NativeHandles.valid(state.reused)
    finally:
        if resource == "stream":
            monkeypatch.setattr(os, "close", close)
            if state.reused is not None:
                close(state.reused)
            close(unrelated)
        else:
            monkeypatch.setattr(module.winapi, "CloseHandle", native_close)
            for handle in events:
                native_close(handle)
        shutdown(runtime)
        if resource == "native_event":
            assert runtime._state == "stopped"
            assert all(issubclass(error, OSError) for error in native_errors)
