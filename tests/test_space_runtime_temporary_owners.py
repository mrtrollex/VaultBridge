"""R8 temporary-owner reachability through real E6 composition."""
import errno
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.space_runtime import compose_space_runtime
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration
from tests.test_space_runtime_resource_owners import exclusive, live, shutdown


@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, OSError])
def test_raw_note_fdopen_interruption_is_owned(tmp_path, backend, monkeypatch, close_fails, interruption):
    settings = configuration(tmp_path, 1)
    identity = (tmp_path / 'default' / 'same.md').stat()
    original, close = os.fdopen, os.close
    state = SimpleNamespace(fd=None, fail=close_fails)

    def interrupted(fd, *args, **kwargs):
        info = os.fstat(fd)
        if state.fd is None and (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
            state.fd = fd
            raise interruption()
        return original(fd, *args, **kwargs)

    def failed(fd):
        if fd == state.fd and state.fail:
            raise OSError("raw note descriptor close failed")
        return close(fd)

    runtime = compose_space_runtime(settings)
    monkeypatch.setattr(os, 'fdopen', interrupted)
    monkeypatch.setattr(os, 'close', failed)
    try:
        runtime.start()
        runtime.scheduler.wait(20)
        assert state.fd is not None
        if close_fails:
            for _ in range(2):
                with pytest.raises((OSError, RuntimeError)):
                    runtime.shutdown()
                assert runtime._state == "stopping"
                os.fstat(state.fd)
            assert binding(runtime)._owners.semantic._index_vault._scoped_resources.pending
        state.fail = False
        if close_fails:
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            os.fstat(state.fd)  # ADR 0010: restoring an opaque closer proves no outcome.
            close(state.fd)  # Fixture knows failed() never dispatched native close.
        shutdown(runtime)
        with pytest.raises(OSError) as error:
            os.fstat(state.fd)
        assert error.value.errno == errno.EBADF
        assert runtime._state == 'stopped'
        (tmp_path / 'default' / 'same.md').unlink()
    finally:
        state.fail = False
        monkeypatch.setattr(os, 'fdopen', original)
        monkeypatch.setattr(os, 'close', close)
        shutdown(runtime)
        try:
            os.fstat(state.fd)
        except (OSError, TypeError):
            pass
        else:
            os.close(state.fd)


@pytest.mark.parametrize('name', ['default', 'space-1'])
def test_disabled_inspection_reader_is_owned(tmp_path, backend, monkeypatch, name):
    settings = configuration(tmp_path)
    initial = compose_space_runtime(settings).start()
    initial.scheduler.wait(20)
    initial.shutdown()
    record = json.loads(settings.knowledge_spaces_json)
    for entry in record['spaces']:
        entry['indexing'] = 'disabled'
    disabled_settings = Settings.model_validate(
        settings.model_dump() | {'knowledge_spaces_json': json.dumps(record)},
    )
    runtime = compose_space_runtime(disabled_settings).start()
    connect = sqlite3.connect
    state = SimpleNamespace(target=None, fail=True)

    class Connection(sqlite3.Connection):
        def close(self):
            if state.fail:
                state.target = self
                raise OSError('reader close failure')
            super().close()

    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: connect(*a, factory=Connection, **kw))
    try:
        runtime.operations.inspect_index(binding(runtime, name).space_id)
        assert state.target is not None and live(state.target)
        assert runtime.resource is runtime.scheduler is runtime.watcher is None
        target = state.target
        for _ in range(3):
            runtime.operations.inspect_index(binding(runtime, name).space_id)
            assert state.target is target  # Failed ownership fences further reader allocation.
        calls = len(backend.calls)
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping" and live(target)
        assert len(backend.calls) == calls
        state.fail = False
        shutdown(runtime)
        assert not live(state.target) and runtime._state == 'stopped'
        database = tmp_path / (name + '-data') / 'semantic-index.sqlite3'
        exclusive(database)
        database.unlink()
    finally:
        state.fail = False
        shutdown(runtime)
        if state.target is not None:
            sqlite3.Connection.close(state.target)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows E3 native note pin')
def test_temporary_indexing_vault_pin_is_owned(tmp_path, backend, monkeypatch):
    import threading

    from app.core.resource_cleanup import NativeHandles
    from app.services.vault import VaultService
    from tests.test_semantic_resources import launch

    instances = []
    initialize, acquire, prepare = VaultService.__init__, NativeHandles.acquire_handle, NativeHandles._prepare_handle
    state = SimpleNamespace(handle=None, fail=True, closes=0)
    entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()

    def created(vault, *a, **kw):
        initialize(vault, *a, **kw)
        instances.append(vault)

    def allocated(owner, factory, *a, **kw):
        lease = acquire(owner, factory, *a, **kw)
        if any(isinstance(cell.cell_contents, Path) and cell.cell_contents.name == 'same.md'
               for cell in (getattr(factory, '__closure__', None) or ())):
            state.handle = lease.resource.number
        return lease

    def failed(owner, handle):
        if handle == state.handle:
            if state.fail:
                raise OSError('native note pin read-only preflight failed')
            state.closes += 1
            entered.set()
            assert release.wait(20)
        return prepare(owner, handle)

    monkeypatch.setattr(VaultService, '__init__', created)
    monkeypatch.setattr(NativeHandles, 'acquire_handle', allocated)
    monkeypatch.setattr(NativeHandles, '_prepare_handle', failed)
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    try:
        runtime.scheduler.wait(20)
        assert state.handle is not None and NativeHandles.valid(state.handle)
        failed_vaults = [vault for vault in instances if vault._scoped_resources.pending]
        assert failed_vaults
        assert binding(runtime)._owners.semantic._index_vault in failed_vaults
        original_handle = state.handle
        calls = len(backend.calls)
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping" and NativeHandles.valid(original_handle)
        assert state.handle == original_handle and len(backend.calls) == calls
        state.fail = False
        first = launch(runtime.shutdown)
        assert entered.wait(20)

        def second():
            second_entered.set()
            runtime.shutdown()

        other = launch(second)
        assert second_entered.wait(20)
        release.set()
        first.result(20)
        other.result(20)
        assert state.closes == 1
        assert not NativeHandles.valid(state.handle)
        assert all(not vault._scoped_resources.pending for vault in failed_vaults)
        assert runtime._state == 'stopped'
        (tmp_path / 'default' / 'same.md').unlink()
    finally:
        state.fail = False
        release.set()
        shutdown(runtime)
        for vault in instances:
            vault.close_scoped_resources()


@pytest.mark.parametrize('kind', ['descriptor', 'inspection'])
def test_concurrent_runtime_cleanup_retries_same_resource_once(tmp_path, backend, monkeypatch, kind):
    import threading

    from app.services.vault import NoteUnavailableError
    from tests.test_semantic_resources import launch

    settings = configuration(tmp_path, 1, disabled=('default',))
    if kind == 'inspection':
        from app.repositories.semantic import SemanticRepository
        repository = SemanticRepository(tmp_path / 'default-data' / 'semantic-index.sqlite3')
        repository.prepare_index('test')
    runtime = compose_space_runtime(settings).start()
    state = SimpleNamespace(fd=None, connection=None, fail=True, calls=0)
    entered, release, other_entered = threading.Event(), threading.Event(), threading.Event()
    from app.core.resource_cleanup import DescriptorDisposal

    fdopen, prepare, connect = os.fdopen, DescriptorDisposal._prepare_descriptor, sqlite3.connect

    def interrupted(fd, *a, **kw):
        state.fd = fd
        raise KeyboardInterrupt()

    def failed(fd):
        if fd == state.fd:
            state.calls += 1
            if state.fail:
                raise OSError('descriptor read-only preflight failed')
            entered.set()
            assert release.wait(20)
        return prepare(fd)

    class Connection(sqlite3.Connection):
        def close(self):
            state.connection = self
            state.calls += 1
            if state.fail:
                raise OSError('inspection close failed')
            entered.set()
            assert release.wait(20)
            super().close()

    try:
        if kind == 'descriptor':
            monkeypatch.setattr(os, 'fdopen', interrupted)
            monkeypatch.setattr(DescriptorDisposal, '_prepare_descriptor', staticmethod(failed))
            with pytest.raises((KeyboardInterrupt, NoteUnavailableError)):
                binding(runtime)._owners.vault.read_verified_markdown_snapshot('same.md')
        else:
            monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: connect(*a, factory=Connection, **kw))
            runtime.operations.inspect_index()
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == 'stopping'
        calls = state.calls
        state.fail = False
        first = launch(runtime.shutdown)
        assert entered.wait(20)

        def other():
            other_entered.set()
            runtime.shutdown()

        second = launch(other)
        assert other_entered.wait(20)
        release.set()
        first.result(20)
        second.result(20)
        assert runtime._state == 'stopped' and state.calls == calls + 1
        assert runtime.resource is runtime.scheduler is runtime.watcher is None
        if kind == 'descriptor':
            with pytest.raises(OSError) as error:
                os.fstat(state.fd)
            assert error.value.errno == errno.EBADF
        else:
            assert not live(state.connection)
    finally:
        state.fail = False
        release.set()
        monkeypatch.setattr(os, 'fdopen', fdopen)
        shutdown(runtime)

@pytest.mark.skipif(os.name == 'nt', reason='POSIX discovery descriptor; Windows uses native pins')
def test_adjacent_discovery_descriptor_close_is_runtime_owned(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=('default',))).start()
    vault = binding(runtime)._owners.vault
    root_identity = vault.root_binding_identity()
    state = SimpleNamespace(fd=None, fail=True)
    opened, close = os.open, os.close

    def tracked(*a, **kw):
        fd = opened(*a, **kw)
        info = os.fstat(fd)
        if state.fd is None and (info.st_dev, info.st_ino) == root_identity:
            state.fd = fd
        return fd

    def failed(fd):
        if fd == state.fd and state.fail:
            raise OSError('discovery root close failed')
        return close(fd)

    monkeypatch.setattr(os, 'open', tracked)
    monkeypatch.setattr(os, 'close', failed)
    try:
        with pytest.raises(OSError):
            list(vault._sorted_markdown_discoveries())
        assert state.fd is not None
        os.fstat(state.fd)
        state.fail = False
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == 'stopping'
            os.fstat(state.fd)
        close(state.fd)  # Fixture-only proof: injected close had no effects.
        runtime.shutdown()
        with pytest.raises(OSError) as error:
            os.fstat(state.fd)
        assert error.value.errno == errno.EBADF and runtime._state == 'stopped'
    finally:
        state.fail = False
        shutdown(runtime)
