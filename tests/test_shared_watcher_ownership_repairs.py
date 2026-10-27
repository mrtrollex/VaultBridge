"""Confirmed coverage interruption, dispatcher death and cleanup ownership."""

import sys
import threading

import pytest
from watchdog.events import FileModifiedEvent

from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_repairs import use_production_observer


def replacement(r, bindings=None):
    return SharedFilesystemWatcher(r.registry, bindings or r.bindings.values(),
                                   scheduler=r.scheduler, debounce_seconds=10, clock=lambda: r.now)


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("early_sync", [False, True])
def test_original_clean_stop_reconstruction_reconciles_gap(tmp_path, shared, changed, early_sync):
    resource, _ = shared
    with rig(tmp_path, resource=resource, real=True) as r:
        owner = r.owners["default"]
        binding = r.bindings["default"]
        (owner.vault_root / "existing.md").write_text("old content", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        # Watch only A: B's coverage is not surrendered by this lifecycle.
        r.watcher = replacement(r, [binding])
        r.watcher.start()
        r.watcher.stop()
        if changed:
            (owner.vault_root / "existing.md").unlink()
            (owner.vault_root / "missed.md").write_text("during gap", encoding="utf-8")
        # Even a successful sync during the gap cannot establish coverage.
        if early_sync:
            r.scheduler.request_full_sync(binding)
            r.scheduler.wait(15)
        interrupted = r.scheduler.status(binding).monitoring_recovering
        # Change again AFTER that early sync so reconstruction must do real work.
        if changed:
            (owner.vault_root / "missed.md").write_text("after early sync", encoding="utf-8")
            (owner.vault_root / "later.md").write_text("also missed", encoding="utf-8")
        r.watcher = replacement(r, [binding])
        r.watcher.start()
        r.scheduler.wait(15)
        # E4 can be idle before the native startup fence requests its matching
        # reconciliation. Assert index contents only after coverage recovery too.
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: not r.scheduler.status(binding).requires_full_sync, 15)
        paths = {c.path for c in owner.repository.load_chunks()}
        print(f"clean stop: gap={interrupted}, reconstructed paths={sorted(paths)}")
        assert interrupted
        assert paths == ({"missed.md", "later.md"} if changed else {"existing.md"})
        assert not r.scheduler.status(binding).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync


def test_original_dispatcher_failure_is_fatal_until_reconstruction(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        use_production_observer(r)
        r.watcher.start()
        watcher, observer, dispatcher = r.watcher, r.watcher._observer, r.watcher._thread
        binding = r.bindings["default"]
        handler = next(h for h in watcher._handlers if h._binding is binding)
        # Admit under the condition before permitting the failing selection.
        with r.scheduler._condition:
            handler.dispatch(FileModifiedEvent(str(r.owners["default"].vault_root / "accepted.md")))
            assert r.scheduler.pending_count == 1
            monkeypatch.setattr(watcher, "_next_locked", lambda: (_ for _ in ()).throw(RuntimeError("selection")))
            r.scheduler._condition.notify_all()
        dispatcher.join(5)
        assert not dispatcher.is_alive() and observer.is_alive()
        r.scheduler.start()
        r.scheduler.wait(5)
        status = r.scheduler.status(binding)
        print(f"dispatcher dead: failed={status.monitoring_failed}, debt={status.requires_full_sync}, "
              f"owns_generation={r.scheduler._watcher_token is watcher._token}")
        assert status.monitoring_failed and status.requires_full_sync
        assert r.scheduler.pending_count == 0 and not watcher._accepting
        assert r.scheduler._watcher_token is watcher._token
        with pytest.raises(RuntimeError, match="admission is unavailable"):
            replacement(r).start()
        watcher.stop()
        assert not observer.is_alive()
        r.watcher = replacement(r)
        r.watcher.start()
        assert r.watcher._thread.is_alive()
        r.scheduler.wait(5)
        assert all(not r.scheduler.status(b).requires_full_sync for b in r.bindings.values())
        watcher._ingress_failure(None, "LateFailure")
        assert all(not r.scheduler.status(b).monitoring_failed for b in r.bindings.values())


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows read shutdown timeout")
def test_original_native_stop_timeout_retains_exclusive_generation(tmp_path, monkeypatch):
    from app.services.watcher_ingress_windows import NativeDirectoryRead

    entered, release, armed = threading.Event(), threading.Event(), threading.Event()
    read = NativeDirectoryRead.read_directory_changes

    def blocked(reader, handle, path, *, recursive):
        if path.endswith("default") and armed.is_set() and not entered.is_set():
            entered.set()
            assert release.wait(20)
        return read(reader, handle, path, recursive=recursive)

    monkeypatch.setattr(NativeDirectoryRead, "read_directory_changes", blocked)
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        armed.set()
        (r.owners["default"].vault_root / "next-read.txt").write_text("next native read", encoding="utf-8")
        watcher, observer = r.watcher, r.watcher._observer
        emitters = tuple(observer.emitters)
        assert entered.wait(5)
        other = None
        try:
            with pytest.raises(RuntimeError, match="Native filesystem read did not stop"):
                watcher.stop()
            retained = r.scheduler._watcher_token is watcher._token
            print(f"native timeout: observer={observer.is_alive()}, emitter={emitters[0].is_alive()}, "
                  f"generation_retained={retained}")
            other = replacement(r)
            with pytest.raises(RuntimeError, match="admission is unavailable"):
                other.start()
            assert retained
        finally:
            if other is not None:
                other.stop()
            cleanup = launch(watcher.stop)
            try:
                # Reconstruction also loses while a retry is actively cleaning
                # the old lifecycle. It must not wait on native scheduler locks.
                with pytest.raises(RuntimeError, match="admission is unavailable"):
                    replacement(r).start()
            finally:
                release.set()
            cleanup.result(10)
        assert not observer.is_alive()
        assert all(not e.is_alive() and e._whandle is None for e in emitters)
        assert r.scheduler._watcher_token is None
        watcher.stop()
        r.watcher = replacement(r)
        r.watcher.start()
        r.scheduler.start()
        r.scheduler.wait(5)
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync


@pytest.mark.parametrize("terminal", [False, True])
def test_stopped_gap_survives_sync_and_failed_reconstruction(tmp_path, monkeypatch, terminal):
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        binding = r.bindings["default"]
        if terminal:
            r.scheduler.begin_shutdown()
        r.watcher.stop()
        revision = r.scheduler._owners[binding.space_id].coverage_revision
        r.watcher.stop()
        assert r.scheduler._owners[binding.space_id].coverage_revision == revision > 0
        assert r.scheduler.status(binding).monitoring_recovering
        if terminal:
            assert not r.scheduler.request_full_sync(binding)
            with pytest.raises(RuntimeError, match="admission is unavailable"):
                replacement(r).start()
            assert not r.trace
            return
        # Construction/start failure cannot clear the gap or release another
        # generation's token; a later verified reconstruction remains possible.
        broken = replacement(r)
        monkeypatch.setattr(broken, "_observer_factory", lambda: (_ for _ in ()).throw(RuntimeError("startup")))
        with pytest.raises(RuntimeError, match="startup"):
            broken.start()
        assert r.scheduler.status(binding).monitoring_recovering
        r.scheduler.request_full_sync(binding)
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.scheduler.status(binding).monitoring_recovering
        r.watcher = replacement(r)
        r.watcher.start()
        r.scheduler.wait(5)
        assert not r.scheduler.status(binding).requires_full_sync


def test_reconstruction_wakes_paused_gap_work_once(tmp_path):
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        binding = r.bindings["default"]
        r.watcher.stop()
        r.owners["default"].fail = True
        r.scheduler.request_full_sync(binding)
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.scheduler.status(binding).paused
        r.owners["default"].fail = False
        r.watcher = replacement(r)
        r.watcher.start()
        r.scheduler.wait(5)
        assert not r.scheduler.status(binding).requires_full_sync


@pytest.mark.parametrize("reconstructions", [1, 3])
def test_early_full_completion_cannot_cover_later_clean_stops(tmp_path, monkeypatch, reconstructions):
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        binding = r.bindings["default"]
        owner = r.owners["default"]
        owner.full_steps = 1
        first, release_first, last, release_last = (threading.Event() for _ in range(4))
        step = owner.sync_step

        def blocked(handle, cancel):
            result = step(handle, cancel)
            entered, release = (first, release_first) if len(owner.begins) == 1 else (last, release_last)
            entered.set()
            assert release.wait(15)
            return result

        monkeypatch.setattr(owner, "sync_step", blocked)
        r.scheduler.request_full_sync(binding)
        r.scheduler.start()
        try:
            assert first.wait(5)
            previous = 0
            for _ in range(reconstructions):
                r.watcher.stop()
                entry = r.scheduler._owners[binding.space_id]
                assert entry.coverage_revision > previous
                r.watcher = replacement(r)
                r.watcher.start()
                previous = entry.coverage_revision
            release_first.set()
            assert last.wait(5)
            assert r.scheduler.status(binding).monitoring_recovering
            assert entry.coverage_reconciled < entry.coverage_revision
        finally:
            release_first.set()
            release_last.set()
        r.scheduler.wait(5)
        assert owner.begins == [None, None]
        assert not r.scheduler.status(binding).requires_full_sync


def test_start_cannot_publish_health_for_dead_dispatcher(tmp_path, monkeypatch):
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        monkeypatch.setattr(r.watcher, "_dispatch_loop", lambda: None)
        with pytest.raises(RuntimeError, match="dispatcher did not start"):
            r.watcher.start()
        assert r.watcher._observer is None and r.watcher._thread is None
        assert r.scheduler._watcher_token is None
        binding = r.bindings["default"]
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.scheduler.status(binding).monitoring_failed
        assert r.scheduler.status(binding).requires_full_sync
