"""R13 real DuplicateHandle access denial and certified/opaque guard outcomes."""
import ctypes
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from app.core.resource_cleanup import CleanupState, NativeHandles
from app.services.space_runtime import compose_space_runtime
from tests.test_semantic_resources import launch
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='real Windows DuplicateHandle')


def native_resources():
    from watchdog.observers import winapi
    api = NativeHandles._api()
    api.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    api.OpenProcess.restype = ctypes.c_void_p
    restricted = api.OpenProcess(0x1000, False, os.getpid())  # QUERY_LIMITED_INFORMATION; no DUP_HANDLE.
    assert restricted
    return api, restricted, winapi.CreateEvent(None, True, False, None)


@pytest.mark.parametrize('composed', [False, True])
def test_real_access_denied_guard_retries_original_resource(tmp_path, backend, monkeypatch, composed):
    api, restricted, event = native_resources()
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=('default',))).start() if composed else None
    owner = binding(runtime)._owners.vault._scoped_resources if composed else NativeHandles()
    lease = owner.acquire_handle(lambda: event)
    state = lease.resource
    witness = ctypes.c_void_p()
    process = api.GetCurrentProcess()
    assert api.DuplicateHandle(process, event, process, ctypes.byref(witness), 0, False, 2)
    control = SimpleNamespace(denied=True, prepared=0)
    entered, proceed, second_started = threading.Event(), threading.Event(), threading.Event()

    class RestrictedAPI:
        def GetCurrentProcess(self):
            return restricted if control.denied else process

        def __getattr__(self, name):
            return getattr(api, name)  # DuplicateHandle itself is the real, typed native function.

    monkeypatch.setattr(NativeHandles, '_api', staticmethod(lambda: RestrictedAPI()))
    prepare = owner._prepare_handle

    def blocked(number):
        prepare(number)
        if number == event and not control.denied:
            control.prepared += 1
            entered.set()
            assert proceed.wait(20)

    monkeypatch.setattr(owner, '_prepare_handle', blocked)
    cleanup = runtime.shutdown if composed else owner.drain
    try:
        for _ in range(2):
            with pytest.raises(OSError) as caught:
                owner.release(lease, retry=True)
            print(dict(error=caught.value.winerror, original_live=NativeHandles.valid(event),
                       guard=state.guard.value, guard_state=state.guard_release.state.value))
            assert caught.value.winerror == 5
            assert NativeHandles.valid(event) and state.number == event
            assert state.guard.value is None
            assert state.guard_release.state is CleanupState.VERIFIED_RELEASED
            assert state.release.state is CleanupState.VERIFIED_SAFE_TO_RETRY
            assert owner.pending
        if runtime:
            with pytest.raises(OSError) as caught:
                runtime.shutdown()
            assert caught.value.winerror == 5 and runtime._state == 'stopping'
        control.denied = False
        first = launch(cleanup)
        assert entered.wait(20)

        def second():
            second_started.set()
            cleanup()

        other = launch(second)
        assert second_started.wait(20)
        proceed.set()
        first.result(20)
        other.result(20)
        cleanup()
        assert control.prepared == 1 and not owner.pending
        assert state.number == event and state.guard.value is not None
        assert state.cleanup_state is CleanupState.TERMINAL_COMPLETE
        if runtime:
            assert runtime._state == 'stopped'
        for number in (event, state.guard.value):
            assert not NativeHandles.valid(number) or not NativeHandles._same(number, witness.value)
    finally:
        control.denied = False
        proceed.set()
        # Fixture knows the original/native allocations, including the pre-fix phantom-guard case.
        if owner.pending:
            if not state.released:
                api.CloseHandle(event)
            if state.guard is not None and state.guard.value is not None and not state.guard_released:
                api.CloseHandle(state.guard.value)
            owner._forget(lease)
        api.CloseHandle(witness.value)
        api.CloseHandle(restricted)
        if runtime:
            runtime.shutdown()


@pytest.mark.parametrize('boundary', ['before', 'after', 'false-empty', 'false-populated', 'false-native-looking'])
def test_opaque_guard_outcome_remains_fail_closed(tmp_path, backend, monkeypatch, boundary):
    api, restricted, event = native_resources()
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=('default',))).start()
    owner = binding(runtime)._owners.vault._scoped_resources
    lease = owner.acquire_handle(lambda: event)
    state, calls, hidden = lease.resource, [], []

    class NativeLooking(ctypes._CFuncPtr):
        _flags_ = api.DuplicateHandle._flags_
        _argtypes_ = tuple(api.DuplicateHandle.argtypes)
        _restype_ = ctypes.c_int

        def __call__(self, *args):
            calls.append(args)
            duplicate = ctypes.c_void_p()
            assert api.DuplicateHandle(args[0], args[1], args[2], ctypes.byref(duplicate), 0, False, 2)
            hidden.append(duplicate.value)  # Opaque side effect never written to owner's output cell.
            NativeLooking.__call__ = ctypes._CFuncPtr.__call__  # Post-call inspection alone is too late.
            ctypes.set_last_error(5)
            return 0

    native_looking = NativeLooking(ctypes.cast(api.DuplicateHandle, ctypes.c_void_p).value)

    class OpaqueAPI:
        def __getattr__(self, name):
            return getattr(api, name)

        def DuplicateHandle(self, *args):
            calls.append(args)
            if boundary in ('after', 'false-populated'):
                assert api.DuplicateHandle(*args)
            if boundary.startswith('false-'):
                ctypes.set_last_error(5)
                return 0
            raise KeyboardInterrupt('opaque duplication outcome')

    opaque = OpaqueAPI()
    if boundary == 'false-native-looking':
        opaque.DuplicateHandle = native_looking
    monkeypatch.setattr(NativeHandles, '_api', staticmethod(lambda: opaque))
    try:
        with pytest.raises(OSError if boundary.startswith('false-') else KeyboardInterrupt):
            owner.release(lease)
        assert state.guard_release.state is CleanupState.AMBIGUOUS
        assert NativeHandles.valid(event)
        for _ in range(2):
            with pytest.raises(OSError):
                runtime.shutdown()
            assert runtime._state == 'stopping' and owner.pending
        assert len(calls) == 1  # No unsafe second duplication or numeric cleanup attempt.
        assert all(NativeHandles.valid(number) for number in hidden)
        assert state.guard_release.state is CleanupState.AMBIGUOUS
        if state.guard.value is not None:
            assert NativeHandles.valid(state.guard.value)
    finally:
        # Only fixture has knowledge of these opaque callbacks' actual side effects.
        if not state.released:
            api.CloseHandle(event)
        if state.guard.value is not None and not state.guard_released:
            api.CloseHandle(state.guard.value)
        owner._forget(lease)
        for number in hidden:
            api.CloseHandle(number)
        api.CloseHandle(restricted)
        runtime.shutdown()
