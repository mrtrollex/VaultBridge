"""Native Windows arming and Linux idle recursive-registration convergence."""

import errno
import importlib
import sys
import threading
from pathlib import Path

import pytest

from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_repairs import use_production_observer


def windows_native():
    try:
        return importlib.import_module("app.services.watcher_ingress_windows")
    except ModuleNotFoundError:
        return importlib.import_module("watchdog.observers.winapi")


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows first native request")
def test_original_first_native_request_cannot_be_reconciled_early(tmp_path, monkeypatch, shared):
    resource, _ = shared
    with rig(tmp_path, 1, resource=resource, real=True) as r:
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.watcher.stop()
        native = windows_native()
        read = native.ReadDirectoryChangesW
        entered, release = threading.Event(), threading.Event()

        def paused(*args):
            entered.set()
            assert release.wait(15)
            return read(*args)

        monkeypatch.setattr(native, "ReadDirectoryChangesW", paused)
        use_production_observer(r)
        starting = launch(r.watcher.start)
        try:
            assert entered.wait(5)
            r.scheduler.wait(10)
            status = r.scheduler.status(r.bindings["default"])
            early = status.monitoring_recovering and status.requires_full_sync
            (r.owners["default"].vault_root / "missed.md").write_text("during unarmed gap", encoding="utf-8")
            print(f"before native submit: recovering={status.monitoring_recovering}, "
                  f"debt={status.requires_full_sync}, start_finished={starting.done()}")
        finally:
            release.set()
        starting.result(10)
        r.scheduler.wait(15)
        paths = {chunk.path for chunk in r.owners["default"].repository.load_chunks()}
        print(f"after native arming: indexed={sorted(paths)}, "
              f"debt={r.scheduler.status(r.bindings['default']).requires_full_sync}")
        assert early
        assert "missed.md" in paths
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual 9000-directory inotify queue")
def test_original_idle_9000_directory_registration_converges(tmp_path, monkeypatch, shared):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    overflows = 0
    full_starts = 0
    observed = threading.Condition()
    parse = _BoundedInotify._parse_event_buffer
    inject, injected, restored = threading.Event(), threading.Event(), threading.Event()

    def counted(reader, data):
        nonlocal overflows
        for record in parse(data):
            if record[1] & InotifyConstants.IN_Q_OVERFLOW:
                with observed:
                    overflows += 1
                    observed.notify_all()
            if inject.is_set() and not injected.is_set() and record[3] == b"restore":
                injected.set()
                yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                continue
            yield record

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", counted)
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        begin = r.owners["default"].begin_sync

        def counted_begin(*, relative_paths, cancel_event):
            nonlocal full_starts
            if relative_paths is None:
                full_starts += 1
            return begin(relative_paths=relative_paths, cancel_event=cancel_event)

        monkeypatch.setattr(r.owners["default"], "begin_sync", counted_begin)
        root = r.owners["default"].vault_root
        for i in range(9000):
            (root / f"d-{i}").mkdir()
        print(f"max_queued_events={Path('/proc/sys/fs/inotify/max_queued_events').read_text().strip()}")
        use_production_observer(r)
        r.watcher.start()
        r.scheduler.start()
        with observed:
            observed.wait_for(lambda: overflows >= 3, 8)
        r.scheduler.wait(30)
        print(f"idle native overflows={overflows}, full_starts={full_starts}, "
              f"recovery={r.scheduler.status(r.bindings['default']).requires_full_sync}")
        assert overflows == 0
        assert full_starts == 1  # Matching first-attachment reconciliation is now mandatory.
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync
        native = next(iter(r.watcher._observer.emitters))._inotify._inotify
        assert not native.event_mask & (InotifyConstants.IN_OPEN | InotifyConstants.IN_CLOSE_NOWRITE)
        callback = native._restored

        def covered(ticket):
            callback(ticket)
            restored.set()

        native._restored = covered
        startup_starts = full_starts
        inject.set()
        (root / "restore").mkdir()  # Lose its actual native directory-create record.
        assert restored.wait(15) and injected.is_set()
        r.scheduler.wait(30)
        starts = full_starts
        with observed:
            observed.wait_for(lambda: overflows > 0, 2)
        r.scheduler.wait(30)
        assert overflows == 0 and full_starts == starts
        assert 1 <= starts - startup_starts <= 2  # Preserve the bounded recovery-turn allowance.
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert bytes(root / "restore") in native._wd_for_path
        print(f"after forced restoration: native_overflows={overflows}, full_starts={starts}, recovered=True")


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows native arming lifecycle")
@pytest.mark.parametrize("action", ["cancel", "timeout", "process", "stop", "root", "generation", "failure"])
def test_windows_arming_fences_invalid_lifecycle(tmp_path, monkeypatch, action):
    from app.services import watcher_ingress
    from app.services.knowledge_spaces import SpaceError

    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        r.watcher.stop()
        native = windows_native()
        read = native.ReadDirectoryChangesW
        entered, release = threading.Event(), threading.Event()
        if action == "timeout":
            monkeypatch.setattr(watcher_ingress, "NATIVE_ARM_TIMEOUT_SECONDS", 0.1)

        def paused(*args):
            entered.set()
            assert release.wait(15)
            if action == "failure":
                raise OSError(errno.EIO, "native submission failed")
            return read(*args)

        monkeypatch.setattr(native, "ReadDirectoryChangesW", paused)
        use_production_observer(r)
        old = r.watcher
        starting = launch(old.start)
        stopping = None
        try:
            assert entered.wait(5)
            observer = old._observer
            producers = tuple(observer.emitters)
            status = r.scheduler.status(r.bindings["default"])
            assert status.monitoring_recovering and status.requires_full_sync
            if action == "cancel":
                r.scheduler.cancel(r.bindings["default"])
            elif action == "process":
                r.scheduler.begin_shutdown()
            elif action == "stop":
                stopping = launch(old.stop)
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(lambda: old._stopping, 5)
            elif action == "root":
                root = r.owners["default"].vault_root
                root.rename(tmp_path / "displaced")
                root.mkdir()
            elif action == "timeout":
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(
                        lambda: r.scheduler.status(r.bindings["default"]).monitoring_error_type
                        == "NativeArmingTimeout", 5,
                    )
            elif action == "generation":
                replacement = SharedFilesystemWatcher(r.registry, r.bindings.values(),
                                                      scheduler=r.scheduler, debounce_seconds=10)
                with pytest.raises(RuntimeError, match="admission is unavailable"):
                    replacement.start()
                assert r.scheduler._watcher_token is old._token
        finally:
            release.set()
        if action == "generation":
            starting.result(10)
        else:
            with pytest.raises((RuntimeError, TimeoutError, OSError, SpaceError)):
                starting.result(10)
            assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        if stopping is not None:
            stopping.result(10)
        old.stop()
        assert not observer.is_alive()
        assert all(not e.is_alive() and e._whandle is None and e._native_read.event is None for e in producers)
        assert r.scheduler._watcher_token is None
        if action == "generation":
            monkeypatch.setattr(native, "ReadDirectoryChangesW", read)
            use_production_observer(r)
            r.watcher.start()
            old._ingress_failure(None, "LateOldFailure")
            r.scheduler.start()
            r.scheduler.wait(5)
            assert not r.scheduler.status(r.bindings["default"]).requires_full_sync
        if action == "process":
            assert r.scheduler._closed and not r.trace


def test_native_mutations_continue_to_update_real_e3(tmp_path, shared, monkeypatch):
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        root, owner = r.owners["default"].vault_root, r.owners["default"]
        marker, delivered = [None], threading.Event()
        seen_paths = set()
        handler = r.watcher._handlers[0]
        dispatch = handler.dispatch

        def observed(event):
            dispatch(event)
            if event.event_type in {"created", "modified", "deleted", "moved"}:
                for raw in (event.src_path, getattr(event, "dest_path", "")):
                    if raw and Path(raw).suffix.lower() == ".md":
                        with r.scheduler._condition:
                            seen_paths.add(Path(raw).relative_to(root).as_posix())
                            r.scheduler._condition.notify_all()
            if event.src_path == marker[0]:
                delivered.set()

        monkeypatch.setattr(handler, "dispatch", observed)

        def synced(paths):
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(
                    # A safe full may absorb reservations before inspection.
                    # Require actual native delivery, then assert E3/SQLite below.
                    lambda: set(paths) <= seen_paths, 5,
                )
            # A later native marker orders all earlier records before advancing
            # the fake debounce clock, including late Windows rename metadata.
            marker[0] = str(root / f"settled-{r.now}.txt")
            delivered.clear()
            Path(marker[0]).write_text("ordered marker", encoding="utf-8")
            assert delivered.wait(5)
            marker[0] = None
            r.advance(r.now + 10)
            r.delivered()
            r.scheduler.wait(15)
            seen_paths.clear()

        note = root / "created.md"
        note.write_text("first content", encoding="utf-8")
        synced(["created.md"])
        assert {chunk.path for chunk in owner.repository.load_chunks()} == {"created.md"}
        note.write_text("updated content", encoding="utf-8")
        synced(["created.md"])
        assert owner.repository.load_chunks()[0].content == "updated content"
        note.rename(root / "renamed.md")
        synced(["created.md", "renamed.md"])
        assert {chunk.path for chunk in owner.repository.load_chunks()} == {"renamed.md"}
        (root / "renamed.md").unlink()
        synced(["renamed.md"])
        assert not owner.repository.load_chunks()
        assert r.scheduler.pending_count == 0


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows observer death after arming")
def test_windows_observer_stopped_during_post_arm_validation_cannot_restore(tmp_path, monkeypatch):
    with rig(tmp_path, 2) as r:
        use_production_observer(r)
        r.watcher.start()
        r.watcher.stop()
        use_production_observer(r)
        validate = r.watcher._validate_binding
        stopped = threading.Event()

        def raced(binding):
            validate(binding)
            observer = r.watcher._observer
            if observer is not None and observer.is_alive() and not stopped.is_set():
                observer.stop()
                observer.join(5)
                stopped.set()

        monkeypatch.setattr(r.watcher, "_validate_binding", raced)
        with pytest.raises(RuntimeError, match="observer did not start|producer did not start"):
            r.watcher.start()
        assert stopped.is_set() and r.scheduler._watcher_token is None
        assert all(r.scheduler.status(binding).requires_full_sync for binding in r.bindings.values())
        assert all(r.scheduler.status(binding).monitoring_failed for binding in r.bindings.values())
        assert not r.watcher._handlers and r.watcher._thread is None
