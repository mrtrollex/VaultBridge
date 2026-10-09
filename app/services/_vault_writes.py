"""Local filesystem coordination used only by VaultService write boundaries."""

from __future__ import annotations

import ctypes
import hashlib
import os
import threading
from contextlib import contextmanager
from pathlib import Path

_THREAD_LOCK = threading.RLock()


class UnsafeWritePathError(Exception):
    """An opened filesystem object violates the write path policy."""


def open_windows_staged_file(path: Path) -> int:
    """Create staging with a handle that denies external writes and replacement."""
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, CREATE_NEW.
    handle = kernel.CreateFileW(str(path), 0xC0000000, 1, None, 1, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def open_root_directory(root: Path) -> int:
    """Open a resolved POSIX root without following swapped ancestor symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root.anchor, flags)
    try:
        for part in root.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def coordinated_write(root: Path):
    """Serialize cooperating VaultService writers across threads and processes.

    No note, capture cache, or persistent lock file is created. POSIX locks the
    vault directory inode; Windows uses a named kernel mutex for the resolved root.
    """
    with _THREAD_LOCK:
        if os.name == "nt":
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            digest = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
            handle = kernel.CreateMutexW(None, False, "Global\\VaultBridge-write-" + digest)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) not in (0, 0x80):
                    raise OSError("write_unavailable")
                try:
                    yield
                finally:
                    kernel.ReleaseMutex(handle)
            finally:
                kernel.CloseHandle(handle)
        else:
            import fcntl

            fd = open_root_directory(root)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)


@contextmanager
def windows_pinned_path(path: Path, *, probe=None):
    """Reject reparse points and deny rename/delete while a path is in use."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    # GENERIC_READ; attribute-only handles do not prevent directory renames.
    # Share read/write, but deliberately not delete.
    if probe is not None:
        probe()
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = (ctypes.c_byte * 52)()
        if probe is not None:
            probe()
        if not kernel.GetFileInformationByHandle(handle, information):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = ctypes.cast(information, ctypes.POINTER(wintypes.DWORD))[0]
        if attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
            raise UnsafeWritePathError("unsafe_destination")
        yield
    finally:
        kernel.CloseHandle(handle)
