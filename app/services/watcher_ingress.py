"""Bound watchdog 6.0.0 ingress without blocking native producers.

Only the private shared watcher installs this adapter, before scheduling roots.
The observer remains watchdog's native Observer. Queue/dedup/stop integration and
the native adapters below are intentionally version checked: watchdog exposes no
public queue/emitter injection API. No process-wide monkeypatch is used.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from importlib.metadata import version

from watchdog.observers.api import BaseObserver, EventDispatcher, EventQueue

INGRESS_EVENT_LIMIT = 256
NATIVE_ARM_TIMEOUT_SECONDS = 5.0


class BoundedEventQueue(EventQueue):
    """Skip adjacent repeats atomically, reject excess data without waiting.

    Loss is reported synchronously after releasing the queue mutex. The callback
    records only E4 debt; it never interprets paths or runs synchronization.
    """

    def __init__(self, loss):
        super().__init__(maxsize=INGRESS_EVENT_LIMIT)
        self._loss = loss
        self._closed = False

    def put(self, item, block=True, timeout=None):
        with self.not_full:
            if item == self._last_item:
                return
            if ((not self._closed or item is EventDispatcher.stop_event)
                    and self._qsize() < self.maxsize):
                self._put(item)
                self.unfinished_tasks += 1
                self.not_empty.notify()
                return
        if item is not EventDispatcher.stop_event:
            self._loss(item[1] if isinstance(item, tuple) and len(item) == 2 else None)

    def close(self):
        # Fence producers before draining. Each discarded tuple retains its
        # exact watch identity. At most 256 callbacks, no copied shutdown batch.
        with self.mutex:
            self._closed = True
        while True:
            try:
                item = self.get_nowait()
            except queue.Empty:
                return
            if item is not EventDispatcher.stop_event:
                self._loss(item[1])
            self.task_done()


def native_watch_alive(observer, watch, *, producers=None):
    if (not isinstance(observer, BaseObserver) or not observer.is_alive()
            or not observer.should_keep_running()):
        return False
    emitter = next((emitter for emitter in (observer.emitters if producers is None else producers)
                    if emitter.watch is watch), None)
    if emitter is None or not emitter.is_alive() or not emitter.should_keep_running():
        return False
    if sys.platform.startswith("linux"):
        reader = emitter._inotify
        return (reader is not None and reader.is_alive() and reader.should_keep_running()
                and reader.failure_type is None)
    return (emitter._whandle is not None and emitter.failure_type is None
            and emitter._native_read is not None and emitter._native_read.armed.is_set())


def wait_native_watch_ready(observer, watch, active):
    if sys.platform != "win32":
        return
    emitter = next((emitter for emitter in observer.emitters if emitter.watch is watch), None)
    deadline = time.monotonic() + NATIVE_ARM_TIMEOUT_SECONDS
    while active():
        if emitter is None or not emitter.is_alive() or emitter.failure_type is not None:
            raise RuntimeError("Shared filesystem producer did not start")
        if emitter._native_read.armed.wait(min(0.05, max(0, deadline - time.monotonic()))):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Shared filesystem native arming timed out")
    raise RuntimeError("Shared filesystem native arming was cancelled")


def install_bounded_ingress(observer, loss, failure=None, *, validate=None, gap=None, restored=None, departures=None):
    """Install before emitters exist; fake lifecycle-only observers need no queue."""
    if not isinstance(observer, BaseObserver):
        return None
    if version("watchdog") != "6.0.0" or observer.emitters or observer.is_alive():
        raise RuntimeError("Unsupported shared watchdog ingress architecture")
    emitter = observer._emitter_class
    if failure is None:
        def failure(watch, error_type):
            loss(watch)
    if sys.platform == "win32":
        from watchdog.observers.read_directory_changes import WindowsApiEmitter
        from watchdog.observers.winapi import (
            CancelIoEx,
            WinAPINativeEvent,
            _parse_event_buffer,
        )

        from app.services.watcher_ingress_windows import (
            NativeDirectoryRead,
            close_directory_handle,
            get_directory_handle,
        )

        if emitter is not WindowsApiEmitter:
            raise RuntimeError("Unsupported shared watchdog emitter")

        class LossAwareEmitter(WindowsApiEmitter):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._handle_close_lock = threading.Lock()
                self._read_done = threading.Event()
                self._read_done.set()
                self.failure_type = None
                self._native_read = None
                self._directory_close_uncertain = False

            def on_thread_start(self):
                self._native_read = NativeDirectoryRead()
                try:
                    self._whandle = get_directory_handle(self.watch.path)
                except BaseException:
                    self._native_read.close(None, time.monotonic() + 5.0)
                    raise

            def on_thread_stop(self):
                # A read can enter the OS after an earlier CancelIoEx returned.
                # Keep the handle valid until that read acknowledges completion;
                # repeat cancellation across the entry race, never close/reuse
                # its handle underneath it. Reader completion needs no lock.
                with self._handle_close_lock:
                    handle = self._whandle
                    deadline = time.monotonic() + 5.0
                    if handle is not None:
                        while not self._read_done.is_set():
                            try:
                                CancelIoEx(handle, None)
                            except OSError:
                                pass  # No pending read yet, or it just completed.
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise RuntimeError("Native filesystem read did not stop")
                            self._read_done.wait(min(0.05, remaining))
                    if self._native_read is not None:
                        self._native_read.close(handle, deadline)
                    if handle is not None:
                        if self._directory_close_uncertain:
                            raise RuntimeError("Native directory handle ownership is uncertain")
                        try:
                            close_directory_handle(handle)
                        except OSError as error:
                            if getattr(error, "winerror", None) == 6:
                                # An invalid handle may already have been closed
                                # outside this owner. Never retry that numeric slot.
                                self._directory_close_uncertain = True
                            raise
                        self._whandle = None  # Transfer ownership only after checked native disposal.

            def run(self):
                try:
                    super().run()
                    if self.should_keep_running():
                        self.failure_type = "NativeProducerStopped"
                        failure(self.watch, self.failure_type)
                except BaseException as error:
                    # Intentional CancelIoEx/CloseHandle can race read entry.
                    # Other errors remain fatal even when stop races failure.
                    if self.should_keep_running() or getattr(error, "winerror", None) not in (6, 995):
                        self.failure_type = type(error).__name__
                        failure(self.watch, self.failure_type)
                finally:
                    self.stop()

            def _read_events(self):
                with self._handle_close_lock:
                    handle = self._whandle
                    if handle is None or not self.should_keep_running():
                        return []
                    self._read_done.clear()
                try:
                    if validate is not None:
                        validate(self.watch)
                    if not self.should_keep_running():
                        return []
                    buf, size = self._native_read.read_directory_changes(
                        handle, self.watch.path, recursive=self.watch.is_recursive,
                    )
                    if self.should_keep_running() and validate is not None:
                        validate(self.watch)
                finally:
                    self._read_done.set()
                # ReadDirectoryChangesW reports a lost OS buffer with zero
                # bytes. An aborted read at stop needs no additional wakeup.
                if size == 0 and self.should_keep_running():
                    loss(self.watch)
                events = [WinAPINativeEvent(action, path) for action, path in _parse_event_buffer(buf, size)]
                if any(event.is_renamed_old for event in events):
                    # Upstream retains OLD_NAME only within this one batch; a
                    # missing/later NEW_NAME otherwise discards departure evidence.
                    # One owner-local trigger for the bounded native batch, before
                    # conversion. No path/rename history or filesystem probing.
                    if departures is None:
                        loss(self.watch)
                    else:
                        departures(self.watch, (e.src_path for e in events if e.is_renamed_old))
                return events

    elif sys.platform.startswith("linux"):
        from watchdog.observers.inotify import InotifyEmitter

        if emitter is not InotifyEmitter:
            raise RuntimeError("Unsupported shared watchdog emitter")

        class LossAwareEmitter(InotifyEmitter):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._buffer_close_lock = threading.Lock()

            def on_thread_start(self):
                from app.services.watcher_ingress_linux import BoundedInotifyBuffer

                # Keep partial initialization reachable for cleanup/retry if a
                # native allocation or descriptor close fails before return.
                self._inotify = BoundedInotifyBuffer.__new__(BoundedInotifyBuffer)
                self._inotify.__init__(
                    os.fsencode(self.watch.path), recursive=self.watch.is_recursive,
                    event_mask=self.get_event_mask_from_filter(), loss=lambda: loss(self.watch),
                    failure=lambda error_type: failure(self.watch, error_type),
                    validate=(lambda: validate(self.watch)) if validate is not None else None,
                    gap=(lambda: gap(self.watch)) if gap is not None else None,
                    restored=(lambda ticket: restored(self.watch, ticket)) if restored is not None else None,
                )

            def on_thread_stop(self):
                # Do not take the emitter's dispatch lock: queue_events holds it
                # while waiting on read_event. Closing the queue wakes that wait.
                # Keep the closed reader reference until emitter disposal so a
                # concurrent queue_events cannot dereference a replaced None.
                with self._buffer_close_lock:
                    if self._inotify is not None:
                        self._inotify.close()

            def queue_events(self, timeout, *, full_events=False):
                reader = self._inotify
                super().queue_events(timeout, full_events=full_events)
                if reader is not None and not reader.should_keep_running():
                    self.stop()  # Closed failed queue must not become a hot loop.

    else:
        raise RuntimeError("Unsupported shared watchdog platform")
    observer._event_queue = BoundedEventQueue(loss)
    observer._emitter_class = LossAwareEmitter
    return observer.event_queue
