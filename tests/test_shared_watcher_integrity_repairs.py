"""Idle root binding verification and benign Linux recursive child churn."""

import errno
import os
import subprocess
import sys
import threading

import pytest

from app.repositories.semantic_binding import IndexBindingUnavailableError
from app.services.knowledge_spaces import SpaceError
from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_lifecycle_repairs import native_write_after_dispatch
from tests.test_shared_watcher_repairs import use_production_observer


@pytest.mark.parametrize("replacement", ["absent", "directory", "alias"])
def test_original_idle_displaced_root_is_detected(tmp_path, shared, monkeypatch, replacement):
    resource, _ = shared
    container = tmp_path / "ancestor"
    with rig(container, 1, resource=resource, real=True) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        root = owner.vault_root
        (root / "existing.md").write_text("original", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        observer, dispatcher = r.watcher._observer, r.watcher._thread
        producer = next(iter(observer.emitters))
        reader = getattr(producer, "_inotify", producer)
        original_inode = root.stat().st_ino
        begin = owner.begin_sync

        def authorized_begin(**kwargs):
            assert root.stat().st_ino == original_inode, "replacement reached E3"
            return begin(**kwargs)

        monkeypatch.setattr(owner, "begin_sync", authorized_begin)
        if sys.platform == "win32":
            displaced = container / "displaced"
            root.rename(displaced)
        else:
            # Moving an ancestor emits no IN_MOVE_SELF on the watched root.
            container.rename(tmp_path / "displaced-ancestor")
            displaced = tmp_path / "displaced-ancestor" / "default"
        if replacement == "alias":
            root.parent.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(root), str(displaced)],
                               check=True, capture_output=True, timeout=5)
            else:
                os.symlink(displaced, root, target_is_directory=True)
        elif replacement == "directory":
            root.mkdir(parents=True)
            (root / "missed.md").write_text("unauthorized", encoding="utf-8")
        with r.scheduler._condition:
            detected = r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(binding).monitoring_failed, 3,
            )
        status = r.scheduler.status(binding)
        print(f"idle replacement={replacement}: producer_alive={producer.is_alive()}, "
              f"reader_alive={reader.is_alive()}, failed={status.monitoring_failed}, "
              f"recovering={status.monitoring_recovering}, debt={status.requires_full_sync}")
        assert detected
        assert producer.is_alive() and reader.is_alive()
        assert status.monitoring_recovering and status.requires_full_sync
        assert r.scheduler._owners[binding.space_id].coverage_revision > 0
        assert dispatcher.is_alive() and observer.is_alive()
        r.scheduler.wait(10)
        assert r.scheduler.status(binding).monitoring_failed
        assert r.scheduler.status(binding).requires_full_sync
        with pytest.raises((OSError, SpaceError)):
            r.scheduler._validate_owner(r.scheduler._entry(binding))
        r.watcher.stop()
        rebuilt = SharedFilesystemWatcher(r.registry, r.bindings.values(),
                                         scheduler=r.scheduler, debounce_seconds=10)
        with pytest.raises((OSError, SpaceError, IndexBindingUnavailableError)):
            rebuilt.start()
        assert r.scheduler.status(binding).monitoring_failed


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux IN_CREATE race")
def test_original_child_disappears_before_registration(tmp_path, monkeypatch):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    entered, release, attempted = (threading.Event() for _ in range(3))
    parse, register = _BoundedInotify._parse_event_buffer, _BoundedInotify._add_watch

    def paused(reader, data):
        for record in parse(data):
            if record[3] == b"ephemeral" and record[1] & InotifyConstants.IN_CREATE:
                entered.set()
                assert release.wait(10)
            yield record

    def attempted_registration(reader, path, mask):
        try:
            return register(reader, path, mask)
        finally:
            if path.endswith(b"/ephemeral"):
                attempted.set()

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", paused)
    monkeypatch.setattr(_BoundedInotify, "_add_watch", attempted_registration)
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        producer = next(iter(r.watcher._observer.emitters))
        reader = producer._inotify
        child = r.owners["default"].vault_root / "ephemeral"
        child.mkdir()
        try:
            assert entered.wait(5)
            child.rmdir()
        finally:
            release.set()
        assert attempted.wait(5)
        with r.scheduler._condition:
            r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).monitoring_failed, 0.2,
            )
        status = r.scheduler.status(r.bindings["default"])
        print(f"vanished child: reader_alive={reader.is_alive()}, emitter_alive={producer.is_alive()}, "
              f"error={status.monitoring_error_type}")
        assert not status.monitoring_failed
        native_write_after_dispatch(r, "default", "survives.md", monkeypatch)
        assert reader.is_alive() and producer.is_alive()
        assert os.fsencode(child) not in reader._inotify._wd_for_path
        assert os.fsencode(child) not in reader._inotify._path_for_wd.values()


def test_idle_checks_all_eight_roots_on_existing_dispatcher_and_isolate_failure(tmp_path, monkeypatch):
    from app.services import shared_filesystem_watcher as module

    monkeypatch.setattr(module, "ROOT_VALIDATION_INTERVAL_SECONDS", 0.05)
    with rig(tmp_path, 8) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        thread = r.watcher._thread
        checked = {sid: 0 for sid in r.bindings}
        twice, invalid = threading.Event(), threading.Event()
        validate = r.watcher._validate_binding

        def tracked(binding):
            if threading.current_thread() is thread:
                checked[binding.space_id.value] += 1
                if all(count >= 2 for count in checked.values()):
                    twice.set()
                if invalid.is_set() and binding is r.bindings["default"]:
                    raise SpaceError("unavailable_space")
            return validate(binding)

        monkeypatch.setattr(r.watcher, "_validate_binding", tracked)
        assert twice.wait(5)
        assert r.scheduler.pending_count == 0
        assert r.watcher._observer.event_queue.qsize() == 0
        invalid.set()
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(
                lambda: r.scheduler.status(r.bindings["default"]).monitoring_failed, 5,
            )
        assert r.watcher._thread is thread and thread.is_alive()
        assert all(not r.scheduler.status(binding).requires_full_sync
                   for sid, binding in r.bindings.items() if sid != "default")
        # Even a restored path cannot reopen targeted admission in this generation.
        handler = next(h for h in r.watcher._handlers if h._binding is r.bindings["default"])
        from watchdog.events import FileModifiedEvent

        handler.dispatch(FileModifiedEvent(str(r.owners["default"].vault_root / "rejected.md")))
        assert r.scheduler.pending_count == 0
        r.scheduler.start()
        r.scheduler.wait(5)
        status = r.scheduler.status(r.bindings["default"])
        assert status.error_type is None  # Full indexing succeeded.
        assert status.monitoring_failed and status.monitoring_recovering and status.requires_full_sync
        assert r.owners["default"].begins == [None]


@pytest.mark.parametrize("native_timeout", [False, True])
def test_periodic_validation_racing_stop_cannot_publish_late_state(tmp_path, monkeypatch, native_timeout):
    from app.services import shared_filesystem_watcher as module

    if native_timeout and sys.platform != "win32":
        pytest.skip("actual Windows native cleanup timeout")
    monkeypatch.setattr(module, "ROOT_VALIDATION_INTERVAL_SECONDS", 0.05)
    read_entered, release_read, arm_read = threading.Event(), threading.Event(), threading.Event()
    if native_timeout:
        from app.services.watcher_ingress_windows import NativeDirectoryRead

        read = NativeDirectoryRead.read_directory_changes

        def paused_read(reader, handle, path, *, recursive):
            if arm_read.is_set() and not read_entered.is_set():
                read_entered.set()
                assert release_read.wait(20)
            return read(reader, handle, path, recursive=recursive)

        monkeypatch.setattr(NativeDirectoryRead, "read_directory_changes", paused_read)
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        if native_timeout:
            arm_read.set()
            (r.owners["default"].vault_root / "next-read.txt").write_text("next native read", encoding="utf-8")
        entered, release = threading.Event(), threading.Event()
        validate = r.watcher._validate_binding
        dispatcher = r.watcher._thread

        def blocked(binding):
            if threading.current_thread() is dispatcher:
                entered.set()
                assert release.wait(10)
                raise SpaceError("unavailable_space")
            return validate(binding)

        monkeypatch.setattr(r.watcher, "_validate_binding", blocked)
        stopping = None
        try:
            assert entered.wait(5)
            if native_timeout:
                assert read_entered.wait(5)
            stopping = launch(r.watcher.stop)
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: r.watcher._stopping, 5)
            release.set()
            if native_timeout:
                with pytest.raises(RuntimeError, match="Native filesystem read did not stop"):
                    stopping.result(10)
                assert r.scheduler._watcher_token is r.watcher._token
            else:
                stopping.result(10)
            status = r.scheduler.status(r.bindings["default"])
            assert status.monitoring_recovering and not status.monitoring_failed
        finally:
            release.set()
            release_read.set()
            if stopping is not None and not native_timeout:
                stopping.result(10)
            r.watcher.stop()


def test_retired_integrity_result_cannot_change_reconstructed_generation(tmp_path, monkeypatch):
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        old = r.watcher
        entered, release = threading.Event(), threading.Event()
        validate = old._validate_binding

        def delayed(binding):
            if threading.current_thread() is not old._thread:
                entered.set()
                assert release.wait(10)
                raise SpaceError("unavailable_space")
            return validate(binding)

        monkeypatch.setattr(old, "_validate_binding", delayed)
        checking = launch(old._check_roots)
        try:
            assert entered.wait(5)
            old.stop()
            use_production_observer(r)
            r.watcher.start()
            entry = r.scheduler._owners[r.bindings["default"].space_id]
            revision = entry.monitoring_revision
        finally:
            release.set()
        checking.result(5)
        assert entry.monitoring_revision == revision and entry.monitoring_error_type is None
        r.scheduler.start()
        r.scheduler.wait(5)
        assert not r.scheduler.status(entry.binding).requires_full_sync


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual recursive Linux registration")
@pytest.mark.parametrize("failure", ["root-loss", "root-replaced", "symlink", "file", "ENOSPC", "EACCES"])
def test_child_registration_does_not_mask_genuine_failure(tmp_path, monkeypatch, failure):
    from watchdog.observers.inotify_c import Inotify, InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    entered, release = threading.Event(), threading.Event()
    parse, register = _BoundedInotify._parse_event_buffer, Inotify._add_watch

    def paused(reader, data):
        for record in parse(data):
            if record[3] == b"racy" and record[1] & InotifyConstants.IN_CREATE:
                entered.set()
                assert release.wait(10)
            yield record

    def failed(reader, path, mask):
        if path.endswith(b"/racy"):
            if failure == "ENOSPC":
                raise OSError(errno.ENOSPC, "watch exhaustion")
            if failure == "EACCES":
                return -1  # Installed watchdog's suppressed EACCES result.
        return register(reader, path, mask)

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", paused)
    monkeypatch.setattr(Inotify, "_add_watch", failed)
    with rig(tmp_path, 2) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        binding, root = r.bindings["default"], r.owners["default"].vault_root
        producer = next(e for e in r.watcher._observer.emitters if e.watch.path == str(root))
        reader = producer._inotify
        child = root / "racy"
        child.mkdir()
        try:
            assert entered.wait(5)
            if failure in ("root-loss", "root-replaced"):
                root.rename(tmp_path / "displaced")
                if failure == "root-replaced":
                    root.mkdir()
            elif failure in ("symlink", "file"):
                child.rmdir()
                if failure == "symlink":
                    outside = tmp_path / "outside"
                    outside.mkdir()
                    os.symlink(outside, child, target_is_directory=True)
                else:
                    child.write_text("unexpected object", encoding="utf-8")
        finally:
            release.set()
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: r.scheduler.status(binding).monitoring_failed, 5)
        reader.join(5)
        producer.join(5)
        assert not reader.is_alive() and not producer.is_alive()
        assert reader._queue._closed and reader._inotify._resources_closed
        assert r.scheduler.status(binding).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        assert all(not path.startswith(os.fsencode(tmp_path / "outside"))
                   for path in reader._inotify._wd_for_path)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux partial registration cleanup")
def test_child_removed_after_native_registration_retires_partial_watch(tmp_path, monkeypatch):
    from watchdog.observers.inotify_c import Inotify

    native = Inotify._add_watch
    retired = threading.Event()

    def removed(reader, path, mask):
        wd = native(reader, path, mask)
        if path.endswith(b"/partial"):
            os.rmdir(path)
            retired.set()
        return wd

    monkeypatch.setattr(Inotify, "_add_watch", removed)
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        producer = next(iter(r.watcher._observer.emitters))
        reader = producer._inotify
        child = r.owners["default"].vault_root / "partial"
        child.mkdir()
        assert retired.wait(5)
        native_write_after_dispatch(r, "default", "after.md", monkeypatch)
        assert reader.is_alive() and producer.is_alive()
        assert os.fsencode(child) not in reader._inotify._wd_for_path
        assert os.fsencode(child) not in reader._inotify._path_for_wd.values()
        assert not r.scheduler.status(r.bindings["default"]).monitoring_failed


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux concurrent directory churn")
@pytest.mark.parametrize("stop_during_registration", [False, True])
def test_concurrent_child_churn_and_shutdown_remain_safe(tmp_path, monkeypatch, stop_during_registration):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    parse = _BoundedInotify._parse_event_buffer
    entered, release = threading.Event(), threading.Event()

    def paused(reader, data):
        for record in parse(data):
            if record[3].startswith(b"churn-") and record[1] & InotifyConstants.IN_CREATE:
                entered.set()
                assert release.wait(10)
            yield record

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", paused)
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        observer = r.watcher._observer
        producer = next(iter(observer.emitters))
        reader = producer._inotify
        root = r.owners["default"].vault_root
        (root / "churn-0").mkdir()
        stopping = None
        try:
            assert entered.wait(5)
            # A finite concurrent batch arrives while the native parser is paused.
            operations = [launch(lambda i=i: (root / f"churn-{i}").mkdir()) for i in range(1, 17)]
            for operation in operations:
                operation.result(5)
            for i in range(17):
                (root / f"churn-{i}").rmdir()
            if stop_during_registration:
                stopping = launch(r.watcher.stop)
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(lambda: r.watcher._stopping, 5)
        finally:
            release.set()
        if stopping is not None:
            stopping.result(10)
            assert not observer.is_alive() and not producer.is_alive() and not reader.is_alive()
            assert reader._inotify._resources_closed
        else:
            native_write_after_dispatch(r, "default", "surviving.md", monkeypatch)
            assert reader.is_alive() and producer.is_alive()
            assert not any(b"/churn-" in path for path in reader._inotify._wd_for_path)
            r.scheduler.start()
            r.scheduler.wait(5)
            assert r.owners["default"].begins == [None]  # Coalesced recovery.
        assert not r.scheduler.status(r.bindings["default"]).monitoring_failed
