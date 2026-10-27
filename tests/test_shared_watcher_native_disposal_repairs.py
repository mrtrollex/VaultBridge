"""Lost kernel move records and truthful native handle disposal."""

import ctypes
import errno
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_departure_repairs import idle_recovered
from tests.test_shared_watcher_lifecycle_repairs import native_write_after_dispatch
from tests.test_shared_watcher_ownership_repairs import replacement
from tests.test_shared_watcher_repairs import use_production_observer


def kernel_watches(native):
    """Kernel-backed registrations, independent of Python bookkeeping."""
    lines = Path(f"/proc/self/fdinfo/{native._inotify_fd}").read_text().splitlines()
    return {int(line.split()[1].split(":")[1], 16) for line in lines if line.startswith("inotify wd:")}


@contextmanager
def unread_stream(native, root, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    check = native._check_inotify_fd

    def blocked():
        if not entered.is_set():
            entered.set()
            assert release.wait(30)
        return check()

    with monkeypatch.context() as patch:
        patch.setattr(native, "_check_inotify_fd", blocked)
        (root / "hold.txt").write_text("wake previous poll", encoding="utf-8")
        try:
            assert entered.wait(5)
            yield
        finally:
            release.set()


def fill_native_queue(root):
    limit = int(Path("/proc/sys/fs/inotify/max_queued_events").read_text())
    churn = root / "churn.txt"
    for _ in range(limit // 3 + 100):
        with churn.open("wb"):
            pass  # Exactly create, close-write, unlink; native reads remain stopped.
        churn.unlink()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux inotify queue overflow")
def test_original_lost_departure_overflow_retires_kernel_watches(tmp_path, monkeypatch, shared):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    pause, entered, release, overflow = (threading.Event() for _ in range(4))
    parse = _BoundedInotify._parse_event_buffer

    def observed(native, data):
        for record in parse(data):
            if record[1] & InotifyConstants.IN_Q_OVERFLOW:
                overflow.set()  # Actual kernel overflow; no synthetic replacement.
            yield record

    monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", observed)
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        root = owner.vault_root
        folder = root / "folder"
        (folder / "deep").mkdir(parents=True)
        (folder / "deep" / "gone.md").write_text("before departure", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        native = next(e for e in r.watcher._producers if e.watch.path == str(root))._inotify._inotify
        before = kernel_watches(native)
        displaced = {wd for wd, path in native._path_for_wd.items() if path.startswith(os.fsencode(folder))}
        assert len(before) == 3 and len(displaced) == 2
        check = native._check_inotify_fd

        def held():
            if pause.is_set() and not entered.is_set():
                entered.set()
                assert release.wait(30)
            return check()

        monkeypatch.setattr(native, "_check_inotify_fd", held)
        handler = next(h for h in r.watcher._handlers if h._binding is binding)
        dispatch, external = handler.dispatch, []

        def delivered(event):
            dispatch(event)
            if event.src_path.endswith("external.md"):
                external.append(event.src_path)

        monkeypatch.setattr(handler, "dispatch", delivered)
        pause.set()
        (root / "hold.txt").write_text("pause before native read", encoding="utf-8")
        try:
            assert entered.wait(5)
            fill_native_queue(root)
            outside = tmp_path / "outside"
            folder.rename(outside)  # Departure record is lost behind the actual full kernel queue.
        finally:
            release.set()
        assert overflow.wait(10)  # Submit the ordered marker only AFTER the full queue has drained.
        reader = next(e for e in r.watcher._producers if e.watch.path == str(root))._inotify
        print(f"overflow drain: reader={reader.is_alive()}, failure={reader.failure_type}, "
              f"status={r.scheduler.status(binding)}, ingress={r.watcher._observer.event_queue.qsize()}")
        assert idle_recovered(r, binding)
        launch(r.watcher._observer.event_queue.join).result(10)
        native_write_after_dispatch(r, "default", "drained.txt", monkeypatch)
        assert overflow.is_set()
        assert idle_recovered(r, binding)
        after = kernel_watches(native)
        (outside / "deep" / "external.md").write_text("outside authorized root", encoding="utf-8")
        native_write_after_dispatch(r, "default", "external-drained.txt", monkeypatch)
        r.advance(10)
        r.delivered()
        r.scheduler.wait(15)
        paths = {c.path for c in owner.repository.load_chunks()}
        stale = any(path.startswith(os.fsencode(folder)) for path in native._path_for_wd.values())
        print(f"lost departure: kernel_before={len(before)}, kernel_after={len(after)}, "
              f"old_watches={displaced & after}, stale_maps={stale}, external={external}, SQLite={paths}")
        assert not external and not paths
        assert not displaced & after and len(after) == 1 and not stale
        assert set(native._wd_for_path) == {os.fsencode(root)}
        assert not native._coverage_pending
        assert not r.scheduler.status(binding).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual queued native records and FIFO fence")
def test_old_buffered_records_and_retirement_markers_cannot_certify_rebuilt_coverage(tmp_path, monkeypatch, shared):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    parse = _BoundedInotify._parse_event_buffer
    entered, release, injected = threading.Event(), threading.Event(), threading.Event()
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        root, binding = r.owners["default"].vault_root, r.bindings["default"]
        (root / "folder" / "deep").mkdir(parents=True)
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        native = next(e for e in r.watcher._producers if e.watch.path == str(root))._inotify._inotify
        old, marker = dict(native._path_for_wd), native._watch_descriptor_high_water
        child = native._wd_for_path[os.fsencode(root / "folder" / "deep")]
        handler = next(h for h in r.watcher._handlers if h._binding is binding)
        dispatch, external = handler.dispatch, []

        def delivered(event):
            dispatch(event)
            if event.src_path.endswith("external.md"):
                external.append(event.src_path)

        def records(current, data):
            for record in parse(data):
                if current is native and record[3] == b"overflow.txt" and not injected.is_set():
                    injected.set()
                    yield child, InotifyConstants.IN_CREATE, 0, b"external.md"
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                    yield child, InotifyConstants.IN_CREATE, 0, b"external.md"
                    yield marker, InotifyConstants.IN_IGNORED, 0, b""
                elif (current is native and injected.is_set() and record[0] == native._fence_wd
                      and record[1] & InotifyConstants.IN_IGNORED and not entered.is_set()):
                    entered.set()
                    assert release.wait(15)
                    yield record
                else:
                    yield record

        monkeypatch.setattr(handler, "dispatch", delivered)
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", records)
        (root / "overflow.txt").write_text("trigger", encoding="utf-8")
        try:
            assert entered.wait(5)
            r.scheduler.request_full_sync(binding)
            r.scheduler.wait(15)
            assert native._coverage_pending and r.scheduler.status(binding).requires_full_sync
            assert r.scheduler.status(binding).monitoring_recovering
            assert not external and not set(old) & kernel_watches(native)
        finally:
            release.set()
        assert idle_recovered(r, binding)
        native_write_after_dispatch(r, "default", "delivered.txt", monkeypatch)
        assert not external and not r.scheduler.status(binding).monitoring_failed
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows directory handle closure")
@pytest.mark.parametrize("mode", ["exception", "false"])
def test_original_directory_close_failure_retains_handle_and_generation(tmp_path, monkeypatch, mode):
    from ctypes.wintypes import BOOL, DWORD, HANDLE

    from watchdog.observers import winapi

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    information = kernel.GetHandleInformation
    information.argtypes = HANDLE, ctypes.POINTER(DWORD)
    information.restype = BOOL
    close, errors = winapi.CloseHandle, []
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()

        watcher = r.watcher
        producer = watcher._producers[0]
        handle = producer._whandle
        hook = threading.excepthook

        def expected(args):
            if args.thread is producer and isinstance(args.exc_value, OSError):
                errors.append(args.exc_value.winerror)
            else:
                hook(args)

        def denied(value):
            if value == handle:
                if mode == "false":
                    ctypes.windll.kernel32.SetLastError(5)
                    return 0
                raise ctypes.WinError(5)
            return close(value)

        monkeypatch.setattr(threading, "excepthook", expected)
        monkeypatch.setattr(winapi, "CloseHandle", denied)
        stop_error = None
        try:
            watcher.stop()
        except OSError as error:
            stop_error = error.winerror
        flags = DWORD()
        opened = bool(information(handle, ctypes.byref(flags)))
        held = r.scheduler._watcher_token is watcher._token
        print(f"CloseHandle denied: error={stop_error}, open={opened}, retained={producer._whandle == handle}, "
              f"generation_held={held}")
        try:
            assert stop_error == 5 and opened and held and producer._whandle == handle
            r.scheduler.start()
            r.scheduler.request_full_sync(r.bindings["default"])
            r.scheduler.wait(15)
            assert r.scheduler.status(r.bindings["default"]).requires_full_sync
            with pytest.raises(RuntimeError, match="admission is unavailable"):
                replacement(r).start()
        finally:
            monkeypatch.setattr(winapi, "CloseHandle", close)
            watcher.stop()
            # The pre-repair reproduction must not leak its confirmed live handle.
            if information(handle, ctypes.byref(flags)):
                close(handle)
        assert not information(handle, ctypes.byref(flags))
        assert ctypes.get_last_error() == 6
        assert not producer.is_alive() and producer._read_done.is_set()
        assert producer._native_read.event is None and not producer._native_read.pending
        assert producer._whandle is None and r.scheduler._watcher_token is None
        watcher.stop()
        r.watcher = replacement(r)
        r.watcher.start()
        r.complete_native_startup()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual repeated native overflow/retirement")
@pytest.mark.parametrize("move", ["outside", "replacement", "internal", "cross"])
def test_repeated_lost_moves_rebuild_only_current_kernel_registrations(tmp_path, monkeypatch, shared, move):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        root, peer = r.owners["default"].vault_root, r.owners["space-1"].vault_root
        binding = r.bindings["default"]
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        native = next(e for e in r.watcher._producers if e.watch.path == str(root))._inotify._inotify
        original, overflow = _BoundedInotify._parse_event_buffer, threading.Event()

        def observed(current, data):
            for record in original(data):
                if current is native and record[1] & InotifyConstants.IN_Q_OVERFLOW:
                    overflow.set()
                yield record

        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", observed)
        counts, expected = [], set()
        for turn in range(3):
            folder = root / f"folder-{turn}"
            (folder / "deep").mkdir(parents=True)
            (folder / "deep" / "note.md").write_text("authorized before move", encoding="utf-8")
            native_write_after_dispatch(r, "default", f"registered-{turn}.txt", monkeypatch)
            assert idle_recovered(r, binding)
            old = kernel_watches(native)
            overflow.clear()
            target = (root / f"inside-{turn}" if move == "internal" else
                      peer / f"arrived-{turn}" if move == "cross" else tmp_path / f"outside-{turn}")
            with unread_stream(native, root, monkeypatch):
                fill_native_queue(root)
                folder.rename(target)
                if move == "replacement":
                    (folder / "deep").mkdir(parents=True)
                    (folder / "deep" / "replacement.md").write_text("new authorized inode", encoding="utf-8")
            assert overflow.wait(10)
            assert idle_recovered(r, binding)
            launch(r.watcher._observer.event_queue.join).result(10)
            native_write_after_dispatch(r, "default", f"recovered-{turn}.txt", monkeypatch)
            assert idle_recovered(r, binding)
            current = kernel_watches(native)
            assert not old & current  # Root and ALL prior wds were actually retired.
            assert len(current) == len(native._path_for_wd) == len(native._wd_for_path)
            assert set(native._path_for_wd) == current
            if move == "internal":
                expected.add(f"inside-{turn}/deep/note.md")
            elif move == "replacement":
                expected.add(f"folder-{turn}/deep/replacement.md")
            elif move == "cross":
                peer_binding = r.bindings["space-1"]
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(
                        lambda: not r.scheduler.status(peer_binding).requires_full_sync
                        and any(e.watch.path == str(peer) and os.fsencode(target / "deep")
                                in e._inotify._inotify._wd_for_path for e in r.watcher._producers), 10,
                    )
                assert {c.path for c in r.owners["space-1"].repository.load_chunks()} == {
                    f"arrived-{i}/deep/note.md" for i in range(turn + 1)
                }
            counts.append(len(current))
            assert len(current) == (1 + 2 * (turn + 1) if move in ("internal", "replacement") else 1)
            assert {c.path for c in r.owners["default"].repository.load_chunks()} == expected
            assert native._retired_watch_paths is None and len(native._moved_from_events) == 0
            assert not r.scheduler.status(binding).monitoring_failed
            assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed
        print(f"three real overflow/departure cycles ({move}): kernel watches={counts}, SQLite={sorted(expected)}")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual native descriptor/fence identity")
@pytest.mark.parametrize("fault", ["watch-reuse", "fence-reuse", "retirement", "symlink", "root"])
def test_rebuild_identity_and_retirement_failures_cannot_publish_health(tmp_path, monkeypatch, shared, fault):
    from watchdog.observers import inotify_c
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services import watcher_ingress_linux as module

    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        root, binding = r.owners["default"].vault_root, r.bindings["default"]
        (root / "ordinary" / "deep").mkdir(parents=True)
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        reader = next(e for e in r.watcher._producers if e.watch.path == str(root))._inotify
        native = reader._inotify
        old = native._wd_for_path[os.fsencode(root / "ordinary")]
        add, remove = module.inotify_add_watch, module.inotify_rm_watch
        parse = module._BoundedInotify._parse_event_buffer
        armed, injected = threading.Event(), threading.Event()

        def records(current, data):
            for record in parse(data):
                if current is native and record[3] == b"overflow.txt" and not injected.is_set():
                    injected.set()
                    if fault == "symlink":
                        (root / "ordinary").rename(tmp_path / "outside")
                        (root / "ordinary").symlink_to(tmp_path / "outside", target_is_directory=True)
                    elif fault == "root":
                        root.rename(tmp_path / "displaced")
                        root.mkdir()
                    armed.set()
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    yield record

        def registration(fd, path, mask):
            result = add(fd, path, mask)
            if armed.is_set() and fd == native._inotify_fd and (
                (fault == "watch-reuse" and path.endswith(b"/ordinary"))
                or (fault == "fence-reuse" and path.startswith(b"/proc/self/fd/"))
            ):
                return old  # Kernel call still real; simulate wrap/reuse at its exact result boundary.
            return result

        def retirement(fd, wd):
            if armed.is_set() and fd == native._inotify_fd and fault == "retirement":
                ctypes.set_errno(errno.EIO)
                return -1
            return remove(fd, wd)

        monkeypatch.setattr(module._BoundedInotify, "_parse_event_buffer", records)
        monkeypatch.setattr(module, "inotify_add_watch", registration)
        monkeypatch.setattr(inotify_c, "inotify_add_watch", registration)
        monkeypatch.setattr(module, "inotify_rm_watch", retirement)
        (root / "overflow.txt").write_text("trigger", encoding="utf-8")
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: r.scheduler.status(binding).monitoring_failed, 10)
        reader.join(5)
        assert injected.is_set() and not reader.is_alive() and native._resources_closed
        armed.clear()
        r.scheduler.request_full_sync(binding)
        r.scheduler.wait(15)
        assert r.scheduler.status(binding).monitoring_failed and r.scheduler.status(binding).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.skipif(sys.platform != "win32", reason="actual reused Windows handle and concurrent disposal")
def test_concurrent_windows_stop_closes_once_and_preserves_reused_numeric_handle(tmp_path, monkeypatch):
    from ctypes.wintypes import DWORD

    from watchdog.observers import winapi

    close = winapi.CloseHandle
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        producer = r.watcher._producers[0]
        handle, closes = producer._whandle, []

        def counted(value):
            if value == handle:
                closes.append(value)
            return close(value)

        monkeypatch.setattr(winapi, "CloseHandle", counted)
        one, two = launch(r.watcher.stop), launch(r.watcher.stop)
        one.result(10)
        two.result(10)
        assert len(closes) == 1 and producer._whandle is None
        events = []
        try:
            # A full suite leaves thousands of vacant handle-table slots. Keep
            # the budget finite while actually observing reuse, not assuming
            # that a freshly closed value must be among the first 256 choices.
            for _ in range(16_384):
                events.append(winapi.CreateEvent(None, True, False, None))
                if events[-1] == handle:
                    break
            assert handle in events  # Observe actual kernel numeric HANDLE reuse.
            for _ in range(10):
                r.watcher.stop()
                producer.stop()
            flags = DWORD()
            assert ctypes.windll.kernel32.GetHandleInformation(handle, ctypes.byref(flags))
            assert len(closes) == 1 and r.scheduler._watcher_token is None
            print(f"Windows concurrent stop: successful_directory_closes={len(closes)}, actual_value_reused=True")
        finally:
            monkeypatch.setattr(winapi, "CloseHandle", close)
            for event in events:
                assert close(event)


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows failed-start native resource retention")
def test_windows_failed_emitter_start_retains_directory_handle_for_retry(tmp_path, monkeypatch):
    from watchdog.observers import winapi
    from watchdog.observers.read_directory_changes import WindowsApiEmitter

    from app.services import watcher_ingress_windows as module

    get, close, start = module.get_directory_handle, winapi.CloseHandle, threading.Thread.start
    handles, armed = [], threading.Event()
    armed.set()

    def opened(path):
        result = get(path)
        handles.append(result)
        return result

    def denied(handle):
        if armed.is_set() and handles and handle == handles[0]:
            raise ctypes.WinError(5)
        return close(handle)

    def failed(thread):
        if isinstance(thread, WindowsApiEmitter):
            raise RuntimeError("injected Thread.start failure after native acquisition")
        return start(thread)

    with rig(tmp_path, 1) as r:
        monkeypatch.setattr(module, "get_directory_handle", opened)
        monkeypatch.setattr(winapi, "CloseHandle", denied)
        monkeypatch.setattr(threading.Thread, "start", failed)
        use_production_observer(r)
        with pytest.raises(OSError):
            r.watcher.start()
        assert r.scheduler._watcher_token is r.watcher._token
        assert not r.watcher._observer.emitters and r.watcher._producers
        producer = r.watcher._producers[0]
        assert producer.ident is None and producer._whandle == handles[0]
        assert producer._native_read.event is None and not producer._native_read.pending
        with pytest.raises(RuntimeError, match="admission is unavailable"):
            replacement(r).start()
        armed.clear()
        r.watcher.stop()
        assert producer._whandle is None and r.scheduler._watcher_token is None
        monkeypatch.setattr(threading.Thread, "start", start)
        r.watcher = replacement(r)
        r.watcher.start()
        r.complete_native_startup()


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows uncertain handle ownership")
def test_invalid_handle_result_never_blindly_retries_its_numeric_slot(tmp_path, monkeypatch):
    from watchdog.observers import winapi

    close = winapi.CloseHandle
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        producer = r.watcher._producers[0]
        handle, calls = producer._whandle, []
        hook = threading.excepthook

        def expected(args):
            if args.thread is not producer:
                hook(args)

        def invalid(value):
            if value == handle:
                calls.append(value)
                raise ctypes.WinError(6)
            return close(value)

        monkeypatch.setattr(threading, "excepthook", expected)
        monkeypatch.setattr(winapi, "CloseHandle", invalid)
        try:
            with pytest.raises((OSError, RuntimeError)):
                r.watcher.stop()
            with pytest.raises(RuntimeError, match="ownership is uncertain"):
                r.watcher.stop()
            assert len(calls) == 1 and producer._directory_close_uncertain
            assert producer._whandle == handle and r.scheduler._watcher_token is r.watcher._token
            with pytest.raises(RuntimeError, match="admission is unavailable"):
                replacement(r).start()
        finally:
            # Test-only resolution: the injected call NEVER reached native close.
            # Production cannot assume this proof after a real ERROR_INVALID_HANDLE.
            monkeypatch.setattr(winapi, "CloseHandle", close)
            producer._directory_close_uncertain = False
            r.watcher.stop()
