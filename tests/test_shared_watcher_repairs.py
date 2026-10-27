"""Confirmed E5 findings: real observer ingress and concurrent quiet periods."""

import sys
import threading
from contextlib import contextmanager

import pytest
from watchdog.events import FileModifiedEvent, FileMovedEvent
from watchdog.observers import Observer
from watchdog.observers.api import EventDispatcher, ObservedWatch

from app.services.knowledge_spaces import QualifiedPath, SpaceId
from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
from app.services.watcher_ingress import INGRESS_EVENT_LIMIT
from tests.test_semantic_resources import launch
from tests.test_shared_filesystem_watcher import rig


def use_production_observer(r):
    r.watcher = SharedFilesystemWatcher(
        r.registry, r.bindings.values(), scheduler=r.scheduler,
        debounce_seconds=10, clock=lambda: r.now,
    )


def test_original_high_real_observer_backlog(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        use_production_observer(r)
        entered, release = threading.Event(), threading.Event()
        original = r.watcher._offer

        def blocked(binding, interpreter, event):
            entered.set()
            assert release.wait(10)
            original(binding, interpreter, event)

        monkeypatch.setattr(r.watcher, "_offer", blocked)
        r.watcher.start()
        r.complete_native_startup()
        observer = r.watcher._observer
        watch = next(e.watch for e in observer.emitters if e.watch.path.endswith("default"))
        queue = observer.event_queue
        try:
            queue.put((FileModifiedEvent(str(tmp_path / "default" / "block.md")), watch))
            assert entered.wait(5)
            for i in range(12_000):
                queue.put((FileModifiedEvent(str(tmp_path / "default" / f"{i}.md")), watch))
            print(f"real Observer capacity={queue.maxsize}, retained={queue.qsize()}, "
                  f"reservations={r.scheduler.pending_count}, "
                  f"debt={r.scheduler.status(r.bindings['default']).requires_full_sync}")
            assert 0 < queue.maxsize <= 2000
            assert queue.qsize() <= queue.maxsize
            assert r.scheduler.status(r.bindings["default"]).requires_full_sync
            assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        finally:
            release.set()


def test_original_medium_older_callback_cannot_regress_deadline(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        r.start()
        entered, release = threading.Event(), threading.Event()
        canonical = r.scheduler._canonical_entry

        def delayed(binding, path):
            if threading.current_thread().name == "older-callback":
                entered.set()
                assert release.wait(5)
            return canonical(binding, path)

        monkeypatch.setattr(r.scheduler, "_canonical_entry", delayed)

        def older():
            threading.current_thread().name = "older-callback"
            r.event()

        callback = launch(older)
        try:
            assert entered.wait(5)  # A has already calculated 0 + 10.
            r.now = 9
            r.event()
            entry = r.scheduler._owners[SpaceId("default")]
            assert entry.deferred == {"same.md": 19}
        finally:
            release.set()
        callback.result(5)
        print(f"deadline after delayed A resumes: {entry.deferred['same.md']}")
        assert entry.deferred == {"same.md": 19}
        r.advance(10)
        r.scheduler._deliver_debounced(r.watcher._token, r.bindings["default"],
                                     QualifiedPath(SpaceId("default"), "same.md"), 10)
        assert not entry.pending
        r.advance(19)
        r.delivered()
        assert entry.pending == {"same.md"}
        assert r.scheduler.pending_count == 1


@contextmanager
def saturated(r, monkeypatch):
    """Block the real Observer's interpreter, fill the actual emitter queue."""
    entered, release = threading.Event(), threading.Event()
    use_production_observer(r)
    r.watcher.start()
    r.complete_native_startup()
    observer = r.watcher._observer
    watches = {e.watch.path: e.watch for e in observer.emitters}
    a = r.bindings["default"]
    root = str(a._owners.vault.vault_root)
    interpreter = next(h._interpreter for h in r.watcher._handlers if h._binding is a)
    original = interpreter.paths_for_event

    def blocked(event):
        if event.src_path.endswith("block.md"):
            entered.set()
            assert release.wait(15)
        return original(event)

    monkeypatch.setattr(interpreter, "paths_for_event", blocked)
    q = observer.event_queue
    try:
        q.put((FileModifiedEvent(root + "/block.md"), watches[root]))
        assert entered.wait(5)
        for i in range(INGRESS_EVENT_LIMIT):
            q.put((FileModifiedEvent(root + f"/{i}.md"), watches[root]))
        assert q.qsize() == INGRESS_EVENT_LIMIT
        yield observer, watches, release
    finally:
        release.set()


@pytest.mark.parametrize("lost_paths", [("old.md", "new.md"), ("", "new.md"), ("old.md", ""), ("", "")])
def test_real_ingress_lost_rename_paths_use_watch_identity(tmp_path, monkeypatch, lost_paths):
    with rig(tmp_path) as r, saturated(r, monkeypatch) as (observer, watches, _):
        root = str(r.owners["default"].vault_root)
        # Deliberately misleading path in the other space is never authority.
        old, new = lost_paths
        destination = str(r.owners["space-1"].vault_root / new) if new else ""
        observer.event_queue.put((FileMovedEvent(root + "/" + old if old else "", destination), watches[root]))
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        assert r.scheduler.pending_count == 0


def test_real_concurrent_overload_coalesces_owners_and_unknown_watch(tmp_path, monkeypatch):
    with rig(tmp_path, 3) as r, saturated(r, monkeypatch) as (observer, watches, _):
        def overload(sid):
            root = str(r.owners[sid].vault_root)
            for i in range(2000):
                observer.event_queue.put((FileModifiedEvent(root + f"/excess-{i}.md"), watches[root]))

        a, b = launch(lambda: overload("default")), launch(lambda: overload("space-1"))
        a.result(10)
        b.result(10)
        assert all(r.scheduler.status(r.bindings[sid]).requires_full_sync for sid in ("default", "space-1"))
        assert not r.scheduler.status(r.bindings["space-2"]).requires_full_sync
        assert observer.event_queue.qsize() == INGRESS_EVENT_LIMIT
        # Equality/path alone is insufficient evidence; another watch instance
        # is ambiguous and justifies conservative debt for every enabled owner.
        unknown = ObservedWatch(str(r.owners["default"].vault_root), recursive=True)
        observer.event_queue.put((FileModifiedEvent("same.md"), unknown))
        assert r.scheduler.status(r.bindings["space-2"]).requires_full_sync
        assert r.scheduler.pending_count == 0
        # A pre-existing paused failure stays paused under repeated overflow.
        entry = r.scheduler._owners[r.bindings["default"].space_id]
        with r.scheduler._condition:
            entry.paused = True
        overload("default")
        assert entry.paused and entry.full_needed


def test_real_saturated_shutdown_never_waits_for_queue_capacity(tmp_path, monkeypatch):
    with rig(tmp_path) as r, saturated(r, monkeypatch) as (observer, watches, release):
        q = observer.event_queue
        emitter_threads = tuple(observer.emitters)
        stopping = threading.Event()
        root = str(r.owners["default"].vault_root)

        def producer():
            i = 0
            while not stopping.is_set():
                q.put((FileModifiedEvent(root + f"/stream-{i}.md"), watches[root]))
                i += 1

        producing = launch(producer)
        stopped = launch(r.watcher.stop)
        try:
            # Stop must fence producers/drain the queue before it reaches the
            # Observer lock held by the deliberately blocked interpreter.
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: not r.watcher._accepting, 5)
            release.set()  # The active interpreter's safe operational boundary.
            stopped.result(10)
        finally:
            stopping.set()
            release.set()
            producing.result(5)
        assert not observer.is_alive() and all(not e.is_alive() for e in emitter_threads)
        assert q.qsize() <= 1  # Only watchdog's stop sentinel may remain.
        assert r.scheduler.pending_count == 0
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert r.watcher._thread is None


def test_real_pressure_subsides_normal_delivery_debounce_and_recovery(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        with saturated(r, monkeypatch) as (observer, watches, release):
            root = str(r.owners["default"].vault_root)
            q = observer.event_queue
            q.put((FileModifiedEvent(root + "/overflow.md"), watches[root]))
            release.set()
            # queue.join has no timeout API; wrap it in a finite test future.
            launch(q.join).result(10)
        r.scheduler.start()
        r.scheduler.wait(10)
        assert r.owners["default"].begins == [None]
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync
        q.put((FileModifiedEvent(root + "/after.md"), watches[root]))
        launch(q.join).result(5)
        entry = r.scheduler._owners[SpaceId("default")]
        assert entry.deferred == {"after.md": 10}
        r.advance(10)
        r.delivered()
        r.scheduler.wait(10)
        assert r.owners["default"].begins == [None, ("after.md",)]
        assert r.scheduler.pending_count == 0


def test_real_queue_deduplicates_even_at_capacity_and_stop_sentinel(tmp_path):
    with rig(tmp_path) as r:
        r.watcher._observer_factory = Observer
        # Constructing an observer allocates no native resources until start.
        observer = Observer()
        from app.services.watcher_ingress import install_bounded_ingress

        r.scheduler._attach_watcher(r.watcher._token)
        q = install_bounded_ingress(observer, r.watcher._ingress_loss)
        watch = observer.schedule(type("Handler", (), {})(), str(r.owners["default"].vault_root))
        r.watcher._watches.append((watch, r.bindings["default"]))
        for i in range(INGRESS_EVENT_LIMIT):
            last = (FileModifiedEvent(f"{i}.md"), watch)
            q.put(last)
        q.put(last)  # A deduplicated event is not loss, even at capacity.
        assert q.qsize() == INGRESS_EVENT_LIMIT
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync
        observer.stop()  # Full queue cannot block watchdog's stop sentinel.
        assert not observer.is_alive()
        q.close()
        assert q.unfinished_tasks == 0
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        q.put(EventDispatcher.stop_event)
        assert q.get_nowait() is EventDispatcher.stop_event
        q.task_done()


@pytest.mark.parametrize("failure", ["registration", "emitter-start", "observer-start"])
def test_real_observer_partial_start_cleans_native_resources(tmp_path, monkeypatch, failure):
    with rig(tmp_path) as r:
        observer = Observer()
        r.watcher._observer_factory = lambda: observer
        original_schedule = observer.schedule
        emitters = []

        def schedule(handler, path, **kwargs):
            if failure == "registration" and emitters:
                raise RuntimeError("registration")
            result = original_schedule(handler, path, **kwargs)
            emitters[:] = observer.emitters
            return result

        original_start = threading.Thread.start

        def start(thread):
            if failure == "observer-start" and thread is observer:
                raise RuntimeError("observer-start")
            if failure == "emitter-start" and thread in emitters:
                raise RuntimeError("emitter-start")
            return original_start(thread)

        monkeypatch.setattr(observer, "schedule", schedule)
        monkeypatch.setattr(threading.Thread, "start", start)
        with pytest.raises(RuntimeError, match=failure):
            r.watcher.start()
        assert not observer.is_alive() and not observer.emitters
        assert all(not emitter.is_alive() for emitter in emitters)
        assert r.watcher._thread is None and r.watcher._observer is None
        assert r.scheduler._watcher_token is None and r.scheduler.pending_count == 0


@pytest.mark.parametrize("transition", ["expiry", "transfer", "cancel", "full", "shutdown"])
def test_delayed_callback_cannot_resurrect_changed_reservation(tmp_path, monkeypatch, transition):
    with rig(tmp_path) as r:
        r.start()
        r.event()
        entered, release = threading.Event(), threading.Event()
        original = r.scheduler._canonical_entry

        def canonical(binding, path):
            if threading.current_thread().name == "delayed":
                entered.set()
                assert release.wait(10)
            return original(binding, path)

        monkeypatch.setattr(r.scheduler, "_canonical_entry", canonical)

        def delayed():
            threading.current_thread().name = "delayed"
            r.event()

        callback = launch(delayed)
        try:
            assert entered.wait(5)
            binding = r.bindings["default"]
            if transition == "expiry":
                r.advance(10)
                r.delivered()
            elif transition == "transfer":
                r.scheduler.enqueue(binding, QualifiedPath(binding.space_id, "same.md"))
            elif transition == "cancel":
                r.scheduler.cancel(binding)
            elif transition == "full":
                r.scheduler.request_full_sync(binding)
                r.scheduler.start()
                r.scheduler.wait(5)
            else:
                r.watcher.stop()
        finally:
            release.set()
        callback.result(5)
        entry = r.scheduler._owners[SpaceId("default")]
        assert not entry.deferred
        assert r.scheduler.pending_count == len(entry.pending | entry.inflight)
        assert r.scheduler.pending_count == (1 if transition in ("expiry", "transfer") else 0)
        if transition != "shutdown":
            r.scheduler.start()
            r.scheduler.wait(5)
            assert r.scheduler.pending_count == 0


def test_many_delayed_callbacks_keep_latest_deadline_and_qualified_independence(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        r.start()
        barrier = threading.Barrier(5, timeout=5)
        release = threading.Event()
        original = r.scheduler._canonical_entry

        def canonical(binding, path):
            if threading.current_thread().name == "older":
                barrier.wait()
                assert release.wait(5)
            return original(binding, path)

        monkeypatch.setattr(r.scheduler, "_canonical_entry", canonical)

        def older():
            threading.current_thread().name = "older"
            r.event()

        callbacks = [launch(older) for _ in range(4)]
        try:
            barrier.wait()
            r.now = 9
            r.event()
            r.now = 12
            r.event("space-1")
        finally:
            release.set()
        for callback in callbacks:
            callback.result(5)
        assert r.scheduler._owners[SpaceId("default")].deferred == {"same.md": 19}
        assert r.scheduler._owners[SpaceId("space-1")].deferred == {"same.md": 22}
        assert r.scheduler.pending_count == 2


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native inotify architecture")
def test_real_linux_upstream_move_buffer_and_cookie_cache_are_bounded(tmp_path, monkeypatch):
    from watchdog.observers.inotify_c import InotifyConstants, InotifyEvent

    from app.services.watcher_ingress_linux import BoundedInotifyBuffer

    with rig(tmp_path) as r:
        entered, release = threading.Event(), threading.Event()
        original = BoundedInotifyBuffer.read_event

        def blocked(buffer):
            entered.set()
            assert release.wait(10)
            return original(buffer)

        monkeypatch.setattr(BoundedInotifyBuffer, "read_event", blocked)
        r.watcher._observer_factory = Observer
        r.watcher.start()
        r.complete_native_startup()
        observer = r.watcher._observer
        assert entered.wait(5)
        emitter = next(e for e in observer.emitters if e.watch.path.endswith("default"))
        buffer = emitter._inotify
        try:
            for i in range(12_000):
                event = InotifyEvent(1, InotifyConstants.IN_MOVED_FROM, i, b"file", b"file")
                buffer._queue.put(event, delay=True)
                buffer._inotify.remember_move_from_event(event)
            assert len(buffer._queue._queue) == INGRESS_EVENT_LIMIT
            assert len(buffer._inotify._moved_from_events) <= INGRESS_EVENT_LIMIT
            assert observer.event_queue.qsize() <= INGRESS_EVENT_LIMIT
            assert r.scheduler.status(r.bindings["default"]).requires_full_sync
            assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        finally:
            release.set()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native inotify architecture")
def test_real_linux_native_read_batch_and_kernel_loss_create_owner_debt(tmp_path, monkeypatch):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    original = _BoundedInotify._parse_event_buffer
    # A fixed native read containing overflow has no source/destination paths.
    # Its reader fd still proves the exact owner. Exercise the actual reader
    # thread, real fd and emitter/observer lifecycle, replacing only parser data.
    injected = threading.Event()

    def parse(reader, data):
        for record in original(data):
            yield record
            if reader.path.endswith(b"default") and record[3] == b"native.md" and not injected.is_set():
                injected.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", parse)
    with rig(tmp_path) as r:
        r.watcher._observer_factory = Observer
        r.watcher.start()
        r.complete_native_startup()
        (r.owners["default"].vault_root / "native.md").write_text("note", encoding="utf-8")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).requires_full_sync, 5,
            )
        assert injected.is_set()
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native recursive inotify recovery")
def test_linux_kernel_loss_restores_native_subdirectory_watch_coverage(tmp_path, monkeypatch):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    original = _BoundedInotify._parse_event_buffer
    injected, refreshed = threading.Event(), threading.Event()
    original_register = _BoundedInotify._add_dir_watch

    def parse(reader, data):
        for record in original(data):
            if (reader.path.endswith(b"default") and record[3] == b"missed-creation"
                    and record[1] & InotifyConstants.IN_CREATE and not injected.is_set()):
                injected.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
            else:
                yield record

    def register(reader, path, mask, *, recursive):
        original_register(reader, path, mask, recursive=recursive)
        if injected.is_set() and reader.path.endswith(b"default"):
            refreshed.set()

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", parse)
    monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", register)
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        root = r.owners["default"].vault_root
        directory = root / "missed-creation"
        directory.mkdir()  # The native create event is deliberately lost.
        assert refreshed.wait(5)
        (directory / "later.md").write_text("later event", encoding="utf-8")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: "missed-creation/later.md" in r.scheduler.status(r.bindings["default"]).queued_paths, 5,
            )
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.skipif(sys.platform != "win32", reason="native ReadDirectoryChangesW architecture")
def test_real_windows_empty_native_buffer_is_owner_local_loss(tmp_path, monkeypatch):
    from app.services.watcher_ingress_windows import NativeDirectoryRead

    original = NativeDirectoryRead.read_directory_changes
    injected = threading.Event()

    def read(reader, handle, path, *, recursive):
        result = original(reader, handle, path, recursive=recursive)
        if path.endswith("default") and not injected.is_set():
            injected.set()
            return b"", 0
        return result

    monkeypatch.setattr(NativeDirectoryRead, "read_directory_changes", read)
    with rig(tmp_path) as r:
        r.watcher._observer_factory = Observer
        r.watcher.start()
        r.complete_native_startup()
        (r.owners["default"].vault_root / "overflow.txt").write_text("complete real read", encoding="utf-8")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).requires_full_sync, 5,
            )
        assert injected.is_set()
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


def test_real_native_filesystem_delivery_after_create_and_rename(tmp_path):
    with rig(tmp_path) as r:
        r.watcher._observer_factory = Observer
        r.watcher.start()
        r.complete_native_startup()
        binding = r.bindings["default"]
        root = r.owners["default"].vault_root
        (root / "old.md").write_text("native", encoding="utf-8")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: "old.md" in r.scheduler.status(binding).queued_paths, 5,
            )
        (root / "old.md").rename(root / "new.md")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: "new.md" in r.scheduler.status(binding).queued_paths, 5,
            )
        assert r.scheduler.status(binding).queued_paths == ("new.md", "old.md")
        assert r.scheduler.pending_count == 2
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native inotify reader cleanup")
@pytest.mark.parametrize("failure", ["reader-start", "native-registration"])
def test_linux_reader_start_failure_closes_descriptors(tmp_path, monkeypatch, failure):
    import os

    from app.services.watcher_ingress_linux import BoundedInotifyBuffer, _BoundedInotify

    before = set(os.listdir("/proc/self/fd"))
    original = threading.Thread.start

    def start(thread):
        if failure == "reader-start" and isinstance(thread, BoundedInotifyBuffer):
            raise RuntimeError("reader-start")
        return original(thread)

    def register(*args, **kwargs):
        raise RuntimeError("native-registration")

    monkeypatch.setattr(threading.Thread, "start", start)
    if failure == "native-registration":
        monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", register)
    with rig(tmp_path) as r:
        r.watcher._observer_factory = Observer
        with pytest.raises(RuntimeError, match=failure):
            r.watcher.start()
        assert not r.watcher._handlers and r.watcher._thread is None
        assert r.scheduler.pending_count == 0
    assert set(os.listdir("/proc/self/fd")) == before
