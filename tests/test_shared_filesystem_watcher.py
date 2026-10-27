"""E5 bounded admission, qualified routing and process lifecycle regressions."""

import os
import threading
import time
from contextlib import contextmanager
from pathlib import PurePosixPath
from types import MappingProxyType

import pytest
from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent

from app.core.config import Settings
from app.services.knowledge_spaces import SpaceError, SpaceId
from app.services.semantic_resources import SharedEmbeddingResource
from app.services.semantic_scheduler import SharedSemanticScheduler
from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
from tests.eval.runner import CORPUS_ROOT, DeterministicConceptEmbedder, build_service, load_cases, run_evaluation
from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_semantic_scheduler import enqueue, setup


class FakeObserver:
    def __init__(self, *, fail_registration=None, fail_start=False):
        self.registrations = []
        self.fail_registration = fail_registration
        self.fail_start = fail_start
        self.started = False
        self.stopped = False
        self.joined = False
        self.unscheduled = False

    def schedule(self, handler, path, *, recursive):
        assert not self.started and recursive
        self.registrations.append((handler, path))
        if len(self.registrations) == self.fail_registration:
            raise RuntimeError("registration failed")

    def start(self):
        self.started = True
        if self.fail_start:
            raise RuntimeError("start failed")

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True

    def is_alive(self):
        return self.started and not self.stopped

    def unschedule_all(self):
        self.unscheduled = True
        self.registrations.clear()


class Rig:
    def __init__(self, tmp_path, count=2, **kwargs):
        self.registry, self.bindings, self.owners, self.trace = setup(tmp_path, count, **kwargs)
        self.scheduler = SharedSemanticScheduler(self.registry, self.bindings.values())
        self.observer = FakeObserver()
        self.now = 0.0
        self.factories = 0
        self.watcher = SharedFilesystemWatcher(
            self.registry, self.bindings.values(), scheduler=self.scheduler,
            debounce_seconds=10, clock=lambda: self.now, observer_factory=self.factory,
        )

    def factory(self):
        self.factories += 1
        return self.observer

    def start(self):
        self.watcher.start()
        self.handlers = {binding.space_id.value: handler
                         for (handler, _), binding in zip(
                             self.observer.registrations, sorted(
                                 self.watcher._bindings.values(), key=lambda b: b.space_id,
                             ), strict=True)}

    def event(self, sid="default", name="same.md", event_class=FileModifiedEvent):
        self.handlers[sid].dispatch(event_class(str(self.bindings[sid]._owners.vault.vault_root / name)))

    def complete_native_startup(self):
        """Arrange a reconciled native baseline without creating an executor.

        Native failure/callback tests exercise an established watcher. Execute
        the real E4 bounded turns for the now-required initial full before fault
        injection; never set health counters or fabricate native proof.
        """
        deadline = time.monotonic() + 10
        if self.scheduler._executor is not None:
            self.scheduler.wait(10)
        while True:
            with self.scheduler._condition:
                if all(not self.scheduler.status(b).requires_full_sync for b in self.watcher._bindings.values()):
                    break
                assert time.monotonic() < deadline
                entry = self.scheduler._select() if self.scheduler._executor is None else None
                if entry is None:
                    self.scheduler._condition.wait(0.01)
                    continue
            self.scheduler._turn(entry)
        # Reset only fake-owner instrumentation for the operation under test.
        # Real E3/SQLite state and all lifecycle/reconciliation evidence remain.
        for owner in self.owners.values():
            if hasattr(owner, "begins"):
                owner.begins.clear()
                owner.handles.clear()
                owner.entered.clear()
        self.trace.clear()

    def advance(self, now=10):
        with self.scheduler._condition:
            self.now = now
            self.scheduler._condition.notify_all()

    def delivered(self):
        with self.scheduler._condition:
            # Bulk capacity tests perform real filesystem validation per target.
            # Allow a finite workload-scaled harness budget; this is not the fake
            # monotonic quiet period or a production delivery/shutdown deadline.
            timeout = max(10, self.scheduler.pending_count / 50)
            assert self.scheduler._condition.wait_for(
                lambda: (not any(e.deferred for e in self.scheduler._owners.values())
                         or self.watcher._thread is None or not self.watcher._thread.is_alive()), timeout,
            )
            assert not any(e.deferred for e in self.scheduler._owners.values())

    def close(self):
        self.scheduler.begin_shutdown()
        self.watcher.stop()
        self.scheduler.shutdown()


@contextmanager
def rig(tmp_path, count=2, **kwargs):
    result = Rig(tmp_path, count, **kwargs)
    try:
        yield result
    finally:
        for owner in result.owners.values():
            if owner is not None and getattr(owner, "release", None) is not None:
                owner.release.set()
        result.close()


@pytest.mark.parametrize("count,disabled", [(1, ("default",)), (1, ()), (8, ()), (8, ("space-1", "space-7"))])
def test_one_observer_dispatcher_per_enabled_set_no_eager_workers(tmp_path, monkeypatch, count, disabled):
    with rig(tmp_path, count, disabled=disabled) as r:
        def forbidden(*args, **kwargs):
            pytest.fail("watcher constructed a semantic executor")

        monkeypatch.setattr("app.services.semantic_scheduler.ThreadPoolExecutor", forbidden)
        assert r.factories == 0 and r.watcher._thread is None and not r.trace
        r.start()
        enabled = count - len(disabled)
        assert r.factories == bool(enabled)
        assert len(r.observer.registrations) == enabled
        assert r.scheduler._executor is None
        assert len({path for _, path in r.observer.registrations}) == enabled
        assert (r.watcher._thread is not None) == bool(enabled)
        if enabled:
            thread = r.watcher._thread
            assert thread.name == "vaultbridge-shared-watch-dispatch" and thread.is_alive()
        r.watcher.stop()
        r.watcher.stop()
        assert r.watcher._thread is None
        assert not r.observer.registrations and not r.watcher._handlers
        assert r.scheduler._watcher_token is None
        assert r.scheduler.pending_count == 0 and not r.trace


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan")])
def test_bad_debounce(tmp_path, seconds):
    with rig(tmp_path, 1) as r:
        with pytest.raises(ValueError, match="positive"):
            SharedFilesystemWatcher(r.registry, r.bindings.values(), scheduler=r.scheduler, debounce_seconds=seconds)


def test_events_coalesce_and_equal_paths_in_two_spaces_are_independent(tmp_path):
    with rig(tmp_path) as r:
        r.start()
        r.event()
        r.event("space-1")
        r.now = 3
        r.event()
        assert r.scheduler.pending_count == 2 and not r.trace
        r.advance(10)
        b = r.scheduler._owners[SpaceId("space-1")]
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: bool(b.pending), 5)
        assert r.scheduler._owners[SpaceId("default")].deferred == {"same.md": 13}
        r.advance(13)
        r.delivered()
        assert r.scheduler.pending_count == 2
        r.scheduler.start()
        r.scheduler.wait(5)
        assert all(owner.begins == [("same.md",)] for owner in r.owners.values())
        assert r.scheduler.pending_count == 0


@pytest.mark.parametrize("event_class", [FileCreatedEvent, FileModifiedEvent, FileDeletedEvent])
def test_local_event_rules(tmp_path, event_class):
    with rig(tmp_path, 1) as r:
        r.start()
        r.event(name="Folder/Note.md", event_class=event_class)
        assert r.scheduler.status(r.bindings["default"]).queued_paths == ("Folder/Note.md",)


def test_move_qualifies_both_ends_and_never_routes_destination_to_other_owner(tmp_path):
    with rig(tmp_path) as r:
        r.start()
        a, b = r.bindings.values()
        root = a._owners.vault.vault_root
        r.handlers["default"].dispatch(FileMovedEvent(str(root / "old.md"), str(root / "new.md")))
        assert r.scheduler.status(a).queued_paths == ("new.md", "old.md")
        r.handlers["default"].dispatch(FileMovedEvent(
            str(root / "other.md"), str(b._owners.vault.vault_root / "new.md"),
        ))
        assert r.scheduler.status(b).queued_paths == ()
        assert r.scheduler.status(a).queued_paths == ("new.md", "old.md", "other.md")


@pytest.mark.parametrize("name", ["../outside.md", ".git/x.md", ".obsidian/x.md", ".#draft.md", "~$draft.md", "x.txt"])
def test_exclusions_and_unsafe_paths(tmp_path, name):
    with rig(tmp_path, 1) as r:
        r.start()
        r.event(name=name)
        assert r.scheduler.pending_count == 0
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync


def test_semantic_storage_excluded_and_callbacks_do_not_sync(tmp_path):
    with rig(tmp_path, 1) as r:
        r.start()
        root = r.owners["default"].vault_root
        r.handlers["default"].dispatch(FileCreatedEvent(str(tmp_path / "default-data" / "index.md")))
        r.handlers["default"]._interpreter._semantic_data_relative = PurePosixPath("derived")
        r.event(name="derived/index.md")
        r.event()
        assert r.scheduler.pending_count == 1 and not r.trace
        assert not list(root.iterdir())


def test_real_symlink_escape_missing_parent_and_internal_alias(tmp_path):
    with rig(tmp_path) as r:
        r.start()
        root = r.owners["default"].vault_root
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "note.md").write_text("private", encoding="utf-8")
        (root / "target.md").write_text("local", encoding="utf-8")
        try:
            os.symlink(outside, root / "escape", target_is_directory=True)
            os.symlink(root / "target.md", root / "alias.md")
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")
        r.event(name="escape/note.md")
        r.event(name="escape/absent.md", event_class=FileDeletedEvent)
        r.event(name="alias.md")
        assert r.scheduler.status(r.bindings["default"]).queued_paths == ("target.md",)


def test_foreign_stale_and_incompatible_bindings_rejected(tmp_path):
    with rig(tmp_path / "a") as r, rig(tmp_path / "b") as other:
        with pytest.raises(SpaceError):
            SharedFilesystemWatcher(other.registry, other.bindings.values(), scheduler=r.scheduler, debounce_seconds=1)
        with pytest.raises(SpaceError):
            SharedFilesystemWatcher(r.registry, other.bindings.values(), scheduler=r.scheduler, debounce_seconds=1)
        bindings = list(r.bindings.values())
        with pytest.raises(SpaceError):
            SharedFilesystemWatcher(r.registry, [bindings[0]] * 9, scheduler=r.scheduler, debounce_seconds=1)
        original = r.registry._owners
        object.__setattr__(r.registry, "_owners", MappingProxyType({}))
        with pytest.raises(SpaceError):
            r.start()
        object.__setattr__(r.registry, "_owners", original)
        assert r.factories == 0


@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("before_start", [False, True])
def test_root_loss_replacement_never_uses_other_space(tmp_path, replacement, before_start):
    with rig(tmp_path) as r:
        if not before_start:
            r.start()
            r.event()
        root = r.owners["default"].vault_root
        root.rename(tmp_path / "original")
        if replacement:
            root.mkdir()
        if before_start:
            with pytest.raises((SpaceError, OSError)):
                r.start()
            assert r.factories == 0
        else:
            r.event()
            r.event("space-1")
            r.advance()
            r.delivered()
            r.scheduler.start()
            r.scheduler.wait(5)
            assert r.scheduler.status(r.bindings["default"]).requires_full_sync
            assert r.scheduler.status(r.bindings["space-1"]).error_type is None
            assert r.owners["space-1"].begins == [("same.md",)]
            assert r.owners["default"].begins == []


@pytest.mark.parametrize("global_limit", [False, True])
def test_exact_caps_combined_stages_transfer_duplicates_and_first_overflow(tmp_path, global_limit):
    with rig(tmp_path, 8 if global_limit else 1) as r:
        r.start()
        selected = list(r.bindings)[:5] if global_limit else ["default"]
        for sid in selected:
            # Both independently submitted and debounced identities share caps.
            for i in range(1000):
                assert enqueue(r.scheduler, r.bindings[sid], f"direct-{i}.md")
                r.event(sid, f"watch-{i}.md")
        count = 10_000 if global_limit else 2000
        assert r.scheduler.pending_count == count
        for sid in selected:
            r.event(sid, "watch-0.md")
            r.event(sid, "direct-0.md")
            assert not r.scheduler.status(r.bindings[sid]).requires_full_sync
        assert r.scheduler.pending_count == count
        r.advance()
        r.delivered()
        assert r.scheduler.pending_count == count
        overflow = "space-5" if global_limit else "default"
        for i in range(3):
            r.event(overflow, f"overflow-{i}.md")
        status = r.scheduler.status(r.bindings[overflow])
        assert status.requires_full_sync
        assert not any(p.startswith("overflow") for p in status.queued_paths)
        assert r.scheduler.pending_count == count
        r.scheduler.start()
        r.scheduler.wait(10)
        assert r.scheduler.pending_count == 0
        assert r.owners[overflow].begins == [None]
        assert not r.scheduler.status(r.bindings[overflow]).requires_full_sync


@pytest.mark.parametrize("global_limit", [False, True])
def test_callbacks_and_direct_submissions_race_at_shared_caps(tmp_path, global_limit):
    with rig(tmp_path, 8 if global_limit else 1) as r:
        r.start()
        names = list(r.bindings)
        for sid in names[:4] if global_limit else ():
            for i in range(2000):
                r.event(sid, f"{i}.md")
        sid = names[4] if global_limit else names[0]
        for i in range(1999):
            r.event(sid, f"{i}.md")
        barrier = threading.Barrier(9)

        def submit(i):
            barrier.wait(10)
            target = names[4 + i % 4] if global_limit else sid
            if i % 2:
                enqueue(r.scheduler, r.bindings[target], f"race-{i}.md")
            else:
                r.event(target, f"race-{i}.md")

        workers = [launch(lambda i=i: submit(i)) for i in range(8)]
        barrier.wait(10)
        for worker in workers:
            worker.result(15)
        assert r.scheduler.pending_count == (10_000 if global_limit else 2000)
        assert sum(p.startswith("race-") for binding in r.bindings.values()
                   for p in r.scheduler.status(binding).queued_paths) == 1


def test_inflight_reservation_shared_with_debounce_and_direct_enqueue(tmp_path):
    with rig(tmp_path, 1) as r:
        r.start()
        owner = r.owners["default"]
        owner.release = threading.Event()
        binding = r.bindings["default"]
        assert enqueue(r.scheduler, binding)
        r.scheduler.start()
        assert owner.entered.wait(5)
        r.event()
        r.event()
        assert r.scheduler.pending_count == 1
        r.advance()
        r.delivered()
        assert r.scheduler.pending_count == 1
        assert not enqueue(r.scheduler, binding)
        owner.release.set()
        r.scheduler.wait(5)
        assert owner.begins == [("same.md",), ("same.md",)]
        assert r.scheduler.pending_count == 0


def test_full_absorbs_debounce_before_start_and_preserves_during_full(tmp_path):
    with rig(tmp_path, 1) as r:
        r.start()
        r.event(name="before.md")
        owner = r.owners["default"]
        owner.release = threading.Event()
        r.scheduler.request_full_sync(r.bindings["default"])
        r.scheduler.start()
        assert owner.entered.wait(5)
        assert r.scheduler.pending_count == 0
        r.event(name="during.md")
        r.event(name="before.md")
        r.advance()
        r.delivered()
        assert r.scheduler.pending_count == 2
        owner.release.set()
        r.scheduler.wait(5)
        assert owner.begins == [None, ("before.md", "during.md")]
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync


@pytest.mark.parametrize("cancel", [False, True])
def test_duplicate_event_recovers_failure_or_cancel_without_losing_wakeup(tmp_path, cancel):
    with rig(tmp_path) as r:
        r.start()
        owner = r.owners["default"]
        owner.release = threading.Event()
        owner.fail = not cancel
        if not cancel:
            original = owner.sync_step
            def fail_once(handle, token):
                try:
                    return original(handle, token)
                finally:
                    owner.fail = False
                    owner.sync_step = original
            owner.sync_step = fail_once
        binding = r.bindings["default"]
        enqueue(r.scheduler, binding)
        r.scheduler.start()
        assert owner.entered.wait(5)
        old_token = r.scheduler._owners[binding.space_id].cancel
        if cancel:
            r.scheduler.cancel(binding)
        r.event()
        r.event()  # Both callback and delivery duplicates must preserve wakeup.
        r.event("space-1")
        r.advance()
        r.delivered()
        assert r.scheduler._owners[binding.space_id].cancel is old_token
        if cancel:
            assert old_token.is_set()
        owner.release.set()
        r.scheduler.wait(10)
        assert owner.begins == [("same.md",), None]
        assert not r.scheduler.status(binding).requires_full_sync
        assert r.owners["space-1"].begins == [("same.md",)]
        if cancel:
            assert r.scheduler._owners[binding.space_id].cancel is not old_token


@pytest.mark.parametrize("cancel", [False, True])
def test_new_event_recovers_drained_failure_or_cancellation(tmp_path, cancel):
    with rig(tmp_path, 1) as r:
        r.start()
        binding = r.bindings["default"]
        owner = r.owners["default"]
        if cancel:
            r.scheduler.cancel(binding)
        else:
            owner.fail = True
            r.scheduler.request_full_sync(binding)
            r.scheduler.start()
            r.scheduler.wait(5)
            assert r.scheduler.status(binding).paused
            owner.fail = False
        r.event()
        r.event()
        r.scheduler.start()
        if cancel:
            r.advance()
            r.delivered()
        r.scheduler.wait(5)
        assert not r.scheduler.status(binding).paused
        assert not r.scheduler.status(binding).requires_full_sync


@pytest.mark.parametrize("failure", ["registration", "start", "dispatcher"])
def test_partial_startup_cleanup_and_idempotent_stop(tmp_path, monkeypatch, failure):
    with rig(tmp_path) as r:
        if failure == "dispatcher":
            original = threading.Thread.start
            def start(thread):
                if thread.name == "vaultbridge-shared-watch-dispatch":
                    raise RuntimeError("dispatcher start failed")
                return original(thread)
            monkeypatch.setattr(threading.Thread, "start", start)
        else:
            r.observer.fail_registration = 2 if failure == "registration" else None
            r.observer.fail_start = failure == "start"
        with pytest.raises(RuntimeError):
            r.start()
        r.watcher.stop()
        assert r.observer.stopped and r.observer.joined and r.observer.unscheduled
        assert r.scheduler._watcher_token is None and r.scheduler.pending_count == 0
        assert not r.watcher._accepting
        assert r.watcher._thread is None or not r.watcher._thread.is_alive()


def test_delivery_failure_becomes_debt_and_healthy_owner_continues(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        r.start()
        deliver = r.scheduler._deliver_debounced
        failed = []
        def fail(token, binding, path, deadline):
            if binding.space_id.value == "default":
                failed.append(path)
                raise RuntimeError("transient delivery failure")
            return deliver(token, binding, path, deadline)
        monkeypatch.setattr(r.scheduler, "_deliver_debounced", fail)
        r.event()
        r.event("space-1")
        r.advance()
        r.delivered()
        assert len(failed) == 1 and r.watcher._thread.is_alive()
        assert r.scheduler.pending_count == 1
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.owners["default"].begins == [None]
        assert r.owners["space-1"].begins == [("same.md",)]


def test_stop_pending_converts_to_debt_and_racing_callback_is_fenced(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        r.start()
        r.event()
        r.event("space-1")
        entered, release = threading.Event(), threading.Event()
        interpreter = r.handlers["default"]._interpreter
        original = interpreter.paths_for_event
        def interpret(event):
            entered.set()
            assert release.wait(5)
            return original(event)
        monkeypatch.setattr(interpreter, "paths_for_event", interpret)
        callback = launch(lambda: r.event(name="racing.md"))
        assert entered.wait(5)
        r.watcher.stop()
        release.set()
        callback.result(5)
        assert r.scheduler.pending_count == 0
        assert all(r.scheduler.status(b).requires_full_sync for b in r.bindings.values())
        r.event(name="after.md")
        assert r.scheduler.pending_count == 0


def test_shutdown_signals_all_before_watcher_stop_and_active_boundary(tmp_path):
    with rig(tmp_path, 8) as r:
        r.start()
        owner = r.owners["default"]
        owner.release = threading.Event()
        r.scheduler.start(startup=True)
        assert owner.entered.wait(5)
        r.event("space-1")
        r.scheduler.begin_shutdown()
        assert all(e.cancel.is_set() for e in r.scheduler._owners.values())
        r.watcher.stop()  # Must return without waiting for the active sync call.
        closing = launch(r.scheduler.shutdown)
        assert not closing.done()
        r.event()
        owner.release.set()
        closing.result(5)
        assert r.trace == ["default"]
        assert r.scheduler.pending_count == 0
        assert all(o.closed for o in r.owners.values())
        assert r.watcher._thread is None and r.watcher._observer is None


def test_only_one_watcher_can_own_scheduler_reservations(tmp_path):
    with rig(tmp_path, 1) as r:
        r.start()
        second = SharedFilesystemWatcher(r.registry, r.bindings.values(), scheduler=r.scheduler, debounce_seconds=1)
        with pytest.raises(RuntimeError):
            second.start()
        assert r.scheduler._watcher_token is r.watcher._token
        r.event()
        assert r.scheduler.pending_count == 1


def test_ready_identity_is_not_starved_by_other_space_activity(tmp_path):
    with rig(tmp_path) as r:
        r.start()
        r.event()
        r.event("space-1")
        # Refresh B's deadline while A is ready. No wall clock sleeps required.
        with r.scheduler._condition:
            r.now = 9
            r.event("space-1")
            assert r.watcher._next_locked() == (10, SpaceId("default"), "same.md")
        r.advance(10)
        entry = r.scheduler._owners[SpaceId("default")]
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: bool(entry.pending), 5)
        assert r.scheduler._owners[SpaceId("space-1")].deferred == {"same.md": 19}


def test_path_validation_does_not_hold_coordination_condition(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        r.start()
        entered, release = threading.Event(), threading.Event()
        vault = r.bindings["default"]._owners.vault
        original = vault.resolve_path
        def resolve(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        monkeypatch.setattr(vault, "resolve_path", resolve)
        callback = launch(r.event)
        try:
            assert entered.wait(5)
            healthy = launch(lambda: r.event("space-1"))
            healthy.result(3)
            assert r.scheduler.pending_count == 1
        finally:
            release.set()
            callback.result(5)


def test_pending_reservation_stays_charged_through_delivery_validation(tmp_path, monkeypatch):
    with rig(tmp_path, 1) as r:
        r.start()
        for i in range(2000):
            r.event(name=f"{i}.md")
        entered, release = threading.Event(), threading.Event()
        deliver = r.scheduler._deliver_debounced
        def blocked(*args):
            entered.set()
            assert release.wait(10)
            return deliver(*args)
        monkeypatch.setattr(r.scheduler, "_deliver_debounced", blocked)
        try:
            r.advance()
            assert entered.wait(5)
            assert r.scheduler.pending_count == 2000
            assert not enqueue(r.scheduler, r.bindings["default"], "overflow.md")
            assert r.scheduler.pending_count == 2000
            assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        finally:
            release.set()
        r.delivered()
        assert r.scheduler.pending_count == 2000


def test_revoked_owner_delivery_does_not_kill_dispatcher_or_touch_peer(tmp_path):
    with rig(tmp_path) as r:
        r.start()
        r.event()
        r.event("space-1")
        original = r.registry._owners
        object.__setattr__(r.registry, "_owners", MappingProxyType({
            sid: owner for sid, owner in original.items() if sid.value != "default"
        }))
        try:
            r.advance()
            r.delivered()
            assert r.watcher._thread.is_alive()
            assert r.scheduler.pending_count == 1
            assert r.scheduler._owners[SpaceId("default")].full_needed
            assert r.scheduler.status(r.bindings["space-1"]).queued_paths == ("same.md",)
        finally:
            object.__setattr__(r.registry, "_owners", original)


def test_fatal_dispatcher_failure_fences_events_and_preserves_all_debt(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        r.start()
        # Install under the coordination condition so no select can race setup.
        with r.scheduler._condition:
            r.event()
            r.event("space-1")
            def fail():
                raise RuntimeError("dispatcher failed")
            monkeypatch.setattr(r.watcher, "_next_locked", fail)
            r.scheduler._condition.notify_all()
        r.watcher._thread.join(5)
        assert not r.watcher._thread.is_alive()
        assert not r.watcher._accepting
        assert r.scheduler.pending_count == 0
        assert all(r.scheduler.status(b).requires_full_sync for b in r.bindings.values())
        r.event(name="after.md")
        r.watcher.stop()
        assert r.scheduler._watcher_token is None


def test_callback_admission_failure_requests_only_affected_full_debt(tmp_path, monkeypatch):
    with rig(tmp_path) as r:
        r.start()
        admit = r.scheduler._debounce
        def fail(token, binding, path, **kwargs):
            if binding.space_id.value == "default":
                raise RuntimeError("admission delivery failure")
            return admit(token, binding, path, **kwargs)
        monkeypatch.setattr(r.scheduler, "_debounce", fail)
        r.event()
        r.event("space-1")
        assert r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        assert r.scheduler.pending_count == 1


def test_windows_compatible_unsafe_symlink_denial(tmp_path, monkeypatch):
    with rig(tmp_path, 1) as r:
        r.start()
        root = r.owners["default"].vault_root
        (root / "unsafe.md").write_text("body", encoding="utf-8")
        monkeypatch.setattr(r.bindings["default"]._owners.vault, "verify_existing_markdown_path", lambda _: None)
        r.event(name="unsafe.md")
        assert r.scheduler.pending_count == 0


def test_failed_owner_is_paused_while_healthy_owner_finishes(tmp_path):
    with rig(tmp_path) as r:
        r.start()
        r.owners["default"].fail = True
        r.event()
        r.event("space-1")
        r.advance()
        r.delivered()
        r.scheduler.start()
        r.scheduler.wait(5)
        assert r.trace == ["default", "space-1"]
        assert r.scheduler.status(r.bindings["default"]).paused
        peer = r.scheduler.status(r.bindings["space-1"])
        assert peer.error_type is None and not peer.requires_full_sync
        assert r.owners["space-1"].begins == [("same.md",)]


def test_real_e3_e4_rename_delete_and_owner_isolation(tmp_path, shared):
    resource, state = shared
    with rig(tmp_path / "spaces", resource=resource, real=True) as r:
        assert not state.instances
        for owner in r.owners.values():
            (owner.vault_root / "same.md").write_text("original note", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        r.start()
        a, b = r.bindings.values()
        root = a._owners.vault.vault_root
        (root / "same.md").rename(root / "new.md")
        r.handlers["default"].dispatch(FileMovedEvent(str(root / "same.md"), str(root / "new.md")))
        r.advance()
        r.delivered()
        r.scheduler.wait(15)
        assert {c.path for c in r.owners["default"].repository.load_chunks()} == {"new.md"}
        assert {c.path for c in r.owners["space-1"].repository.load_chunks()} == {"same.md"}
        (root / "new.md").unlink()
        r.event(name="new.md", event_class=FileDeletedEvent)
        r.advance(20)
        r.delivered()
        r.scheduler.wait(15)
        assert r.owners["default"].repository.load_chunks() == []
        assert {c.path for c in r.owners["space-1"].repository.load_chunks()} == {"same.md"}
        assert len(state.instances) == 1 and state.maximum == 1


def test_real_file_replacement_after_delete_event_revalidates_only_bound_owner(tmp_path, shared, monkeypatch):
    resource, _ = shared
    with rig(tmp_path / "spaces", resource=resource, real=True) as r:
        for owner in r.owners.values():
            (owner.vault_root / "same.md").write_text("original note", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        peer = r.owners["space-1"].repository.load_chunks()
        r.start()
        root = r.owners["default"].vault_root
        note = root / "same.md"
        note.unlink()
        interpreter = r.handlers["default"]._interpreter
        original = interpreter.paths_for_event
        def replace_during_callback(event):
            paths = original(event)
            replacement = root / "replacement.tmp"
            replacement.write_text("replacement note", encoding="utf-8")
            os.replace(replacement, note)
            return paths
        monkeypatch.setattr(interpreter, "paths_for_event", replace_during_callback)
        r.event(event_class=FileDeletedEvent)
        r.advance()
        r.delivered()
        r.scheduler.wait(15)
        chunks = r.owners["default"].repository.load_chunks()
        assert len(chunks) == 1 and chunks[0].path == "same.md" and chunks[0].content == "replacement note"
        assert r.owners["space-1"].repository.load_chunks() == peer


def test_watcher_all_thirteen_evaluation_cases_have_exact_legacy_scores(tmp_path, monkeypatch):
    instances = []
    class LocalBackend(DeterministicConceptEmbedder):
        def __init__(self, *args, **kwargs):
            super().__init__()
            instances.append(self)

        def invalidate_embedding_compatibility_cache(self):
            pass

    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", LocalBackend)
    resource = SharedEmbeddingResource(Settings(
        semantic_model="vb022/deterministic-concepts", semantic_data_path=tmp_path / "shared-data",
    ))
    try:
        reference, _ = build_service(tmp_path / "reference")
        expected = run_evaluation(reference, load_cases())
        with rig(tmp_path / "watched", resource=resource, real=True, chunk_chars=250, chunk_overlap=0) as r:
            r.start()
            assert not instances
            for sid, service in r.owners.items():
                for source in CORPUS_ROOT.rglob("*.md"):
                    relative = source.relative_to(CORPUS_ROOT)
                    target = service.vault_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_text(encoding="utf-8").encode("utf-8"))
                    r.event(sid, relative.as_posix(), FileCreatedEvent)
            r.advance()
            r.delivered()
            r.scheduler.start()
            r.scheduler.wait(20)
            for service in r.owners.values():
                actual = run_evaluation(service, load_cases())
                assert actual == expected  # Every score and best chunk, not just ranks.
                assert len(actual) == 13 and all(outcome.rank == 1 for outcome in actual)
            assert len(instances) == 1
    finally:
        resource.close()
