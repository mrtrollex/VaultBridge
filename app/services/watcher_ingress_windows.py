"""One bounded overlapped read per shared Windows watchdog emitter.

ReadDirectoryChangesW with FILE_FLAG_OVERLAPPED acknowledges native queueing
before returning. Only that acknowledgment sets armed; a pre-call signal, open
handle or Python thread is never readiness evidence. Windows retains the change
buffer for the handle's lifetime, including between completed reads.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes.wintypes import BOOL, DWORD, HANDLE, LPVOID

from watchdog.observers import winapi

from app.core.resource_cleanup import NativeHandles, native_close_result, windows_file_allocation_failure


class _Overlapped(ctypes.Structure):
    # Native OVERLAPPED: the offset pair and Pointer are a UNION, not consecutive
    # fields. No offsets are used for directory reads. Keep the native ABI here.
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", DWORD), ("OffsetHigh", DWORD), ("hEvent", HANDLE)]


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
ReadDirectoryChangesW = _kernel.ReadDirectoryChangesW
ReadDirectoryChangesW.argtypes = (HANDLE, LPVOID, DWORD, BOOL, DWORD, ctypes.POINTER(DWORD),
                                 ctypes.POINTER(_Overlapped), LPVOID)
ReadDirectoryChangesW.restype = BOOL
GetOverlappedResult = _kernel.GetOverlappedResult
GetOverlappedResult.argtypes = (HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(DWORD), BOOL)
GetOverlappedResult.restype = BOOL
_SUBMIT = ReadDirectoryChangesW
CancelIoEx = _kernel.CancelIoEx
CancelIoEx.argtypes = (HANDLE, ctypes.POINTER(_Overlapped))
CancelIoEx.restype = BOOL


class NativeDirectoryRead:
    """Fixed 64,000-byte buffer, one event, one request; no helper thread."""

    def __init__(self):
        self.pending = False
        self._request_lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._request_condition = threading.Condition(self._request_lock)
        self._active_reader = None
        self._closing = False
        self.event = None
        self.overlapped = None
        self._events = NativeHandles()
        self.armed = threading.Event()
        try:
            self.buffer = ctypes.create_string_buffer(winapi.BUFFER_SIZE)
            event = self._events.acquire_handle(winapi.CreateEvent, None, True, False, None,
                                                closer=lambda handle: native_close_result(winapi.CloseHandle, handle))
            self.event = event.resource.number
            self.overlapped = _Overlapped(hEvent=self.event)
        except BaseException:
            try:
                self.close(None, time.monotonic() + 5.0)
            except BaseException:
                pass  # Preassigned emitter shell retains this exact native owner.
            raise

    @property
    def cleanup_pending(self):
        return self.pending or self._active_reader is not None or self._events.pending

    def read_directory_changes(self, handle, path, *, recursive):
        consumer = object()
        try:
            with self._request_condition:
                if self._closing or self._active_reader is not None:
                    raise RuntimeError("Native request owner is unavailable")
                self._active_reader = consumer
            return self._read_directory_changes(handle, path, recursive=recursive)
        finally:
            with self._request_condition:
                if self._active_reader is consumer:
                    self._active_reader = None
                    self._request_condition.notify_all()

    def _read_directory_changes(self, handle, path, *, recursive):
        with self._request_lock:
            if self._closing or self.pending or self.event is None:
                raise RuntimeError("Native request owner is unavailable")
            self.overlapped = _Overlapped(hEvent=self.event)
            self.pending = True  # BEFORE dispatch; an interruption is not non-submission.
            accepted = ReadDirectoryChangesW(
                handle, self.buffer, len(self.buffer), recursive, winapi.WATCHDOG_FILE_NOTIFY_FLAGS,
                None, ctypes.byref(self.overlapped), None,
            )
            if not accepted and ctypes.get_last_error() != 997:
                error = ctypes.get_last_error()
                if ReadDirectoryChangesW is _SUBMIT:
                    self.pending = False  # Trusted native negative result: no request accepted.
                raise ctypes.WinError(error)
        self.armed.set()  # AFTER the kernel accepted this exact handle's request.
        nbytes = DWORD()
        completed = GetOverlappedResult(handle, ctypes.byref(self.overlapped), ctypes.byref(nbytes), True)
        error = 0 if completed else ctypes.get_last_error()
        if completed or error == winapi.ERROR_OPERATION_ABORTED:
            self.pending = False
        if error == winapi.ERROR_OPERATION_ABORTED:
            return self.buffer.raw, 0
        if error:
            raise ctypes.WinError(error)
        return self.buffer.raw, nbytes.value

    def close(self, handle, deadline):
        with self._close_lock:
            with self._request_condition:
                self._closing = True
                self._close_request(handle, deadline)
                # Cancellation receipt is not the waiter's exit receipt. Release
                # the condition lock while waiting for this exact consumer.
                if not self._request_condition.wait_for(
                    lambda: self._active_reader is None, max(0, deadline - time.monotonic()),
                ):
                    raise RuntimeError("Native filesystem reader did not release its resources")
                self._events.drain(retire_active=True)
                self.event = None

    def _close_request(self, handle, deadline):
        # A failed read must never free OVERLAPPED/buffer/event while the kernel
        # can still access them. The consumer may still be waiting for completion;
        # close also waits for its exit before disposing the event.
        while self.pending:
            try:
                CancelIoEx(handle, ctypes.byref(self.overlapped))
            except OSError:
                pass
            nbytes = DWORD()
            if GetOverlappedResult(handle, ctypes.byref(self.overlapped), ctypes.byref(nbytes), False):
                self.pending = False
            elif ctypes.get_last_error() == winapi.ERROR_OPERATION_ABORTED:
                self.pending = False  # Verified cancellation, never cancellation initiation.
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Native filesystem request did not complete cancellation")
                winapi.WaitForSingleObjectEx(self.event, max(1, int(min(0.05, remaining) * 1000)), False)


def get_directory_handle(path):
    # Observe the native sentinel before watchdog's exception-only errcheck.
    create = NativeHandles._api().CreateFileW
    create.argtypes = (ctypes.c_wchar_p, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE)
    create.restype = HANDLE
    result = create(path, winapi.FILE_LIST_DIRECTORY, winapi.WATCHDOG_FILE_SHARE_FLAGS,
                             None, winapi.OPEN_EXISTING,
                             winapi.WATCHDOG_FILE_FLAGS | winapi.FILE_FLAG_OVERLAPPED, None)
    if result == ctypes.c_void_p(-1).value:
        return windows_file_allocation_failure(create, ctypes.WinError(ctypes.get_last_error()))
    return result


def close_directory_handle(handle):
    # Called under the emitter's handle lock AFTER read completion/cancellation.
    # watchdog's helper suppresses exceptions AND ignores FALSE from CloseHandle.
    # A failed call retains the original exclusively owned handle for retry.
    return native_close_result(winapi.CloseHandle, handle)
