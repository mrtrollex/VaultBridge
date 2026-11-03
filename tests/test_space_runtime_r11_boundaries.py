"""Native R11 submission, allocation and whole-request accounting regressions."""
import ctypes
import os
import sys
import time

import pytest

from app.services.knowledge_spaces import SpaceError
from app.services.space_runtime import compose_space_runtime
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows overlapped request')
def test_interrupted_accepted_submission_retains_pending_memory(tmp_path, monkeypatch):
    from watchdog.observers import winapi

    from app.services import watcher_ingress_windows as module

    handle = module.get_directory_handle(str(tmp_path))
    reader = module.NativeDirectoryRead()
    submit = module.ReadDirectoryChangesW

    def interrupted(*args):
        accepted = submit(*args)
        assert accepted or ctypes.get_last_error() == 997
        raise KeyboardInterrupt()

    monkeypatch.setattr(module, 'ReadDirectoryChangesW', interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            reader.read_directory_changes(handle, str(tmp_path), recursive=True)
        count = module.DWORD()
        assert not module.GetOverlappedResult(handle, ctypes.byref(reader.overlapped), ctypes.byref(count), False)
        assert ctypes.get_last_error() == 996
        assert reader.pending and reader.cleanup_pending
        reader.close(handle, time.monotonic() + 5)
        assert not reader.pending and not reader.cleanup_pending
    finally:
        # The old pending flag is wrong; verify native cancellation even on failure.
        try:
            winapi.CancelIoEx(handle, None)
        except OSError:
            pass
        count = module.DWORD()
        module.GetOverlappedResult(handle, ctypes.byref(reader.overlapped), ctypes.byref(count), True)
        reader.close(handle, time.monotonic() + 5)
        winapi.CloseHandle(handle)


@pytest.mark.skipif(os.name == 'nt', reason='actual POSIX permission failure')
def test_sqlite_creation_permission_failure_has_no_phantom_owner(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repo = binding(runtime)._owners.semantic.repository
    repo.db_path.parent.mkdir()
    repo.db_path.parent.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            repo.get_metadata('index_state')
        assert not repo.db_path.exists()
        assert not repo._initializations
        repo.db_path.parent.chmod(0o700)
        assert repo.get_metadata('index_state') is None
        runtime.shutdown()
        assert runtime._state == 'stopped'
    finally:
        repo.db_path.parent.chmod(0o700)
        # Only the fixture knows the original native failure allocated nothing.
        for key, attempt in tuple(repo._initializations.items()):
            if attempt.descriptor is None and attempt.connection is None:
                del repo._initializations[key]
        runtime.shutdown()


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows CreateFileW failure')
def test_absent_watcher_directory_has_no_phantom_handle(tmp_path):
    from app.core.resource_cleanup import NativeHandles
    from app.services.watcher_ingress_windows import get_directory_handle
    owner = NativeHandles()
    try:
        with pytest.raises(OSError) as error:
            owner.acquire_handle(get_directory_handle, str(tmp_path / 'absent'))
        assert error.value.winerror == 2
        assert not owner.pending
        owner.drain()
    finally:
        for lease in tuple(owner._leases):
            assert lease.resource.number is None
            owner._forget(lease)  # Fixture verified real INVALID_HANDLE_VALUE.


@pytest.mark.skipif(os.name == 'nt', reason='POSIX scoped cleanup primitive accounting')
@pytest.mark.parametrize('ceiling', [0, 8, 9, 40, 128, 1024])
def test_scoped_list_whole_request_counts_cleanup(tmp_path, backend, monkeypatch, ceiling):
    from app.services import _scoped_federation as federation
    budget_type, budgets = federation.ScopedBudget, []

    def captured(*args, **kwargs):
        budget = budget_type(*args, **kwargs)
        budgets.append(budget)
        return budget

    monkeypatch.setattr(federation, 'ScopedBudget', captured)
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=('default',))).start()
    calls = []
    for name in ('open', 'fstat', 'dup', 'stat', 'lstat', 'readlink'):
        original = getattr(os, name)

        def counted(*args, _original=original, _name=name, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(os, name, counted)
    scandir = os.scandir

    class CountedEntry:
        def __init__(self, entry):
            self.entry = entry
            self.name = entry.name

        def stat(self, **kwargs):
            calls.append('entry.stat')
            return self.entry.stat(**kwargs)

    class CountedDirectory:
        def __init__(self, directory):
            self.directory = directory

        def __iter__(self):
            return self

        def __next__(self):
            return CountedEntry(next(self.directory))

        def close(self):
            self.directory.close()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def counted_scandir(*args):
        calls.append('scandir')
        return CountedDirectory(scandir(*args))

    monkeypatch.setattr(os, 'scandir', counted_scandir)
    try:
        try:
            result = runtime.operations.scoped_list(limits={'probes': (ceiling, ceiling)})
        except SpaceError as error:
            assert str(error) == 'unavailable_space'
            result = None
        print('observed primitives', len(calls), 'coverage', result)
        assert len(calls) <= ceiling
        assert budgets[0].total['probes'] == len(calls)
        assert not any(budgets[0].reserved.values())
        if ceiling == 1024:
            assert result.state == 'complete'
        assert not binding(runtime)._owners.vault._scoped_resources.pending
    finally:
        runtime.shutdown()


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows active completion consumer')
def test_close_retains_event_until_completion_consumer_exits(tmp_path, monkeypatch):
    import threading

    from app.core.resource_cleanup import NativeHandles
    from app.services import watcher_ingress_windows as module
    from tests.test_semantic_resources import launch

    handle = module.get_directory_handle(str(tmp_path))
    reader = module.NativeDirectoryRead()
    get = module.GetOverlappedResult
    completed, leave = threading.Event(), threading.Event()

    def held(*args):
        result = get(*args)
        if args[-1]:
            completed.set()
            assert leave.wait(10)
        return result

    monkeypatch.setattr(module, 'GetOverlappedResult', held)
    operation = launch(lambda: reader.read_directory_changes(handle, str(tmp_path), recursive=True))
    try:
        assert reader.armed.wait(5)
        event = reader.event
        closing = launch(lambda: reader.close(handle, time.monotonic() + 1))
        assert completed.wait(5)
        with pytest.raises(RuntimeError, match='reader did not release'):
            closing.result(5)
        assert NativeHandles.valid(event) and reader.cleanup_pending
        leave.set()
        operation.result(5)
        first = launch(lambda: reader.close(handle, time.monotonic() + 5))
        other = launch(lambda: reader.close(handle, time.monotonic() + 5))
        first.result(5)
        other.result(5)
        assert not reader.cleanup_pending and not NativeHandles.valid(event)
    finally:
        leave.set()
        module.CancelIoEx(handle, ctypes.byref(reader.overlapped))
        operation.result(5)
        reader.close(handle, time.monotonic() + 5)
        module.close_directory_handle(handle)


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows certified non-submission')
def test_certified_non_submission_releases_event(tmp_path):
    from app.services import watcher_ingress_windows as module
    reader = module.NativeDirectoryRead()
    with pytest.raises(OSError):
        reader.read_directory_changes(None, str(tmp_path), recursive=True)
    assert not reader.pending and not reader.armed.is_set()
    reader.close(None, time.monotonic() + 5)
    assert not reader.cleanup_pending


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows unresolved completion')
def test_unresolved_completion_keeps_original_request_resources(tmp_path, monkeypatch):
    from app.core.resource_cleanup import NativeHandles
    from app.services import watcher_ingress_windows as module
    handle = module.get_directory_handle(str(tmp_path))
    reader = module.NativeDirectoryRead()
    submit, get = module.ReadDirectoryChangesW, module.GetOverlappedResult

    def interrupted(*args):
        assert submit(*args)
        raise KeyboardInterrupt()

    monkeypatch.setattr(module, 'ReadDirectoryChangesW', interrupted)
    with pytest.raises(KeyboardInterrupt):
        reader.read_directory_changes(handle, str(tmp_path), recursive=True)
    event, buffer, overlapped = reader.event, reader.buffer, reader.overlapped

    def uncertain(*args):
        raise KeyboardInterrupt('no completion receipt')

    monkeypatch.setattr(module, 'GetOverlappedResult', uncertain)
    try:
        for _ in range(2):
            with pytest.raises(KeyboardInterrupt):
                reader.close(handle, time.monotonic() + 5)
            assert reader.pending and NativeHandles.valid(event)
            assert reader.buffer is buffer and reader.overlapped is overlapped
        monkeypatch.setattr(module, 'GetOverlappedResult', get)
        reader.close(handle, time.monotonic() + 5)
        assert not reader.cleanup_pending
    finally:
        monkeypatch.setattr(module, 'GetOverlappedResult', get)
        reader.close(handle, time.monotonic() + 5)
        module.close_directory_handle(handle)


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows rename-before-open rollback')
def test_missing_directory_during_startup_rolls_back_cleanly(tmp_path, backend, monkeypatch):
    from app.services import watcher_ingress_windows as module
    settings = configuration(tmp_path, 1, watch=True)
    runtime = compose_space_runtime(settings)
    root, away = tmp_path / 'default', tmp_path / 'away'
    get = module.get_directory_handle

    def absent(path):
        root.rename(away)
        try:
            return get(path)
        finally:
            away.rename(root)

    monkeypatch.setattr(module, 'get_directory_handle', absent)
    with pytest.raises(OSError) as error:
        runtime.start()
    assert error.value.winerror == 2
    runtime.shutdown()
    assert runtime._state == 'stopped' and runtime.watcher._observer is None
    monkeypatch.setattr(module, 'get_directory_handle', get)
    replacement = compose_space_runtime(settings).start()
    replacement.scheduler.wait(20)
    replacement.shutdown()
    assert replacement._state == 'stopped'


def test_nested_cleanup_credits_keep_original_space_accounting(tmp_path):
    from app.core.resource_cleanup import VaultResources, scoped_cleanup_budget
    from app.services.knowledge_spaces import SpaceId
    from app.services.scoped_budget import ScopedBudget
    path = tmp_path / 'note.md'
    path.write_bytes(b'owned')
    a, b = SpaceId('default'), SpaceId('other')
    budget = ScopedBudget((a, b), limits={'probes': (20, 20)})
    owner = VaultResources()
    with scoped_cleanup_budget(budget, a):
        budget.charge(a, 'probes')
        first = owner.open_descriptor(os.open, path, os.O_RDONLY)
        with scoped_cleanup_budget(budget, b):
            budget.charge(b, 'probes')
            second = owner.open_descriptor(os.open, path, os.O_RDONLY)
            assert budget.reserved == {a: 8, b: 8}
            owner.release(first)
            assert budget.used[a]['probes'] == 5
            assert budget.used[b]['probes'] == 1
            owner.release(second)
    assert not owner.pending and not any(budget.reserved.values())
    assert budget.total['probes'] == 10


@pytest.mark.parametrize('cancelled', [False, True])
def test_reserved_cleanup_survives_body_error_or_cancellation(tmp_path, cancelled):
    from app.core.resource_cleanup import VaultResources, scoped_cleanup_budget
    from app.services.knowledge_spaces import SpaceId
    from app.services.scoped_budget import CancellationToken, ScopedBudget
    sid = SpaceId('default')
    budget = ScopedBudget((sid,), limits={'probes': (9, 9)})
    owner, token = VaultResources(), CancellationToken()
    path = tmp_path / 'note.md'
    path.write_bytes(b'owned')
    with pytest.raises(SpaceError if cancelled else OSError):
        with scoped_cleanup_budget(budget, sid):
            budget.charge(sid, 'probes')
            with owner.descriptor(os.open, path, os.O_RDONLY):
                assert budget.remaining(sid, 'probes') == 0
                if cancelled:
                    token.cancel()
                    token.check()
                raise OSError('primary body failure')
    assert not owner.pending and budget.total['probes'] == 5
    assert not any(budget.reserved.values())
