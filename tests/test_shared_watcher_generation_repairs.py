"""Native root identity, Windows producer failure and overflow ordering."""

import errno
import os
import sys
import threading

import pytest

from app.services.semantic_sync import SyncStepState
from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_lifecycle_repairs import native_write_after_dispatch
from tests.test_shared_watcher_repairs import use_production_observer


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux root move")
@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_original_root_rename_replacement_is_not_healthy(tmp_path, shared, monkeypatch, replacement):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    delivered = threading.Event()
    parse = _BoundedInotify._parse_event_buffer

    def observe(reader, data):
        for record in parse(data):
            if reader.path.endswith(b"default") and record[1] & InotifyConstants.IN_MOVE_SELF:
                delivered.set()
            yield record

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", observe)
    resource, _ = shared
    with rig(tmp_path, resource=resource, real=True) as r:
        owner = r.owners["default"]
        root = owner.vault_root
        (root / "old.md").write_text("original inode", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        original_chunks = owner.repository.load_chunks()
        use_production_observer(r)
        r.watcher.start()
        native_write_after_dispatch(r, "default", "ready.md", monkeypatch)
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))
        reader = emitter._inotify
        original_identity = root.stat().st_ino
        begin = owner.begin_sync

        def authorized_begin(**kwargs):
            assert root.stat().st_ino == original_identity, "E3 reached unauthorized replacement"
            return begin(**kwargs)

        monkeypatch.setattr(owner, "begin_sync", authorized_begin)
        root.rename(tmp_path / "original-inode")
        if replacement == "directory":
            root.mkdir()
        else:
            target = tmp_path / "unauthorized"
            target.mkdir()
            os.symlink(target, root, target_is_directory=True)
        (root / "replacement.md").write_text("unauthorized replacement", encoding="utf-8")
        with r.scheduler._condition:
            observed = r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).monitoring_failed, 3,
            )
        status = r.scheduler.status(r.bindings["default"])
        mask = reader._inotify.event_mask
        print(f"root inode {original_identity}->{root.stat().st_ino}; "
              f"MOVE_SELF={bool(mask & InotifyConstants.IN_MOVE_SELF)}, "
              f"reader_alive={reader.is_alive()}, failed={status.monitoring_failed}, debt={status.requires_full_sync}")
        assert observed
        reader.join(5)
        emitter.join(5)
        assert delivered.is_set() and not reader.is_alive() and not emitter.is_alive()
        assert mask & InotifyConstants.IN_MOVE_SELF
        assert root.stat().st_ino != original_identity
        r.scheduler.request_full_sync(r.bindings["default"])
        r.scheduler.wait(15)
        assert r.scheduler.status(r.bindings["default"]).monitoring_failed
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert owner.repository.load_chunks() == original_chunks
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual IN_MOVE_SELF delivery")
def test_root_moved_back_to_same_inode_still_requires_reconstruction(tmp_path, monkeypatch):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    entered, release = threading.Event(), threading.Event()
    parse = _BoundedInotify._parse_event_buffer

    def paused(reader, data):
        for record in parse(data):
            if reader.path.endswith(b"default") and record[1] & InotifyConstants.IN_MOVE_SELF:
                entered.set()
                assert release.wait(10)
            yield record

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", paused)
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        native_write_after_dispatch(r, "default", "ready.md", monkeypatch)
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))
        reader = emitter._inotify
        root = r.owners["default"].vault_root
        identity = root.stat().st_ino
        moved = tmp_path / "moved"
        root.rename(moved)
        try:
            assert entered.wait(5)
            moved.rename(root)
            assert root.stat().st_ino == identity
        finally:
            release.set()
        reader.join(5)
        emitter.join(5)
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.owners["default"].begins == [None]
        assert r.scheduler.status(r.bindings["default"]).monitoring_failed
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows native emitter")
def test_original_windows_native_read_failure_is_not_healthy(tmp_path, monkeypatch, shared):
    from app.services import watcher_ingress_windows as native
    from app.services.watcher_ingress_windows import NativeDirectoryRead

    read = NativeDirectoryRead.read_directory_changes
    armed, failed = threading.Event(), threading.Event()
    close = native.close_directory_handle
    closed = {}

    def close_once(handle):
        key = getattr(handle, "value", handle)
        closed[key] = closed.get(key, 0) + 1
        close(handle)

    def broken(reader, handle, path, *, recursive):
        result = read(reader, handle, path, recursive=recursive)
        if path.endswith("default") and armed.is_set():
            failed.set()
            raise OSError(errno.EIO, "injected Windows native read failure")
        return result

    monkeypatch.setattr(NativeDirectoryRead, "read_directory_changes", broken)
    monkeypatch.setattr(native, "close_directory_handle", close_once)
    resource, _ = shared
    with rig(tmp_path, resource=resource, real=True) as r:
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        notify = r.watcher._ingress_failure
        failures = 0

        def notified(watch, reason):
            nonlocal failures
            failures += 1
            notify(watch, reason)

        monkeypatch.setattr(r.watcher, "_ingress_failure", notified)
        r.watcher.start()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))
        handle = getattr(emitter._whandle, "value", emitter._whandle)
        armed.set()
        (r.owners["default"].vault_root / "trigger.md").write_text("native failure", encoding="utf-8")
        assert failed.wait(5)
        emitter.join(5)
        assert not emitter.is_alive()
        r.scheduler.wait(15)
        status = r.scheduler.status(r.bindings["default"])
        print(f"Windows emitter_alive={emitter.is_alive()}, failed={status.monitoring_failed}, "
              f"debt={status.requires_full_sync}, handle_released={emitter._whandle is None}")
        assert status.monitoring_failed and status.requires_full_sync
        assert emitter._whandle is None
        assert failures == 1 and closed[handle] == 1
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        r.watcher.stop()
        assert failures == 1 and all(count == 1 for count in closed.values())


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows cancel/read races")
@pytest.mark.parametrize("winerror", [6, 995])
def test_normal_windows_cancel_does_not_latch_failure(tmp_path, monkeypatch, winerror):
    from app.services.watcher_ingress_windows import NativeDirectoryRead

    read = NativeDirectoryRead.read_directory_changes
    closing = threading.Event()

    def cancelled(reader, handle, path, *, recursive):
        result = read(reader, handle, path, recursive=recursive)
        if closing.is_set():
            error = OSError(errno.EINVAL, "intentional native cancellation")
            error.winerror = winerror
            raise error
        return result

    monkeypatch.setattr(NativeDirectoryRead, "read_directory_changes", cancelled)
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        emitters = tuple(r.watcher._observer.emitters)
        closing.set()
        launch(r.watcher.stop).result(10)
        r.watcher.stop()
        assert all(not e.is_alive() and e._whandle is None for e in emitters)
        assert all(not r.scheduler.status(b).monitoring_failed for b in r.bindings.values())


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows producer generation")
def test_windows_reconstruction_rejects_late_old_generation_callbacks(tmp_path, monkeypatch):
    from app.services.watcher_ingress_windows import NativeDirectoryRead

    read = NativeDirectoryRead.read_directory_changes
    armed, failed = threading.Event(), threading.Event()

    def broken(reader, handle, path, *, recursive):
        result = read(reader, handle, path, recursive=recursive)
        if path.endswith("default") and armed.is_set():
            failed.set()
            raise OSError(errno.EIO, "unexpected native failure")
        return result

    monkeypatch.setattr(NativeDirectoryRead, "read_directory_changes", broken)
    with rig(tmp_path) as r:
        use_production_observer(r)
        old = r.watcher
        old.start()
        observer = old._observer
        emitter = next(e for e in observer.emitters if e.watch.path.endswith("default"))
        watch = emitter.watch
        armed.set()
        (r.owners["default"].vault_root / "trigger.md").write_text("event", encoding="utf-8")
        assert failed.wait(5)
        emitter.join(5)
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.scheduler.status(r.bindings["default"]).monitoring_failed
        old.stop()
        assert not observer.is_alive()
        monkeypatch.setattr(NativeDirectoryRead, "read_directory_changes", read)
        r.owners["default"].release = threading.Event()
        r.owners["default"].entered.clear()
        use_production_observer(r)
        r.watcher.start()
        try:
            assert r.owners["default"].entered.wait(5)
            binding = r.bindings["default"]
            # The old failure now retires only after this matching full, not
            # merely at verified native startup. Old callbacks must not change
            # its failure evidence while the full is intentionally blocked.
            assert r.scheduler.status(binding).monitoring_failed
            assert r.scheduler.status(binding).monitoring_recovering
            revision = r.scheduler._owners[binding.space_id].coverage_revision
            failure_revision = r.scheduler._owners[binding.space_id].monitoring_failure_revision
            for _ in range(1000):
                old._ingress_failure(watch, "LateOldFailure")
                old._ingress_loss(watch)
                old._ingress_gap(watch)
                old._ingress_restored(watch, ((binding, revision),))
            assert r.scheduler._owners[binding.space_id].coverage_revision == revision
            assert r.scheduler.status(binding).monitoring_error_type == "OSError"
            assert r.scheduler._owners[binding.space_id].monitoring_failure_revision == failure_revision
            assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed
        finally:
            r.owners["default"].release.set()
        r.scheduler.wait(5)
        assert not r.scheduler.status(binding).monitoring_failed
        assert not r.scheduler.status(binding).requires_full_sync
        (r.owners["default"].vault_root / "fresh.md").write_text("restored", encoding="utf-8")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: "fresh.md" in r.scheduler.status(binding).queued_paths, 5,
            )


@pytest.mark.parametrize("transition", ["overflow", "reconstruction"])
def test_overlapping_coverage_revisions_fence_full_completion_and_coalesce(tmp_path, monkeypatch, transition):
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        watch = next(e.watch for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))
        binding = r.bindings["default"]
        owner = r.owners["default"]
        owner.full_steps = 1
        before_publish, release_first, second_entered, release_second = (threading.Event() for _ in range(4))
        step = owner.sync_step

        def gated(handle, cancel):
            result = step(handle, cancel)
            if len(owner.begins) == 1:
                before_publish.set()
                assert release_first.wait(10)
            else:
                second_entered.set()
                assert release_second.wait(10)
            return result

        monkeypatch.setattr(owner, "sync_step", gated)
        first = r.watcher._ingress_gap(watch)
        if transition == "reconstruction":
            r.watcher._ingress_restored(watch, first)
        r.scheduler.start()
        executor = r.scheduler._executor
        try:
            assert before_publish.wait(5)
            entry = r.scheduler._owners[binding.space_id]
            if transition == "overflow":
                second = r.watcher._ingress_gap(watch)
                r.watcher._ingress_restored(watch, first)
                assert entry.coverage_restored < entry.coverage_revision
                r.watcher._ingress_restored(watch, second)
            else:
                old, old_observer = r.watcher, r.watcher._observer
                old.stop()
                use_production_observer(r)
                r.watcher.start()
                assert not old_observer.is_alive() and r.watcher._observer.is_alive()
                old._ingress_failure(watch, "LateOldFailure")
                assert not r.scheduler.status(binding).monitoring_failed
                watch = next(e.watch for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))
                second = ((binding, entry.coverage_revision),)
                assert r.scheduler._executor is executor
            release_first.set()
            assert second_entered.wait(5)
            assert entry.coverage_reconciled < entry.coverage_revision
            assert r.scheduler.status(binding).monitoring_recovering
            peer = r.scheduler.status(r.bindings["space-1"])
            if transition == "reconstruction":
                assert not peer.monitoring_failed  # Its producer was also stopped.
            else:
                assert not peer.requires_full_sync
        finally:
            release_first.set()
            release_second.set()
        r.scheduler.wait(5)
        assert owner.begins == [None, None]
        assert not r.scheduler.status(binding).monitoring_recovering
        assert not r.scheduler.status(binding).requires_full_sync
        for _ in range(1000):
            r.watcher._ingress_restored(watch, first)
            r.watcher._ingress_restored(watch, second)
        r.scheduler.wait(5)
        assert owner.begins == [None, None] and r.scheduler.pending_count == 0


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows read entry/stop race")
@pytest.mark.parametrize("fatal", [False, True])
def test_windows_read_entry_racing_stop_retires_handle_safely(tmp_path, monkeypatch, fatal):
    from app.services.watcher_ingress_windows import NativeDirectoryRead

    read = NativeDirectoryRead.read_directory_changes
    entered, release, armed = threading.Event(), threading.Event(), threading.Event()

    def entering(reader, handle, path, *, recursive):
        if path.endswith("default") and armed.is_set() and not entered.is_set():
            entered.set()
            assert release.wait(10)
            if fatal:
                raise OSError(errno.EIO, "unexpected native error during stop")
        return read(reader, handle, path, recursive=recursive)

    monkeypatch.setattr(NativeDirectoryRead, "read_directory_changes", entering)
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        observer = r.watcher._observer
        emitters = tuple(observer.emitters)
        armed.set()
        (r.owners["default"].vault_root / "next-read.txt").write_text("next native read", encoding="utf-8")
        assert entered.wait(5)
        stopping = launch(r.watcher.stop)
        try:
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: not r.watcher._accepting, 5)
        finally:
            release.set()
        stopping.result(10)
        assert not observer.is_alive()
        assert all(not e.is_alive() and e._whandle is None and e._read_done.is_set() for e in emitters)
        assert r.scheduler.status(r.bindings["default"]).monitoring_failed == fatal
        assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed
        r.watcher.stop()


def blocked_linux_restoration(monkeypatch):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    injected, restoring, release, registered = (threading.Event() for _ in range(4))
    parse = _BoundedInotify._parse_event_buffer
    register = _BoundedInotify._add_dir_watch

    def overflow(reader, data):
        for record in parse(data):
            if (reader.path.endswith(b"default") and record[3] == b"missed-dir"
                    and record[1] & InotifyConstants.IN_CREATE and not injected.is_set()):
                injected.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
            else:
                yield record

    def paused(reader, path, mask, *, recursive):
        if reader.path.endswith(b"default") and injected.is_set() and not registered.is_set():
            restoring.set()
            assert release.wait(15)
            result = register(reader, path, mask, recursive=recursive)
            registered.set()
            return result
        return register(reader, path, mask, recursive=recursive)

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", overflow)
    monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", paused)
    return restoring, release, registered


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux overflow restoration")
def test_original_overflow_gap_requires_post_restoration_sync(tmp_path, monkeypatch, shared):
    restoring, release, registered = blocked_linux_restoration(monkeypatch)
    resource, _ = shared
    with rig(tmp_path, resource=resource, real=True) as r:
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        owner = r.owners["default"]
        post_sync = threading.Event()
        step = owner.sync_step

        def completed(handle, cancel):
            result = step(handle, cancel)
            if result.state is SyncStepState.COMPLETE and handle.full and registered.is_set():
                post_sync.set()
            return result

        monkeypatch.setattr(owner, "sync_step", completed)
        use_production_observer(r)
        r.watcher.start()
        missed = owner.vault_root / "missed-dir"
        missed.mkdir()
        try:
            assert restoring.wait(5)
            r.scheduler.wait(15)  # Pre-restoration full sync really completes.
            before = r.scheduler.status(r.bindings["default"])
            print(f"during restoration: failed={before.monitoring_failed}, debt={before.requires_full_sync}, "
                  f"recovering={getattr(before, 'monitoring_recovering', False)}")
            (missed / "lost.md").write_text("created while unwatched", encoding="utf-8")
        finally:
            release.set()
        assert registered.wait(5)
        reconciled = post_sync.wait(3)
        r.scheduler.wait(15)
        paths = {c.path for c in owner.repository.load_chunks()}
        status = r.scheduler.status(r.bindings["default"])
        print(f"after restoration: post_sync={reconciled}, paths={sorted(paths)}, debt={status.requires_full_sync}")
        assert reconciled
        assert "missed-dir/lost.md" in paths
        assert not status.monitoring_failed and not status.requires_full_sync


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual native coverage/shutdown")
@pytest.mark.parametrize("mode", ["cancel", "stop", "process"])
def test_restoration_cancellation_shutdown_and_healthy_owner_progress(tmp_path, monkeypatch, mode):
    from tests.test_semantic_scheduler import enqueue

    restoring, release, registered = blocked_linux_restoration(monkeypatch)
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        observer = r.watcher._observer
        emitters = tuple(observer.emitters)
        r.scheduler.start()
        (r.owners["default"].vault_root / "missed-dir").mkdir()
        stopping = None
        try:
            assert restoring.wait(5)
            r.scheduler.wait(5)
            assert r.scheduler.status(r.bindings["default"]).monitoring_recovering
            enqueue(r.scheduler, r.bindings["space-1"], "healthy.md")
            r.scheduler.wait(5)
            assert r.owners["space-1"].begins == [("healthy.md",)]
            assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
            if mode == "cancel":
                r.scheduler.cancel(r.bindings["default"])
            else:
                if mode == "process":
                    r.scheduler.begin_shutdown()
                stopping = launch(r.watcher.stop)
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(lambda: not r.watcher._accepting, 5)
        finally:
            release.set()
        if stopping is None:
            assert registered.wait(5)
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(
                    lambda: not r.scheduler.status(r.bindings["default"]).monitoring_recovering, 5,
                )
            r.scheduler.wait(5)
            assert r.owners["default"].begins == [None, None]
        else:
            stopping.result(10)
            assert not observer.is_alive() and all(not e.is_alive() for e in emitters)
            assert r.scheduler.status(r.bindings["default"]).monitoring_recovering
            assert r.scheduler.status(r.bindings["default"]).requires_full_sync
            assert not r.scheduler.status(r.bindings["default"]).monitoring_failed
            if mode == "process":
                assert r.scheduler._closed
                with pytest.raises(RuntimeError, match="shut down"):
                    r.scheduler.start()
            else:
                use_production_observer(r)
                r.watcher.start()
                r.scheduler.wait(5)
                assert not r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert r.scheduler.pending_count == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual native restoration failure")
def test_failed_restoration_cannot_be_published_as_recovered(tmp_path, monkeypatch):
    from tests.test_shared_watcher_lifecycle_repairs import inject_linux_registration_failure

    injected, _ = inject_linux_registration_failure(monkeypatch)
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))
        reader = emitter._inotify
        (r.owners["default"].vault_root / "overflow.md").write_text("failure", encoding="utf-8")
        assert injected.wait(5)
        reader.join(5)
        emitter.join(5)
        r.scheduler.start()
        r.scheduler.wait(5)
        binding = r.bindings["default"]
        entry = r.scheduler._owners[binding.space_id]
        revision = entry.coverage_revision
        for _ in range(1000):
            r.watcher._ingress_restored(emitter.watch, ((binding, revision),))
        r.scheduler.wait(5)
        status = r.scheduler.status(binding)
        assert status.monitoring_failed and status.monitoring_recovering and status.requires_full_sync
        assert r.owners["default"].begins == [None]
        assert entry.coverage_restored < revision and entry.coverage_reconciled < revision
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
