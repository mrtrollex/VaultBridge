"""E5 native producer health and accepted callback lifecycle regressions."""

import errno
import os
import sys
import threading

import pytest
from watchdog.events import FileModifiedEvent
from watchdog.observers.api import ObservedWatch

from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_repairs import use_production_observer


def native_write_after_dispatch(r, sid, name, monkeypatch):
    """The native close-write marker follows all modifications for this write."""
    handler = next(h for h in r.watcher._handlers if h._binding is r.bindings[sid])
    target = r.owners[sid].vault_root / name
    closed = threading.Event()
    dispatch = handler.dispatch

    def delivered(event):
        dispatch(event)
        if event.src_path == str(target) and event.event_type == "closed":
            closed.set()

    with monkeypatch.context() as patch:
        patch.setattr(handler, "dispatch", delivered)
        target.write_text("native event", encoding="utf-8")
        assert closed.wait(5)


def inject_linux_registration_failure(monkeypatch, error_number=errno.ENOSPC, blocked=None):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    injected = threading.Event()
    parse = _BoundedInotify._parse_event_buffer
    register = _BoundedInotify._add_dir_watch

    def overflow(reader, data):
        for record in parse(data):
            yield record
            if reader.path.endswith(b"default") and record[3] == b"overflow.md" and not injected.is_set():
                injected.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""

    def fail_registration(reader, path, mask, *, recursive):
        if injected.is_set() and reader.path.endswith(b"default"):
            if blocked is not None:
                entered, release = blocked
                entered.set()
                assert release.wait(10)
            raise OSError(error_number, "injected native registration failure")
        return register(reader, path, mask, recursive=recursive)

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", overflow)
    monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", fail_registration)
    return injected, register


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux watchdog reader")
def test_original_high_overflow_registration_failure_stays_observable(tmp_path, monkeypatch):
    injected, _ = inject_linux_registration_failure(monkeypatch)
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        observer = r.watcher._observer
        emitter = next(e for e in observer.emitters if e.watch.path.endswith("default"))
        reader = emitter._inotify
        (r.owners["default"].vault_root / "overflow.md").write_text("event", encoding="utf-8")
        assert injected.wait(5)
        reader.join(5)
        assert not reader.is_alive()
        emitter.join(0.1)
        r.scheduler.start()
        r.scheduler.wait(5)
        status = r.scheduler.status(r.bindings["default"])
        print(f"reader_alive={reader.is_alive()}, delayed_closed={reader._queue._closed}, "
              f"emitter_alive={emitter.is_alive()}, full_debt={status.requires_full_sync}, "
              f"monitoring_failed={getattr(status, 'monitoring_failed', False)}")
        assert reader._queue._closed
        assert not emitter.is_alive()
        assert status.monitoring_failed and status.requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


def test_original_medium_active_callback_crosses_watcher_stop(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        binding = r.bindings["default"]
        observer = r.watcher._observer
        watch = next(e.watch for e in observer.emitters if e.watch.path.endswith("default"))
        interpreter = next(h._interpreter for h in r.watcher._handlers if h._binding is binding)
        original = interpreter.paths_for_event
        entered, release = threading.Event(), threading.Event()

        def blocked(event):
            if event.src_path.endswith("accepted.md"):
                entered.set()
                assert release.wait(10)
            return original(event)

        monkeypatch.setattr(interpreter, "paths_for_event", blocked)
        observer.event_queue.put((FileModifiedEvent(str(r.owners["default"].vault_root / "accepted.md")), watch))
        assert entered.wait(5)
        assert observer.event_queue.qsize() == 0 and r.scheduler.pending_count == 0
        stopping = launch(r.watcher.stop)
        try:
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: not r.watcher._accepting, 5)
        finally:
            release.set()
        stopping.result(10)
        print(f"after watcher stop: reservations={r.scheduler.pending_count}, "
              f"full_debt={r.scheduler.status(binding).requires_full_sync}")
        assert r.scheduler.pending_count == 0
        assert r.scheduler.status(binding).requires_full_sync
        # B has no callback debt, but stopping its producer is a coverage gap.
        peer = r.scheduler._owners[r.bindings["space-1"].space_id]
        assert not peer.full_needed and r.scheduler.status(peer.binding).monitoring_recovering
        assert not observer.is_alive()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux watchdog reader")
@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EACCES, errno.ENOENT])
def test_native_failure_is_latched_until_verified_reconstruction(tmp_path, monkeypatch, error_number):
    from app.services.watcher_ingress_linux import _BoundedInotify

    injected, register = inject_linux_registration_failure(monkeypatch, error_number)
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        original_observer = r.watcher._observer
        emitter = next(e for e in original_observer.emitters if e.watch.path.endswith("default"))
        reader = emitter._inotify
        (r.owners["default"].vault_root / "overflow.md").write_text("event", encoding="utf-8")
        assert injected.wait(5)
        reader.join(5)
        emitter.join(5)
        assert not reader.is_alive() and not emitter.is_alive()
        assert reader._queue._closed and reader._inotify._resources_closed
        binding = r.bindings["default"]
        error_type = type(OSError(error_number, "category")).__name__
        assert reader.failure_type == error_type
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.owners["default"].begins == [None]
        assert not r.scheduler._owners[binding.space_id].full_needed
        assert r.scheduler.status(binding).monitoring_error_type == error_type
        assert r.scheduler.status(binding).requires_full_sync

        for _ in range(2000):
            r.watcher._ingress_failure(emitter.watch, error_type)
        r.scheduler.wait(5)
        assert r.owners["default"].begins == [None]  # No automatic retry loop.
        assert original_observer.event_queue.qsize() <= 256

        # The independent native producer and owner keep working.
        native_write_after_dispatch(r, "space-1", "healthy.md", monkeypatch)
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: "healthy.md" in r.scheduler.status(r.bindings["space-1"]).queued_paths, 5,
            )
        r.advance(10)
        r.delivered()
        r.scheduler.wait(5)
        assert r.owners["space-1"].begins == [("healthy.md",)]
        assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed
        assert r.scheduler.status(binding).error_type is None  # Sync succeeded.

        with pytest.raises(RuntimeError, match="already started"):
            r.watcher.start()  # An instance cannot restart its failed producers.
        r.watcher.stop()
        assert not original_observer.is_alive()
        assert r.scheduler.status(binding).monitoring_failed  # Stop is not recovery.
        r.scheduler.request_full_sync(binding)
        r.scheduler.wait(5)
        assert r.scheduler.status(binding).requires_full_sync

        # Explicit reconstruction: old observer is joined before a new watcher
        # can attach. Fresh native producer liveness AND matching reconciliation
        # precede latch clearing.
        monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", register)
        use_production_observer(r)
        r.watcher.start()
        assert r.watcher._observer is not original_observer
        assert not original_observer.is_alive()
        r.scheduler.wait(5)
        assert not r.scheduler.status(binding).monitoring_failed
        assert not r.scheduler.status(binding).requires_full_sync
        native_write_after_dispatch(r, "default", "recovered.md", monkeypatch)
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: "recovered.md" in r.scheduler.status(binding).queued_paths, 5,
            )
        r.advance(20)
        r.delivered()
        r.scheduler.wait(5)
        assert r.owners["default"].begins[-1] == ("recovered.md",)
        assert r.scheduler.pending_count == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux watchdog shutdown")
@pytest.mark.parametrize("process_shutdown", [False, True])
def test_native_failure_racing_shutdown_closes_reader_and_emitter(tmp_path, monkeypatch, process_shutdown):
    entered, release = threading.Event(), threading.Event()
    inject_linux_registration_failure(monkeypatch, blocked=(entered, release))
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        observer = r.watcher._observer
        emitters = tuple(observer.emitters)
        readers = tuple(e._inotify for e in emitters)
        (r.owners["default"].vault_root / "overflow.md").write_text("event", encoding="utf-8")
        assert entered.wait(5)
        if process_shutdown:
            r.scheduler.begin_shutdown()
        stopping = launch(r.watcher.stop)
        try:
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: not r.watcher._accepting, 5)
        finally:
            release.set()
        stopping.result(10)
        r.watcher.stop()
        assert not observer.is_alive() and all(not e.is_alive() for e in emitters)
        assert all(not reader.is_alive() and reader._queue._closed for reader in readers)
        assert all(reader._inotify._resources_closed for reader in readers)
        assert r.scheduler.status(r.bindings["default"]).monitoring_failed
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed
        assert r.scheduler.pending_count == 0


def test_ambiguous_failure_latches_enabled_owners_only_and_notifications_coalesce(tmp_path):
    with rig(tmp_path, 3, disabled=("space-2",)) as r:
        use_production_observer(r)
        r.watcher.start()
        # An equal-but-distinct watch supplies no exact registration identity.
        unknown = ObservedWatch(str(r.owners["default"].vault_root), recursive=True)
        for _ in range(1000):
            r.watcher._ingress_failure(unknown, "NativeProducerUnavailable")
        r.scheduler.start()
        r.scheduler.wait(5)
        for sid in ("default", "space-1"):
            status = r.scheduler.status(r.bindings[sid])
            assert status.monitoring_failed and status.requires_full_sync
            assert r.owners[sid].begins == [None]
        assert len(r.scheduler._owners) == 2 and r.scheduler.pending_count == 0
        for _ in range(1000):
            r.watcher._ingress_failure(unknown, "NativeProducerUnavailable")
        r.scheduler.wait(5)
        assert all(r.owners[sid].begins == [None] for sid in ("default", "space-1"))


def test_start_cannot_clear_failure_notified_after_health_snapshot(tmp_path, monkeypatch):
    from app.services import shared_filesystem_watcher

    with rig(tmp_path) as r:
        use_production_observer(r)
        binding = r.bindings["default"]
        r.scheduler._watcher_failed(binding, "PriorFailure")
        alive = shared_filesystem_watcher.native_watch_alive

        def raced(observer, watch):
            result = alive(observer, watch)
            if watch.path.endswith("default"):
                r.watcher._ingress_failure(watch, "NewFailure")
            return result

        monkeypatch.setattr(shared_filesystem_watcher, "native_watch_alive", raced)
        r.watcher.start()
        assert r.scheduler.status(binding).monitoring_error_type == "NewFailure"
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.scheduler.status(binding).requires_full_sync


@pytest.mark.parametrize("process_shutdown", [False, True])
@pytest.mark.parametrize("blocking_stage", ["interpretation", "validation"])
def test_overlapping_callbacks_are_owned_through_shutdown(tmp_path, monkeypatch,
                                                        process_shutdown, blocking_stage):
    with rig(tmp_path, 3) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        observer = r.watcher._observer
        emitters = tuple(observer.emitters)
        bindings = r.bindings
        entered = threading.Barrier(3, timeout=5)
        release = threading.Event()
        callback_context = threading.local()
        handlers = {h._binding.space_id.value: h for h in r.watcher._handlers}
        if blocking_stage == "interpretation":
            for sid in ("default", "space-1"):
                interpreter = handlers[sid]._interpreter
                original = interpreter.paths_for_event

                def blocked(event, original=original):
                    entered.wait()
                    assert release.wait(10)
                    return original(event)

                monkeypatch.setattr(interpreter, "paths_for_event", blocked)
        else:
            validate = r.scheduler._validate_owner

            def blocked(entry):
                if getattr(callback_context, "active", False) and not release.is_set():
                    entered.wait()
                    assert release.wait(10)
                return validate(entry)

            monkeypatch.setattr(r.scheduler, "_validate_owner", blocked)

        def callback(sid):
            callback_context.active = True
            try:
                handlers[sid].dispatch(FileModifiedEvent(str(r.owners[sid].vault_root / "same.md")))
            finally:
                callback_context.active = False

        callbacks = [launch(lambda: callback("default")), launch(lambda: callback("space-1"))]
        try:
            entered.wait()
            assert all(r.scheduler.status(bindings[sid]).active_watcher_callbacks == 1
                       for sid in ("default", "space-1"))
            if process_shutdown:
                r.scheduler.begin_shutdown()
            stopping = launch(r.watcher.stop)
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: not r.watcher._accepting, 5)
            assert all(r.scheduler.status(bindings[sid]).requires_full_sync
                       for sid in ("default", "space-1"))
        finally:
            release.set()
        for callback in callbacks:
            callback.result(5)
        stopping.result(10)
        r.watcher.stop()
        assert all(r.scheduler.status(bindings[sid]).active_watcher_callbacks == 0
                   for sid in bindings)
        assert all(r.scheduler.status(bindings[sid]).requires_full_sync
                   for sid in ("default", "space-1"))
        peer = r.scheduler._owners[bindings["space-2"].space_id]
        assert not peer.full_needed and r.scheduler.status(peer.binding).monitoring_recovering
        assert r.scheduler.pending_count == 0
        assert not observer.is_alive() and all(not e.is_alive() for e in emitters)
        if process_shutdown:
            assert r.scheduler._closed and not r.trace
            assert not r.scheduler.request_full_sync(bindings["default"])
            with pytest.raises(RuntimeError, match="shut down"):
                r.scheduler.start()
        else:
            monkeypatch.undo()  # Restore validation before running E4.
            r.scheduler.start()
            r.scheduler.wait(5)
            assert r.owners["default"].begins == [None]
            assert r.owners["space-1"].begins == [None]


def test_closed_callback_debt_stays_effective_while_e4_finishes_full(tmp_path, monkeypatch):
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        binding = r.bindings["default"]
        handler = r.watcher._handlers[0]
        entered, release = threading.Event(), threading.Event()
        original = handler._interpreter.paths_for_event

        def blocked(event):
            entered.set()
            assert release.wait(10)
            return original(event)

        monkeypatch.setattr(handler._interpreter, "paths_for_event", blocked)
        callback = launch(lambda: handler.dispatch(FileModifiedEvent(str(r.owners["default"].vault_root / "same.md"))))
        stopping = None
        try:
            assert entered.wait(5)
            stopping = launch(r.watcher.stop)
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: not r.watcher._accepting, 5)
            r.scheduler.start()
            r.scheduler.wait(5)
            assert not r.scheduler._owners[binding.space_id].full_needed
            status = r.scheduler.status(binding)
            assert status.requires_full_sync and status.active_watcher_callbacks == 1
            # External callbacks must resolve before a new watcher can attach.
            from app.services.shared_filesystem_watcher import SharedFilesystemWatcher

            replacement = SharedFilesystemWatcher(r.registry, r.bindings.values(),
                                                  scheduler=r.scheduler, debounce_seconds=10)
            with pytest.raises(RuntimeError, match="admission is unavailable"):
                replacement.start()
        finally:
            release.set()
        callback.result(5)
        stopping.result(10)
        r.scheduler.wait(5)
        assert r.scheduler.status(binding).active_watcher_callbacks == 0
        assert r.scheduler.status(binding).monitoring_recovering
        assert not r.scheduler._owners[binding.space_id].full_needed
        assert r.scheduler.pending_count == 0


def test_rejected_admission_becomes_exact_owner_debt(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        r.start()
        monkeypatch.setattr(r.scheduler, "_debounce", lambda *args, **kwargs: False)
        r.event()
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        assert r.scheduler.pending_count == 0
        assert r.scheduler.status(r.bindings["default"]).active_watcher_callbacks == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native root loss")
def test_native_root_loss_closes_producers_and_latches_health(tmp_path):
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))
        reader = emitter._inotify
        r.owners["default"].vault_root.rmdir()
        reader.join(5)
        emitter.join(5)
        assert not reader.is_alive() and not emitter.is_alive()
        assert reader._queue._closed and reader._inotify._resources_closed
        assert r.scheduler.status(r.bindings["default"]).monitoring_error_type == "FileNotFoundError"
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real E3/native Linux integration")
def test_successful_real_e3_index_reconciliation_does_not_clear_dead_monitor(tmp_path, monkeypatch, shared):
    injected, _ = inject_linux_registration_failure(monkeypatch)
    resource, state = shared
    with rig(tmp_path, resource=resource, real=True) as r:
        (r.owners["space-1"].vault_root / "peer.md").write_text("peer", encoding="utf-8")
        use_production_observer(r)
        r.watcher.start()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))
        reader = emitter._inotify
        (r.owners["default"].vault_root / "overflow.md").write_text("indexable", encoding="utf-8")
        assert injected.wait(5)
        reader.join(5)
        emitter.join(5)
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        assert {c.path for c in r.owners["default"].repository.load_chunks()} == {"overflow.md"}
        assert {c.path for c in r.owners["space-1"].repository.load_chunks()} == {"peer.md"}
        status = r.scheduler.status(r.bindings["default"])
        assert status.error_type is None and status.monitoring_failed and status.requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        assert r.scheduler.pending_count == 0 and len(state.instances) == 1


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="watchdog EACCES suppression")
def test_native_permission_denial_cannot_return_an_invalid_watch(tmp_path, monkeypatch):
    from watchdog.observers.inotify_c import Inotify

    from app.services.watcher_ingress_linux import _BoundedInotify

    directory = tmp_path / "root"
    directory.mkdir()
    before = set(os.listdir("/proc/self/fd"))
    # Watchdog's suppressed EACCES path returns wd=-1; exercise that exact
    # superclass result, retaining actual descriptor allocation/cleanup.
    monkeypatch.setattr(Inotify, "_add_watch", lambda *args: -1)
    with pytest.raises(PermissionError):
        _BoundedInotify(bytes(directory), recursive=True, loss=lambda: None)
    assert set(os.listdir("/proc/self/fd")) == before
