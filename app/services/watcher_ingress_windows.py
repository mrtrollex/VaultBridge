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


class NativeDirectoryRead:
    """Fixed 64,000-byte buffer, one event, one request; no helper thread."""

    def __init__(self):
        self.armed = threading.Event()
        self.buffer = ctypes.create_string_buffer(winapi.BUFFER_SIZE)
        self.event = winapi.CreateEvent(None, True, False, None)
        self.overlapped = _Overlapped(hEvent=self.event)
        self.pending = False

    def read_directory_changes(self, handle, path, *, recursive):
        self.overlapped = _Overlapped(hEvent=self.event)
        accepted = ReadDirectoryChangesW(
            handle, self.buffer, len(self.buffer), recursive, winapi.WATCHDOG_FILE_NOTIFY_FLAGS,
            None, ctypes.byref(self.overlapped), None,
        )
        if not accepted and ctypes.get_last_error() != 997:  # ERROR_IO_PENDING also acknowledges submission.
            raise ctypes.WinError(ctypes.get_last_error())
        self.pending = True
        self.armed.set()  # AFTER the kernel accepted this exact handle's request.
        nbytes = DWORD()
        completed = GetOverlappedResult(handle, ctypes.byref(self.overlapped), ctypes.byref(nbytes), True)
        error = 0 if completed else ctypes.get_last_error()
        if error != 996:  # ERROR_IO_INCOMPLETE must retain pending storage for safe cleanup.
            self.pending = False
        if error == winapi.ERROR_OPERATION_ABORTED:
            return self.buffer.raw, 0
        if error:
            raise ctypes.WinError(error)
        return self.buffer.raw, nbytes.value

    def close(self, handle, deadline):
        # A failed read must never free OVERLAPPED/buffer/event while the kernel
        # can still access them. Called only after the Python read has exited.
        while self.pending:
            try:
                winapi.CancelIoEx(handle, None)
            except OSError:
                pass
            nbytes = DWORD()
            if GetOverlappedResult(handle, ctypes.byref(self.overlapped), ctypes.byref(nbytes), False):
                self.pending = False
            elif ctypes.get_last_error() != 996:
                self.pending = False  # Terminal completion, including cancellation.
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Native filesystem request did not complete cancellation")
                winapi.WaitForSingleObjectEx(self.event, max(1, int(min(0.05, remaining) * 1000)), False)
        if self.event is not None:
            if not winapi.CloseHandle(self.event):
                raise ctypes.WinError()
            self.event = None


def get_directory_handle(path):
    return winapi.CreateFileW(path, winapi.FILE_LIST_DIRECTORY, winapi.WATCHDOG_FILE_SHARE_FLAGS,
                             None, winapi.OPEN_EXISTING,
                             winapi.WATCHDOG_FILE_FLAGS | winapi.FILE_FLAG_OVERLAPPED, None)


def close_directory_handle(handle):
    # Called under the emitter's handle lock AFTER read completion/cancellation.
    # watchdog's helper suppresses exceptions AND ignores FALSE from CloseHandle.
    # A failed call retains the original exclusively owned handle for retry.
    if not winapi.CloseHandle(handle):
        raise ctypes.WinError()
