"""Root constructor authority, reconstruction progress and native close fences."""

import errno
import inspect
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_lifecycle_repairs import native_write_after_dispatch
from tests.test_shared_watcher_repairs import use_production_observer

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux native transitions")


def delete_after_dispatch(r, path, monkeypatch):
    handler = next(h for h in r.watcher._handlers if h._binding is r.bindings["default"])
    deleted = threading.Event()
    dispatch = handler.dispatch

    def delivered(event):
        dispatch(event)
        if event.src_path == str(path) and event.event_type == "deleted":
            deleted.set()

    with monkeypatch.context() as patch:
        patch.setattr(handler, "dispatch", delivered)
        path.unlink()
        assert deleted.wait(5)
    r.advance(r.now + 10)
    r.delivered()
    r.scheduler.wait(15)


@pytest.mark.parametrize("number", [errno.EIO, errno.EACCES, errno.EPERM])
def test_original_root_metadata_cannot_select_root_only_startup(tmp_path, monkeypatch, shared, number):
    from watchdog.observers import inotify_c

    from app.services import watcher_ingress_linux as module

    stat, init, pipe = os.stat, inotify_c.inotify_init, os.pipe
    allocated, injected, preflight = threading.Event(), threading.Event(), threading.Event()
    descriptors, boundary = [], []
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        deep = owner.vault_root / "ordinary" / "deep"
        deep.mkdir(parents=True)
        note = deep / "gone.md"
        note.write_text("root-only watch misses deletion", encoding="utf-8")
        use_production_observer(r)
        validate = r.watcher._validate_binding

        def validated(binding):
            result = validate(binding)
            preflight.set()
            return result

        def native_init():
            fd = init()
            descriptors.append(fd)
            allocated.set()
            return fd

        def native_pipe():
            pair = pipe()
            if allocated.is_set():
                descriptors.extend(pair)
            return pair

        def metadata(path, *args, **kwargs):
            if allocated.is_set() and not injected.is_set() and os.fsdecode(path) == str(owner.vault_root):
                assert preflight.is_set()
                boundary.append(inspect.currentframe().f_back.f_code.co_name)
                injected.set()
                raise OSError(number, "root metadata unavailable", path)
            return stat(path, *args, **kwargs)

        monkeypatch.setattr(r.watcher, "_validate_binding", validated)
        monkeypatch.setattr(inotify_c, "inotify_init", native_init)
        monkeypatch.setattr(module, "inotify_init", native_init, raising=False)
        monkeypatch.setattr(os, "stat", metadata)
        monkeypatch.setattr(os, "pipe", native_pipe)
        error = None
        try:
            r.watcher.start()
        except OSError as failure:
            error = failure
        assert injected.is_set()
        if error is None:
            native = next(iter(r.watcher._observer.emitters))._inotify._inotify
            r.scheduler.start(startup=True)
            r.scheduler.wait(15)
            note.unlink()
            # Ordered native marker, independent of absent descendant delivery.
            native_write_after_dispatch(r, "default", "marker.md", monkeypatch)
            r.advance(10)
            r.delivered()
            r.scheduler.wait(15)
            print(f"root stat errno={number}, boundary={boundary}, watches={len(native._wd_for_path)}, "
                  f"deep_watched={os.fsencode(deep) in native._wd_for_path}, "
                  f"status={r.scheduler.status(binding)}, rows={[c.path for c in owner.repository.load_chunks()]}")
        assert error is not None, "root metadata failure silently selected root-only monitoring"
        assert error.errno == number
        assert r.watcher._observer is None and r.watcher._thread is None
        assert r.scheduler._watcher_token is None
        for descriptor in descriptors:
            with pytest.raises(OSError) as closed:
                os.fstat(descriptor)
            assert closed.value.errno == errno.EBADF
        use_production_observer(r)
        r.watcher.start()
        native = next(iter(r.watcher._observer.emitters))._inotify._inotify
        assert os.fsencode(deep) in native._wd_for_path
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        delete_after_dispatch(r, note, monkeypatch)
        assert not owner.repository.load_chunks()
        assert not r.scheduler.status(binding).requires_full_sync
        print(f"root stat errno={number}, boundary={boundary}, startup_rejected=True, "
              "allocated descriptors closed, reconstructed deep watch=True, final rows=[]")


def fail_native_reader(r, monkeypatch):
    from app.services.watcher_ingress_linux import _BoundedInotify

    parse = _BoundedInotify._parse_event_buffer

    def failed(native, data):
        for record in parse(data):
            if record[3] == b"fatal.txt":
                raise OSError(errno.EIO, "native reader failed")
            yield record

    with monkeypatch.context() as patch:
        patch.setattr(_BoundedInotify, "_parse_event_buffer", failed)
        reader = next(e for e in r.watcher._observer.emitters if e.watch.path.endswith("default"))._inotify
        (r.owners["default"].vault_root / "fatal.txt").write_text("native failure", encoding="utf-8")
        binding = r.bindings["default"]
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: r.scheduler.status(binding).monitoring_failed, 5)
        reader.join(5)
        assert not reader.is_alive() and reader.failure_type == "OSError"


@pytest.mark.parametrize("order", ["before", "after", "multiple", "early-full"])
def test_original_startup_directory_activity_can_finish_reconstruction(tmp_path, monkeypatch, shared, order):
    from app.services import shared_filesystem_watcher as module

    entered, release, fenced, fence_entered, release_fence, full_entered, release_full = (
        threading.Event() for _ in range(7)
    )
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        fail_native_reader(r, monkeypatch)
        r.watcher.stop()
        use_production_observer(r)
        ready = module.wait_native_watch_ready
        restored = r.watcher._ingress_restored
        owner, binding = r.owners["default"], r.bindings["default"]
        begin = owner.begin_sync
        turns = []

        def authoritative(*, relative_paths, cancel_event):
            entry = r.scheduler._owners[binding.space_id]
            if (relative_paths is None and entry.watcher_verified is not None
                    and entry.coverage_restored == entry.coverage_revision):
                turns.append(entry.coverage_revision)
                full_entered.set()
                assert release_full.wait(15)
            return begin(relative_paths=relative_paths, cancel_event=cancel_event)

        def paused(observer, watch, active):
            ready(observer, watch, active)
            if watch.path.endswith("default"):
                entered.set()
                assert release.wait(15)

        def completed(watch, tickets):
            if watch.path.endswith("default") and order in ("after", "early-full"):
                fence_entered.set()
                assert release_fence.wait(15)
            restored(watch, tickets)
            if watch.path.endswith("default"):
                fenced.set()

        monkeypatch.setattr(module, "wait_native_watch_ready", paused)
        monkeypatch.setattr(r.watcher, "_ingress_restored", completed)
        monkeypatch.setattr(owner, "begin_sync", authoritative)
        starting = launch(r.watcher.start)
        try:
            assert entered.wait(5)
            directory = r.owners["default"].vault_root / "during-start"
            directory.mkdir()
            if order == "multiple":
                for name in ("second", "third", "fourth"):
                    (owner.vault_root / name).mkdir()
            if order in ("after", "early-full"):
                assert fence_entered.wait(5)
                release.set()
                starting.result(10)
                if order == "early-full":
                    r.scheduler.request_full_sync(binding)
                    r.scheduler.wait(15)
                    assert not full_entered.is_set()
                assert r.scheduler.status(binding).monitoring_failed
                release_fence.set()
            assert fenced.wait(5)
            release.set()
            starting.result(10)
            assert full_entered.wait(5)
            assert r.scheduler.status(r.bindings["default"]).monitoring_failed
            assert r.scheduler.status(binding).monitoring_recovering
            (directory / "live.md").write_text("created during recovery", encoding="utf-8")
        finally:
            release.set()
            release_fence.set()
            release_full.set()
        starting.result(10)
        r.scheduler.wait(15)
        binding = r.bindings["default"]
        status = r.scheduler.status(binding)
        native = next(e for e in r.watcher._producers if e.watch.path.endswith("default"))._inotify._inotify
        print(f"startup directory: watched={os.fsencode(directory) in native._wd_for_path}, "
              f"failed={status.monitoring_failed}, recovering={status.monitoring_recovering}, "
              f"debt={status.requires_full_sync}")
        assert not status.monitoring_failed and not status.monitoring_recovering and not status.requires_full_sync
        assert os.fsencode(directory) in native._wd_for_path
        assert 1 <= len(turns) <= 6
        native_write_after_dispatch(r, "default", "during-start/live.md", monkeypatch)
        r.advance(10)
        r.delivered()
        r.scheduler.wait(15)
        assert {c.path for c in r.owners["default"].repository.load_chunks()} == {"during-start/live.md"}
        assert all("native event" in c.content for c in owner.repository.load_chunks())
        delete_after_dispatch(r, directory / "live.md", monkeypatch)
        assert not r.owners["default"].repository.load_chunks()
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.parametrize("count", [1, 4])
@pytest.mark.parametrize("cleanup_retry", [False, True])
def test_original_close_between_read_unlock_and_parse_fences_buffer(tmp_path, monkeypatch, count, cleanup_retry):
    from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
    from app.services.watcher_ingress_linux import _BoundedInotify

    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    reading, release_read = threading.Event(), threading.Event()
    attempted = []
    add, read = _BoundedInotify._add_watch, os.read
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        watcher, binding = r.watcher, r.bindings["default"]
        emitter = next(iter(watcher._producers))
        reader, native = emitter._inotify, emitter._inotify._inotify
        lock = native._lock
        join = watcher._observer.join
        failed_join = threading.Event()

        def incomplete(*args, **kwargs):
            if cleanup_retry and not failed_join.is_set():
                failed_join.set()
                raise RuntimeError("incomplete native cleanup")
            return join(*args, **kwargs)

        def batch(fd, size):
            if fd == native._inotify_fd and not reading.is_set():
                reading.set()
                assert release_read.wait(15)
                data = read(fd, size)
                records = sum(1 for record in native._parse_event_buffer(data) if record[3].startswith(b"buffered"))
                assert records >= count
                return data
            return read(fd, size)

        class BoundaryLock:
            def __enter__(self):
                lock.acquire()
                return self

            def __exit__(self, *args):
                pause = (threading.current_thread() is reader and not native._is_reading
                         and not native._closed and not entered.is_set())
                done = native._resources_closed
                lock.release()
                if done:
                    closed.set()
                if pause:
                    entered.set()
                    assert release.wait(15)

        def counted(current, path, mask):
            if current is native and current._resources_closed:
                attempted.append(path)
            return add(current, path, mask)

        native._lock = BoundaryLock()
        monkeypatch.setattr(watcher._observer, "join", incomplete)
        monkeypatch.setattr(_BoundedInotify, "_add_watch", counted)
        monkeypatch.setattr(os, "read", batch)
        (r.owners["default"].vault_root / "buffered-0").mkdir()
        stopping = None
        try:
            assert reading.wait(5)
            for i in range(1, count):
                (r.owners["default"].vault_root / f"buffered-{i}").mkdir()
            release_read.set()
            assert entered.wait(5)
            stopping = launch(watcher.stop)
            assert closed.wait(5)
            assert native._closed and native._resources_closed
            for descriptor in (native._inotify_fd, native._kill_r, native._kill_w):
                with pytest.raises(OSError) as dead:
                    os.fstat(descriptor)
                assert dead.value.errno == errno.EBADF
            assert r.scheduler._watcher_token is watcher._token
            replacement = SharedFilesystemWatcher(r.registry, r.bindings.values(), scheduler=r.scheduler,
                                                  debounce_seconds=10)
            with pytest.raises(RuntimeError, match="admission is unavailable"):
                replacement.start()
        finally:
            release_read.set()
            release.set()
        if cleanup_retry:
            with pytest.raises(RuntimeError, match="incomplete native cleanup"):
                stopping.result(10)
            assert r.scheduler._watcher_token is watcher._token
            watcher.stop()
        else:
            stopping.result(10)
        status = r.scheduler.status(binding)
        print(f"post-close parsing: registrations={len(attempted)}, native_failure={reader.failure_type}, "
              f"failed={status.monitoring_failed}, recovering={status.monitoring_recovering}")
        assert not attempted and reader.failure_type is None and not status.monitoring_failed
        assert status.monitoring_recovering and status.requires_full_sync
        assert not reader.is_alive() and not emitter.is_alive() and watcher._observer is None
        watcher.stop()
        use_production_observer(r)
        r.watcher.start()
        r.scheduler.start()
        r.scheduler.wait(5)
        assert not r.scheduler.status(binding).requires_full_sync


@pytest.mark.parametrize("boundary", ["parser", "before-registration", "during-registration"])
def test_active_parser_retains_descriptor_ownership_until_shutdown_can_close(tmp_path, monkeypatch, boundary):
    from watchdog.observers.inotify_c import Inotify, InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    entered, release = threading.Event(), threading.Event()
    parse, add, native_add = _BoundedInotify._parse_event_buffer, _BoundedInotify._add_watch, Inotify._add_watch
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        emitter = next(iter(r.watcher._producers))
        native = emitter._inotify._inotify

        def pause():
            entered.set()
            assert release.wait(10)
            assert not native._closed and not native._resources_closed
            os.fstat(native._inotify_fd)

        def parser(current, data):
            for record in parse(data):
                if (boundary == "parser" and record[3] == b"active"
                        and record[1] & InotifyConstants.IN_CREATE):
                    pause()
                yield record

        def registration(current, path, mask):
            if boundary == "before-registration" and path.endswith(b"/active"):
                pause()
            return add(current, path, mask)

        def installed(current, path, mask):
            wd = native_add(current, path, mask)
            if boundary == "during-registration" and path.endswith(b"/active"):
                pause()
            return wd

        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", parser)
        monkeypatch.setattr(_BoundedInotify, "_add_watch", registration)
        monkeypatch.setattr(Inotify, "_add_watch", installed)
        (r.owners["default"].vault_root / "active").mkdir()
        stopping = None
        try:
            assert entered.wait(5)
            stopping = launch(r.watcher.stop)
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: r.watcher._stopping, 5)
            assert not native._closed and not native._resources_closed
        finally:
            release.set()
        stopping.result(10)
        assert emitter._inotify.failure_type is None
        assert not emitter.is_alive() and not emitter._inotify.is_alive() and native._resources_closed
        status = r.scheduler.status(r.bindings["default"])
        assert not status.monitoring_failed and status.monitoring_recovering and status.requires_full_sync


@pytest.mark.parametrize("number", [errno.EBADF, errno.EIO, errno.EACCES])
def test_active_native_read_error_is_not_intentional_shutdown(tmp_path, monkeypatch, number):
    read = os.read
    with rig(tmp_path, 2) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        emitter = next(e for e in r.watcher._producers if e.watch.path.endswith("default"))
        reader, native = emitter._inotify, emitter._inotify._inotify

        def failed(fd, size):
            if fd == native._inotify_fd:
                assert not native._closed
                raise OSError(number, "active native read failed")
            return read(fd, size)

        monkeypatch.setattr(os, "read", failed)
        (r.owners["default"].vault_root / "failure.txt").write_text("read trigger", encoding="utf-8")
        reader.join(5)
        emitter.join(5)
        assert not reader.is_alive() and not emitter.is_alive()
        status = r.scheduler.status(r.bindings["default"])
        assert status.monitoring_failed and status.requires_full_sync
        assert native._resources_closed and reader._queue._closed
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


@pytest.mark.parametrize("action", ["native", "root", "cancel", "stop", "terminal", "old-generation"])
def test_reconstruction_activity_cannot_hide_new_failure_or_invalid_lifecycle(tmp_path, monkeypatch, shared, action):
    from app.repositories.semantic_binding import IndexBindingUnavailableError
    from app.services import shared_filesystem_watcher as module
    from app.services.knowledge_spaces import SpaceError

    entered, release, fenced = threading.Event(), threading.Event(), threading.Event()
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        fail_native_reader(r, monkeypatch)
        old = r.watcher
        old.stop()
        use_production_observer(r)
        watcher, binding = r.watcher, r.bindings["default"]
        ready, restored = module.wait_native_watch_ready, watcher._ingress_restored

        def paused(observer, watch, active):
            ready(observer, watch, active)
            if watch.path.endswith("default"):
                entered.set()
                assert release.wait(15)

        def completed(watch, tickets):
            restored(watch, tickets)
            if watch.path.endswith("default"):
                fenced.set()

        monkeypatch.setattr(module, "wait_native_watch_ready", paused)
        monkeypatch.setattr(watcher, "_ingress_restored", completed)
        starting = launch(watcher.start)
        stopping = None
        try:
            assert entered.wait(5)
            (r.owners["default"].vault_root / "startup-child").mkdir()
            assert fenced.wait(5)
            entry = r.scheduler._owners[binding.space_id]
            failure_revision = entry.monitoring_failure_revision
            native_write_after_dispatch(r, "space-1", "peer.md", monkeypatch)
            assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed
            if action == "native":
                fail_native_reader(r, monkeypatch)
                assert entry.monitoring_failure_revision > failure_revision
            elif action == "root":
                root = r.owners["default"].vault_root
                root.rename(tmp_path / "displaced")
                root.mkdir()
            elif action == "cancel":
                r.scheduler.cancel(binding)
            elif action == "stop":
                stopping = launch(watcher.stop)
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(lambda: watcher._stopping, 5)
            elif action == "terminal":
                r.scheduler.begin_shutdown()
            else:
                old._ingress_failure(None, "LateOldFailure")
                old._ingress_restored(None, ((binding, (entry.coverage_revision, failure_revision)),))
                assert entry.monitoring_failure_revision == failure_revision
        finally:
            release.set()
        if action == "old-generation":
            starting.result(10)
            r.scheduler.wait(15)
            assert not r.scheduler.status(binding).requires_full_sync
        else:
            with pytest.raises((OSError, RuntimeError, SpaceError, IndexBindingUnavailableError)):
                starting.result(10)
            assert r.scheduler.status(binding).monitoring_failed
            assert r.scheduler.status(binding).requires_full_sync and entry.watcher_verified is None
        if stopping is not None:
            stopping.result(10)
        if action == "terminal":
            assert r.scheduler._closed and not r.scheduler.request_full_sync(binding)


@pytest.mark.parametrize("component", ["reader", "emitter"])
def test_failure_during_matching_reconstruction_full_invalidates_verification(tmp_path, monkeypatch, shared, component):
    from app.services import shared_filesystem_watcher as module

    monkeypatch.setattr(module, "ROOT_VALIDATION_INTERVAL_SECONDS", 0.05)
    entered, release = threading.Event(), threading.Event()
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        fail_native_reader(r, monkeypatch)
        r.watcher.stop()
        owner, binding = r.owners["default"], r.bindings["default"]
        begin = owner.begin_sync

        def paused(**kwargs):
            entered.set()
            assert release.wait(10)
            return begin(**kwargs)

        monkeypatch.setattr(owner, "begin_sync", paused)
        use_production_observer(r)
        r.watcher.start()
        try:
            assert entered.wait(5)
            entry = r.scheduler._owners[binding.space_id]
            previous = entry.monitoring_failure_revision
            assert entry.watcher_verified is not None and r.scheduler.status(binding).monitoring_failed
            if component == "reader":
                fail_native_reader(r, monkeypatch)
            else:
                emitter = next(e for e in r.watcher._producers if e.watch.path.endswith("default"))
                emitter.stop()
                emitter.join(5)
                with r.scheduler._condition:
                    assert r.scheduler._condition.wait_for(lambda: entry.monitoring_failure_revision > previous, 1)
            assert entry.monitoring_failure_revision > previous and entry.watcher_verified is None
        finally:
            release.set()
        r.scheduler.wait(15)
        assert r.scheduler.status(binding).monitoring_failed and r.scheduler.status(binding).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).monitoring_failed


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_root_substitution_at_native_initialization_is_rejected(tmp_path, monkeypatch, replacement):
    from app.services import watcher_ingress_linux as module

    stat, init = os.stat, module.inotify_init
    allocated, changed = threading.Event(), threading.Event()
    with rig(tmp_path, 1) as r:
        root = r.owners["default"].vault_root
        displaced, outside = tmp_path / "old-root", tmp_path / "outside"
        outside.mkdir()

        def started():
            descriptor = init()
            allocated.set()
            return descriptor

        def substituted(path, *args, **kwargs):
            if allocated.is_set() and not changed.is_set() and os.fsdecode(path) == str(root):
                changed.set()
                root.rename(displaced)
                if replacement == "directory":
                    root.mkdir()
                else:
                    os.symlink(outside, root, target_is_directory=True)
            return stat(path, *args, **kwargs)

        monkeypatch.setattr(module, "inotify_init", started)
        monkeypatch.setattr(os, "stat", substituted)
        use_production_observer(r)
        with pytest.raises(OSError):
            r.watcher.start()
        assert changed.is_set() and r.watcher._observer is None and r.scheduler._watcher_token is None
        if replacement == "directory":
            root.rmdir()
        else:
            root.unlink()
        displaced.rename(root)
        use_production_observer(r)
        r.watcher.start()
        native_write_after_dispatch(r, "default", "restored.md", monkeypatch)


@pytest.mark.parametrize("failure", ["pipe", "poll"])
def test_partial_native_initialization_cleans_allocations(tmp_path, monkeypatch, failure):
    from app.services import watcher_ingress_linux as module

    init, pipe, poll = module.inotify_init, os.pipe, module.select.poll
    descriptors = []

    def initialized():
        descriptor = init()
        descriptors.append(descriptor)
        return descriptor

    def pipes():
        if failure == "pipe":
            raise OSError(errno.EMFILE, "native pipe allocation failed")
        pair = pipe()
        descriptors.extend(pair)
        return pair

    class FailedPoll:
        def register(self, *args):
            raise OSError(errno.EIO, "native poll registration failed")

    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        with monkeypatch.context() as patch:
            patch.setattr(module, "inotify_init", initialized)
            patch.setattr(os, "pipe", pipes)
            patch.setattr(module.select, "poll", FailedPoll if failure == "poll" else poll)
            with pytest.raises(OSError):
                r.watcher.start()
        for descriptor in descriptors:
            with pytest.raises(OSError) as closed:
                os.fstat(descriptor)
            assert closed.value.errno == errno.EBADF
        assert r.scheduler._watcher_token is None
        use_production_observer(r)
        r.watcher.start()


def test_native_select_readiness_fallback_preserves_recursive_delivery(tmp_path, monkeypatch, shared):
    import select

    from app.services import watcher_ingress_linux as module

    monkeypatch.setattr(module, "select", SimpleNamespace(select=select.select))
    resource, _ = shared
    with rig(tmp_path, 1, real=True, resource=resource) as r:
        root = r.owners["default"].vault_root
        (root / "ordinary" / "deep").mkdir(parents=True)
        use_production_observer(r)
        r.watcher.start()
        native = next(iter(r.watcher._producers))._inotify._inotify
        assert not hasattr(native, "_poller") and os.fsencode(root / "ordinary" / "deep") in native._wd_for_path
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        native_write_after_dispatch(r, "default", "ordinary/deep/live.md", monkeypatch)
        r.advance(10)
        r.delivered()
        r.scheduler.wait(15)
        assert {c.path for c in r.owners["default"].repository.load_chunks()} == {"ordinary/deep/live.md"}
        delete_after_dispatch(r, root / "ordinary" / "deep" / "live.md", monkeypatch)
        assert not r.owners["default"].repository.load_chunks()
