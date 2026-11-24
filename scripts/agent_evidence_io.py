"""Open evidence by repository handle without following filesystem redirects."""

from __future__ import annotations

import ctypes
import os
import platform
import re
import stat
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path


@contextmanager
def repository_reader(root: Path):
    if sys.platform == "linux":
        with _linux_reader(root.absolute()) as reader:
            yield reader
    elif os.name == "nt":
        with _windows_reader(root.absolute()) as reader:
            yield reader
    else:
        raise OSError("safe evidence opening is unavailable on this platform")


@contextmanager
def _linux_reader(root: Path):
    # openat2 numbers for the supported Linux ABIs. Never fall back to a path open.
    if platform.machine().lower() not in {"x86_64", "aarch64", "armv7l", "i686", "riscv64"}:
        raise OSError("openat2 is unavailable for this architecture")
    if platform.machine().lower() == "x86_64" and ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError("openat2 is unavailable for this ABI")
    syscall = ctypes.CDLL(None, use_errno=True).syscall
    syscall.restype = ctypes.c_long

    class OpenHow(ctypes.Structure):
        _fields_ = [(field, ctypes.c_uint64) for field in ("flags", "mode", "resolve")]

    def open_beneath(directory: int, name: str, flags: int, *, same_mount: bool) -> int:
        # RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS (which includes magic links).
        resolve = 0x08 | 0x04
        if same_mount:
            resolve |= 0x01  # RESOLVE_NO_XDEV, including bind mounts
        how = OpenHow(flags | os.O_CLOEXEC, 0, resolve)
        fd = syscall(
            ctypes.c_long(437), ctypes.c_int(directory), ctypes.c_char_p(os.fsencode(name)),
            ctypes.byref(how), ctypes.c_size_t(ctypes.sizeof(how)),
        )
        if fd < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return fd

    with ExitStack() as stack:
        anchor = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        stack.callback(os.close, anchor)
        directory = open_beneath(
            anchor, root.as_posix().lstrip("/") or ".", os.O_PATH | os.O_DIRECTORY, same_mount=False,
        )
        stack.callback(os.close, directory)

        @contextmanager
        def reader(parts: tuple[str, ...]):
            # Resolve all parts in one scoped kernel lookup, including renamed ancestors.
            fd = open_beneath(directory, "/".join(parts), os.O_RDONLY | os.O_NONBLOCK, same_mount=True)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("evidence is not a regular file")
                with open(fd, "rb", closefd=False) as stream:
                    yield stream
            finally:
                os.close(fd)

        yield reader


@contextmanager
def _windows_reader(root: Path):
    import msvcrt
    from ctypes import wintypes

    if not re.fullmatch(r"[A-Za-z]:", root.drive) or not root.is_absolute():
        raise OSError("safe evidence opening requires a local drive")

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusOrPointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")
    volume_open = kernel.CreateFileW
    volume_open.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    volume_open.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    info = kernel.GetFileInformationByHandleEx
    info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    info.restype = wintypes.BOOL
    final_name = kernel.GetFinalPathNameByHandleW
    final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final_name.restype = wintypes.DWORD
    open_relative = nt.NtCreateFile
    open_relative.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
        wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
    ]
    open_relative.restype = wintypes.LONG
    to_winerror = nt.RtlNtStatusToDosError
    to_winerror.argtypes = [wintypes.LONG]
    to_winerror.restype = wintypes.ULONG
    share_read = 1
    share_write = 2
    read_attributes = 0x80
    directory_attribute = 0x10
    reparse_attribute = 0x400

    def verify_handle(handle: int, path: Path, *, directory: bool) -> None:
        attributes_and_tag = (wintypes.DWORD * 2)()
        if not info(handle, 9, ctypes.byref(attributes_and_tag), ctypes.sizeof(attributes_and_tag)):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = attributes_and_tag[0]
        if attributes & reparse_attribute or bool(attributes & directory_attribute) != directory:
            raise OSError("unexpected file type or reparse point")
        buffer = ctypes.create_unicode_buffer(32768)
        count = final_name(handle, buffer, len(buffer), 0)
        if not 0 < count < len(buffer) or os.path.normcase(buffer.value) != os.path.normcase(
            "\\\\?\\" + str(path)
        ):
            raise OSError("noncanonical evidence path")

    def open_part(parent: int, path: Path, *, directory: bool) -> int:
        # One component relative to the previously verified handle. Open any
        # reparse point itself, then deny it based on the returned handle.
        name = path.name
        length = len(name.encode("utf-16-le"))
        if length > 510:
            raise OSError("evidence path component is too long")
        buffer = ctypes.create_unicode_buffer(name)
        string = UnicodeString(length, length + 2, ctypes.cast(buffer, wintypes.LPWSTR))
        attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent, ctypes.pointer(string), 0x40)
        handle = wintypes.HANDLE()
        # SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_READ_DATA/FILE_LIST_DIRECTORY.
        access = 0x100000 | read_attributes | 1
        share = share_read | share_write if directory else share_read
        status = open_relative(
            ctypes.byref(handle), access, ctypes.byref(attributes), ctypes.byref(IoStatusBlock()),
            None, 0, share, 1, 0x00200000 | 0x20, None, 0,
        )
        if status < 0:
            raise ctypes.WinError(to_winerror(status))
        try:
            verify_handle(handle.value, path, directory=directory)
        except BaseException:
            close(handle)
            raise
        return handle.value

    with ExitStack() as stack:
        prefix = Path(root.anchor)
        # Backup semantics permits a directory open. Open reparse points without
        # following them. The local volume root is the only absolute native open.
        handle = volume_open(
            "\\\\?\\" + root.anchor, read_attributes | 1, share_read | share_write,
            None, 3, 0x02000000 | 0x00200000, None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        stack.callback(close, handle)
        verify_handle(handle, prefix, directory=True)
        for part in root.parts[1:]:
            prefix /= part
            handle = open_part(handle, prefix, directory=True)
            stack.callback(close, handle)

        @contextmanager
        def reader(parts: tuple[str, ...]):
            with ExitStack() as ancestors:
                directory, path = handle, root
                for part in parts[:-1]:
                    path /= part
                    directory = open_part(directory, path, directory=True)
                    ancestors.callback(close, directory)
                leaf = open_part(directory, path / parts[-1], directory=False)
                try:
                    fd = msvcrt.open_osfhandle(leaf, os.O_RDONLY | os.O_BINARY)
                except BaseException:
                    close(leaf)
                    raise
                ancestors.callback(os.close, fd)
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("evidence is not a regular file")
                with open(fd, "rb", closefd=False) as stream:
                    yield stream

        yield reader
