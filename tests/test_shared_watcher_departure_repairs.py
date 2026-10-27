"""First native coverage, subtree departure, and resource disposition."""

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

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux native ownership")


def idle_recovered(r, binding):
    with r.scheduler._condition:
        return r.scheduler._condition.wait_for(lambda: not r.scheduler.status(binding).requires_full_sync, 5)


@pytest.mark.parametrize("order", ["prior-sync", "early-sync", "active-sync", "directory-create"])
def test_original_first_attachment_requires_post_registration_full(tmp_path, monkeypatch, shared, order):
    from app.services.watcher_ingress_linux import _BoundedInotify

    entered, release = threading.Event(), threading.Event()
    full_entered, release_full = threading.Event(), threading.Event()
    add = _BoundedInotify._add_watch
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        directory = owner.vault_root / "existing"
        directory.mkdir()
        note = directory / "gone.md"
        note.write_text("before native coverage", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        assert owner.repository.load_chunks()
        if order == "active-sync":
            from app.services.semantic_sync import SyncStepState

            step = owner.sync_step

            def completed(handle, cancel):
                result = step(handle, cancel)
                if handle.full and result.state is SyncStepState.COMPLETE and not full_entered.is_set():
                    full_entered.set()
                    assert release_full.wait(10)
                return result

            monkeypatch.setattr(owner, "sync_step", completed)
            r.scheduler.request_full_sync(binding)
            assert full_entered.wait(5)

        def paused(native, path, mask):
            if path == os.fsencode(directory):
                entered.set()
                assert release.wait(10)
            return add(native, path, mask)

        monkeypatch.setattr(_BoundedInotify, "_add_watch", paused)
        use_production_observer(r)
        starting = launch(r.watcher.start)
        try:
            assert entered.wait(5)
            before = r.scheduler.status(binding)
            if order == "early-sync":
                r.scheduler.request_full_sync(binding)
                r.scheduler.wait(15)
                assert r.scheduler.status(binding).monitoring_recovering
            elif order == "active-sync":
                release_full.set()
                r.scheduler.wait(15)
                assert r.scheduler.status(binding).monitoring_recovering
            elif order == "directory-create":
                (owner.vault_root / "during-registration").mkdir()
            note.unlink()
        finally:
            release_full.set()
            release.set()
        starting.result(10)
        native_write_after_dispatch(r, "default", "marker.txt", monkeypatch)
        r.scheduler.wait(15)
        recovered = idle_recovered(r, binding)
        paths = [chunk.path for chunk in owner.repository.load_chunks()]
        print(f"first attachment: initial_debt={before.requires_full_sync}, "
              f"initial_recovering={before.monitoring_recovering}, final_rows={paths}, recovered={recovered}")
        assert before.requires_full_sync and before.monitoring_recovering
        assert not paths and recovered


def test_first_startup_queued_subtree_cannot_certify_before_registration(tmp_path, monkeypatch, shared):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    walked, release_walk, held, release_subtree = (threading.Event() for _ in range(4))
    add, parse = _BoundedInotify._add_dir_watch, _BoundedInotify._parse_event_buffer
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)

        def after_walk(native, path, mask, *, recursive):
            result = add(native, path, mask, recursive=recursive)
            if path == native.path and not walked.is_set():
                walked.set()
                assert release_walk.wait(15)
            return result

        def delayed(native, data):
            for record in parse(data):
                if record[3] == b"late" and record[1] & InotifyConstants.IN_CREATE:
                    held.set()
                    assert release_subtree.wait(15)
                yield record

        monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", after_walk)
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", delayed)
        use_production_observer(r)
        starting = launch(r.watcher.start)
        try:
            assert walked.wait(5)
            directory = owner.vault_root / "late"
            directory.mkdir()
            note = directory / "gone.md"
            note.write_text("will disappear before coverage", encoding="utf-8")
            release_walk.set()
            assert held.wait(5)
            starting.result(10)
            r.scheduler.request_full_sync(binding)
            r.scheduler.wait(15)
            assert {c.path for c in owner.repository.load_chunks()} == {"late/gone.md"}
            assert r.scheduler.status(binding).monitoring_recovering
            assert r.scheduler.status(binding).requires_full_sync
            note.unlink()
        finally:
            release_walk.set()
            release_subtree.set()
        assert idle_recovered(r, binding)
        assert not owner.repository.load_chunks()


@pytest.mark.parametrize("terminal", [False, True])
def test_first_reconciliation_does_not_outlive_native_generation(tmp_path, monkeypatch, shared, terminal):
    entered, release = threading.Event(), threading.Event()
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        begin = owner.begin_sync

        def blocked(**kwargs):
            entered.set()
            assert release.wait(10)
            return begin(**kwargs)

        monkeypatch.setattr(owner, "begin_sync", blocked)
        use_production_observer(r)
        r.watcher.start()
        try:
            assert entered.wait(5)
            assert r.scheduler.status(binding).monitoring_recovering
            if terminal:
                r.scheduler.begin_shutdown()
            r.watcher.stop()
        finally:
            release.set()
        r.scheduler.wait(15)
        assert r.scheduler.status(binding).monitoring_recovering
        if terminal:
            assert r.scheduler._closed and not r.scheduler.request_full_sync(binding)
        else:
            use_production_observer(r)
            r.watcher.start()
            assert idle_recovered(r, binding)


def test_repeated_verified_readiness_does_not_restart_initial_full(tmp_path, monkeypatch, shared):
    entered, release = threading.Event(), threading.Event()
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        begin, reconstructed = owner.begin_sync, r.scheduler._watcher_reconstructed
        snapshots, begins = [], [0]

        def capture(token, owner_binding, snapshot):
            snapshots.append((token, owner_binding, snapshot))
            return reconstructed(token, owner_binding, snapshot)

        def blocked(**kwargs):
            begins[0] += 1
            entered.set()
            assert release.wait(10)
            return begin(**kwargs)

        monkeypatch.setattr(r.scheduler, "_watcher_reconstructed", capture)
        monkeypatch.setattr(owner, "begin_sync", blocked)
        use_production_observer(r)
        r.watcher.start()
        try:
            assert entered.wait(5) and len(snapshots) == 1
            for _ in range(1000):
                reconstructed(*snapshots[0])
            assert not r.scheduler._owners[binding.space_id].full_again
        finally:
            release.set()
        assert idle_recovered(r, binding)
        for _ in range(1000):
            reconstructed(*snapshots[0])
        r.scheduler.wait(15)
        assert begins[0] == 1


def test_original_move_out_retires_descendants_and_reconciles(tmp_path, monkeypatch, shared):
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        folder = owner.vault_root / "folder"
        deep = folder / "deep"
        deep.mkdir(parents=True)
        (folder / "gone.md").write_text("moved away", encoding="utf-8")
        (deep / "also.md").write_text("descendant moved away", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        assert idle_recovered(r, binding)
        native = next(e for e in r.watcher._producers if e.watch.path.endswith("default"))._inotify._inotify
        handler = next(h for h in r.watcher._handlers if h._binding is binding)
        removed, dispatch = threading.Event(), handler.dispatch
        displaced_events = []

        def delivered(event):
            dispatch(event)
            if event.src_path == str(folder) and event.event_type in ("deleted", "moved"):
                removed.set()
            if event.src_path.endswith("external.md"):
                displaced_events.append(event.src_path)

        monkeypatch.setattr(handler, "dispatch", delivered)
        outside = tmp_path / "outside"
        folder.rename(outside)
        assert removed.wait(5)
        native_write_after_dispatch(r, "default", "marker.txt", monkeypatch)
        r.scheduler.wait(15)
        assert idle_recovered(r, binding)
        paths = [c.path for c in owner.repository.load_chunks()]
        stale = any(path == os.fsencode(folder) or path.startswith(os.fsencode(folder) + b"/")
                    for path in native._wd_for_path)
        print(f"move out: rows={paths}, stale_native={stale}, status={r.scheduler.status(binding)}")
        assert not paths and not stale
        (outside / "deep" / "external.md").write_text("must not route under the former owner path", encoding="utf-8")
        native_write_after_dispatch(r, "default", "second-marker.txt", monkeypatch)
        assert not displaced_events
        assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed


def test_original_external_native_close_disposes_both_pipe_ends(tmp_path, monkeypatch):
    expected = threading.Event()
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        watcher, binding = r.watcher, r.bindings["default"]
        reader = next(iter(watcher._producers))._inotify
        native = reader._inotify
        pipes = native._kill_r, native._kill_w
        hook = threading.excepthook

        def failed(args):
            if args.thread is reader and isinstance(args.exc_value, OSError) and args.exc_value.errno == errno.EBADF:
                expected.set()
            else:
                hook(args)

        monkeypatch.setattr(threading, "excepthook", failed)
        os.close(native._inotify_fd)
        os.write(native._kill_w, b"wake")
        reader.join(5)
        assert not reader.is_alive() and r.scheduler.status(binding).monitoring_failed
        stop_error = None
        try:
            watcher.stop()
        except OSError as error:
            stop_error = error
        alive = []
        for descriptor in pipes:
            try:
                os.fstat(descriptor)
                alive.append(descriptor)
            except OSError as error:
                assert error.errno == errno.EBADF
        print(f"external EBADF: cleanup_error={stop_error}, complete={native._resources_closed}, "
              f"pipes_open={len(alive)}, owner_held={r.scheduler._watcher_token is watcher._token}")
        try:
            assert not alive and native._resources_closed
            assert r.scheduler._watcher_token is None
            watcher.stop()
        finally:
            # Pre-repair reproduction must not itself leak the confirmed pipes.
            for descriptor in alive:
                os.close(descriptor)


@pytest.mark.parametrize("name", ["_inotify_fd", "_kill_r", "_kill_w"])
@pytest.mark.parametrize("reuse", ["none", "file", "inotify"])
def test_partial_close_retains_owner_and_retry_does_not_close_reused_slots(tmp_path, monkeypatch, name, reuse):
    from watchdog.observers.inotify_c import inotify_init

    from app.services.shared_filesystem_watcher import SharedFilesystemWatcher

    close = os.close
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        watcher = r.watcher
        emitter = next(iter(watcher._producers))
        reader, native = emitter._inotify, emitter._inotify._inotify
        target = getattr(native, name)
        failing, exceptions = threading.Event(), []
        failing.set()
        hook = threading.excepthook

        def expected(args):
            if args.thread in (emitter, reader) and isinstance(args.exc_value, OSError):
                exceptions.append(type(args.exc_value).__name__)
            else:
                hook(args)

        def failed(fd):
            if fd == target and failing.is_set():
                raise OSError(errno.EIO, "close outcome unresolved")
            return close(fd)

        monkeypatch.setattr(threading, "excepthook", expected)
        monkeypatch.setattr(os, "close", failed)
        with pytest.raises(OSError):
            watcher.stop()
        assert not native._resources_closed
        assert r.scheduler._watcher_token is watcher._token
        assert watcher._observer is not None and watcher._producers
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert native._descriptors[name].state == "uncertain"
        for other in ("_inotify_fd", "_kill_r", "_kill_w"):
            if other != name:
                assert native._descriptors[other].state in ("closed", "absent", "replaced")
        candidate = SharedFilesystemWatcher(r.registry, r.bindings.values(), scheduler=r.scheduler,
                                             debounce_seconds=10)
        with pytest.raises(RuntimeError, match="admission is unavailable"):
            candidate.start()
        failing.clear()
        extra = None
        try:
            if reuse != "none":
                extra = (inotify_init() if reuse == "inotify"
                         else os.open(tmp_path / "unrelated", os.O_RDWR | os.O_CREAT, 0o600))
                os.dup2(extra, target)
                before = os.fstat(target)
            watcher.stop()
            watcher.stop()
            assert native._resources_closed and r.scheduler._watcher_token is None
            assert all(d.state in ("closed", "absent", "replaced") for d in native._descriptors.values())
            if extra is not None:
                after = os.fstat(target)
                assert (before.st_dev, before.st_ino, before.st_mode) == (after.st_dev, after.st_ino, after.st_mode)
                os.fstat(extra)
            use_production_observer(r)
            r.watcher.start()
            r.complete_native_startup()
        finally:
            if extra is not None:
                close(extra)
                if extra != target:
                    close(target)


def test_unavailable_retry_identity_proof_retains_ownership_until_resolution(tmp_path, monkeypatch):
    from app.services import watcher_ingress_linux as module

    close = os.close
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        emitter = next(iter(r.watcher._producers))
        reader, native = emitter._inotify, emitter._inotify._inotify
        fd = native._inotify_fd
        armed = threading.Event()
        armed.set()
        hook = threading.excepthook

        def expected(args):
            if args.thread not in (emitter, reader) or not isinstance(args.exc_value, OSError):
                hook(args)

        def failed(number):
            if number == fd and armed.is_set():
                raise OSError(errno.EIO, "uncertain native close")
            return close(number)

        monkeypatch.setattr(threading, "excepthook", expected)
        monkeypatch.setattr(os, "close", failed)
        with pytest.raises(OSError):
            r.watcher.stop()
        armed.clear()
        monkeypatch.setattr(module, "_same_description", lambda *args: None)
        with pytest.raises(OSError, match="ownership uncertain"):
            r.watcher.stop()
        assert r.scheduler._watcher_token is r.watcher._token and not native._resources_closed
        close(fd)  # Explicit resolution of the known original descriptor.
        r.watcher.stop()
        assert native._resources_closed and r.scheduler._watcher_token is None


@pytest.mark.parametrize("name", ["_inotify_fd", "_kill_r", "_kill_w"])
def test_uncertain_guard_close_is_not_published_as_complete(tmp_path, monkeypatch, name):
    close = os.close
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        emitter = next(iter(r.watcher._producers))
        reader, native = emitter._inotify, emitter._inotify._inotify
        armed = threading.Event()
        armed.set()
        hook = threading.excepthook

        def expected(args):
            if args.thread not in (emitter, reader) or not isinstance(args.exc_value, OSError):
                hook(args)

        def failed(number):
            guard = native._descriptors[name].guard
            if armed.is_set() and guard is not None and number == guard.number:
                raise OSError(errno.EIO, "last reference close uncertain")
            return close(number)

        monkeypatch.setattr(threading, "excepthook", expected)
        monkeypatch.setattr(os, "close", failed)
        with pytest.raises(OSError):
            r.watcher.stop()
        assert not native._resources_closed and r.scheduler._watcher_token is r.watcher._token
        assert all(native._descriptors[key].state == "closed" for key in ("_inotify_fd", "_kill_r", "_kill_w"))
        guard = native._descriptors[name].guard
        assert guard.state == "uncertain"
        armed.clear()
        close(guard.number)  # Verify disposition; never blindly retry a last reference.
        r.watcher.stop()
        assert native._resources_closed and r.scheduler._watcher_token is None


def test_partial_constructor_cleanup_failure_keeps_failed_emitter_reachable(tmp_path, monkeypatch):
    import ctypes

    from app.services import watcher_ingress_linux as module
    from app.services.shared_filesystem_watcher import SharedFilesystemWatcher

    init, add, close = module.inotify_init, module.inotify_add_watch, os.close
    descriptors = []
    armed = threading.Event()
    armed.set()

    def allocated():
        descriptor = init()
        descriptors.append(descriptor)
        return descriptor

    def exhausted(*args):
        ctypes.set_errno(errno.ENOSPC)
        return -1

    def failed(number):
        if armed.is_set() and descriptors and number == descriptors[0]:
            raise OSError(errno.EIO, "partial constructor cleanup failed")
        return close(number)

    with rig(tmp_path, 1) as r:
        monkeypatch.setattr(module, "inotify_init", allocated)
        monkeypatch.setattr(module, "inotify_add_watch", exhausted)
        monkeypatch.setattr(os, "close", failed)
        use_production_observer(r)
        with pytest.raises(OSError):
            r.watcher.start()
        assert r.scheduler._watcher_token is r.watcher._token
        assert r.watcher._producers and not r.watcher._observer.emitters
        producer = r.watcher._producers[0]
        reader = producer._inotify
        assert reader.ident is None and reader._queue._closed
        assert not reader._inotify._resources_closed
        candidate = SharedFilesystemWatcher(r.registry, r.bindings.values(), scheduler=r.scheduler,
                                             debounce_seconds=10)
        with pytest.raises(RuntimeError, match="admission is unavailable"):
            candidate.start()
        armed.clear()
        r.watcher.stop()
        assert reader._inotify._resources_closed and r.scheduler._watcher_token is None
        monkeypatch.setattr(module, "inotify_add_watch", add)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()


@pytest.mark.parametrize("move", ["rename", "relocate", "into", "cross", "delete", "missing-from",
                                  "missing-to", "delayed-to", "interleaved", "overflow"])
def test_directory_move_categories_have_qualified_fenced_recovery(tmp_path, monkeypatch, shared, move):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    parse = _BoundedInotify._parse_event_buffer
    entered, release = threading.Event(), threading.Event()
    pending = []  # Test-only one-record interleaving; production retains no directory-cookie map.
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, peer = r.owners["default"], r.owners["space-1"]
        binding = r.bindings["default"]
        folder = owner.vault_root / "folder"
        source = tmp_path / "incoming" if move == "into" else folder
        (source / "deep").mkdir(parents=True)
        (source / "deep" / "gone.md").write_text("moving subtree", encoding="utf-8")
        (owner.vault_root / "container").mkdir()
        if move == "interleaved":
            (owner.vault_root / "second").mkdir()
            (owner.vault_root / "second" / "also.md").write_text("second subtree", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        native = next(e for e in r.watcher._producers if e.watch.path.endswith("default"))._inotify._inotify
        original_gap, gaps = native._gap, [0]

        def debt():
            ticket = original_gap()
            gaps[0] += 1
            assert r.scheduler.status(binding).requires_full_sync
            return ticket

        native._gap = debt

        def records(current, data):
            for record in parse(data):
                if current is not native:
                    yield record
                    continue
                moved_from = record[1] & InotifyConstants.IN_MOVED_FROM
                moved_to = record[1] & InotifyConstants.IN_MOVED_TO
                if move == "missing-from" and moved_from:
                    continue
                if move == "missing-to" and moved_to:
                    continue
                if move == "interleaved" and moved_to and record[3] == b"renamed":
                    pending.append(record)
                    assert len(pending) == 1
                    continue
                if move == "delayed-to" and moved_to:
                    entered.set()
                    assert release.wait(10)
                yield record
                if move == "interleaved" and moved_from and record[3] == b"second":
                    if pending:
                        yield pending.pop()
                if move == "overflow" and moved_from:
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""

        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", records)
        target = (peer.vault_root / "arrived" if move == "cross" else
                  owner.vault_root / "container" / "arrived" if move == "relocate" else
                  folder if move == "into" else owner.vault_root / "renamed")
        try:
            if move == "delete":
                (source / "deep" / "gone.md").unlink()
                (source / "deep").rmdir()
                source.rmdir()
            else:
                source.rename(target)
            if move == "interleaved":
                (owner.vault_root / "second").rename(owner.vault_root / "second-renamed")
            if move == "delayed-to":
                assert entered.wait(5)
                missed = target / "deep" / "transient.md"
                missed.write_text("before delayed registration", encoding="utf-8")
                r.scheduler.request_full_sync(binding)
                r.scheduler.wait(15)
                assert r.scheduler.status(binding).requires_full_sync
                missed.unlink()
        finally:
            release.set()
        # A move/overflow can now retire the root watch as part of the exact
        # native-set rebuild. Coverage debt accounts for events in that gap;
        # submit an ordered delivery marker only after restored reconciliation.
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: gaps[0] > 0, 5)
        assert idle_recovered(r, binding)
        native_write_after_dispatch(r, "default", "marker.txt", monkeypatch)
        assert idle_recovered(r, binding) and idle_recovered(r, r.bindings["space-1"])
        expected = set() if move in ("delete", "cross") else {
            (target / "deep" / "gone.md").relative_to(owner.vault_root).as_posix(),
        }
        if move == "interleaved":
            expected.add("second-renamed/also.md")
        assert {c.path for c in owner.repository.load_chunks()} == expected
        assert {c.path for c in peer.repository.load_chunks()} == (
            {"arrived/deep/gone.md"} if move == "cross" else set()
        )
        assert not pending and gaps[0] >= 1 and len(native._moved_from_events) == 0
        assert all(native._wd_for_path[path] == wd for wd, path in native._path_for_wd.items())
        if move != "into":
            assert not any(path == os.fsencode(folder) or path.startswith(os.fsencode(folder) + b"/")
                           for path in native._wd_for_path)
        assert not r.scheduler.status(binding).monitoring_failed
        assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed


def test_retirement_ignored_records_cannot_complete_the_private_coverage_fence(tmp_path, monkeypatch, shared):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    parse = _BoundedInotify._parse_event_buffer
    entered, release = threading.Event(), threading.Event()
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        folder = owner.vault_root / "folder"
        (folder / "deep").mkdir(parents=True)
        (folder / "deep" / "gone.md").write_text("internal rename", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        native = next(iter(r.watcher._producers))._inotify._inotify
        retired = {wd for path, wd in native._wd_for_path.items() if path.startswith(os.fsencode(folder))}
        restored, publications = native._restored, [0]

        def published(ticket):
            publications[0] += 1
            restored(ticket)

        def held(current, data):
            for record in parse(data):
                if (current is native and record[0] in retired and record[1] & InotifyConstants.IN_IGNORED
                        and not entered.is_set()):
                    entered.set()
                    assert release.wait(10)
                yield record

        native._restored = published
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", held)
        folder.rename(owner.vault_root / "renamed")
        try:
            assert entered.wait(5)
            assert publications[0] == 0 and r.scheduler.status(binding).monitoring_recovering
        finally:
            release.set()
        assert idle_recovered(r, binding)
        assert publications[0] >= 1
        assert {c.path for c in owner.repository.load_chunks()} == {"renamed/deep/gone.md"}


def test_stop_during_subtree_retirement_keeps_gap_until_reconstruction(tmp_path, monkeypatch, shared):
    from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
    from app.services.watcher_ingress_linux import _BoundedInotify

    entered, release = threading.Event(), threading.Event()
    add = _BoundedInotify._add_dir_watch
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        folder = owner.vault_root / "folder"
        folder.mkdir()
        (folder / "gone.md").write_text("leaving root", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        old = r.watcher

        def blocked(native, path, mask, *, recursive):
            result = add(native, path, mask, recursive=recursive)
            entered.set()
            assert release.wait(10)
            return result

        monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", blocked)
        folder.rename(tmp_path / "departed")
        stopping = None
        try:
            assert entered.wait(5)
            assert r.scheduler.status(binding).requires_full_sync
            stopping = launch(old.stop)
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: old._stopping, 5)
            candidate = SharedFilesystemWatcher(r.registry, r.bindings.values(), scheduler=r.scheduler,
                                                 debounce_seconds=10)
            with pytest.raises(RuntimeError, match="admission is unavailable"):
                candidate.start()
        finally:
            release.set()
        stopping.result(10)
        assert r.scheduler.status(binding).monitoring_recovering
        monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", add)
        use_production_observer(r)
        r.watcher.start()
        assert idle_recovered(r, binding)
        assert not owner.repository.load_chunks()
        old._ingress_failure(None, "LateRetirement")
        assert not r.scheduler.status(binding).monitoring_failed


def test_concurrent_stops_dispose_all_descriptors_without_leak(tmp_path):
    with rig(tmp_path, 1) as r:
        baseline = len(os.listdir("/proc/self/fd"))
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        native = next(iter(r.watcher._producers))._inotify._inotify
        first, second = launch(r.watcher.stop), launch(r.watcher.stop)
        first.result(10)
        second.result(10)
        assert native._resources_closed and r.scheduler._watcher_token is None
        assert len(native._descriptors) <= 6
        assert len(os.listdir("/proc/self/fd")) == baseline


def test_directory_departure_during_move_restoration_is_reconciled(tmp_path, monkeypatch, shared):
    entered, release = threading.Event(), threading.Event()
    scan = os.scandir
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        for name in ("first", "second"):
            (owner.vault_root / name).mkdir()
            (owner.vault_root / name / "gone.md").write_text("departing", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        reader = next(iter(r.watcher._producers))._inotify

        class Entry:
            def __init__(self, entry):
                self.entry = entry

            def __getattr__(self, name):
                return getattr(self.entry, name)

            def stat(self, **kwargs):
                entered.set()
                assert release.wait(10)
                return self.entry.stat(**kwargs)

        class Scan:
            def __init__(self, iterator):
                self.iterator = iterator

            def __iter__(self):
                return self

            def __next__(self):
                entry = next(self.iterator)
                return Entry(entry) if os.fsdecode(entry.name) == "second" and not entered.is_set() else entry

            def close(self):
                self.iterator.close()

        def delayed(path):
            iterator = scan(path)
            if threading.current_thread() is reader and os.fsdecode(path) == str(owner.vault_root):
                return Scan(iterator)
            return iterator

        monkeypatch.setattr(os, "scandir", delayed)
        (owner.vault_root / "first").rename(tmp_path / "outside-first")
        try:
            assert entered.wait(5)
            (owner.vault_root / "second").rename(tmp_path / "outside-second")
        finally:
            release.set()
        native_write_after_dispatch(r, "default", "marker.txt", monkeypatch)
        assert idle_recovered(r, binding)
        assert not owner.repository.load_chunks()
        assert not r.scheduler.status(binding).monitoring_failed
