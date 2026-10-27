"""Strict native enumeration and runtime delivery-pipeline health."""

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

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux native pipeline")


class Entry:
    def __init__(self, entry, method, number):
        self.entry, self.method, self.number = entry, method, number

    def __getattr__(self, name):
        return getattr(self.entry, name)

    def is_dir(self, *args, **kwargs):
        if self.method == "vanish":
            os.rmdir(self.entry.path)
            raise FileNotFoundError(errno.ENOENT, "child disappeared during classification", self.entry.path)
        if self.method == "is_dir":
            raise OSError(self.number, "directory classification failed", self.entry.path)
        return self.entry.is_dir(*args, **kwargs)

    def is_symlink(self):
        if self.method == "is_symlink":
            raise OSError(self.number, "link classification failed", self.entry.path)
        return self.entry.is_symlink()

    def stat(self, *args, **kwargs):
        if self.method == "stat":
            raise OSError(self.number, "entry metadata failed", self.entry.path)
        return self.entry.stat(*args, **kwargs)


class Scan:
    def __init__(self, iterator, child, method, number, counts):
        self.iterator, self.child, self.method, self.number = iterator, child, method, number
        self.counts, self.closed = counts, False
        counts[0] += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        if self.method == "next":
            raise OSError(self.number, "iterator failed")
        entry = next(self.iterator)
        return Entry(entry, self.method, self.number) if os.fsdecode(entry.name) == self.child else entry

    def close(self):
        if not self.closed:
            self.closed = True
            self.iterator.close()
            self.counts[1] += 1


@pytest.mark.parametrize("number", [errno.EACCES, errno.EPERM, errno.EIO])
@pytest.mark.parametrize("method", ["is_dir", "is_symlink", "stat", "next"])
def test_original_startup_classification_error_is_fatal(tmp_path, monkeypatch, number, method):
    scan, counts = os.scandir, [0, 0]
    with rig(tmp_path, 1) as r:
        root = r.owners["default"].vault_root
        (root / "ordinary" / "deep").mkdir(parents=True)

        def faulty(path):
            return Scan(scan(path), "deep", method, number, counts)

        monkeypatch.setattr(os, "scandir", faulty)
        use_production_observer(r)
        with pytest.raises(OSError) as failure:
            r.watcher.start()
        assert failure.value.errno == number
        assert counts[0] == counts[1]
        assert r.watcher._observer is None and r.watcher._thread is None
        assert r.scheduler._watcher_token is None


@pytest.mark.parametrize("stage", ["create", "overflow"])
@pytest.mark.parametrize("method", ["is_dir", "is_symlink", "stat", "next"])
def test_runtime_entry_and_iterator_errors_close_partial_traversal(tmp_path, monkeypatch, stage, method):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    scan, parse, counts = os.scandir, _BoundedInotify._parse_event_buffer, [0, 0]
    entered, release = threading.Event(), threading.Event()
    with rig(tmp_path, 2) as r:
        root = r.owners["default"].vault_root
        ordinary = root / "ordinary"
        if stage == "overflow":
            ordinary.mkdir()
        use_production_observer(r)
        r.watcher.start()
        emitter = next(e for e in r.watcher._observer.emitters if e.watch.path == str(root))
        reader = emitter._inotify

        def faulty(path):
            iterator = scan(path)
            if threading.current_thread() is reader and os.fsdecode(path) == str(ordinary):
                return Scan(iterator, "deep", method, errno.EIO, counts)
            return iterator

        def events(native, data):
            for record in parse(data):
                if stage == "overflow" and record[3] == b"deep" and record[1] & InotifyConstants.IN_CREATE:
                    native._begin_registration()
                    native._queue_coverage_fence()  # Actual old marker cannot conceal failure.
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    if stage == "create" and record[3] == b"ordinary" and record[1] & InotifyConstants.IN_CREATE:
                        entered.set()
                        assert release.wait(10)
                    yield record

        monkeypatch.setattr(os, "scandir", faulty)
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", events)
        try:
            if stage == "create":
                ordinary.mkdir()
                assert entered.wait(5)
            (ordinary / "deep").mkdir()
        finally:
            release.set()
        binding = r.bindings["default"]
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: r.scheduler.status(binding).monitoring_failed, 5)
        reader.join(5)
        emitter.join(5)
        assert not reader.is_alive() and not emitter.is_alive()
        assert counts[0] > 0 and counts[0] == counts[1]
        assert reader._queue._closed and reader._inotify._resources_closed
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.scheduler.status(binding).monitoring_failed and r.scheduler.status(binding).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


def test_original_overflow_classification_cannot_certify_missing_watch(tmp_path, monkeypatch, shared):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import BoundedInotifyBuffer, _BoundedInotify

    parse, scan, report = _BoundedInotify._parse_event_buffer, os.scandir, BoundedInotifyBuffer._report_failure
    entered, release, outcome, fence, drained = (threading.Event() for _ in range(5))
    armed, injected, counts = threading.Event(), threading.Event(), [0, 0]
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
        reader, restored = emitter._inotify, emitter._inotify._inotify._restored

        def events(native, data):
            for record in parse(data):
                if record[3] == b"deep" and record[1] & InotifyConstants.IN_CREATE and not injected.is_set():
                    injected.set()
                    entered.set()
                    assert release.wait(10)
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    yield record
                if record[3] == b"ordered.txt":
                    drained.set()

        def faulty(path):
            iterator = scan(path)
            if armed.is_set() and threading.current_thread() is reader and os.fsdecode(path) == str(ordinary):
                return Scan(iterator, "deep", "is_dir", errno.EIO, counts)
            return iterator

        def established(ticket):
            fence.set()
            restored(ticket)
            outcome.set()

        def failed(buffer, error_type):
            report(buffer, error_type)
            if buffer is reader:
                outcome.set()

        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", events)
        monkeypatch.setattr(BoundedInotifyBuffer, "_report_failure", failed)
        monkeypatch.setattr(os, "scandir", faulty)
        reader._inotify._restored = established
        deep = ordinary / "deep"
        deep.mkdir()
        note = deep / "gone.md"
        try:
            assert entered.wait(5)
            note.write_text("lost deletion", encoding="utf-8")
            armed.set()
        finally:
            release.set()
        assert outcome.wait(5)
        r.scheduler.request_full_sync(binding)
        r.scheduler.wait(15)
        assert {c.path for c in owner.repository.load_chunks()} == {"ordinary/deep/gone.md"}
        note.unlink()
        (owner.vault_root / "ordered.txt").write_text("native drain", encoding="utf-8")
        if not r.scheduler.status(binding).monitoring_failed:
            assert drained.wait(5)
        status = r.scheduler.status(binding)
        print(f"classification EIO: fence={fence.is_set()}, failed={status.monitoring_failed}, "
              f"recovering={status.monitoring_recovering}, debt={status.requires_full_sync}, "
              f"deep_watched={os.fsencode(deep) in reader._inotify._wd_for_path}, "
              f"rows={[c.path for c in owner.repository.load_chunks()]}")
        assert status.monitoring_failed and status.monitoring_recovering and status.requires_full_sync
        assert not fence.is_set() and counts[0] == counts[1]
        native_write_after_dispatch(r, "space-1", "peer.md", monkeypatch)
        r.advance(10)
        r.delivered()
        r.scheduler.wait(15)
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        armed.clear()
        r.watcher.stop()
        use_production_observer(r)
        r.watcher.start()
        r.scheduler.wait(15)
        assert not owner.repository.load_chunks()
        assert not r.scheduler.status(binding).requires_full_sync


@pytest.mark.parametrize("component", ["emitter", "observer"])
def test_original_runtime_thread_death_cannot_remain_healthy(tmp_path, monkeypatch, shared, component):
    from app.services import shared_filesystem_watcher as module

    monkeypatch.setattr(module, "ROOT_VALIDATION_INTERVAL_SECONDS", 0.05)
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        peer = r.bindings["space-1"]
        peer_owner = r.owners["space-1"]
        (peer_owner.vault_root / "existing.md").write_text("healthy peer", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        # Scheduler startup preceded native coverage. Establish the generation's
        # actual post-attachment reconciliation before injecting runtime death.
        r.complete_native_startup()
        peer_entry = r.scheduler._owners[peer.space_id]

        def peer_revisions():
            return (peer_entry.monitoring_revision, peer_entry.monitoring_failure_revision,
                    peer_entry.coverage_revision, peer_entry.coverage_restored,
                    peer_entry.coverage_reconciled, peer_entry.watcher_verified)

        with r.scheduler._condition:
            baseline = peer_revisions()
            assert not r.scheduler.status(peer).requires_full_sync
            assert not r.scheduler.status(peer).running
            assert peer_entry.coverage_reconciled == peer_entry.coverage_revision
        peer_begin, peer_fulls = peer_owner.begin_sync, [0]

        def counted_peer_begin(*, relative_paths, cancel_event):
            if relative_paths is None:
                peer_fulls[0] += 1
            return peer_begin(relative_paths=relative_paths, cancel_event=cancel_event)

        monkeypatch.setattr(peer_owner, "begin_sync", counted_peer_begin)
        owner, binding = r.owners["default"], r.bindings["default"]
        observer = r.watcher._observer
        emitter = next(e for e in observer.emitters if e.watch.path == str(owner.vault_root))
        reader = emitter._inotify
        worker = emitter if component == "emitter" else observer
        terminated = threading.Event()
        exception_hook = threading.excepthook

        def expected_failure(args):
            if args.thread is worker and isinstance(args.exc_value, OSError) and args.exc_value.errno == errno.EIO:
                terminated.set()
            else:
                exception_hook(args)

        def failed(*args, **kwargs):
            raise OSError(errno.EIO, "runtime delivery thread failed")

        monkeypatch.setattr(threading, "excepthook", expected_failure)
        monkeypatch.setattr(worker, "_decode_path" if component == "emitter" else "dispatch_events", failed)
        (owner.vault_root / "trigger.md").write_text("trigger native delivery", encoding="utf-8")
        assert terminated.wait(5)
        worker.join(5)
        assert not worker.is_alive() and reader.is_alive()
        with r.scheduler._condition:
            detected = r.scheduler._condition.wait_for(lambda: r.scheduler.status(binding).monitoring_failed, 0.5)
        status = r.scheduler.status(binding)
        print(f"runtime {component}: detected={detected}, observer={observer.is_alive()}, "
              f"emitter={emitter.is_alive()}, reader={reader.is_alive()}, "
              f"failed={status.monitoring_failed}, recovering={status.monitoring_recovering}, "
              f"debt={status.requires_full_sync}")
        assert detected and status.monitoring_recovering and status.requires_full_sync
        assert status.monitoring_error_type == ("NativeProducerUnavailable" if component == "emitter"
                                                else "NativeObserverUnavailable")
        if component == "emitter":
            with r.scheduler._condition:
                assert peer_revisions() == baseline
                assert not r.scheduler.status(peer).requires_full_sync
                assert not r.scheduler.status(peer).monitoring_failed
            assert peer_fulls[0] == 0  # Catch even a spurious full that already completed.
            native_write_after_dispatch(r, "space-1", "peer.md", monkeypatch)
            r.advance(10)
            r.delivered()
            r.scheduler.wait(15)
            assert {c.path for c in peer_owner.repository.load_chunks()} == {"existing.md", "peer.md"}
            assert peer_fulls[0] == 0
            print(f"peer isolated: revisions={baseline[:5]}, full_starts=0, SQLite=existing.md,peer.md")
        else:
            assert r.scheduler.status(peer).monitoring_failed
        revision = r.scheduler._owners[binding.space_id].monitoring_revision
        r.watcher._check_roots()
        assert r.scheduler._owners[binding.space_id].monitoring_revision == revision
        r.scheduler.request_full_sync(binding)
        r.scheduler.wait(15)
        assert r.scheduler.status(binding).monitoring_failed and r.scheduler.status(binding).requires_full_sync
        r.watcher.stop()
        assert not reader.is_alive() and reader._inotify._resources_closed
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        assert not r.scheduler.status(binding).requires_full_sync
        assert not r.scheduler.status(peer).requires_full_sync


@pytest.mark.parametrize("mode", ["clean-stop", "terminal", "reconstruction"])
def test_retired_liveness_result_cannot_publish_failure(tmp_path, monkeypatch, mode):
    from app.services import shared_filesystem_watcher as module

    alive = module.native_watch_alive
    entered, release = threading.Event(), threading.Event()
    main = threading.current_thread()
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        old, binding = r.watcher, r.bindings["default"]
        observer = old._observer

        def delayed(candidate, watch, **kwargs):
            if candidate is observer and threading.current_thread() not in (main, old._thread):
                entered.set()
                assert release.wait(10)
                return False
            return alive(candidate, watch, **kwargs)

        monkeypatch.setattr(module, "native_watch_alive", delayed)
        checking = launch(old._check_roots)
        try:
            assert entered.wait(5)
            if mode == "terminal":
                r.scheduler.begin_shutdown()
            old.stop()
            if mode == "reconstruction":
                use_production_observer(r)
                r.watcher.start()
                r.scheduler.start()
                r.scheduler.wait(5)
            revision = r.scheduler._owners[binding.space_id].monitoring_revision
        finally:
            release.set()
        checking.result(5)
        status = r.scheduler.status(binding)
        assert not status.monitoring_failed
        assert r.scheduler._owners[binding.space_id].monitoring_revision == revision
        assert status.monitoring_recovering == (mode != "reconstruction")


def test_periodic_check_does_not_misclassify_unfinished_native_startup(tmp_path, monkeypatch):
    from app.services import shared_filesystem_watcher as module
    from app.services.watcher_ingress_linux import _BoundedInotify

    monkeypatch.setattr(module, "ROOT_VALIDATION_INTERVAL_SECONDS", 0.05)
    add = _BoundedInotify._add_dir_watch
    entered, release, checked = threading.Event(), threading.Event(), threading.Event()
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        validate = r.watcher._validate_binding

        def slow(native, *args, **kwargs):
            entered.set()
            assert release.wait(10)
            return add(native, *args, **kwargs)

        def observed(binding):
            if threading.current_thread() is r.watcher._thread and entered.is_set():
                checked.set()
            return validate(binding)

        monkeypatch.setattr(_BoundedInotify, "_add_dir_watch", slow)
        monkeypatch.setattr(r.watcher, "_validate_binding", observed)
        starting = launch(r.watcher.start)
        try:
            assert entered.wait(5) and checked.wait(5)
            assert not r.scheduler.status(r.bindings["default"]).monitoring_failed
            assert all(entry.watcher_verified is None for entry in r.scheduler._owners.values())
            assert r.scheduler.status(r.bindings["default"]).monitoring_recovering
        finally:
            release.set()
        starting.result(5)
        assert len(r.watcher._producers) == 1
        assert not r.scheduler.status(r.bindings["default"]).monitoring_failed


def test_native_reader_termination_is_detected_without_a_reader_failure_callback(tmp_path, monkeypatch):
    from app.services import shared_filesystem_watcher as module

    monkeypatch.setattr(module, "ROOT_VALIDATION_INTERVAL_SECONDS", 0.05)
    with rig(tmp_path, 2) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        binding = r.bindings["default"]
        emitter = next(e for e in r.watcher._producers if e.watch.path.endswith("default"))
        reader = emitter._inotify
        # Stop only the native reader, not the owning watcher lifecycle. The
        # buffer's intentional-stop path reports no fatal reader exception.
        reader.stop()
        reader.join(5)
        assert not reader.is_alive() and reader.failure_type is None
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: r.scheduler.status(binding).monitoring_failed, 1)
        status = r.scheduler.status(binding)
        assert status.monitoring_error_type == "NativeProducerUnavailable"
        assert status.monitoring_recovering and status.requires_full_sync
        assert r.scheduler._watcher_token is r.watcher._token
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        native_write_after_dispatch(r, "space-1", "surviving.md", monkeypatch)


@pytest.mark.parametrize("previously_watched", [False, True])
@pytest.mark.parametrize("boundary", ["classification", "registration"])
def test_classification_enoent_requires_proven_unregistered_child(tmp_path, monkeypatch, previously_watched, boundary):
    from watchdog.observers.inotify_c import InotifyConstants

    from app.services.watcher_ingress_linux import _BoundedInotify

    scan, parse, add, counts = os.scandir, _BoundedInotify._parse_event_buffer, _BoundedInotify._add_watch, [0, 0]
    removed = threading.Event()
    with rig(tmp_path, 1) as r:
        root = r.owners["default"].vault_root
        ordinary = root / "ordinary"
        ordinary.mkdir()
        child = ordinary / "deep"
        if previously_watched:
            child.mkdir()
        use_production_observer(r)
        r.watcher.start()
        emitter = next(iter(r.watcher._producers))
        reader = emitter._inotify

        def faulty(path):
            iterator = scan(path)
            if threading.current_thread() is reader and os.fsdecode(path) == str(ordinary):
                return Scan(iterator, "deep", "vanish" if boundary == "classification" else "normal",
                            errno.ENOENT, counts)
            return iterator

        def vanished(native, path, mask):
            if boundary == "registration" and path == os.fsencode(child) and not removed.is_set():
                child.rmdir()
                removed.set()
            return add(native, path, mask)

        def overflow(native, data):
            for record in parse(data):
                if record[3] == (b"overflow.txt" if previously_watched else b"deep"):
                    yield -1, InotifyConstants.IN_Q_OVERFLOW, 0, b""
                else:
                    yield record

        monkeypatch.setattr(os, "scandir", faulty)
        monkeypatch.setattr(_BoundedInotify, "_add_watch", vanished)
        monkeypatch.setattr(_BoundedInotify, "_parse_event_buffer", overflow)
        if previously_watched:
            (root / "overflow.txt").write_text("loss", encoding="utf-8")
        else:
            child.mkdir()
        binding = r.bindings["default"]
        if previously_watched:
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: r.scheduler.status(binding).monitoring_failed, 5)
            reader.join(5)
            emitter.join(5)
            assert not reader.is_alive() and not emitter.is_alive()
        else:
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: counts[1] > 0, 5)
            r.complete_native_startup()  # Reconcile the rebuilt coverage before testing fresh delivery.
            native_write_after_dispatch(r, "default", "survives.md", monkeypatch)
            assert reader.is_alive() and emitter.is_alive()
            assert not r.scheduler.status(binding).monitoring_failed
            assert os.fsencode(child) not in reader._inotify._wd_for_path
            r.scheduler.start()
            r.scheduler.wait(5)
            # E4 idle is not native coverage completion: wait for the real
            # post-registration fence and its matching reconciliation as well.
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(lambda: not r.scheduler.status(binding).requires_full_sync, 5)
            assert not r.scheduler.status(binding).requires_full_sync
        assert counts[0] > 0 and counts[0] == counts[1]
