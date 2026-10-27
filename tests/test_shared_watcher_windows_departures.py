"""Windows native removal ambiguity must reconcile indexed descendants."""

import sys
import threading

import pytest

from tests.test_semantic_resources import launch
from tests.test_semantic_resources import shared as shared
from tests.test_shared_filesystem_watcher import rig
from tests.test_shared_watcher_ownership_repairs import replacement
from tests.test_shared_watcher_repairs import use_production_observer

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="actual Windows native departure conversion")


def native_write_after_dispatch(r, sid, name, monkeypatch):
    """Windows ordered native creation marker (it does not emit close-write)."""
    handler = next(h for h in r.watcher._handlers if h._binding is r.bindings[sid])
    path = r.owners[sid].vault_root / name
    dispatch, arrived = handler.dispatch, threading.Event()

    def observed(event):
        dispatch(event)
        if event.src_path == str(path) and event.event_type == "created":
            arrived.set()

    with monkeypatch.context() as patch:
        patch.setattr(handler, "dispatch", observed)
        path.write_text("ordered native marker", encoding="utf-8")
        assert arrived.wait(5)


def test_original_windows_directory_move_out_reconciles_sqlite(tmp_path, monkeypatch, shared):
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        root, folder = owner.vault_root, owner.vault_root / "folder"
        (folder / "deep").mkdir(parents=True)
        (folder / "deep" / "note.md").write_text("authorized before departure", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        assert {c.path for c in owner.repository.load_chunks()} == {"folder/deep/note.md"}
        emitter = next(e for e in r.watcher._producers if e.watch.path == str(root))
        handler = next(h for h in r.watcher._handlers if h._binding is binding)
        read, dispatch, begin = emitter._read_events, handler.dispatch, owner.begin_sync
        reading, converted, full, release = (threading.Event() for _ in range(4))
        native, events = [], []

        def observed():
            reading.set()
            result = read()
            native.extend((e.action, e.src_path) for e in result if e.src_path == "folder")
            return result

        def delivered(event):
            dispatch(event)
            if event.src_path == str(folder) and event.event_type == "deleted":
                events.append((type(event).__name__, event.is_directory))
                converted.set()

        def blocked(**kwargs):
            if kwargs.get("relative_paths") is None:
                full.set()
                assert release.wait(15)
            return begin(**kwargs)

        monkeypatch.setattr(emitter, "_read_events", observed)
        monkeypatch.setattr(handler, "dispatch", delivered)
        monkeypatch.setattr(owner, "begin_sync", blocked)
        native_write_after_dispatch(r, "default", "armed.txt", monkeypatch)
        assert reading.wait(5)
        outside = tmp_path / "outside"
        folder.rename(outside)
        try:
            assert converted.wait(5)
            native_write_after_dispatch(r, "default", "ordered.txt", monkeypatch)
            before = r.scheduler.status(binding)
            rows = {c.path for c in owner.repository.load_chunks()}
            print(f"Windows native={native}, converted={events}, SQLite_before={rows}, "
                  f"debt={before.requires_full_sync}, full_started={full.is_set()}")
            assert events == [("FileDeletedEvent", False)]
            assert any(action == 2 for action, _ in native)  # FILE_ACTION_REMOVED, actual OS data.
            assert before.requires_full_sync and full.wait(5)
            assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        finally:
            release.set()
        r.advance(10)
        r.delivered()
        r.scheduler.wait(15)
        assert not owner.repository.load_chunks()
        print(f"Windows SQLite_after=[], debt={r.scheduler.status(binding).requires_full_sync}, "
              f"monitoring_failed={r.scheduler.status(binding).monitoring_failed}")
        assert not r.scheduler.status(binding).requires_full_sync
        assert not r.scheduler.status(binding).monitoring_failed
        (outside / "deep" / "external.md").write_text("outside", encoding="utf-8")
        native_write_after_dispatch(r, "default", "after-outside.txt", monkeypatch)
        assert not owner.repository.load_chunks()
        native_write_after_dispatch(r, "default", "survives.md", monkeypatch)
        r.advance(20)
        r.delivered()
        r.scheduler.wait(15)
        assert {c.path for c in owner.repository.load_chunks()} == {"survives.md"}


@pytest.mark.parametrize("change", ["delete-tree", "deep-outside", "file-md", "file-txt", "empty",
                                    "rename", "relocate", "into", "cross", "directory-md", "replace",
                                    "replace-file", "ambiguous", "burst", "excluded-to-visible"])
def test_native_departure_categories_reconcile_authorized_sqlite(tmp_path, monkeypatch, shared, change):
    from watchdog.events import FileDeletedEvent

    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        root, peer = owner.vault_root, r.owners["space-1"].vault_root
        folder = root / ("directory.md" if change == "directory-md" else "folder")
        source = tmp_path / "incoming" if change == "into" else folder
        if change == "excluded-to-visible":
            source = root / ".git" / "hidden"
        deep = source / "deep" / "deeper"
        deep.mkdir(parents=True)
        (deep / "one.md").write_text("first", encoding="utf-8")
        (source / "two.md").write_text("second", encoding="utf-8")
        if change == "empty":
            (deep / "one.md").unlink()
            (source / "two.md").unlink()
        if change == "file-txt":
            (root / "ordinary.txt").write_text("irrelevant content", encoding="utf-8")
        if change == "burst":
            for i in range(20):
                (root / f"extra-{i}").mkdir()
                (root / f"extra-{i}" / "gone.md").write_text("burst", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        begin, entered, release = owner.begin_sync, threading.Event(), threading.Event()
        fulls = []

        def held(**kwargs):
            if kwargs.get("relative_paths") is None:
                fulls.append(None)
                entered.set()
                assert release.wait(20)
            return begin(**kwargs)

        monkeypatch.setattr(owner, "begin_sync", held)
        expected = set()
        original_paths = {c.path for c in owner.repository.load_chunks()}
        target = root / "container" / "arrived" if change == "relocate" else root / "renamed"
        if change == "relocate":
            target.parent.mkdir()
        try:
            if change in ("delete-tree", "empty"):
                for path in (deep / "one.md", source / "two.md"):
                    path.unlink(missing_ok=True)
                deep.rmdir()
                deep.parent.rmdir()
                source.rmdir()
            elif change == "file-md":
                (deep / "one.md").unlink()
                expected = {(source / "two.md").relative_to(root).as_posix()}
            elif change == "file-txt":
                (root / "ordinary.txt").unlink()
                expected = original_paths
            elif change in ("rename", "relocate", "excluded-to-visible"):
                source.rename(target)
                expected = {f"{target.relative_to(root).as_posix()}/deep/deeper/one.md",
                            f"{target.relative_to(root).as_posix()}/two.md"}
            elif change == "into":
                source.rename(folder)
                expected = {"folder/deep/deeper/one.md", "folder/two.md"}
            elif change == "cross":
                source.rename(peer / "arrived")
            else:
                source.rename(tmp_path / "outside")
                if change == "replace":
                    (source / "replacement").mkdir(parents=True)
                    (source / "replacement" / "new.md").write_text("replacement", encoding="utf-8")
                    expected = {"folder/replacement/new.md"}
                elif change == "replace-file":
                    source.write_text("ordinary file occupies former directory name", encoding="utf-8")
                elif change == "ambiguous":
                    handler = next(h for h in r.watcher._handlers if h._binding is binding)
                    event = FileDeletedEvent(str(source))
                    assert not event.is_directory
                    handler.dispatch(event)
                elif change == "burst":
                    for i in range(20):
                        (root / f"extra-{i}").rename(tmp_path / f"departed-{i}")
            native_write_after_dispatch(r, "default", "ordered.txt", monkeypatch)
            assert entered.wait(5) and r.scheduler.status(binding).requires_full_sync
            if change == "cross":
                # Independent native handles have independent notification
                # ordering; establish the destination owner's boundary too.
                native_write_after_dispatch(r, "space-1", "peer-ordered.txt", monkeypatch)
            else:
                assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        finally:
            release.set()
        r.advance(10)
        r.delivered()
        r.scheduler.wait(15)
        with r.scheduler._condition:
            assert r.scheduler._condition.wait_for(lambda: not r.scheduler.status(binding).requires_full_sync, 10)
        assert {c.path for c in owner.repository.load_chunks()} == expected
        assert not r.scheduler.status(binding).monitoring_failed
        assert 1 <= len(fulls) <= 2  # One active and at most one coalesced follow-up.
        if change == "cross":
            with r.scheduler._condition:
                assert r.scheduler._condition.wait_for(
                    lambda: not r.scheduler.status(r.bindings["space-1"]).requires_full_sync, 10,
                )
            assert {c.path for c in r.owners["space-1"].repository.load_chunks()} == {
                "arrived/deep/deeper/one.md", "arrived/two.md",
            }
        native_write_after_dispatch(r, "default", "still.md", monkeypatch)
        r.advance(20)
        r.delivered()
        r.scheduler.wait(15)
        assert {c.path for c in owner.repository.load_chunks()} == expected | {"still.md"}


@pytest.mark.parametrize("mode", ["active-full", "cancel", "paused", "pressure", "stop", "process", "failure",
                                  "recovery"])
def test_departure_debt_survives_sync_cancellation_pressure_and_shutdown(tmp_path, monkeypatch, shared, mode):
    from app.services.knowledge_spaces import QualifiedPath
    from app.services.semantic_sync import SyncStepState

    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        folder = owner.vault_root / "folder"
        folder.mkdir()
        (folder / "gone.md").write_text("will depart", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        entered, release, second, release_second = (threading.Event() for _ in range(4))
        step, rounds = owner.sync_step, [0]

        def held(handle, cancel):
            result = step(handle, cancel)
            if handle.full and result.state is SyncStepState.COMPLETE:
                rounds[0] += 1
                gate, allow = (entered, release) if rounds[0] == 1 else (second, release_second)
                gate.set()
                assert allow.wait(20)
            return result

        monkeypatch.setattr(owner, "sync_step", held)
        r.scheduler.request_full_sync(binding)
        assert entered.wait(5)  # Its SQLite snapshot still contains folder/gone.md.
        if mode == "pressure":
            for i in range(2000):
                assert r.scheduler._debounce(r.watcher._token, binding,
                                            QualifiedPath(binding.space_id, f"pending-{i}.md"), deadline=100)
            assert r.scheduler.pending_count == 2000
        if mode in ("cancel", "paused"):
            r.scheduler.cancel(binding)
        if mode == "failure":
            r.watcher._ingress_failure(r.watcher._watches[0][0], "InjectedMonitoringFailure")
        if mode == "recovery":
            watch = r.watcher._watches[0][0]
            tickets = r.watcher._ingress_gap(watch)
        folder.rename(tmp_path / "outside")
        try:
            native_write_after_dispatch(r, "default", "ordered.txt", monkeypatch)
            assert r.scheduler.status(binding).requires_full_sync
            if mode in ("stop", "process"):
                if mode == "process":
                    r.scheduler.begin_shutdown()
                r.watcher.stop()
            release.set()
            if mode in ("active-full", "pressure", "failure", "recovery"):
                assert second.wait(5)
                assert r.scheduler.status(binding).requires_full_sync
                assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
                if mode == "recovery":
                    assert r.scheduler.status(binding).monitoring_recovering
                    r.watcher._ingress_restored(watch, tickets)
            elif mode in ("cancel", "paused"):
                r.scheduler.wait(15)
                assert r.scheduler.status(binding).requires_full_sync
                assert r.scheduler.retry(binding)
                assert second.wait(5)
                assert r.scheduler.status(binding).requires_full_sync
        finally:
            release.set()
            release_second.set()
        r.scheduler.wait(15)
        if mode in ("stop", "process", "failure"):
            assert r.scheduler.status(binding).requires_full_sync
            if mode == "process":
                assert not r.scheduler.request_full_sync(binding)
                return
            old = r.watcher
            old.stop()
            r.watcher = replacement(r)
            r.watcher.start()
            r.complete_native_startup()
            old._ingress_departures(old._watches[0][0] if old._watches else object(), ["late"])
        assert not owner.repository.load_chunks()
        assert not r.scheduler.status(binding).requires_full_sync
        assert r.scheduler.pending_count == 0
        if mode == "recovery":
            assert rounds[0] >= 3  # Neither pre-gap nor pre-restoration full certifies coverage.


@pytest.mark.parametrize("directory", [".git", ".obsidian", ".obsidian-chatgpt-data"])
def test_known_excluded_subtrees_do_not_generate_full_sync_loops(tmp_path, monkeypatch, directory):
    from app.services.vault import SEMANTIC_EXCLUDED_DIRECTORIES

    assert directory in SEMANTIC_EXCLUDED_DIRECTORIES
    with rig(tmp_path, 1) as r:
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        root = r.owners["default"].vault_root
        nested = root / directory / "cache"
        nested.mkdir(parents=True)
        for i in range(20):
            path = nested / f"temporary-{i}.txt"
            path.write_text("temporary", encoding="utf-8")
            path.unlink()
        nested.rename(root / directory / "renamed-cache")
        native_write_after_dispatch(r, "default", "ordered.txt", monkeypatch)
        launch(r.watcher._observer.event_queue.join).result(10)
        assert not r.scheduler.status(r.bindings["default"]).requires_full_sync
        assert not r.owners["default"].begins


def test_native_old_rename_without_new_record_still_requests_full(tmp_path, monkeypatch, shared):
    from watchdog.observers import winapi

    parse = winapi._parse_event_buffer
    seen, entered, release = threading.Event(), threading.Event(), threading.Event()

    def missing_new(data, size):
        result = []
        for action, name in parse(data, size):
            if action == winapi.FILE_ACTION_RENAMED_OLD_NAME and name == "folder":
                seen.set()
            if action != winapi.FILE_ACTION_RENAMED_NEW_NAME or name != "renamed":
                result.append((action, name))
        return result

    monkeypatch.setattr(winapi, "_parse_event_buffer", missing_new)
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        folder = owner.vault_root / "folder"
        folder.mkdir()
        (folder / "note.md").write_text("rename", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        r.watcher.start()
        r.complete_native_startup()
        begin = owner.begin_sync

        def held(**kwargs):
            if kwargs.get("relative_paths") is None:
                entered.set()
                assert release.wait(15)
            return begin(**kwargs)

        monkeypatch.setattr(owner, "begin_sync", held)
        folder.rename(owner.vault_root / "renamed")
        try:
            assert seen.wait(5) and entered.wait(5)
            native_write_after_dispatch(r, "default", "ordered.txt", monkeypatch)
            assert r.scheduler.status(binding).requires_full_sync
            assert not r.scheduler.status(r.bindings["space-1"]).requires_full_sync
        finally:
            release.set()
        r.advance(10)
        r.delivered()
        r.scheduler.wait(15)
        assert {c.path for c in owner.repository.load_chunks()} == {"renamed/note.md"}
        assert not r.scheduler.status(binding).requires_full_sync


def test_departure_during_native_startup_cannot_certify_early_sync(tmp_path, monkeypatch, shared):
    entered, release = threading.Event(), threading.Event()
    resource, _ = shared
    with rig(tmp_path, 2, real=True, resource=resource) as r:
        owner, binding = r.owners["default"], r.bindings["default"]
        folder = owner.vault_root / "folder"
        folder.mkdir()
        (folder / "note.md").write_text("during startup", encoding="utf-8")
        r.scheduler.start(startup=True)
        r.scheduler.wait(15)
        use_production_observer(r)
        validate = r.watcher._validate_binding

        def held(b):
            if (threading.current_thread().name == "departure-start" and r.watcher._producers
                    and b is binding):
                entered.set()
                assert release.wait(15)
            return validate(b)

        def start():
            threading.current_thread().name = "departure-start"
            r.watcher.start()

        monkeypatch.setattr(r.watcher, "_validate_binding", held)
        starting = launch(start)
        try:
            assert entered.wait(5)
            folder.rename(tmp_path / "outside")
            native_write_after_dispatch(r, "default", "ordered.txt", monkeypatch)
            r.scheduler.request_full_sync(binding)
            r.scheduler.wait(15)
            assert r.scheduler.status(binding).requires_full_sync
            assert r.scheduler.status(binding).monitoring_recovering
            assert not r.scheduler.status(binding).monitoring_failed
        finally:
            release.set()
        starting.result(15)
        r.complete_native_startup()
        assert not owner.repository.load_chunks()
        assert not r.scheduler.status(binding).requires_full_sync
