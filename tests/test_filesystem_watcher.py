import io
import logging
import os
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient
from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent

import app.main as main
from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.filesystem_watcher import (
    DebouncedPathQueue,
    FilesystemEventInterpreter,
    SemanticFilesystemWatcher,
)
from app.services.indexer import BackgroundSemanticIndexer
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService


class ConstantEmbedder:
    def embed(self, texts):
        return [np.array([1.0, 0.1], dtype=np.float32) for _ in texts]


class FakeObserver:
    def __init__(self):
        self.handler = None
        self.path = None
        self.recursive = None
        self.started = False
        self.stopped = False

    def schedule(self, event_handler, path, *, recursive):
        self.handler = event_handler
        self.path = path
        self.recursive = recursive

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return self.started and not self.stopped


class RecordingIndexer:
    def __init__(self, order=None):
        self.paths = []
        self.threads = []
        self.order = order

    def enqueue(self, path):
        self.paths.append(path)
        self.threads.append(threading.current_thread().name)
        return True

    def enqueue_paths(self, paths):
        self.paths.extend(paths)
        self.threads.extend(threading.current_thread().name for _path in paths)
        return len(paths)

    def start(self):
        if self.order is not None:
            self.order.append("indexer_start")
        return True

    def shutdown(self):
        if self.order is not None:
            self.order.append("indexer_stop")

    @property
    def requires_full_sync(self):
        return True

    @property
    def is_running(self):
        return False


class RecordingWatcher:
    def __init__(self, order):
        self.order = order

    def start(self):
        self.order.append("watcher_start")

    def stop(self):
        self.order.append("watcher_stop")


def make_interpreter(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault_service = VaultService(vault_root=vault_root, max_note_bytes=1_000_000)
    return (
        vault_root,
        FilesystemEventInterpreter(
            vault_service,
            semantic_data_path=vault_root / ".obsidian-chatgpt-data",
        ),
    )


def make_watcher(tmp_path, indexer=None):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    observer = FakeObserver()
    watcher = SemanticFilesystemWatcher(
        vault_service=VaultService(vault_root=vault_root, max_note_bytes=1_000_000),
        semantic_indexer=indexer or RecordingIndexer(),
        semantic_data_path=vault_root / ".obsidian-chatgpt-data",
        debounce_seconds=60,
        observer_factory=lambda: observer,
    )
    return vault_root, observer, watcher


def semantic_service(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(exist_ok=True)
    return SemanticSearchService(
        vault_root=vault_root,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        embedder=ConstantEmbedder(),
    )


def test_watcher_disabled_by_default_creates_no_watcher(tmp_path):
    settings = Settings(vault_path=tmp_path / "vault")
    order = []
    indexer = RecordingIndexer(order)
    application = main.create_app(
        settings=settings,
        semantic_search_service=semantic_service(tmp_path),
        semantic_indexer=indexer,
    )

    assert settings.semantic_watch_enabled is False
    assert application.state.semantic_watcher is None
    with TestClient(application) as client:
        assert client.get("/health/live").json() == {"ok": True}
        readiness = client.get("/health/ready")
        assert readiness.status_code == 503
        assert readiness.json() == {"ready": False}
        health = client.get("/health").json()
        assert "semantic_watcher_enabled" not in health
        assert "semantic_watcher_running" not in health
    assert order == ["indexer_start", "indexer_stop"]


def test_enabled_configuration_starts_watcher_and_shutdown_order(tmp_path):
    order = []
    settings = Settings(
        api_key="test-secret",
        vault_path=tmp_path / "vault",
        semantic_watch_enabled=True,
    )
    settings.vault_path.mkdir()
    application = main.create_app(
        settings=settings,
        semantic_search_service=semantic_service(tmp_path),
        semantic_indexer=RecordingIndexer(order),
        semantic_watcher=RecordingWatcher(order),
    )

    with TestClient(application):
        assert order == ["indexer_start", "watcher_start"]

    assert order == ["indexer_start", "watcher_start", "watcher_stop", "indexer_stop"]


def test_enabled_configuration_constructs_watcher(tmp_path, monkeypatch):
    constructed = []

    class ConstructedWatcher(RecordingWatcher):
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            super().__init__([])

    monkeypatch.setattr(main, "SemanticFilesystemWatcher", ConstructedWatcher)
    settings = Settings(vault_path=tmp_path, semantic_watch_enabled=True)
    application = main.create_app(
        settings=settings,
        semantic_search_service=semantic_service(tmp_path),
        semantic_indexer=RecordingIndexer(),
    )

    assert isinstance(application.state.semantic_watcher, ConstructedWatcher)
    assert constructed[0]["debounce_seconds"] == 1.0


@pytest.mark.parametrize("event_class", [FileCreatedEvent, FileModifiedEvent])
def test_created_and_modified_markdown_are_interpreted(tmp_path, event_class):
    vault_root, interpreter = make_interpreter(tmp_path)
    note = vault_root / "Folder" / "Note.md"
    note.parent.mkdir()
    note.write_text("Body", encoding="utf-8")

    assert interpreter.paths_for_event(event_class(str(note))) == ("Folder/Note.md",)


def test_deleted_markdown_is_interpreted_after_file_disappears(tmp_path):
    vault_root, interpreter = make_interpreter(tmp_path)

    assert interpreter.paths_for_event(FileDeletedEvent(str(vault_root / "Gone.md"))) == (
        "Gone.md",
    )


def test_rename_keeps_old_and_new_markdown_paths(tmp_path):
    vault_root, interpreter = make_interpreter(tmp_path)
    old_path = vault_root / "Old.md"
    new_path = vault_root / "New.md"
    new_path.write_text("New body", encoding="utf-8")

    assert interpreter.paths_for_event(FileMovedEvent(str(old_path), str(new_path))) == (
        "Old.md",
        "New.md",
    )


def test_non_markdown_internal_semantic_data_and_temp_paths_are_ignored(tmp_path):
    vault_root, interpreter = make_interpreter(tmp_path)
    paths = [
        vault_root / "note.txt",
        vault_root / ".obsidian" / "internal.md",
        vault_root / ".obsidian-chatgpt-data" / "derived.md",
        vault_root / ".#draft.md",
        vault_root / "~$draft.md",
    ]

    assert all(interpreter.paths_for_event(FileCreatedEvent(str(path))) == () for path in paths)


def test_outside_and_external_symlink_events_are_rejected(tmp_path):
    vault_root, interpreter = make_interpreter(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("External", encoding="utf-8")

    assert interpreter.paths_for_event(FileCreatedEvent(str(outside))) == ()

    link = vault_root / "linked.md"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    assert interpreter.paths_for_event(FileCreatedEvent(str(link))) == ()


def test_disappearing_file_race_remains_safe_targeted_work(tmp_path):
    vault_root, interpreter = make_interpreter(tmp_path)
    note = vault_root / "Race.md"
    note.write_text("Body", encoding="utf-8")
    note.unlink()

    assert interpreter.paths_for_event(FileCreatedEvent(str(note))) == ("Race.md",)


def test_debounce_coalesces_repeated_paths_and_preserves_different_paths():
    queue = DebouncedPathQueue(1.0)

    assert queue.add(["a.md"], now=10.0) is True
    assert queue.add(["a.md", "b.md"], now=10.5) is True
    assert queue.pop_ready(now=11.0) == ()
    assert queue.pop_ready(now=11.5) == ("a.md", "b.md")


@pytest.mark.parametrize("debounce_seconds", [0.0, -1.0, float("inf"), float("nan")])
def test_debounce_rejects_non_positive_or_non_finite_windows(debounce_seconds):
    with pytest.raises(ValueError, match="positive"):
        DebouncedPathQueue(debounce_seconds)


def test_debounce_preserves_both_rename_sides():
    queue = DebouncedPathQueue(1.0)

    queue.add(["old.md", "new.md"], now=1.0)
    queue.add(["new.md"], now=1.5)

    assert queue.pop_ready(now=2.0) == ("old.md",)
    assert queue.pop_ready(now=2.5) == ("new.md",)


def test_callback_only_dispatches_from_debounce_worker(tmp_path):
    indexer = RecordingIndexer()
    vault_root, observer, watcher = make_watcher(tmp_path, indexer)
    note = vault_root / "Note.md"
    note.write_text("Body", encoding="utf-8")
    watcher.start()

    observer.handler.on_created(FileCreatedEvent(str(note)))
    assert indexer.paths == []
    watcher.stop()

    assert indexer.paths == ["Note.md"]
    assert indexer.threads == ["vaultbridge-watch-dispatch"]


def test_events_after_shutdown_are_ignored(tmp_path):
    indexer = RecordingIndexer()
    vault_root, observer, watcher = make_watcher(tmp_path, indexer)
    note = vault_root / "Note.md"
    note.write_text("Body", encoding="utf-8")
    watcher.start()
    watcher.stop()

    observer.handler.on_modified(FileModifiedEvent(str(note)))

    assert indexer.paths == []


def test_watcher_start_failure_stops_dispatcher(tmp_path):
    class FailingObserver(FakeObserver):
        def start(self):
            raise RuntimeError("observer startup failure")

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    watcher = SemanticFilesystemWatcher(
        vault_service=VaultService(vault_root=vault_root, max_note_bytes=1_000_000),
        semantic_indexer=RecordingIndexer(),
        semantic_data_path=vault_root / ".obsidian-chatgpt-data",
        debounce_seconds=1.0,
        observer_factory=FailingObserver,
    )

    with pytest.raises(RuntimeError, match="observer startup failure"):
        watcher.start()
    assert watcher._dispatcher.submit(["Note.md"]) is False


def test_api_and_watcher_enqueues_coalesce_in_existing_indexer(tmp_path):
    full_started = threading.Event()
    release_full = threading.Event()
    targeted_calls = []

    def full_sync(_cancel_event):
        full_started.set()
        release_full.wait(timeout=5)
        return {"indexed": 0, "unchanged": 0, "removed": 0}

    def sync_paths(paths, _cancel_event):
        targeted_calls.append(tuple(paths))
        return {"indexed": len(paths), "unchanged": 0, "removed": 0}

    indexer = BackgroundSemanticIndexer(full_sync, sync_paths)
    vault_root, observer, watcher = make_watcher(tmp_path, indexer)
    note = vault_root / "Note.md"
    note.write_text("Body", encoding="utf-8")
    try:
        indexer.start()
        assert full_started.wait(timeout=2)
        assert indexer.enqueue("Note.md") is True
        watcher.start()
        observer.handler.on_modified(FileModifiedEvent(str(note)))
        watcher.stop()
        assert indexer.queued_paths == ("Note.md",)

        release_full.set()
        indexer.wait(timeout=2)
        assert targeted_calls == [("Note.md",)]
    finally:
        release_full.set()
        watcher.stop()
        indexer.shutdown()


def test_watcher_rename_removes_old_semantic_row_and_indexes_new_path(tmp_path):
    service = semantic_service(tmp_path)
    old_path = service.vault_root / "Old.md"
    new_path = service.vault_root / "New.md"
    old_path.write_text("Original body", encoding="utf-8")
    service.sync()
    old_path.rename(new_path)
    indexer = BackgroundSemanticIndexer(service.sync, service.sync_paths)
    observer = FakeObserver()
    watcher = SemanticFilesystemWatcher(
        vault_service=VaultService(vault_root=service.vault_root, max_note_bytes=1_000_000),
        semantic_indexer=indexer,
        semantic_data_path=tmp_path / "data",
        debounce_seconds=60,
        observer_factory=lambda: observer,
    )
    try:
        watcher.start()
        observer.handler.on_moved(FileMovedEvent(str(old_path), str(new_path)))
        watcher.stop()

        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 1}
        assert [chunk.path for chunk in service.repository.load_chunks()] == ["New.md"]
    finally:
        watcher.stop()
        indexer.shutdown()


def test_watcher_lifecycle_logs_do_not_include_note_content(tmp_path):
    indexer = RecordingIndexer()
    vault_root, observer, watcher = make_watcher(tmp_path, indexer)
    private_content = "private-note-body-must-not-be-logged"
    note = vault_root / "Note.md"
    note.write_text(private_content, encoding="utf-8")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(event)s %(message)s"))
    watcher_logger = logging.getLogger("vaultbridge.filesystem_watcher")
    watcher_logger.addHandler(handler)
    try:
        watcher.start()
        observer.handler.on_created(FileCreatedEvent(str(note)))
        watcher.stop()
    finally:
        watcher_logger.removeHandler(handler)

    output = stream.getvalue()
    assert private_content not in output
    assert "semantic_watcher_started" in output
    assert "semantic_watcher_stopped" in output
