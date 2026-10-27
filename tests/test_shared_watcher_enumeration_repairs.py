"""Fail closed on incomplete Linux native recursive enumeration."""

import errno
import os
import sys
import threading

import pytest

from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_lifecycle_repairs import native_write_after_dispatch
from tests.test_shared_watcher_repairs import use_production_observer

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux native enumeration")


def test_original_overflow_permission_error_cannot_certify_coverage(tmp_path, monkeypatch, shared):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import BoundedInotifyBuffer, _BoundedInotify

    entered, release, observed, outcome, fence, drained = (threading.Event() for _ in range(6))
    parse, add = _BoundedInotify._parse_event_buffer, _BoundedInotify._add_watch
    report, scandir = BoundedInotifyBuffer._report_failure, os.scandir
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        ordinary = owner.vault_root / "ordinary"
        ordinary.mkdir()
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path == str(owner.vault_root))
        reader = emitter._inotify
        native = reader._inotify
        restored = native._restored
        injected = threading.Event()

        def overflow(native, data):
            for record in parse(data):
                if record[3] == b"deep" and record[1] & InotifyConstants.IN_CREATE and not injected.is_set():
                    injected.set()
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    yield record
                if record[3] == b"ordered.txt":
                    drained.set()

        def pause_after_add(native, path, mask):
            result = add(native, path, mask)
            if native is reader._inotify and path == os.fsencode(ordinary) and injected.is_set():
                entered.set()
                assert release.wait(15)
            return result

        def checked_scandir(path):
            try:
                return scandir(path)
            except PermissionError as error:
                if os.fsdecode(path) == str(ordinary) and error.errno == errno.EACCES:
                    observed.set()
                raise

        def restored_permissions(ticket):
            ordinary.chmod(0o700)
            fence.set()
            restored(ticket)
            outcome.set()

        def failed(buffer, error_type):
            report(buffer, error_type)
            if buffer is reader:
                outcome.set()

        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", overflow)
        monkeypatch.setattr(_BoundedInotify, "_add_watch", pause_after_add)
        monkeypatch.setattr(BoundedInotifyBuffer, "_report_failure", failed)
        monkeypatch.setattr(os, "scandir", checked_scandir)
        native._restored = restored_permissions
        deep = ordinary / "deep"
        deep.mkdir()
        note = deep / "gone.md"
        note.write_text("missed deletion", encoding="utf-8")
        try:
            assert entered.wait(5)
            ordinary.chmod(0)
            with pytest.raises(PermissionError) as denied:
                with scandir(ordinary):
                    pass
            assert denied.value.errno == errno.EACCES, "effective chmod did not deny enumeration"
            release.set()
            assert outcome.wait(5) and observed.is_set()
        finally:
            ordinary.chmod(0o700)
            release.set()
        r.scheduler.request_full_sync(binding)
        r.scheduler.wait(15)
        assert {chunk.path for chunk in owner.repository.load_chunks()} == {"ordinary/deep/gone.md"}
        note.unlink()
        (owner.vault_root / "ordered.txt").write_text("ordered native drain", encoding="utf-8")
        if not r.scheduler.status(binding).monitoring_failed:
            assert drained.wait(5)
        status = r.scheduler.status(binding)
        print(f"actual EACCES={observed.is_set()}, fence={fence.is_set()}, "
              f"deep_watched={os.fsencode(deep) in native._wd_for_path}, "
              f"failed={status.monitoring_failed}, recovering={status.monitoring_recovering}, "
              f"debt={status.requires_full_sync}, rows={[c.path for c in owner.repository.load_chunks()]}")
        assert status.monitoring_failed and status.monitoring_error_type == "PermissionError"
        assert status.monitoring_recovering and status.requires_full_sync and not fence.is_set()
        reader.join(5)
        emitter.join(5)
        assert not reader.is_alive() and not emitter.is_alive()
        assert reader._queue._closed and native._resources_closed
        native_write_after_dispatch(r, "space-1", "peer.md", monkeypatch)
        r.advance(10)
        r.delivered()
        r.scheduler.wait(15)
        assert {c.path for c in r.owners["space-1"].repository.load_chunks()} == {"peer.md"}
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        old = r.watcher
        old.stop()
        use_production_observer(r)
        r.watcher.start()
        r.scheduler.wait(15)
        assert not owner.repository.load_chunks()
        assert not r.scheduler.status(binding).requires_full_sync
        # Retired native evidence cannot alter verified reconstruction.
        entry = r.scheduler._owners[binding.space_id]
        revision = entry.coverage_revision
        restored(native._coverage_ticket)
        old._ingress_failure(None, "PermissionError")
        assert entry.coverage_revision == revision and not r.scheduler.status(binding).monitoring_failed


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EPERM, errno.EIO, errno.ENOENT])
@pytest.mark.parametrize("stage", ["startup", "create", "overflow", "queued-fence"])
def test_existing_directory_enumeration_errors_are_fatal(tmp_path, monkeypatch, error_number, stage):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    scan, parse, walk = os.scandir, _BoundedInotify._parse_event_buffer, _BoundedInotify._registration_walk
    armed, observed = threading.Event(), threading.Event()
    initializing = []
    with rig(tmp_path, 2) as r:
        root = r.owners["default"].vault_root
        child = root / "ordinary"
        if stage != "create":
            (child / "deep").mkdir(parents=True)

        def denied(path):
            if armed.is_set() and os.fsdecode(path) == str(child):
                observed.set()
                raise OSError(error_number, "deterministic enumeration failure", path)
            return scan(path)

        def overflow(reader, data):
            for record in parse(data):
                if record[3] == b"overflow.txt":
                    if stage == "queued-fence":
                        reader._begin_registration()
                        reader._queue_coverage_fence()
                        assert reader._fence_wd is not None
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    yield record

        monkeypatch.setattr(os, "scandir", denied)
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", overflow)

        def captured(reader, path):
            if stage == "startup":
                initializing.append(reader)
            return walk(reader, path)

        monkeypatch.setattr(_BoundedInotify, "_registration_walk", captured)
        use_production_observer(r)
        if stage == "startup":
            armed.set()
            with pytest.raises(OSError) as error:
                r.watcher.start()
            assert error.value.errno == error_number
            assert observed.is_set()
            assert r.watcher._observer is None and r.watcher._thread is None
            assert r.scheduler._watcher_token is None
            assert initializing
            for reader in initializing:
                for descriptor in (reader._inotify_fd, reader._kill_r, reader._kill_w):
                    with pytest.raises(OSError) as closed:
                        os.fstat(descriptor)
                    assert closed.value.errno == errno.EBADF
            armed.clear()
            use_production_observer(r)
            r.watcher.start()
            native_write_after_dispatch(r, "default", "after.md", monkeypatch)
            return
        r.watcher.start()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path == str(root))
        reader = emitter._inotify
        armed.set()
        if stage == "create":
            child.mkdir()
        else:
            (root / "overflow.txt").write_text("loss", encoding="utf-8")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).monitoring_failed, 5,
            )
        assert observed.is_set()
        reader.join(5)
        emitter.join(5)
        assert not reader.is_alive() and not emitter.is_alive()
        assert reader._queue._closed and reader._inotify._resources_closed
        r.scheduler.start()
        r.scheduler.wait(5)
        status = r.scheduler.status(r.bindings["default"])
        assert status.monitoring_failed and status.monitoring_recovering and status.requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.parametrize("stage", ["startup", "create", "overflow"])
def test_unregistered_child_disappears_before_enumeration(tmp_path, monkeypatch, stage):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    add, parse = _BoundedInotify._add_watch, _BoundedInotify._parse_event_buffer
    enumeration_error = _BoundedInotify._enumeration_error
    entered, release, removed, observed = (threading.Event() for _ in range(4))
    with rig(tmp_path, 1) as r:
        root = r.owners["default"].vault_root
        ordinary = root / "ordinary"
        child = ordinary / "ephemeral"
        if stage != "create":
            ordinary.mkdir()
        if stage == "startup":
            child.mkdir()

        def disappearing(reader, path, mask):
            if path == os.fsencode(child) and not removed.is_set():
                assert path not in reader._wd_for_path
                child.rmdir()
                removed.set()
            return add(reader, path, mask)

        def vanished(native, error, parent, identity):
            # Strict traversal validates absence before opening scandir. Observe
            # the same actual ENOENT at its earlier classification boundary.
            if error.errno == errno.ENOENT and os.fsdecode(error.filename) == str(child):
                observed.set()
            return enumeration_error(native, error, parent, identity)

        def events(reader, data):
            for record in parse(data):
                if stage == "overflow" and record[3] == b"ephemeral" and record[1] & InotifyConstants.IN_CREATE:
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    if stage == "create" and record[3] == b"ordinary" and record[1] & InotifyConstants.IN_CREATE:
                        entered.set()
                        assert release.wait(10)
                    yield record

        monkeypatch.setattr(_BoundedInotify, "_enumeration_error", vanished)
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", events)
        monkeypatch.setattr(_BoundedInotify, "_add_watch", disappearing)
        use_production_observer(r)
        r.watcher.start()
        emitter = next(iter(r.watcher._observer.emitters))
        reader = emitter._inotify
        if stage == "create":
            ordinary.mkdir()
            try:
                assert entered.wait(5)
                child.mkdir()
            finally:
                release.set()
        elif stage == "overflow":
            # Replace its CREATE record with actual overflow processing before
            # any registration; restoration discovers and loses the new child.
            child.mkdir()
        assert removed.wait(5) and observed.wait(5)
        r.complete_native_startup()  # Wait through any resulting delete/overflow native-set rebuild.
        native_write_after_dispatch(r, "default", "after.md", monkeypatch)
        assert reader.is_alive() and emitter.is_alive()
        assert not r.scheduler.status(r.bindings["default"]).monitoring_failed
        assert os.fsencode(child) not in reader._inotify._wd_for_path
        assert os.fsencode(child) not in reader._inotify._path_for_wd.values()
        r.scheduler.start()
        r.scheduler.wait(5)
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync


@pytest.mark.parametrize("mode", ["full-complete", "overlap", "shutdown"])
def test_enumeration_failure_fences_inflight_recovery_and_shutdown(tmp_path, monkeypatch, mode):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    parse, scan = _BoundedInotify._parse_event_buffer, os.scandir
    entered, release = threading.Event(), threading.Event()
    with rig(tmp_path, 2) as r:
        root, binding = r.owners["default"].vault_root, r.bindings["default"]
        ordinary = root / "ordinary"
        ordinary.mkdir()
        use_production_observer(r)
        r.watcher.start()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path == str(root))
        reader = emitter._inotify

        def denied(path):
            if threading.current_thread() is reader and os.fsdecode(path) == str(ordinary):
                entered.set()
                assert release.wait(10)
                raise PermissionError(errno.EACCES, "runtime enumeration denied", path)
            return scan(path)

        def overflow(native, data):
            for record in parse(data):
                if record[3] == b"overflow.txt":
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                    if mode == "overlap":
                        yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    yield record

        monkeypatch.setattr(os, "scandir", denied)
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", overflow)
        (root / "overflow.txt").write_text("loss", encoding="utf-8")
        stopping = None
        try:
            assert entered.wait(5)
            # A real E4 full finishes while the native walk remains incomplete.
            r.scheduler.start()
            r.scheduler.wait(5)
            assert r.owners["default"].begins == [None]
            entry = r.scheduler._owners[binding.space_id]
            revision = entry.coverage_revision
            assert revision >= (2 if mode == "overlap" else 1)
            assert r.scheduler.status(binding).monitoring_recovering
            if mode == "shutdown":
                stopping = launch(r.watcher.stop)
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(lambda: r.watcher._stopping, 5)
        finally:
            release.set()
        reader.join(5)
        emitter.join(5)
        if stopping is not None:
            stopping.result(10)
            assert r.scheduler._watcher_token is None
        status = r.scheduler.status(binding)
        assert status.monitoring_failed and status.monitoring_recovering and status.requires_full_sync
        assert entry.coverage_revision > revision
        assert reader._queue._closed and reader._inotify._resources_closed
        assert not reader.is_alive() and not emitter.is_alive()
        if mode != "shutdown":
            r.scheduler.request_full_sync(binding)
            r.scheduler.wait(5)
            assert r.scheduler.status(binding).monitoring_failed and r.scheduler.status(binding).requires_full_sync
            native_write_after_dispatch(r, "space-1", "survives.md", monkeypatch)
            assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.parametrize("change", ["ordinary-parent", "symlink-parent", "root-loss", "root-replaced",
                                   "watched-child"])
def test_enoent_cannot_hide_parent_substitution_or_monitored_directory_loss(tmp_path, monkeypatch, change):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    add, parse, scan = _BoundedInotify._add_watch, _BoundedInotify._parse_event_buffer, os.scandir
    changed = threading.Event()
    with rig(tmp_path, 2) as r:
        root = r.owners["default"].vault_root
        parent = root / "ordinary"
        parent.mkdir()
        child = parent / "ephemeral"
        if change == "watched-child":
            child.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path == str(root))
        reader = emitter._inotify

        def replaced(native, path, mask):
            if change != "watched-child" and path == os.fsencode(child) and not changed.is_set():
                assert path not in native._wd_for_path
                child.rmdir()
                if change.endswith("parent"):
                    parent.rename(tmp_path / "displaced-parent")
                    if change == "ordinary-parent":
                        parent.mkdir()
                    else:
                        os.symlink(outside, parent, target_is_directory=True)
                else:
                    root.rename(tmp_path / "displaced-root")
                    if change == "root-replaced":
                        root.mkdir()
                changed.set()
            return add(native, path, mask)

        def removed(path):
            if change == "watched-child" and os.fsdecode(path) == str(child) and not changed.is_set():
                assert os.fsencode(child) in reader._inotify._wd_for_path
                child.rmdir()
                changed.set()
            return scan(path)

        def overflow(native, data):
            for record in parse(data):
                if record[3] in (b"ephemeral", b"overflow.txt") and record[1] & InotifyConstants.IN_CREATE:
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    yield record

        monkeypatch.setattr(_BoundedInotify, "_add_watch", replaced)
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", overflow)
        monkeypatch.setattr(os, "scandir", removed)
        if change == "watched-child":
            (root / "overflow.txt").write_text("loss", encoding="utf-8")
        else:
            child.mkdir()
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).monitoring_failed, 5,
            )
        assert changed.is_set()
        reader.join(5)
        emitter.join(5)
        assert not reader.is_alive() and not emitter.is_alive()
        assert reader._queue._closed and reader._inotify._resources_closed
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        assert not any(path.startswith(os.fsencode(outside)) for path in reader._inotify._wd_for_path)
