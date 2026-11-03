"""Native R9 identity and proof-guard retry reproductions."""
import errno
import os
import sys
from types import SimpleNamespace

import pytest

from app.services.space_runtime import compose_space_runtime
from app.services.vault import NoteUnavailableError
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration
from tests.test_space_runtime_resource_owners import shutdown


def test_stream_guard_transient_close_recovers_on_first_shutdown(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=('default',))).start()
    vault = binding(runtime)._owners.vault
    identity = (tmp_path / 'default' / 'same.md').stat()
    duplicate, close = os.dup, os.close
    state = SimpleNamespace(guard=None, fail=True)

    def observed(fd):
        result = duplicate(fd)
        info = os.fstat(fd)
        if state.guard is None and (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
            state.guard = result
        return result

    def failed(fd):
        if fd == state.guard and state.fail:
            raise OSError('proof guard close failed before release')
        return close(fd)

    monkeypatch.setattr(os, 'dup', observed)
    monkeypatch.setattr(os, 'close', failed)
    try:
        with pytest.raises((OSError, NoteUnavailableError)):
            vault.read_verified_markdown_snapshot('same.md')
        os.fstat(state.guard)
        state.fail = False
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == 'stopping' and vault._scoped_resources.pending
            os.fstat(state.guard)  # Original guard really remains live; not reported released.
    finally:
        state.fail = False
        shutdown(runtime)
        try:
            os.fstat(state.guard)
        except (OSError, TypeError):
            pass
        else:
            close(state.guard)


@pytest.mark.skipif(sys.platform != 'win32', reason='actual Windows watcher event proof')
def test_native_event_guard_transient_close_recovers(tmp_path, backend, monkeypatch):
    import threading

    from app.core.resource_cleanup import NativeHandles
    from app.services import watcher_ingress_windows as module

    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True)).start()
    runtime.scheduler.wait(20)
    native = runtime.watcher._producers[0]._native_read
    close = module.winapi.CloseHandle
    state = SimpleNamespace(guard=None, fail=True)
    thread_errors = []
    monkeypatch.setattr(threading, 'excepthook', lambda args: thread_errors.append(args.exc_type))

    def failed(handle):
        leases = tuple(native._events._leases)
        if state.guard is None:
            for lease in leases:
                guard = lease.resource.guard
                if guard is not None and guard.value == handle:
                    state.guard = handle
        if handle == state.guard and state.fail:
            raise OSError('native proof close failed before release')
        return close(handle)

    monkeypatch.setattr(module.winapi, 'CloseHandle', failed)
    try:
        with pytest.raises((OSError, RuntimeError)):
            runtime.shutdown()
        assert NativeHandles.valid(state.guard) and runtime._state == 'stopping'
        state.fail = False
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == 'stopping' and NativeHandles.valid(state.guard)
    finally:
        state.fail = False
        shutdown(runtime)
        if state.guard is not None and NativeHandles.valid(state.guard):
            close(state.guard)
        shutdown(runtime)
        assert runtime._state == 'stopped'
        assert all(issubclass(error, OSError) for error in thread_errors)


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows raw HANDLE conversion and value reuse')
def test_interrupted_crt_conversion_never_closes_reused_event(tmp_path, backend, monkeypatch):
    import msvcrt

    from watchdog.observers import winapi

    from app.core.resource_cleanup import NativeHandles

    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    original_kernel = repository._windows_kernel
    events = []
    state = SimpleNamespace(original=None, fail=True)

    def interrupted(handle, flags):
        state.original = handle
        raise KeyboardInterrupt()

    class Kernel:
        def __init__(self):
            self.api = original_kernel()
            self.CreateFileW = self.api.CreateFileW

        def CloseHandle(self, handle):
            result = self.api.CloseHandle(handle)
            if handle == state.original and state.fail:
                for _ in range(16_384):
                    events.append(winapi.CreateEvent(None, True, False, None))
                    if events[-1] == handle:
                        break
                assert handle in events
                raise OSError('release completed, numeric slot reused')
            return result

    monkeypatch.setattr(msvcrt, 'open_osfhandle', interrupted)
    monkeypatch.setattr(repository, '_windows_kernel', Kernel)
    try:
        with pytest.raises(KeyboardInterrupt):
            repository.get_metadata('index_state')
        assert not events  # Unreturned CRT transfer is ambiguous; do not invoke raw CloseHandle.
        assert NativeHandles.valid(state.original)
        state.fail = False
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == 'stopping' and NativeHandles.valid(state.original)
    finally:
        state.fail = False
        if state.original is not None and NativeHandles.valid(state.original):
            # Test-only proof: interrupted() never called the real converter.
            assert winapi.CloseHandle(state.original)
        shutdown(runtime)
        for event in events:
            if NativeHandles.valid(event):
                assert winapi.CloseHandle(event)

def test_certified_pre_dispatch_failure_retries_original_resource(tmp_path, backend, monkeypatch):
    from app.core.resource_cleanup import CleanupState

    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=('default',))).start()
    owner = binding(runtime)._owners.vault._scoped_resources
    lease = owner.open_descriptor(os.open, tmp_path / 'default' / 'same.md', os.O_RDONLY)
    descriptor = lease.resource.descriptor
    fstat = os.fstat
    state = SimpleNamespace(fail=True)

    def read_only_preflight(fd):
        if fd == descriptor and state.fail:
            raise OSError('read-only preflight failed before close dispatch')
        return fstat(fd)

    monkeypatch.setattr(os, 'fstat', read_only_preflight)
    try:
        with pytest.raises(OSError):
            owner.release(lease)
        assert lease.resource.cleanup_state is CleanupState.VERIFIED_SAFE_TO_RETRY
        fstat(descriptor)
        state.fail = False
        runtime.shutdown()
        assert runtime._state == 'stopped' and not owner.pending
        assert lease.resource.cleanup_state is CleanupState.TERMINAL_COMPLETE
        with pytest.raises(OSError) as error:
            fstat(descriptor)
        assert error.value.errno == errno.EBADF
    finally:
        state.fail = False
        shutdown(runtime)


@pytest.mark.parametrize('same_object', [False, True])
@pytest.mark.parametrize('raises', [False, True])
def test_fd_reuse_never_reauthorizes_original_allocation(tmp_path, backend, same_object, raises):
    from app.core.resource_cleanup import CleanupState, DescriptorDisposal, DescriptorState

    path = tmp_path / 'a'
    path.write_bytes(b'original')
    other_path = tmp_path / 'b'
    other_path.write_bytes(b'unrelated')
    original = os.open(path, os.O_RDONLY)
    witness = os.dup(original)
    other = os.open(other_path, os.O_RDONLY)
    owner = DescriptorDisposal()
    held = DescriptorState(path, descriptor=original, created=os.fstat(original))
    calls = []

    def replaced(fd):
        calls.append(fd)
        os.close(fd)
        os.dup2(witness if same_object else other, fd)
        if raises:
            raise OSError('released and reused')

    try:
        try:
            owner._release_descriptor(held, _close=replaced)
        except OSError:
            pass
        for _ in range(3):
            try:
                owner._release_descriptor(held)
            except OSError:
                pass
            os.fstat(original)  # Still the replacement, never a collateral close.
        assert calls == [original]
        if same_object:
            assert held.cleanup_state is CleanupState.AMBIGUOUS
            assert not held.descriptor_released
        else:
            assert held.descriptor_released
    finally:
        for fd in (original, witness, other):
            os.close(fd)
        if held.guard is not None and not held.guard_released:
            os.close(held.guard)

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows raw HANDLE allocation reuse')
@pytest.mark.parametrize('same_object', [False, True])
def test_raw_native_retry_preserves_replacement_allocation(tmp_path, backend, monkeypatch, same_object):
    import ctypes

    from watchdog.observers import winapi

    from app.core.resource_cleanup import CleanupState, NativeHandles
    from app.repositories.semantic import _InitializationOwnership

    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    api = NativeHandles._api()
    original = winapi.CreateEvent(None, True, False, None)
    witness = ctypes.c_void_p()
    process = api.GetCurrentProcess()
    assert api.DuplicateHandle(process, original, process, ctypes.byref(witness), 0, False, 2)
    events = [witness.value]
    calls = []
    kernel = repository._windows_kernel()

    class Kernel:
        def CloseHandle(self, handle):
            calls.append(handle)
            assert handle == original
            assert kernel.CloseHandle(handle)
            for _ in range(16_384):
                if same_object:
                    slot = ctypes.c_void_p()
                    assert api.DuplicateHandle(process, witness.value, process, ctypes.byref(slot), 0, False, 2)
                    replacement = slot.value
                else:
                    replacement = winapi.CreateEvent(None, True, False, None)
                events.append(replacement)
                if replacement == original:
                    break
            assert original in events
            raise OSError('original released; replacement allocation belongs to test')

    monkeypatch.setattr(repository, '_windows_kernel', Kernel)
    attempt = _InitializationOwnership(repository.db_path, native_handle=original)
    repository._remember_initialization(attempt)
    try:
        with pytest.raises(OSError):
            repository._dispose_initialization(attempt)
        for _ in range(3):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert NativeHandles.valid(original)
            assert runtime._state == 'stopping'
            assert attempt.native_release.state is CleanupState.AMBIGUOUS
        assert calls == [original]
    finally:
        for handle in events:
            assert winapi.CloseHandle(handle)
        # Now the fixture's replacement has gone; invalid slot is new release evidence.
        runtime.shutdown()


@pytest.mark.parametrize('after_release', [False, True])
def test_final_descriptor_guard_has_its_own_release_evidence(tmp_path, backend, monkeypatch, after_release):
    from app.core.resource_cleanup import CleanupState

    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=('default',))).start()
    owner = binding(runtime)._owners.vault._scoped_resources
    lease = owner.open_descriptor(os.open, tmp_path / 'default' / 'same.md', os.O_RDONLY)
    close = os.close
    calls = []

    def fail_guard(fd):
        if fd == lease.resource.guard:
            calls.append(fd)
            if after_release:
                close(fd)
            raise KeyboardInterrupt('guard release interrupted')
        return close(fd)

    monkeypatch.setattr(os, 'close', fail_guard)
    try:
        with pytest.raises(KeyboardInterrupt):
            owner.release(lease)
        assert lease.resource.descriptor_released
        guard = lease.resource.guard
        monkeypatch.setattr(os, 'close', close)
        if after_release:
            assert lease.resource.guard_release.state is CleanupState.VERIFIED_RELEASED
            runtime.shutdown()
            assert runtime._state == 'stopped' and not owner.pending
        else:
            assert lease.resource.guard_release.state is CleanupState.AMBIGUOUS
            for _ in range(2):
                with pytest.raises((OSError, RuntimeError)):
                    runtime.shutdown()
                assert runtime._state == 'stopping'
                os.fstat(guard)
            assert calls == [guard]
            close(guard)  # Fixture knows fail_guard did not release the allocation.
            runtime.shutdown()
        for fd in (lease.resource.descriptor, guard):
            with pytest.raises(OSError) as error:
                os.fstat(fd)
            assert error.value.errno == errno.EBADF
    finally:
        monkeypatch.setattr(os, 'close', close)
        shutdown(runtime)


@pytest.mark.parametrize('native', [False, pytest.param(True, marks=pytest.mark.skipif(
    sys.platform != 'win32', reason='Windows event guard allocation',
))])
def test_certified_final_guard_failure_retries_with_concurrent_shutdown(tmp_path, backend, monkeypatch, native):
    import threading

    from app.core.resource_cleanup import CleanupState, NativeHandles
    from tests.test_semantic_resources import launch

    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=('default',))).start()
    owner = binding(runtime)._owners.vault._scoped_resources
    entered, proceed, second_started = threading.Event(), threading.Event(), threading.Event()
    state = SimpleNamespace(fail=True, calls=0)
    witness = None
    if native:
        import ctypes

        from watchdog.observers import winapi

        lease = owner.acquire_handle(lambda: winapi.CreateEvent(None, True, False, None))
        original = lease.resource.number
        witness = ctypes.c_void_p()
        api = NativeHandles._api()
        process = api.GetCurrentProcess()
        assert api.DuplicateHandle(process, original, process, ctypes.byref(witness), 0, False, 2)
        prepare = owner._prepare_handle

        def preflight(number):
            if lease.resource.guard is not None and number == lease.resource.guard.value:
                check_guard()
            return prepare(number)

        monkeypatch.setattr(owner, '_prepare_handle', preflight)
    else:
        lease = owner.open_descriptor(os.open, tmp_path / 'default' / 'same.md', os.O_RDONLY)
        original = lease.resource.descriptor
        fstat = os.fstat

        def preflight(number):
            if number == lease.resource.guard:
                check_guard()
            return fstat(number)

        monkeypatch.setattr(os, 'fstat', preflight)

    def check_guard():
        state.calls += 1
        if state.fail:
            raise OSError('read-only guard preflight before close dispatch')
        entered.set()
        assert proceed.wait(20)

    try:
        with pytest.raises(OSError):
            owner.release(lease)
        assert lease.resource.cleanup_state is CleanupState.VERIFIED_SAFE_TO_RETRY
        guard = lease.resource.guard.value if native else lease.resource.guard
        state.fail = False
        first = launch(runtime.shutdown)
        assert entered.wait(20)

        def second():
            second_started.set()
            runtime.shutdown()

        other = launch(second)
        assert second_started.wait(20)
        proceed.set()
        first.result(20)
        other.result(20)
        runtime.shutdown()
        assert state.calls == 2 and not owner.pending and runtime._state == 'stopped'
        assert lease.resource.cleanup_state is CleanupState.TERMINAL_COMPLETE
        for number in (original, guard):
            if native:
                # Thread/Future completion can allocate a new Windows event in
                # this slot. Validity alone is not evidence of our allocation.
                assert not NativeHandles.valid(number) or not NativeHandles._same(number, witness.value)
            else:
                with pytest.raises(OSError) as error:
                    fstat(number)
                assert error.value.errno == errno.EBADF
    finally:
        state.fail = False
        proceed.set()
        shutdown(runtime)
        if witness is not None:
            assert api.CloseHandle(witness.value)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows watcher event guard')
def test_watcher_guard_certified_preflight_failure_recovers_first_retry(tmp_path, backend, monkeypatch):
    import threading

    from app.core.resource_cleanup import CleanupState, NativeHandles

    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True)).start()
    runtime.scheduler.wait(20)
    native = runtime.watcher._producers[0]._native_read
    event = next(iter(native._events._leases)).resource
    prepare = native._events._prepare_handle
    errors = []
    monkeypatch.setattr(threading, 'excepthook', lambda args: errors.append(args.exc_type))

    def preflight(number):
        if event.guard is not None and number == event.guard.value:
            raise OSError('read-only event guard preflight failed')
        return prepare(number)

    monkeypatch.setattr(native._events, '_prepare_handle', preflight)
    try:
        with pytest.raises((OSError, RuntimeError)):
            runtime.shutdown()
        assert runtime._state == 'stopping'
        assert event.release.state is CleanupState.VERIFIED_RELEASED
        assert event.guard_release.state is CleanupState.VERIFIED_SAFE_TO_RETRY
        assert NativeHandles.valid(event.guard.value)
        calls = len(backend.calls)
        monkeypatch.setattr(native._events, '_prepare_handle', prepare)
        runtime.shutdown()
        assert runtime._state == 'stopped' and not native._events.pending
        assert event.cleanup_state is CleanupState.TERMINAL_COMPLETE
        assert len(backend.calls) == calls
        assert all(issubclass(error, OSError) for error in errors)
    finally:
        monkeypatch.setattr(native._events, '_prepare_handle', prepare)
        shutdown(runtime)
