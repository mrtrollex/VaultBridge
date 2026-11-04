"""Private owner-local resource disposal; no global queue, worker or GC recovery."""

import errno
import os
import sqlite3
import sys
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock, RLock

_scoped_cleanup = ContextVar("scoped_cleanup_reservation", default=None)
_cleanup_credit = ContextVar("active_cleanup_credit", default=None)


def cleanup_probe():
    credit = _cleanup_credit.get()
    if credit is not None:
        credit.probe()


def reserve_cleanup():
    scope = _scoped_cleanup.get()
    return None if scope is None else scope[0].reserve_cleanup(scope[1])


@contextmanager
def scoped_cleanup_budget(budget, sid):
    token = _scoped_cleanup.set((budget, sid))
    try:
        yield
    finally:
        _scoped_cleanup.reset(token)


@contextmanager
def cleanup_accounting(credit):
    token = _cleanup_credit.set(credit)
    try:
        yield
    finally:
        _cleanup_credit.reset(token)
        if credit is not None:
            credit.finish()


class CleanupIncompleteError(OSError):
    def __init__(self):
        super().__init__("Resource cleanup is incomplete")


class CleanupState(str, Enum):
    OWNED_OPEN = "owned_open"
    RELEASE_IN_PROGRESS = "release_in_progress"
    VERIFIED_RELEASED = "verified_released"
    VERIFIED_SAFE_TO_RETRY = "verified_safe_to_retry"
    AMBIGUOUS = "ambiguous_release_outcome"
    TERMINAL_COMPLETE = "terminal_cleanup_complete"


@dataclass
class ReleaseEvidence:
    """One allocation's history; object equality never restores ownership."""

    state: CleanupState = CleanupState.OWNED_OPEN

    def run(self, release, absent, *, prepare=None, number=None):
        if self.state is CleanupState.VERIFIED_RELEASED:
            return
        if self.state in (CleanupState.AMBIGUOUS, CleanupState.RELEASE_IN_PROGRESS):
            if absent():
                self.state = CleanupState.VERIFIED_RELEASED
                return
            self.state = CleanupState.AMBIGUOUS
            raise CleanupIncompleteError
        try:
            if absent():
                self.state = CleanupState.VERIFIED_RELEASED
                return
            if prepare is not None:
                prepare()  # Owner-controlled read-only work; release not dispatched.
        except BaseException:
            self.state = CleanupState.VERIFIED_SAFE_TO_RETRY
            raise
        epoch = object()
        context = _release_context.set((epoch, number))
        self.state = CleanupState.RELEASE_IN_PROGRESS  # BEFORE opaque dispatch.
        try:
            verified = release()
            native_success = (isinstance(verified, _NativeRelease)
                              and verified.epoch is epoch and verified.number == number)
            if not native_success and not absent():
                raise CleanupIncompleteError
            self.state = CleanupState.VERIFIED_RELEASED
        except BaseException:
            self.state = CleanupState.AMBIGUOUS
            try:
                if absent():
                    self.state = CleanupState.VERIFIED_RELEASED
            except BaseException:
                pass
            raise
        finally:
            _release_context.reset(context)


@dataclass(frozen=True)
class _NativeRelease:
    epoch: object
    number: int


_release_context = ContextVar("private_native_release", default=None)
_OS_CLOSE = os.close
_OS_OPEN = os.open
_OS_DUP = os.dup
_OS_PIPE = os.pipe


@dataclass(frozen=True)
class NoAllocation:
    """A native adapter observed its documented failure sentinel, not an exception."""

    error: BaseException


def descriptor_allocation(factory, *args, _exclusive=False, **kwargs):
    """Keep negative native results distinct from interrupted opaque acquisition.

    An interposed Python factory keeps the existing ambiguous-exception contract.
    Known primitives use the same native descriptor table, flags and errno, but
    expose their negative result before converting it to the filesystem error.
    """
    if factory is not _OS_OPEN and not (os.name != "nt" and factory in (_OS_DUP, _OS_PIPE)):
        return factory(*args, **kwargs)
    import ctypes

    # Keep this adapter restricted to the private read/dup paths. Creation and
    # unusual flags retain Python's argument validation and existing ownership.
    exclusive = (_exclusive and os.name != "nt"
                 and args[1] == os.O_CREAT | os.O_EXCL | os.O_WRONLY) if factory is _OS_OPEN else False
    if factory is _OS_OPEN and args[1] & (os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_RDWR) and not exclusive:
        return factory(*args, **kwargs)
    library = ctypes.CDLL("ucrtbase" if os.name == "nt" else None, use_errno=True)
    pair = None
    if factory is _OS_PIPE:
        pair = (ctypes.c_int * 2)(-1, -1)
        function = library.pipe2
        function.argtypes = (ctypes.POINTER(ctypes.c_int), ctypes.c_int)
        values = (pair, os.O_CLOEXEC)
    elif factory is _OS_DUP:
        import fcntl

        function = library.fcntl
        function.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int)
        values = (args[0], fcntl.F_DUPFD_CLOEXEC, 0)
    else:
        path, flags = args[:2]
        mode = args[2] if len(args) == 3 else kwargs.get("mode", 0o777)
        if os.name == "nt":
            function = library._wopen
            function.argtypes = (ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int)
            path = os.fsdecode(path)
            flags |= os.O_NOINHERIT
            values = (path, flags, mode)
        else:
            path = os.fsencode(path)
            flags |= os.O_CLOEXEC
            if kwargs.get("dir_fd") is not None:
                function = library.openat
                function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint)
                values = (kwargs["dir_fd"], path, flags, mode)
            else:
                function = library.open
                function.argtypes = (ctypes.c_char_p, ctypes.c_int, ctypes.c_uint)
                values = (path, flags, mode)
        if ("\0" if isinstance(path, str) else b"\0") in path:
            return NoAllocation(ValueError("embedded null character"))
        try:
            sys.audit("open", args[0], None, flags)
        except BaseException as error:
            return NoAllocation(error)  # Kernel allocation has not been dispatched.
    while True:
        result = function(*values)
        if result >= 0 or ctypes.get_errno() != errno.EINTR:
            break  # Match os.open/dup's retry of certified interrupted syscalls.
    if result < 0:
        code = ctypes.get_errno()
        return NoAllocation(OSError(code, os.strerror(code), args[0] if factory is _OS_OPEN else None))
    return tuple(pair) if pair is not None else result


def _windows_address(name):
    if os.name == "nt":
        import ctypes

        return ctypes.cast(getattr(ctypes.WinDLL("kernel32", use_last_error=True), name), ctypes.c_void_p).value
    return None


_WINDOWS_CLOSE_ADDRESS = _windows_address("CloseHandle")
_WINDOWS_FILE_ADDRESS = _windows_address("CreateFileW")
_WINDOWS_EVENT_ADDRESS = _windows_address("CreateEventW")
_WINDOWS_DUPLICATE_ADDRESS = _windows_address("DuplicateHandle")


def event_allocation(factory, *args, **kwargs):
    """Observe CreateEventW's NULL before watchdog converts it to an exception."""
    import ctypes

    if os.name == "nt" and isinstance(factory, ctypes._CFuncPtr) and (
        ctypes.cast(factory, ctypes.c_void_p).value == _WINDOWS_EVENT_ADDRESS
        and factory.restype is ctypes.c_void_p
    ):
        from watchdog.observers import winapi

        if getattr(factory, "errcheck", None) not in (None, winapi._errcheck_handle):
            return factory(*args, **kwargs)  # Opaque acquisition remains ambiguous.
        native = ctypes.WinDLL("kernel32", use_last_error=True).CreateEventW
        native.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
        native.restype = ctypes.c_void_p
        result = native(*args, **kwargs)
        if result is None:  # CreateEventW fails with NULL, not INVALID_HANDLE_VALUE.
            return NoAllocation(ctypes.WinError(ctypes.get_last_error()))
        return result
    return factory(*args, **kwargs)



def windows_file_allocation_failure(function, error):
    """Only the real CreateFileW's negative result certifies no allocation."""
    import ctypes

    if (isinstance(function, ctypes._CFuncPtr) and function.restype is ctypes.c_void_p
            and getattr(function, "errcheck", None) is None
            and ctypes.cast(function, ctypes.c_void_p).value == _WINDOWS_FILE_ADDRESS):
        return NoAllocation(error)
    raise error  # Opaque failed-return adapter has no authoritative receipt.


def native_close_result(function, number):
    """Normal return of the actual platform close, not an opaque wrapper receipt.

    Exceptions never carry a no-effect certificate. Callers must already own the
    allocation, serialize disposal, and never dispatch this for ambiguous history.
    """
    import ctypes

    trusted = function is _OS_CLOSE
    if os.name == "nt" and isinstance(function, ctypes._CFuncPtr):
        trusted = (function.restype in (ctypes.c_int, ctypes.c_long)
                   and function.argtypes is not None and tuple(function.argtypes) == (ctypes.c_void_p,)
                   and getattr(function, "errcheck", None) is None
                   and ctypes.cast(function, ctypes.c_void_p).value == _WINDOWS_CLOSE_ADDRESS)
    result = function(number)
    if os.name == "nt" and function is not _OS_CLOSE and result is not None and not result:
        # ctypes maintains a separate last-error copy only for use_last_error
        # functions. watchdog's CloseHandle uses the ordinary native error slot.
        copied_error = (isinstance(function, ctypes._CFuncPtr)
                        and function._flags_ & ctypes._FUNCFLAG_USE_LASTERROR)
        raise ctypes.WinError(ctypes.get_last_error() if copied_error else None)
    context = _release_context.get()
    if context is None:
        return None
    epoch, expected = context
    if trusted and number == expected:
        return _NativeRelease(epoch, number)
    if isinstance(result, _NativeRelease) and result.epoch is epoch and result.number == number:
        return result
    return None


def _fd_absent(number):
    cleanup_probe()
    try:
        os.fstat(number)
    except OSError as error:
        if error.errno == errno.EBADF:
            return True
        raise
    return False


@dataclass(eq=False)
class DescriptorState:
    path: Path | None
    credit: object = field(default=None, kw_only=True)
    descriptor: int | None = None
    native_handle: int | None = None
    native_transfer_ambiguous: bool = False
    native_release: ReleaseEvidence = field(default_factory=ReleaseEvidence)
    descriptor_release: ReleaseEvidence = field(default_factory=ReleaseEvidence)
    guard_release: ReleaseEvidence = field(default_factory=ReleaseEvidence)
    created: os.stat_result | None = None
    descriptor_released: bool = False
    descriptor_failed: bool = False
    descriptor_uncertain: bool = False
    guard: int | None = None
    guard_released: bool = False
    guard_uncertain: bool = False
    retired: bool = False
    preserve_storage: bool = False
    lock: object = field(default_factory=RLock)

    @property
    def cleanup_state(self):
        if self.native_transfer_ambiguous:
            return CleanupState.AMBIGUOUS
        if self.descriptor_released and self.guard_released and self.native_handle is None:
            return CleanupState.TERMINAL_COMPLETE
        phases = (self.native_release.state, self.descriptor_release.state, self.guard_release.state)
        for phase in (CleanupState.AMBIGUOUS, CleanupState.RELEASE_IN_PROGRESS,
                      CleanupState.VERIFIED_SAFE_TO_RETRY):
            if phase in phases:
                return phase
        return CleanupState.OWNED_OPEN

    def __iter__(self):
        return iter((self.path, self.descriptor, self.created))

    def __getitem__(self, index):
        return (self.path, self.descriptor, self.created)[index]


class DescriptorDisposal:
    _cleanup_error = CleanupIncompleteError

    @staticmethod
    def _windows_kernel():
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                       ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel.CloseHandle.restype = ctypes.c_int
        return kernel


    @staticmethod
    def _prepare_descriptor(descriptor):
        """Owner-controlled read-only preparation; never closes/transfers an FD."""
        cleanup_probe()
        return os.fstat(descriptor)

    @staticmethod
    def _descriptor_current(attempt):
        cleanup_probe()
        try:
            held = os.fstat(attempt.descriptor)
        except OSError as error:
            if error.errno == errno.EBADF:
                return False
            raise
        created = attempt.created
        return created is None or (held.st_dev, held.st_ino) == (
            created.st_dev, created.st_ino,
        )


    @staticmethod
    def _same_descriptor(one, other):
        # Same proof as E5's close-time guard: inode identity alone cannot
        # distinguish a recycled descriptor reopened onto the SAME file.
        import ctypes

        if os.name == "nt":
            import msvcrt

            compare = ctypes.WinDLL("kernelbase", use_last_error=True).CompareObjectHandles
            compare.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
            compare.restype = ctypes.c_int
            return bool(compare(msvcrt.get_osfhandle(one), msvcrt.get_osfhandle(other)))
        if sys.platform.startswith("linux") and os.uname().machine == "x86_64":
            result = ctypes.CDLL(None, use_errno=True).syscall(312, os.getpid(), os.getpid(), 0, one, other)
            return None if result < 0 else result == 0
        return None


    def _release_guard(self, attempt):
        if attempt.guard is None or attempt.guard_released:
            if attempt.guard_release.state is CleanupState.AMBIGUOUS:
                raise self._cleanup_error
            attempt.guard_released = True
            return
        try:
            attempt.guard_release.run(
                lambda: native_close_result(os.close, attempt.guard),
                lambda: _fd_absent(attempt.guard), number=attempt.guard,
            )
        finally:
            attempt.guard_uncertain = attempt.guard_release.state is CleanupState.AMBIGUOUS
            attempt.guard_released = attempt.guard_release.state is CleanupState.VERIFIED_RELEASED

    def _release_descriptor(self, attempt, *, _close=None, _prepare=None):
        with cleanup_accounting(attempt.credit):
            return self._dispose_descriptor(attempt, _close=_close, _prepare=_prepare)

    def _dispose_descriptor(self, attempt, *, _close=None, _prepare=None):
        with attempt.lock:
            if attempt.descriptor_uncertain and attempt.descriptor_release.state is CleanupState.OWNED_OPEN:
                attempt.descriptor_release.state = CleanupState.AMBIGUOUS
            if attempt.guard_uncertain and attempt.guard_release.state is CleanupState.OWNED_OPEN:
                attempt.guard_release.state = CleanupState.AMBIGUOUS
            if attempt.native_transfer_ambiguous and attempt.descriptor is None:
                raise self._cleanup_error  # An unreturned CRT descriptor is a separate obligation.
            if attempt.descriptor is not None:
                attempt.native_transfer_ambiguous = False
                attempt.native_handle = None  # Completed CRT handoff owns its native handle.
            if attempt.native_handle is None and attempt.native_release.state is CleanupState.AMBIGUOUS:
                raise self._cleanup_error
            if attempt.native_handle is not None:
                try:
                    attempt.native_release.run(
                        lambda: native_close_result(self._windows_kernel().CloseHandle, attempt.native_handle),
                        lambda: not NativeHandles.valid(attempt.native_handle), number=attempt.native_handle,
                    )
                except BaseException:
                    attempt.descriptor_failed = True
                    raise
                attempt.native_handle = None
            if attempt.descriptor is None or attempt.descriptor_released:
                if attempt.descriptor is None and attempt.descriptor_release.state is CleanupState.AMBIGUOUS:
                    raise self._cleanup_error
                attempt.descriptor_released = True
                self._release_guard(attempt)
                return

            def prepare():
                # This phase never invokes a close or ownership transfer.
                identity = self._prepare_descriptor(attempt.descriptor)
                if attempt.created is None:
                    attempt.created = identity
                if attempt.guard is None:
                    if attempt.guard_release.state is CleanupState.AMBIGUOUS:
                        raise self._cleanup_error
                    attempt.guard_release.state = CleanupState.AMBIGUOUS
                    cleanup_probe()
                    result = descriptor_allocation(os.dup, attempt.descriptor)
                    if isinstance(result, NoAllocation):
                        attempt.guard_release.state = CleanupState.VERIFIED_RELEASED
                        raise result.error
                    attempt.guard = result
                    attempt.guard_release.state = CleanupState.OWNED_OPEN
                if _prepare is not None:
                    _prepare()  # Non-owning stream cleanup precedes native FD dispatch.

            try:
                attempt.descriptor_release.run(
                    (lambda: _close(attempt.descriptor)) if _close is not None else
                    (lambda: native_close_result(os.close, attempt.descriptor)),
                    lambda: not self._descriptor_current(attempt), prepare=prepare, number=attempt.descriptor,
                )
            except BaseException:
                attempt.descriptor_failed = True
                attempt.preserve_storage = True
                attempt.descriptor_uncertain = attempt.descriptor_release.state is CleanupState.AMBIGUOUS
                attempt.descriptor_released = attempt.descriptor_release.state is CleanupState.VERIFIED_RELEASED
                if attempt.descriptor_released:
                    try:
                        self._release_guard(attempt)
                    except BaseException:
                        pass  # Keep both obligations; preserve original failure.
                raise
            attempt.descriptor_released = True
            self._release_guard(attempt)
            attempt.descriptor_failed = False



@dataclass(eq=False)
class _Lease:
    resource: object = None
    closer: object = None
    probe: object = None
    retired: bool = False
    failed: bool = False
    released: bool = False
    retry_closer: bool = False
    lock: object = field(default_factory=RLock)


class OwnedResources:
    """A bounded set of caller-owned leases, retained across failed callbacks.

    The owning service publishes this registry before allocating resources.
    Cleanup waits only on one disposal lock, never while holding registry locks.
    New allocations are fenced after failure, not collected in a recovery queue.
    """

    def __init__(self):
        self._lock = Lock()
        self._leases = set()

    @property
    def failed(self):
        with self._lock:
            return any(lease.failed for lease in self._leases)

    @property
    def pending(self):
        with self._lock:
            return bool(self._leases)

    def check(self):
        if self.failed:
            raise CleanupIncompleteError

    def acquire(self, factory, *args, closer=None, probe=None, retry_closer=False, **kwargs):
        self.check()
        lease = _Lease(closer=closer or (lambda resource: resource.close()), probe=probe,
                       retry_closer=retry_closer)
        with self._lock:
            self._leases.add(lease)
        try:
            lease.resource = factory(*args, **kwargs)
        except BaseException:
            # A primitive that raises without returning retains its own native
            # partial construction. Producers with inner allocations publish a
            # shell first (SyncWork / NativeDirectoryRead / descriptor state).
            if lease.resource is None:
                self._forget(lease)
            else:
                try:
                    self.release(lease)
                except BaseException:
                    pass
            raise
        return lease

    def _forget(self, lease):
        with self._lock:
            self._leases.discard(lease)

    def release(self, lease, *, retry=False):
        with lease.lock:
            if lease.released:
                self._forget(lease)
                return
            lease.retired = True
            if lease.failed and not retry:
                raise CleanupIncompleteError
            error = None
            returned = False
            try:
                if lease.retry_closer:
                    lease.closer(lease.resource, retry=retry)
                else:
                    lease.closer(lease.resource)
                returned = True
            except BaseException as exc:
                error = exc
            try:
                lease.released = bool(lease.probe(lease.resource)) if lease.probe is not None else returned
            except BaseException as exc:
                error = error or exc
            if lease.released:
                self._forget(lease)
            else:
                lease.failed = True
            if error is not None:
                raise error
            if not lease.released:
                raise CleanupIncompleteError

    @contextmanager
    def context(self, factory, *args, closer=None, probe=None, **kwargs):
        def close(resource):
            if hasattr(resource, "__exit__"):
                resource.__exit__(None, None, None)
            else:
                resource.close()

        lease = self.acquire(factory, *args, closer=closer or close, probe=probe, **kwargs)
        try:
            resource = lease.resource.__enter__() if hasattr(lease.resource, "__enter__") else lease.resource
            yield resource
        except BaseException:
            try:
                self.release(lease)
            except BaseException:
                pass  # The primary failure propagates; the resource stays owned.
            raise
        else:
            self.release(lease)

    def drain(self, *, retry=True, retire_active=False, defer_active=False):
        with self._lock:
            leases = tuple(self._leases)
        error = None
        for lease in leases:
            if not lease.retired and not retire_active:
                if not defer_active:
                    error = error or CleanupIncompleteError()
                continue
            try:
                self.release(lease, retry=retry)
            except BaseException as exc:
                error = error or exc
        if error is not None:
            raise error
        with self._lock:
            pending = any(lease.retired or not defer_active for lease in self._leases)
        if pending:
            raise CleanupIncompleteError


def sqlite_closed(connection):
    try:
        if isinstance(connection, sqlite3.Connection):
            connection.in_transaction
        else:
            connection.execute("SELECT 1")  # Existing connection-wrapper seam.
    except sqlite3.ProgrammingError:
        return True
    return False


def sqlite_cursor_closed(cursor):
    try:
        cursor.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


class SQLiteConnections(OwnedResources):
    def __init__(self):
        super().__init__()
        self._cursors = {}

    def connect(self, *args, **kwargs):
        factory = kwargs.pop("_factory", sqlite3.connect)
        kwargs.setdefault("check_same_thread", False)  # Retired cleanup may run on shutdown's thread.
        cursors = OwnedResources()

        def close(connection, *, retry=False):
            # sqlite3_close_v2 may logically close the Python connection while
            # an active cursor still holds native locks. Never orphan that child.
            cursors.drain(retry=retry, retire_active=True)
            connection.close()

        lease = self.acquire(factory, *args, closer=close, probe=sqlite_closed, retry_closer=True, **kwargs)
        try:
            with self._lock:
                self._cursors[id(lease.resource)] = cursors
        except BaseException:
            try:
                self.release(lease)
            except BaseException:
                pass
            raise
        return lease.resource

    def _forget(self, lease):
        with self._lock:
            self._leases.discard(lease)
            self._cursors.pop(id(lease.resource), None)

    @contextmanager
    def cursor(self, connection):
        self.check()
        with self._lock:
            cursors = self._cursors[id(connection)]
        with cursors.context(connection.cursor, probe=sqlite_cursor_closed) as cursor:
            yield cursor

    def close(self, connection, *, retry=False):
        with self._lock:
            lease = next((item for item in self._leases if item.resource is connection), None)
        if lease is not None:
            self.release(lease, retry=retry)

    @contextmanager
    def connection(self, *args, **kwargs):
        connection = self.connect(*args, **kwargs)
        try:
            yield connection
        except BaseException:
            try:
                self.close(connection)
            except BaseException:
                pass
            raise
        else:
            self.close(connection)


class ScopedResources(OwnedResources, DescriptorDisposal):
    def open_descriptor(self, factory, *args, _credit=None, **kwargs):
        self.check()
        state = DescriptorState(None, credit=_credit or reserve_cleanup())
        try:
            lease = self.acquire(lambda: state, closer=self._release_descriptor,
                                 probe=lambda item: item.descriptor_released and item.guard_released)
        except BaseException:
            if state.credit is not None:
                state.credit.finish()
            raise
        state.descriptor_release.state = CleanupState.AMBIGUOUS  # Unreturned allocation is unresolved.
        try:
            result = descriptor_allocation(factory, *args, **kwargs)
            if isinstance(result, NoAllocation):
                state.descriptor_release.state = CleanupState.VERIFIED_RELEASED
                raise result.error
            state.descriptor = result
            state.descriptor_release.state = CleanupState.OWNED_OPEN
        except BaseException:
            try:
                self.release(lease)
            except BaseException:
                pass
            raise
        return lease

    @contextmanager
    def descriptor(self, factory, *args, **kwargs):
        lease = self.open_descriptor(factory, *args, **kwargs)
        try:
            yield lease
        except BaseException:
            try:
                self.release(lease)
            except BaseException:
                pass
            raise
        else:
            self.release(lease)

    @contextmanager
    def stream(self, factory, *args, **kwargs):
        # The wrapper NEVER owns the raw descriptor. Its finalizer therefore
        # cannot close a recycled allocation after failed/partial construction.
        with self.descriptor(factory, *args, **kwargs) as lease:
            yield self.wrap_stream(lease, "rb", buffering=0)

    def wrap_stream(self, lease, mode, *, buffering=-1):
        """Borrow a registered descriptor; only its lease may release the allocation.

        Construction failure leaves the original descriptor closer intact. Once
        construction returns, disposal closes the wrapper before the descriptor.
        The enclosing descriptor context owns release, including exception paths.
        """
        stream = os.fdopen(lease.resource.descriptor, mode, buffering=buffering, closefd=False)

        def closed():
            try:
                stream.fileno()
            except (ValueError, OSError):
                return True
            return False

        def prepare():
            if not closed():
                stream.__exit__(None, None, None)
            if not closed():
                raise CleanupIncompleteError

        lease.closer = lambda state: self._release_descriptor(state, _prepare=prepare)
        lease.probe = lambda state: closed() and state.descriptor_released and state.guard_released
        return stream.__enter__()




@dataclass(eq=False)
class _HandleState:
    credit: object = None
    number: int | None = None
    guard: object = None
    released: bool = False
    guard_released: bool = False
    uncertain: bool = False
    guard_uncertain: bool = False
    release: ReleaseEvidence = field(default_factory=ReleaseEvidence)
    guard_release: ReleaseEvidence = field(default_factory=ReleaseEvidence)

    @property
    def cleanup_state(self):
        if self.released and self.guard_released:
            return CleanupState.TERMINAL_COMPLETE
        phases = (self.release.state, self.guard_release.state)
        for phase in (CleanupState.AMBIGUOUS, CleanupState.RELEASE_IN_PROGRESS,
                      CleanupState.VERIFIED_SAFE_TO_RETRY):
            if phase in phases:
                return phase
        return CleanupState.OWNED_OPEN


class NativeHandles(OwnedResources):
    """Windows object identity proof, bounded to one close-time duplicate."""

    @staticmethod
    def _api():
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetHandleInformation.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        kernel.GetHandleInformation.restype = ctypes.c_int
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel.CloseHandle.restype = ctypes.c_int
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.DuplicateHandle.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
                                           ctypes.c_int, ctypes.c_uint32)
        kernel.DuplicateHandle.restype = ctypes.c_int
        return kernel

    @classmethod
    def valid(cls, number):
        cleanup_probe()
        import ctypes

        flags = ctypes.c_uint32()
        if cls._api().GetHandleInformation(number, ctypes.byref(flags)):
            return True
        if ctypes.get_last_error() == 6:
            return False
        raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _same(one, other):
        import ctypes

        compare = ctypes.WinDLL("kernelbase", use_last_error=True).CompareObjectHandles
        compare.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        compare.restype = ctypes.c_int
        return bool(compare(one, other))

    def _prepare_handle(self, number):
        """Read-only pre-dispatch check; failure cannot release a native handle."""
        self.valid(number)

    def acquire_handle(self, factory, *args, closer=None, _credit=None, **kwargs):

        state = _HandleState(credit=_credit or reserve_cleanup())
        close = closer or self._api().CloseHandle

        def checked(number):
            return native_close_result(close, number)

        try:
            lease = self.acquire(lambda: state, closer=lambda item: self._dispose_handle(item, checked),
                                 probe=lambda item: item.released and item.guard_released)
        except BaseException:
            if state.credit is not None:
                state.credit.finish()
            raise
        state.release.state = CleanupState.AMBIGUOUS
        try:
            result = event_allocation(factory, *args, **kwargs)
            if isinstance(result, NoAllocation):
                state.release.state = CleanupState.VERIFIED_RELEASED
                raise result.error
            state.number = result
            state.release.state = CleanupState.OWNED_OPEN
        except BaseException:
            try:
                self.release(lease)
            except BaseException:
                pass
            raise
        return lease

    @contextmanager
    def handle(self, factory, *args, closer=None, **kwargs):
        lease = self.acquire_handle(factory, *args, closer=closer, **kwargs)
        try:
            yield lease.resource.number
        except BaseException:
            try:
                self.release(lease)
            except BaseException:
                pass
            raise
        else:
            self.release(lease)

    def _dispose_handle(self, state, close):
        with cleanup_accounting(state.credit):
            return self._release_handle(state, close)

    def _release_handle(self, state, close):
        import ctypes

        if state.uncertain and state.release.state is CleanupState.OWNED_OPEN:
            state.release.state = CleanupState.AMBIGUOUS
        if state.guard_uncertain and state.guard_release.state is CleanupState.OWNED_OPEN:
            state.guard_release.state = CleanupState.AMBIGUOUS

        def prepare():
            self._prepare_handle(state.number)
            if state.guard is None or state.guard.value is None:
                if state.guard_release.state is CleanupState.AMBIGUOUS:
                    raise CleanupIncompleteError
                if state.guard is None:
                    state.guard = ctypes.c_void_p()
                kernel = self._api()
                process = kernel.GetCurrentProcess()
                state.guard_release.state = CleanupState.AMBIGUOUS
                cleanup_probe()
                duplicate = kernel.DuplicateHandle
                # Inspect dispatch BEFORE calling: a Python override can keep a
                # native address/ABI and even restore its type while returning.
                trusted = (isinstance(duplicate, ctypes._CFuncPtr)
                           and type(duplicate).__call__ is ctypes._CFuncPtr.__call__
                           and duplicate.restype is ctypes.c_int
                           and tuple(duplicate.argtypes or ()) == (
                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
                               ctypes.c_int, ctypes.c_uint32)
                           and getattr(duplicate, "errcheck", None) is None
                           and duplicate._flags_ & ctypes._FUNCFLAG_USE_LASTERROR
                           and ctypes.cast(duplicate, ctypes.c_void_p).value == _WINDOWS_DUPLICATE_ADDRESS)
                if not duplicate(process, state.number, process, ctypes.byref(state.guard), 0, False, 2):
                    error = ctypes.get_last_error()
                    # SAME_ACCESS only (never CLOSE_SOURCE): native FALSE with
                    # no output certifies no guard allocation, not opaque failure.
                    if trusted and state.guard.value is None:
                        state.guard_release.state = CleanupState.VERIFIED_RELEASED
                    raise ctypes.WinError(error)
                state.guard_release.state = CleanupState.OWNED_OPEN

        def release_guard():
            number = state.guard.value if state.guard is not None else None
            if number is None:
                if state.guard_release.state is CleanupState.AMBIGUOUS:
                    raise CleanupIncompleteError
                state.guard_released = True
            else:
                try:
                    state.guard_release.run(lambda: close(number), lambda: not self.valid(number),
                                            prepare=lambda: self._prepare_handle(number), number=number)
                finally:
                    state.guard_uncertain = state.guard_release.state is CleanupState.AMBIGUOUS
                    state.guard_released = state.guard_release.state is CleanupState.VERIFIED_RELEASED

        if state.number is None:
            if state.release.state is CleanupState.AMBIGUOUS:
                raise CleanupIncompleteError
            state.released = True
        elif not state.released:
            try:
                state.release.run(lambda: close(state.number), lambda: not self.valid(state.number),
                                  prepare=prepare, number=state.number)
            except BaseException:
                state.uncertain = state.release.state is CleanupState.AMBIGUOUS
                state.released = state.release.state is CleanupState.VERIFIED_RELEASED
                if state.released:
                    try:
                        release_guard()
                    except BaseException:
                        pass
                raise
            state.released = True
        if state.released:
            release_guard()



class VaultResources(ScopedResources, NativeHandles):
    """One vault-owned registry for scoped descriptors, pins and iterators."""


@contextmanager
def retained_exit_stack():
    """Callbacks drive cleanup; persistent lease owners survive invocation."""
    stack = ExitStack()
    try:
        yield stack
    except BaseException:
        try:
            stack.close()
        except BaseException:
            pass
        raise
    else:
        stack.close()

