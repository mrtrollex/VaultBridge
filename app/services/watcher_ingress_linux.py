"""Bound watchdog 6.0.0's Linux buffers, preserving native move grouping.

The native reader's read/close/watch bookkeeping is reused. Its parser is driven
with a bounded output collection and streaming recursive simulation below; the
upstream implementation otherwise builds a whole moved-in tree's event list.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import select
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from watchdog.observers.inotify_buffer import InotifyBuffer
from watchdog.observers.inotify_c import (
    DEFAULT_EVENT_BUFFER_SIZE,
    Inotify,
    InotifyConstants,
    InotifyEvent,
    inotify_add_watch,
    inotify_init,
    inotify_rm_watch,
)
from watchdog.utils import BaseThread
from watchdog.utils.delayed_queue import DelayedQueue

from app.repositories.semantic_binding import ordinary_identity
from app.services.semantic_sync import directory_identity
from app.services.watcher_ingress import INGRESS_EVENT_LIMIT

# E5 needs mutations and completed writes, never read-only traversal activity.
# IN_Q_OVERFLOW and IN_IGNORED are delivered by inotify irrespective of this mask.
SEMANTIC_INOTIFY_MASK = (
    InotifyConstants.IN_MODIFY | InotifyConstants.IN_ATTRIB | InotifyConstants.IN_CLOSE_WRITE
    | InotifyConstants.IN_MOVED_FROM | InotifyConstants.IN_MOVED_TO | InotifyConstants.IN_CREATE
    | InotifyConstants.IN_DELETE | InotifyConstants.IN_DELETE_SELF | InotifyConstants.IN_MOVE_SELF
    | InotifyConstants.IN_UNMOUNT
)


@dataclass
class _Descriptor:
    number: int
    identity: tuple | None = None
    state: str = "owned"
    guard: _Descriptor | None = None


def _same_description(one, other):
    # Only needed after a close has an uncertain outcome. kcmp compares open
    # file descriptions, unlike fstat (all inotify anon inodes can look alike).
    # The x86-64 syscall is verified on the supported WSL kernel. Where a kernel
    # or sandbox cannot prove identity, retain ownership rather than guess.
    if os.uname().machine != "x86_64":
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(312, os.getpid(), os.getpid(), 0, one, other)
    return None if result < 0 else result == 0


class _BoundedDelayedQueue(DelayedQueue):
    def __init__(self, delay, loss):
        super().__init__(delay)
        self._loss = loss

    def put(self, element, *, delay=False):
        with self._not_empty:
            if not self._closed and len(self._queue) < INGRESS_EVENT_LIMIT:
                self._queue.append((element, time.time(), delay))
                self._not_empty.notify()
                return
        self._loss()

    def close(self):
        with self._not_empty:
            self._closed = True
            lost = bool(self._queue)
            self._queue.clear()
            self._not_empty.notify_all()
        if lost:
            self._loss()


class _BoundedInotify(Inotify):
    def __init__(self, path, *, recursive=False, event_mask=None, loss, validate=None, gap=None, restored=None):
        self._lock = threading.Lock()
        self._closed = False
        self._is_reading = False
        self._descriptors = {}
        self._resources_closed = False
        self._path = path
        self._wd_for_path = {}
        self._path_for_wd = {}
        self._loss = loss
        self._root = Path(os.fsdecode(path))
        self._root_identity = directory_identity(self._root)
        self._validate = validate
        self._gap = gap
        self._restored = restored
        self._coverage_pending = False
        self._coverage_ticket = None
        self._registration_revision = 0
        self._fence_wd = None
        self._fence_revision = 0
        self._watch_descriptor_high_water = 0
        self._retired_watch_paths = None  # One prior set, only during the current rebuild.
        self._move_recovery = False  # Traversal policy only, scoped under the native lock.
        self._validate_root()
        try:
            self._validate_root()
            self._initialize_native(path, recursive, event_mask)
        except BaseException:
            self.close_after_reader()
            raise

    def _own(self, name, number):
        descriptor = _Descriptor(number)
        self._descriptors[name] = descriptor
        descriptor.identity = self._descriptor_identity(number)
        return descriptor

    @staticmethod
    def _descriptor_identity(number):
        info = os.fstat(number)
        return info.st_dev, info.st_ino, info.st_mode

    def _initialize_native(self, path, recursive, event_mask):
        # watchdog 6.0.0 initialization, with its isdir/file fallback removed.
        # E5 accepts only bound ordinary roots; a transient metadata error must
        # never select nonrecursive registration. Keep native poll/close state
        # compatible with the inherited read/stop and move bookkeeping methods.
        descriptor = inotify_init()
        if descriptor == -1:
            Inotify._raise_error()
            raise PermissionError(errno.EACCES, "Native initialization denied")
        self._inotify_fd = descriptor
        owned = self._own("_inotify_fd", descriptor)
        # Anonymous inotify fstat identities are shared. Keep one original OFD
        # reference so an unexpected EBADF cannot make a reused slot ours again.
        number = os.dup(descriptor)
        owned.guard = _Descriptor(number)
        self._descriptors["guard:_inotify_fd"] = owned.guard
        owned.guard.identity = self._descriptor_identity(number)
        self._is_reading = True
        self._kill_r, self._kill_w = os.pipe()
        # Record BOTH allocations before an identity probe can fail.
        self._descriptors["_kill_r"] = _Descriptor(self._kill_r)
        self._descriptors["_kill_w"] = _Descriptor(self._kill_w)
        for name in ("_kill_r", "_kill_w"):
            self._descriptors[name].identity = self._descriptor_identity(getattr(self, name))
        if hasattr(select, "poll"):
            self._poller = select.poll()
            self._poller.register(descriptor, select.POLLIN)
            self._poller.register(self._kill_r, select.POLLIN)

            def ready():
                return any(fd == descriptor for fd, _ in self._poller.poll())
        else:
            def ready():
                available = select.select([descriptor, self._kill_r], [], [])[0]
                return descriptor in available
        self._check_inotify_fd = ready
        self._wd_for_path = {}
        self._path_for_wd = {}
        self._path = path
        self._event_mask = SEMANTIC_INOTIFY_MASK if event_mask is None else event_mask
        self._is_recursive = recursive
        self._moved_from_events = {}
        info = os.stat(path, follow_symlinks=False)  # Errors propagate at the actual decision boundary.
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._root_identity:
            raise OSError(errno.ESTALE, "Native root classification changed")
        self._validate_root()
        self._add_dir_watch(path, self._event_mask, recursive=recursive)
        if recursive:
            # Initial traversal has the same queued-subtree race as overflow
            # restoration. Native FIFO proof, not traversal completion, closes
            # this generation's first registration interval.
            self._begin_registration()
            self._queue_coverage_fence()

    def _validate_root(self):
        if directory_identity(self._root) != self._root_identity:
            raise OSError(errno.ESTALE, "Native root identity changed")
        if self._validate is not None:
            self._validate()  # Reuse exact E1/E3 owner/root binding validation.

    def _add_dir_watch(self, path, mask, *, recursive):
        self._validate_root()
        if self._add_watch(path, mask) is None:
            return
        if recursive:
            with contextlib.closing(self._registration_walk(path)) as walk:
                for root, directories, _ in walk:
                    for directory in directories:
                        full = os.path.join(root, directory)
                        if not self._excluded_directory_link(full):
                            self._add_watch(full, mask)
        self._validate_root()

    def _registration_walk(self, path):
        # os.walk also hides DirEntry.is_dir errors, even with onerror. Stream
        # checked entries instead: singleton batches and one iterator per depth,
        # never a whole directory/tree or a queue of registration work.
        stack = []

        def validate(root, identity):
            self._validate_watch_path(root)
            if ordinary_identity(Path(os.fsdecode(root)), directory=True) != identity:
                raise OSError(errno.ESTALE, "Native enumeration directory changed")

        def descend(root, parent=None, parent_identity=None, identity=None):
            try:
                self._validate_watch_path(root)
                current = ordinary_identity(Path(os.fsdecode(root)), directory=True)
                if identity is not None and current != identity:
                    raise OSError(errno.ESTALE, "Native enumeration directory changed")
                iterator = os.scandir(root)
            except OSError as error:
                self._enumeration_error(error, parent, parent_identity)
                return
            stack.append((root, current, iterator))

        def current_frame_valid():
            root, identity, _ = stack[-1]
            try:
                validate(root, identity)
            except FileNotFoundError as error:
                parent, previous = stack[-2][:2] if len(stack) > 1 else (None, None)
                self._enumeration_error(error, parent, previous)
                return False
            return True

        try:
            descend(path)
            while stack:
                root, identity, iterator = stack[-1]
                if not current_frame_valid():
                    iterator.close()
                    stack.pop()
                    continue
                try:
                    entry = next(iterator)  # Iterator errors are never end-of-directory.
                except StopIteration:
                    current_frame_valid()
                    iterator.close()
                    stack.pop()
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    linked = entry.is_symlink()
                    directory = entry.is_dir(follow_symlinks=False)
                except OSError as error:
                    self._enumeration_error(error, root, identity)
                    continue
                if linked != stat.S_ISLNK(info.st_mode) or directory != stat.S_ISDIR(info.st_mode):
                    raise OSError(errno.ESTALE, "Native entry classification changed")
                if not current_frame_valid():
                    iterator.close()
                    stack.pop()
                    continue
                if linked:
                    if not self._excluded_directory_link(entry.path):
                        raise OSError(errno.ESTALE, "Native excluded entry changed")
                    continue  # Never classify/follow the link's target.
                if directory:
                    yield root, (entry.name,), ()  # Caller installs the watch BEFORE descent.
                    if not current_frame_valid():
                        iterator.close()
                        stack.pop()
                        continue
                    descend(entry.path, root, identity, (info.st_dev, info.st_ino))
                else:
                    yield root, (), (entry.name,)
        finally:
            for _, _, iterator in reversed(stack):
                iterator.close()

    def _enumeration_error(self, error, parent, identity):
        path = os.fsencode(error.filename) if error.filename is not None else None
        if (error.errno == errno.ENOENT and path is not None
                and (not self._was_watched(path) or self._move_recovery)
                and os.path.dirname(path) == parent):
            # Only an unregistered, proven absent child is ordinary churn. An
            # unreadable/existing directory, monitored directory or root is not.
            # The active traversal frame supplies the exact parent's identity.
            self._validate_watch_path(parent)
            if ordinary_identity(Path(os.fsdecode(parent)), directory=True) != identity:
                raise OSError(errno.ESTALE, "Native enumeration parent changed") from error
            if self._vanished_child(path):
                self._validate_watch_path(parent)
                if ordinary_identity(Path(os.fsdecode(parent)), directory=True) != identity:
                    raise OSError(errno.ESTALE, "Native enumeration parent changed") from error
                if self._move_recovery:
                    self._discard_child_watches(path)
                self._loss()
                return
        raise error

    def _excluded_directory_link(self, path):
        if not os.path.islink(path) or self._was_watched(path):
            return False  # A previously watched ordinary directory cannot become an exclusion.
        parent = os.path.dirname(path)
        try:
            self._validate_watch_path(parent)
        except FileNotFoundError as error:
            if error.errno != errno.ENOENT or not self._vanished_child(parent):
                raise
            self._discard_child_watches(parent)
            self._loss()
            return True
        if not os.path.islink(path):
            if self._vanished_child(path):
                self._loss()
                return True
            raise OSError(errno.ESTALE, "Native excluded directory changed")
        return True  # Explicit, unregistered symlink; never follow/register its target.

    def _was_watched(self, path):
        return (path in self._wd_for_path
                or (self._retired_watch_paths is not None and path in self._retired_watch_paths))

    def _begin_registration(self, *, overflow=False):
        self._registration_revision += 1
        if overflow:
            # Overflow may have dropped an earlier fence. Its native watch was
            # already removed; retain no old marker collection or retry task.
            self._fence_wd = None
        if overflow or not self._coverage_pending:
            self._coverage_pending = True
            if self._gap is not None:
                self._coverage_ticket = self._gap()
            else:
                self._loss()

    def _queue_coverage_fence(self):
        # inotify_rm_watch appends IN_IGNORED to THIS fd's ordered kernel stream.
        # Watch the reader's existing private kill-pipe inode via procfs: no
        # vault writes, temporary tree, extra fd/thread or public watch mapping.
        marker = os.fsencode(f"/proc/self/fd/{self._kill_r}")
        wd = inotify_add_watch(self._inotify_fd, marker, InotifyConstants.IN_ATTRIB)
        if wd < 0:
            Inotify._raise_error()
            raise PermissionError(errno.EACCES, "Native coverage fence denied")
        self._claim_watch_descriptor(wd)
        if wd in self._path_for_wd:
            raise RuntimeError("Native coverage fence identity conflict")
        self._fence_wd = wd
        self._fence_revision = self._registration_revision
        if inotify_rm_watch(self._inotify_fd, wd) == -1:
            Inotify._raise_error()

    def _claim_watch_descriptor(self, wd, *, previous=None):
        # Linux normally allocates increasing wds, but can wrap/reuse them while
        # older records remain queued. Check, never assume, that a new public
        # watch OR private marker cannot give a retired wd a new meaning. One
        # scalar bounds the entire history; reuse requires explicit reconstruction.
        if wd != previous:
            if wd <= self._watch_descriptor_high_water:
                raise OSError(errno.ESTALE, "Native watch descriptor was reused")
            self._watch_descriptor_high_water = wd

    def _finish_registration_batch(self, fenced_revision):
        if not self._coverage_pending:
            return
        if fenced_revision == self._registration_revision:
            self._validate_root()
            if self._restored is not None:
                self._restored(self._coverage_ticket)
            self._coverage_pending = False
            self._coverage_ticket = None
        elif self._fence_wd is None:
            # A registration since the marker was queued invalidates it. Append
            # ONE replacement after that work, return, and let this reader drain
            # it normally. Never wait for ourselves while holding the read lock.
            self._queue_coverage_fence()

    def _dispose(self, name, descriptor, *, guarded):
        if descriptor.state in ("closed", "absent", "replaced"):
            return
        try:
            identity = self._descriptor_identity(descriptor.number)
        except OSError as error:
            if error.errno == errno.EBADF:
                descriptor.state = "absent"
                return
            raise
        if descriptor.identity is not None and identity != descriptor.identity:
            descriptor.state = "replaced"  # Do not close an unrelated reused slot.
            return
        if descriptor.state == "uncertain":
            if descriptor.guard is not None and descriptor.guard.state == "owned":
                try:
                    guard_identity = self._descriptor_identity(descriptor.guard.number)
                except OSError as error:
                    if error.errno != errno.EBADF:
                        raise
                    descriptor.guard.state = "absent"
                else:
                    if guard_identity != descriptor.guard.identity:
                        descriptor.guard.state = "replaced"
            same = (_same_description(descriptor.number, descriptor.guard.number)
                    if descriptor.guard is not None and descriptor.guard.state == "owned" else None)
            if same is False:
                descriptor.state = "replaced"
                return
            if same is not True:
                raise OSError(errno.ESTALE, "Native descriptor ownership uncertain")
        if guarded and descriptor.guard is None:
            # At most three close-time guards, retained only across an uncertain
            # close. They prove safe retry even for reused anonymous inotify fds.
            try:
                number = os.dup(descriptor.number)
            except OSError as error:
                if error.errno not in (errno.EMFILE, errno.ENFILE):
                    raise
                # Resource pressure must not prevent the first close of a
                # known-owned fd. If that close fails, retry will fail closed
                # without proof instead of blindly closing a possibly reused fd.
            else:
                descriptor.guard = _Descriptor(number)
                self._descriptors["guard:" + name] = descriptor.guard
                descriptor.guard.identity = self._descriptor_identity(number)
        try:
            os.close(descriptor.number)
        except OSError as error:
            descriptor.state = "uncertain"
            try:
                current = self._descriptor_identity(descriptor.number)
            except OSError as probe:
                if probe.errno == errno.EBADF:
                    descriptor.state = "absent"
            else:
                if descriptor.identity is not None and current != descriptor.identity:
                    descriptor.state = "replaced"
            if error.errno != errno.EBADF or descriptor.state == "uncertain":
                raise
        else:
            descriptor.state = "closed"

    def _close_resources(self):
        error = None
        for name in ("_inotify_fd", "_kill_r", "_kill_w"):
            descriptor = self._descriptors.get(name)
            if descriptor is None:
                continue
            try:
                self._dispose(name, descriptor, guarded=True)
            except OSError as failure:
                error = error or failure
        for name in ("_inotify_fd", "_kill_r", "_kill_w"):
            descriptor = self._descriptors.get(name)
            if (descriptor is not None and descriptor.guard is not None
                    and descriptor.state in ("closed", "absent", "replaced")):
                try:
                    self._dispose("guard:" + name, descriptor.guard, guarded=False)
                except OSError as failure:
                    error = error or failure
        self._resources_closed = all(d.state in ("closed", "absent", "replaced")
                                     for d in self._descriptors.values())
        if error is not None:
            raise error

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._path in self._wd_for_path:
                    inotify_rm_watch(self._inotify_fd, self._wd_for_path[self._path])
                if self._is_reading:
                    os.write(self._kill_w, b"!")
            if not self._is_reading:
                self._close_resources()  # Retry even if a previous close set _closed.

    def close_after_reader(self):
        with self._lock:
            self._closed = True
            self._is_reading = False
            self._close_resources()

    def _add_watch(self, path, mask):
        previously_watched = self._was_watched(path)
        previous = self._wd_for_path.get(path)
        try:
            self._validate_watch_path(path)
            wd = super()._add_watch(path, mask | InotifyConstants.IN_ONLYDIR | InotifyConstants.IN_DONT_FOLLOW)
            if wd < 0:
                # watchdog suppresses EACCES and can return wd=-1. Do not let
                # a later disappearance hide that genuine registration failure.
                raise PermissionError(errno.EACCES, "Native watch registration denied")
            self._claim_watch_descriptor(wd, previous=previous)
            self._validate_watch_path(path)
        except FileNotFoundError as error:
            if ((previously_watched and not self._move_recovery)
                    or error.errno != errno.ENOENT or not self._vanished_child(path)):
                raise
            self._discard_child_watches(path)
            self._loss()  # Coalesced index debt; no surviving monitoring gap.
            return None
        return wd

    def _validate_watch_path(self, path):
        self._validate_root()
        candidate = Path(os.fsdecode(path))
        relative = candidate.relative_to(self._root)
        parent = self._root
        for part in relative.parts:
            parent /= part
            ordinary_identity(parent, directory=True)  # Reject symlinks/non-directories.

    def _vanished_child(self, path):
        self._validate_root()  # Root/ancestor loss is NEVER ordinary child churn.
        candidate = Path(os.fsdecode(path))
        relative = candidate.relative_to(self._root)
        if not relative.parts:
            return False
        try:
            self._validate_watch_path(path)
        except FileNotFoundError as error:
            if error.errno == errno.ENOENT:
                self._validate_root()
                return True
            raise
        return False  # Existing replacements or ambiguous errors fail closed.

    def _discard_child_watches(self, path):
        # Called on the reader's existing serialized registration path. Retire
        # partial/stale subtree registrations one at a time, with no copied map.
        prefix = path + os.path.sep.encode()
        while True:
            wd = next((wd for wd, registered in self._path_for_wd.items()
                       if registered == path or registered.startswith(prefix)), None)
            if wd is None:
                return
            registered = self._path_for_wd.pop(wd)
            if self._wd_for_path.get(registered) == wd:
                del self._wd_for_path[registered]
            if inotify_rm_watch(self._inotify_fd, wd) == -1:
                # A deleted inode can already have removed its kernel watch.
                if ctypes.get_errno() != errno.EINVAL:
                    Inotify._raise_error()
                    raise PermissionError(errno.EACCES, "Native watch retirement denied")

    def _retire_coverage(self):
        if self._retired_watch_paths is None:
            # Preserve only exclusion evidence for THIS walk, not native identity
            # claims. An accepted ordinary path cannot become a symlink exclusion.
            self._retired_watch_paths = self._wd_for_path
            self._wd_for_path = {}
        self._discard_child_watches(self.path)

    def remember_move_from_event(self, event):
        if len(self._moved_from_events) >= INGRESS_EVENT_LIMIT:
            self._moved_from_events.clear()
            self._loss()
        super().remember_move_from_event(event)

    def read_events(self, *, event_buffer_size=DEFAULT_EVENT_BUFFER_SIZE):
        # watchdog 6.0.0 native read/kill-pipe/close protocol. The byte read is
        # fixed-size, even when a caller requests a larger batch.
        event_buffer = b""
        while True:
            try:
                with self._lock:
                    if self._closed:
                        return []
                    self._is_reading = True
                if self._check_inotify_fd():
                    event_buffer = os.read(self._inotify_fd, min(event_buffer_size, DEFAULT_EVENT_BUFFER_SIZE))
                with self._lock:
                    self._is_reading = False
                    if self._closed:
                        self._close_resources()
                        return []
            except OSError as error:
                if error.errno == errno.EINTR:
                    continue
                if error.errno == errno.EBADF:
                    with self._lock:
                        if self._closed:
                            return []
                        self._descriptors["_inotify_fd"].state = "uncertain"
                self._loss()
                raise
            break

        events = []
        restore_tree = False
        retiring_tree = False
        fenced_revision = None
        validated = False

        def retain(event):
            if len(events) < INGRESS_EVENT_LIMIT:
                events.append(event)
            else:
                self._loss()

        with self._lock:
            # close() owns this SAME lock. A buffer read before shutdown is not
            # permission to register after it: fence at the parsing transition,
            # then retain descriptor ownership through the entire native batch.
            if self._closed:
                return []
            for wd, mask, cookie, name in self._parse_event_buffer(event_buffer):
                if wd == -1:
                    # Upstream silently ignores IN_Q_OVERFLOW. This fd belongs
                    # to one exact binding even though individual paths are lost.
                    if mask & InotifyConstants.IN_Q_OVERFLOW:
                        self._validate_root()
                        self._begin_registration(overflow=True)
                        # Lost departures make EVERY old path->inode assertion
                        # uncertain. Retire the actual kernel set before rebuilding
                        # the authorized tree. Old queued records then have no map;
                        # the high-water check forbids reuse as a new path/fence.
                        self._retire_coverage()
                        events.clear()
                        restore_tree = True
                    continue
                if wd == self._fence_wd and mask & InotifyConstants.IN_IGNORED:
                    fenced_revision = self._fence_revision
                    self._fence_wd = None
                    continue  # Private marker, never a filesystem event/owner path.
                wd_path = self._path_for_wd.get(wd)
                if wd_path is None:
                    if not mask & InotifyConstants.IN_IGNORED and not self._coverage_pending:
                        self._loss()
                    # During a rebuild, unresolved coverage already includes
                    # these retired records. Do not start redundant early fulls.
                    continue
                src = os.path.join(wd_path, name) if name else wd_path
                event = InotifyEvent(wd, mask, cookie, name, src)
                if src == self.path:
                    if event.is_delete_self or event.is_ignored:
                        raise FileNotFoundError(errno.ENOENT, "Native watched root unavailable")
                    if mask & (InotifyConstants.IN_MOVE_SELF | InotifyConstants.IN_UNMOUNT):
                        raise OSError(errno.ESTALE, "Native watched root invalidated")
                if not validated:
                    self._validate_root()
                    validated = True
                if self.is_recursive and event.is_directory:
                    if event.is_moved_from or event.is_delete:
                        retiring_tree = True
                        # Do not guess the destination from this read buffer.
                        # Retire old names immediately and restore the current
                        # authorized tree; a delayed/missing TO gets the same
                        # revision-fenced recovery as any other coverage gap.
                        self._begin_registration()
                        try:
                            self._validate_watch_path(src)
                        except FileNotFoundError as error:
                            if error.errno != errno.ENOENT or not self._vanished_child(src):
                                raise
                        self._discard_child_watches(src)
                        events[:] = [prior for prior in events
                                     if prior.src_path != src and not prior.src_path.startswith(src + b"/")]
                        restore_tree = True
                    elif event.is_moved_to:
                        retiring_tree = True
                        self._begin_registration()
                        try:
                            self._validate_watch_path(src)
                        except FileNotFoundError as error:
                            if error.errno != errno.ENOENT or not self._vanished_child(src):
                                raise
                            self._discard_child_watches(src)
                        restore_tree = True
                if event.is_ignored:
                    path = self._path_for_wd.pop(wd)
                    if self._wd_for_path.get(path) == wd:
                        del self._wd_for_path[path]
                    self._begin_registration()
                    restore_tree = True
                retain(event)
                if self.is_recursive and event.is_directory and event.is_create:
                    self._begin_registration()
                    if self._add_watch(src, self._event_mask) is None:
                        continue
                    # Same native recursive-watch installation as watchdog;
                    # stream synthesized events rather than building a list.
                    with contextlib.closing(self._registration_walk(src)) as walk:
                        for root, directories, files in walk:
                            for directory in directories:
                                full = os.path.join(root, directory)
                                if self._excluded_directory_link(full):
                                    continue
                                child_wd = self._add_watch(full, self._event_mask)
                                if child_wd is None:
                                    continue
                                retain(InotifyEvent(child_wd, InotifyConstants.IN_CREATE |
                                                    InotifyConstants.IN_ISDIR, 0, directory, full))
                            for filename in files:
                                full = os.path.join(root, filename)
                                if root not in self._wd_for_path:
                                    self._loss()
                                    continue
                                retain(InotifyEvent(self._wd_for_path[root], InotifyConstants.IN_CREATE,
                                                    0, filename, full))
            if restore_tree and self.is_recursive:
                if retiring_tree:
                    # A missing/delayed FROM can leave the same inode watched
                    # under an old prefix. Rebuild, rather than adopting that wd
                    # as a new path while older records are still in the stream.
                    self._retire_coverage()
                # OS loss can include directory creation/moves, not just note
                # events. Reuse native watch registration to restore future
                # coverage; E4 alone reconciles the index. No new crawler or
                # event list is introduced. Current wd mappings win old names.
                # Concurrent departures during THIS known move/delete recovery
                # can retire proven absent watched children under unchanged
                # parents. Ordinary/overflow registration remains fail-closed.
                self._move_recovery = retiring_tree
                try:
                    self._add_dir_watch(self.path, self._event_mask, recursive=True)
                finally:
                    self._move_recovery = False
                    self._retired_watch_paths = None
                self._wd_for_path = {path: wd for wd, path in self._path_for_wd.items()}
                self._validate_root()
            # Publish only after a native FIFO marker with no intervening
            # registration work, and after ALL records in this batch. Later
            # subtree events start a new gap even if an older full has finished.
            self._finish_registration_batch(fenced_revision)
        return events


class BoundedInotifyBuffer(InotifyBuffer):
    def __init__(self, path, *, recursive, event_mask, loss, failure=None,
                 validate=None, gap=None, restored=None):
        # Upstream starts its reader in __init__; install both bounds FIRST.
        BaseThread.__init__(self)
        self.failure_type = None
        self._failure = failure if failure is not None else lambda error_type: loss()
        self._queue = _BoundedDelayedQueue(self.delay, loss)
        # watchdog's default/filter masks omit IN_MOVE_SELF. Reader liveness
        # alone says nothing about the registered root's continuing identity.
        event_mask = SEMANTIC_INOTIFY_MASK
        self._inotify = _BoundedInotify.__new__(_BoundedInotify)
        self._inotify.__init__(
            path, recursive=recursive, event_mask=event_mask, loss=loss,
            validate=validate, gap=gap, restored=restored,
        )
        try:
            self.start()
        except BaseException:
            # Inotify initializes _is_reading=True before any reader exists.
            # A failed Thread.start has no read loop to finish deferred close.
            with self._inotify._lock:
                self._inotify._is_reading = False
            self._inotify.close()
            self._queue.close()
            raise

    def run(self):
        try:
            super().run()  # Preserve watchdog's grouping and root-loss semantics.
            if self.should_keep_running():
                self._report_failure("NativeWatchStopped")
        except BaseException as error:
            self._report_failure(type(error).__name__)
        finally:
            self._stopped_event.set()
            try:
                # No reader remains to complete Inotify's deferred close after
                # a read/registration exception or a concurrent stop request.
                try:
                    self._inotify.close_after_reader()
                except OSError as error:
                    self._report_failure(type(error).__name__)
            finally:
                self._queue.close()  # Wake the dependent emitter on EVERY exit.

    def _report_failure(self, error_type):
        self.failure_type = error_type  # Category only; never retain exceptions.
        self._failure(error_type)

    def on_thread_stop(self):
        try:
            self._inotify.close()
        finally:
            self._queue.close()

    def close(self):
        error = None
        try:
            self.stop()
        except OSError as failure:
            error = failure
        if self.ident is not None:
            self.join()
        try:
            self._inotify.close_after_reader()
        except OSError as failure:
            error = error or failure
        if error is not None:
            raise error
