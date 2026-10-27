"""Linux native subtree coverage fences and explicit symlink exclusions."""

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

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux native registration")


@pytest.mark.parametrize("mode", ["single", "nested", "multiple", "overlap", "cancel", "stop", "process",
                                  "root", "registration"])
def test_original_queued_subtree_missed_deletion(tmp_path, monkeypatch, shared, mode):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    walk_done, release_walk, late_held, release_late, registered, post_sync = (
        threading.Event() for _ in range(6)
    )
    injected = threading.Event()
    overlapped = threading.Event()
    parse, register = _BoundedInotify._parse_event_buffer, _BoundedInotify._add_dir_watch
    add = _BoundedInotify._add_watch

    def overflow_and_hold(reader, data):
        for record in parse(data):
            if record[3] == b"overflow.txt" and not injected.is_set():
                injected.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
            elif record[3] == b"overlap.txt" and not overlapped.is_set():
                overlapped.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
            else:
                if record[3] == b"late" and record[1] & InotifyConstants.IN_CREATE:
                    late_held.set()
                    assert release_late.wait(15)
                yield record

    def after_walk(reader, path, mask, *, recursive):
        result = register(reader, path, mask, recursive=recursive)
        if path == reader.path and injected.is_set() and not walk_done.is_set():
            walk_done.set()
            assert release_walk.wait(15)
        return result

    def registration(reader, path, mask):
        if mode == "registration" and path.endswith(b"/late") and release_late.is_set():
            raise OSError(errno.ENOSPC, "native registration exhausted")
        result = add(reader, path, mask)
        if path.endswith(b"/late"):
            registered.set()
        return result

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", overflow_and_hold)
    monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", after_walk)
    monkeypatch.setattr(_BoundedInotify, "_add_watch", registration)
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        binding, owner = r.bindings["default"], r.owners["default"]
        step = owner.sync_step

        def completed(handle, cancel):
            result = step(handle, cancel)
            if result.state is SyncStepState.COMPLETE and handle.full and release_late.is_set():
                post_sync.set()
            return result

        monkeypatch.setattr(owner, "sync_step", completed)
        (owner.vault_root / "overflow.txt").write_text("kernel loss", encoding="utf-8")
        try:
            assert walk_done.wait(5)
            late = owner.vault_root / "late"
            late.mkdir()
            directories = [late]
            if mode == "nested":
                directories = [late / "nested" / "deeper"]
                directories[0].mkdir(parents=True)
            elif mode == "multiple":
                directories += [owner.vault_root / "late-2", owner.vault_root / "late-3"]
                for directory in directories[1:]:
                    directory.mkdir()
            notes = [directory / "gone.md" for directory in directories]
            for note in notes:
                note.write_text("will be deleted while unwatched", encoding="utf-8")
            release_walk.set()
            assert late_held.wait(5)
            r.scheduler.request_full_sync(binding)
            r.scheduler.wait(15)
            assert {chunk.path for chunk in owner.repository.load_chunks()} == {
                str(note.relative_to(owner.vault_root)) for note in notes
            }
            before = r.scheduler.status(binding)
            print(f"before late registration: recovering={before.monitoring_recovering}, "
                  f"debt={before.requires_full_sync}, failed={before.monitoring_failed}")
            assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
            native_write_after_dispatch(r, "space-1", "peer.md", monkeypatch)
            r.advance(10)
            r.delivered()
            r.scheduler.wait(15)
            assert {chunk.path for chunk in r.owners["space-1"].repository.load_chunks()} == {"peer.md"}
            for note in notes:
                note.unlink()
            stopping = None
            if mode == "overlap":
                (owner.vault_root / "overlap.txt").write_text("new native overflow", encoding="utf-8")
            elif mode == "cancel":
                r.scheduler.cancel(binding)
            elif mode in ("stop", "process"):
                if mode == "process":
                    r.scheduler.begin_shutdown()
                stopping = launch(r.watcher.stop)
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(lambda: r.watcher._stopping, 5)
            elif mode == "root":
                owner.vault_root.rename(tmp_path / "displaced")
                owner.vault_root.mkdir()
        finally:
            release_walk.set()
            release_late.set()
        assert before.monitoring_recovering and before.requires_full_sync
        if mode in ("root", "registration"):
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: r.scheduler.status(binding).monitoring_failed, 5)
            assert r.scheduler.status(binding).requires_full_sync
            assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
            return
        if stopping is not None:
            stopping.result(10)
            assert r.scheduler.status(binding).monitoring_recovering
            if mode == "process":
                assert r.scheduler._closed and not r.scheduler.request_full_sync(binding)
                return
            use_production_observer(r)
            r.watcher.start()
            r.scheduler.wait(15)
            assert not owner.repository.load_chunks()
            assert not r.scheduler.status(binding).requires_full_sync
            return
        assert registered.wait(5)
        post = post_sync.wait(3)
        r.scheduler.wait(15)
        paths = {chunk.path for chunk in owner.repository.load_chunks()}
        status = r.scheduler.status(binding)
        print(f"after registration: post_sync={post}, indexed={sorted(paths)}, "
              f"debt={status.requires_full_sync}, recovering={status.monitoring_recovering}")
        assert post and not paths
        assert not status.requires_full_sync and not status.monitoring_failed
        assert r.scheduler.pending_count == 0
        if mode == "overlap":
            assert overlapped.is_set()


def test_original_recursive_create_excludes_directory_symlink(tmp_path, monkeypatch):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    entered, release = threading.Event(), threading.Event()
    parse = _BoundedInotify._parse_event_buffer

    def paused(reader, data):
        for record in parse(data):
            if record[3] == b"new" and record[1] & InotifyConstants.IN_CREATE:
                entered.set()
                assert release.wait(10)
            yield record

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", paused)
    with rig(tmp_path, 1) as r:
        root = r.owners["default"].vault_root
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, root / "initial-link", target_is_directory=True)
        use_production_observer(r)
        r.watcher.start()
        emitter = next(iter(r.watcher._observer.emitters))
        reader = emitter._inotify
        directory = root / "new"
        directory.mkdir()
        try:
            assert entered.wait(5)
            os.symlink(outside, directory / "ignored-link", target_is_directory=True)
        finally:
            release.set()
        with r.scheduler._condition:
            r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).monitoring_failed, 0.2,
            )
        status = r.scheduler.status(r.bindings["default"])
        print(f"excluded link: reader_alive={reader.is_alive()}, emitter_alive={emitter.is_alive()}, "
              f"failure={status.monitoring_error_type}")
        assert not status.monitoring_failed
        native_write_after_dispatch(r, "default", "surviving.md", monkeypatch)
        assert reader.is_alive() and emitter.is_alive()
        assert not any(b"link" in path or path.startswith(os.fsencode(outside))
                       for path in reader._inotify._wd_for_path)


@pytest.mark.parametrize("boundary", ["after-check", "previously-watched"])
def test_directory_substitution_cannot_become_a_symlink_exclusion(tmp_path, monkeypatch, boundary):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    parse = _BoundedInotify._parse_event_buffer
    excluded = _BoundedInotify._excluded_directory_link
    changed = threading.Event()
    with rig(tmp_path, 1) as r:
        root = r.owners["default"].vault_root
        outside = tmp_path / "outside"
        outside.mkdir()
        directory = root / "ordinary"
        directory.mkdir()
        use_production_observer(r)
        r.watcher.start()
        emitter = next(iter(r.watcher._observer.emitters))
        reader = emitter._inotify

        if boundary == "previously-watched":
            directory.rename(tmp_path / "old-inode")
            os.symlink(outside, directory, target_is_directory=True)

        def loss(native, data):
            for record in parse(data):
                if record[3] == b"overflow.txt" and not changed.is_set():
                    changed.set()
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    yield record

        def replaced(native, path):
            result = excluded(native, path)
            if boundary == "after-check" and path == os.fsencode(directory) and not result:
                directory.rmdir()
                os.symlink(outside, directory, target_is_directory=True)
            return result

        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", loss)
        monkeypatch.setattr(_BoundedInotify, "_excluded_directory_link", replaced)
        (root / "overflow.txt").write_text("restore", encoding="utf-8")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).monitoring_failed, 5,
            )
        reader.join(5)
        emitter.join(5)
        assert not reader.is_alive() and not emitter.is_alive()
        assert reader._queue._closed and reader._inotify._resources_closed
        assert not any(path.startswith(os.fsencode(outside)) for path in reader._inotify._wd_for_path)


@pytest.mark.parametrize("failure", ["dropped-marker", "ENOSPC"])
def test_native_fence_loss_or_failure_retains_safe_recovery(tmp_path, monkeypatch, failure):
    import ctypes

    from watchdog.observers.inotify_c import InotifyConstants

    from app.services import watcher_ingress_linux as module

    injected, marker_lost = threading.Event(), threading.Event()
    parse = module._BoundedInotify._parse_event_buffer
    add_watch = module.inotify_add_watch

    def loss(reader, data):
        for record in parse(data):
            if record[3] == b"overflow.txt" and not injected.is_set():
                injected.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
            elif (injected.is_set() and failure == "dropped-marker" and record[0] == reader._fence_wd
                  and record[1] & InotifyConstants.IN_IGNORED and not marker_lost.is_set()):
                marker_lost.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
            else:
                yield record

    def exhausted(*args):
        if not injected.is_set():
            return add_watch(*args)
        ctypes.set_errno(errno.ENOSPC)
        return -1

    monkeypatch.setattr(module._BoundedInotify, "_parse_event_buffer", loss)
    if failure == "ENOSPC":
        monkeypatch.setattr(module, "inotify_add_watch", exhausted)
    with rig(tmp_path, 2) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        r.scheduler.start()
        (r.owners["default"].vault_root / "overflow.txt").write_text("loss", encoding="utf-8")
        binding = r.bindings["default"]
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(binding).monitoring_failed
                if failure == "ENOSPC" else marker_lost.is_set()
                and not r.scheduler.status(binding).requires_full_sync, 5,
            )
        r.scheduler.wait(5)
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        if failure == "ENOSPC":
            assert r.scheduler.status(binding).monitoring_failed and r.scheduler.status(binding).requires_full_sync
        else:
            native = next(e for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))._inotify._inotify
            assert not native._coverage_pending and native._fence_wd is None
            assert r.scheduler._owners[binding.space_id].coverage_revision >= 2
            assert len(r.owners["default"].begins) <= 3


def test_registration_can_complete_before_authoritative_reconciliation(tmp_path, monkeypatch, shared):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    parse = _BoundedInotify._parse_event_buffer
    injected, coverage, sync_started, release_sync = (threading.Event() for _ in range(4))

    def loss(reader, data):
        for record in parse(data):
            if record[3] == b"overflow.txt" and not injected.is_set():
                injected.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
            else:
                yield record

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", loss)
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        directory = owner.vault_root / "ordinary"
        directory.mkdir()
        note = directory / "gone.md"
        note.write_text("indexed", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        native = next(iter(r.watcher._observer.emitters))._inotify._inotify
        restored, begin = native._restored, owner.begin_sync

        def established(ticket):
            assert os.fsencode(directory) in native._wd_for_path
            coverage.set()
            restored(ticket)

        def wait_for_full(*, relative_paths, cancel_event):
            if relative_paths is None and coverage.is_set():
                sync_started.set()
                assert release_sync.wait(15)
            return begin(relative_paths=relative_paths, cancel_event=cancel_event)

        native._restored = established
        monkeypatch.setattr(owner, "begin_sync", wait_for_full)
        (owner.vault_root / "overflow.txt").write_text("loss", encoding="utf-8")
        try:
            assert coverage.wait(5) and sync_started.wait(5)
            assert r.scheduler.status(binding).monitoring_recovering
            note.unlink()
        finally:
            release_sync.set()
        r.scheduler.wait(15)
        assert not owner.repository.load_chunks()
        assert not r.scheduler.status(binding).requires_full_sync
