import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.indexer import BackgroundSemanticIndexer
from app.services.semantic_search import (
    IndexState,
    SemanticSearchService,
    SynchronizationCancelledError,
)


class ConstantEmbedder:
    def embed(self, texts):
        return [np.array([1.0, 0.1], dtype=np.float32) for _ in texts]


class BlockingSync:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, _cancel_event):
        self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("test synchronization was not released")
        return {"indexed": 0, "unchanged": 0, "removed": 0}


def test_application_startup_is_non_blocking_and_shutdown_waits_for_sync(tmp_path):
    blocking_sync = BlockingSync()
    indexer = BackgroundSemanticIndexer(blocking_sync)
    settings = Settings(api_key="test-secret", vault_path=tmp_path)
    service = SemanticSearchService(
        vault_root=settings.vault_path,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        embedder=ConstantEmbedder(),
    )
    application = main.create_app(
        settings=settings,
        semantic_search_service=service,
        semantic_indexer=indexer,
    )
    startup_completed = threading.Event()
    allow_shutdown = threading.Event()
    shutdown_completed = threading.Event()
    errors = []

    def run_lifespan():
        try:
            with TestClient(application):
                startup_completed.set()
                allow_shutdown.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
        finally:
            shutdown_completed.set()

    lifespan_thread = threading.Thread(target=run_lifespan)
    lifespan_thread.start()
    try:
        assert blocking_sync.started.wait(timeout=2)
        assert startup_completed.wait(timeout=2)
        assert indexer.is_running is True

        allow_shutdown.set()
        assert shutdown_completed.wait(timeout=0.1) is False

        blocking_sync.release.set()
        assert shutdown_completed.wait(timeout=2)
        lifespan_thread.join(timeout=2)
        assert errors == []
        assert lifespan_thread.is_alive() is False
        assert blocking_sync.calls == 1
    finally:
        allow_shutdown.set()
        blocking_sync.release.set()
        lifespan_thread.join(timeout=2)


def test_ordinary_http_requests_continue_during_full_rebuild(tmp_path):
    blocking_sync = BlockingSync()
    indexer = BackgroundSemanticIndexer(blocking_sync)
    settings = Settings(api_key="test-secret", vault_path=tmp_path)
    service = SemanticSearchService(
        vault_root=settings.vault_path,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        embedder=ConstantEmbedder(),
    )
    application = main.create_app(
        settings=settings,
        semantic_search_service=service,
        semantic_indexer=indexer,
    )

    with TestClient(application) as client:
        try:
            assert blocking_sync.started.wait(timeout=2)
            assert indexer.is_running is True

            liveness = client.get("/health/live")
            ordinary_request = client.get(
                "/api/v1/notes/list",
                headers={"Authorization": "Bearer test-secret"},
            )

            assert liveness.status_code == 200
            assert liveness.json() == {"ok": True}
            assert ordinary_request.status_code == 200
            assert ordinary_request.json() == {"folder": "", "notes": []}
            assert indexer.is_running is True
        finally:
            blocking_sync.release.set()


def test_background_indexer_prevents_duplicate_jobs(tmp_path):
    blocking_sync = BlockingSync()
    indexer = BackgroundSemanticIndexer(blocking_sync)
    try:
        assert indexer.start() is True
        assert blocking_sync.started.wait(timeout=2)
        assert indexer.start() is False
        assert blocking_sync.calls == 1
    finally:
        blocking_sync.release.set()
        indexer.shutdown()


def test_repeated_writes_are_coalesced_behind_one_active_full_sync():
    blocking_sync = BlockingSync()
    targeted_calls = []
    targeted_started = threading.Event()

    def sync_paths(paths, _cancel_event):
        targeted_calls.append(tuple(paths))
        targeted_started.set()
        return {"indexed": len(paths), "unchanged": 0, "removed": 0}

    indexer = BackgroundSemanticIndexer(blocking_sync, sync_paths)
    try:
        assert indexer.start() is True
        assert blocking_sync.started.wait(timeout=2)

        assert indexer.enqueue("Folder/Note.md") is True
        assert indexer.enqueue("Folder\\Note.md") is False
        assert indexer.enqueue("Folder/./Note.md") is False
        with pytest.raises(ValueError, match="vault-relative"):
            indexer.enqueue("/Folder/Note.md")
        with pytest.raises(ValueError, match="vault-relative"):
            indexer.enqueue("../Folder/Note.md")
        assert indexer.queued_paths == ("Folder/Note.md",)
        assert targeted_started.wait(timeout=0.1) is False
        assert blocking_sync.calls == 1

        blocking_sync.release.set()
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}
        assert targeted_calls == [("Folder/Note.md",)]
        assert blocking_sync.calls == 1
    finally:
        blocking_sync.release.set()
        indexer.shutdown()


def test_enqueue_submission_failure_retains_path_for_later_retry(monkeypatch):
    targeted_calls = []

    def sync(_cancel_event):
        return {"indexed": 0, "unchanged": 0, "removed": 0}

    def sync_paths(paths, _cancel_event):
        targeted_calls.append(tuple(paths))
        return {"indexed": len(paths), "unchanged": 0, "removed": 0}

    indexer = BackgroundSemanticIndexer(sync, sync_paths)
    original_submit = indexer._executor.submit

    def fail_submit(*_args, **_kwargs):
        raise RuntimeError("executor submission failed")

    monkeypatch.setattr(indexer._executor, "submit", fail_submit)
    try:
        assert indexer.enqueue("Folder/Note.md") is True
        assert indexer.queued_paths == ("Folder/Note.md",)
        assert isinstance(indexer.last_error, RuntimeError)

        monkeypatch.setattr(indexer._executor, "submit", original_submit)
        assert indexer.enqueue("Folder/Note.md") is False
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}
        assert targeted_calls == [("Folder/Note.md",)]
    finally:
        indexer.shutdown()


def test_write_during_failing_full_sync_schedules_one_full_retry():
    first_started = threading.Event()
    release_first = threading.Event()
    retry_started = threading.Event()
    release_retry = threading.Event()
    full_calls = 0
    targeted_calls = []

    def sync(_cancel_event):
        nonlocal full_calls
        full_calls += 1
        if full_calls == 1:
            first_started.set()
            if not release_first.wait(timeout=5):
                raise RuntimeError("first full synchronization was not released")
            raise RuntimeError("first full synchronization failed")
        retry_started.set()
        if not release_retry.wait(timeout=5):
            raise RuntimeError("full retry was not released")
        return {"indexed": 1, "unchanged": 0, "removed": 0}

    def sync_paths(paths, _cancel_event):
        targeted_calls.append(tuple(paths))

    indexer = BackgroundSemanticIndexer(sync, sync_paths)
    try:
        assert indexer.start() is True
        assert first_started.wait(timeout=2)
        assert indexer.enqueue("Folder/Note.md") is True

        release_first.set()
        assert retry_started.wait(timeout=2)
        assert full_calls == 2
        assert targeted_calls == []
        assert indexer.requires_full_sync is True

        release_retry.set()
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}
        assert indexer.requires_full_sync is False
        assert indexer.queued_paths == ()
    finally:
        release_first.set()
        release_retry.set()
        indexer.shutdown()


def test_failed_follow_up_does_not_create_an_immediate_retry_loop():
    first_started = threading.Event()
    release_first = threading.Event()
    retry_started = threading.Event()
    release_retry = threading.Event()
    unexpected_third_started = threading.Event()
    retry_completed = threading.Event()
    full_calls = 0

    def sync(_cancel_event):
        nonlocal full_calls
        full_calls += 1
        if full_calls == 1:
            first_started.set()
            if not release_first.wait(timeout=5):
                raise RuntimeError("first full synchronization was not released")
        elif full_calls == 2:
            retry_started.set()
            if not release_retry.wait(timeout=5):
                raise RuntimeError("full retry was not released")
        else:
            unexpected_third_started.set()
        raise RuntimeError(f"full synchronization attempt {full_calls} failed")

    indexer = BackgroundSemanticIndexer(sync, lambda _paths, _cancel_event: None)
    original_record_completion = indexer._record_completion
    completed_jobs = 0

    def record_completion(future):
        nonlocal completed_jobs
        original_record_completion(future)
        completed_jobs += 1
        if completed_jobs == 2:
            retry_completed.set()

    indexer._record_completion = record_completion
    try:
        assert indexer.start() is True
        assert first_started.wait(timeout=2)
        assert indexer.enqueue("Folder/Note.md") is True

        release_first.set()
        assert retry_started.wait(timeout=2)
        with indexer._lock:
            retry_future = indexer._future
        assert retry_future is not None

        release_retry.set()
        assert retry_completed.wait(timeout=2)
        with pytest.raises(RuntimeError, match="attempt 2 failed"):
            retry_future.result()
        assert unexpected_third_started.wait(timeout=0.1) is False
        assert full_calls == 2
        assert indexer.is_running is False
        assert indexer.requires_full_sync is True
        assert indexer.queued_paths == ("Folder/Note.md",)
    finally:
        release_first.set()
        release_retry.set()
        indexer.shutdown()


def test_shutdown_discards_queued_work_after_active_sync_returns():
    blocking_sync = BlockingSync()
    targeted_calls = []

    def sync_paths(paths, _cancel_event):
        targeted_calls.append(tuple(paths))

    indexer = BackgroundSemanticIndexer(blocking_sync, sync_paths)
    assert indexer.start() is True
    assert blocking_sync.started.wait(timeout=2)
    assert indexer.enqueue("Folder/Note.md") is True

    shutdown_thread = threading.Thread(target=indexer.shutdown)
    shutdown_thread.start()
    try:
        assert shutdown_thread.is_alive() is True
        blocking_sync.release.set()
        shutdown_thread.join(timeout=2)
        assert shutdown_thread.is_alive() is False
        assert targeted_calls == []
        assert indexer.queued_paths == ()
        with pytest.raises(RuntimeError, match="shut down"):
            indexer.start()
    finally:
        blocking_sync.release.set()
        shutdown_thread.join(timeout=2)


def test_next_application_startup_recovers_markdown_dropped_from_shutdown_queue(tmp_path):
    blocking_sync = BlockingSync()
    targeted_calls = []

    def sync_paths(paths, _cancel_event):
        targeted_calls.append(tuple(paths))

    first_indexer = BackgroundSemanticIndexer(blocking_sync, sync_paths)
    first_indexer.start()
    assert blocking_sync.started.wait(timeout=2)
    note = tmp_path / "Recovered.md"
    note.write_text("Durable semantic content.", encoding="utf-8")
    assert first_indexer.enqueue("Recovered.md") is True

    shutdown_thread = threading.Thread(target=first_indexer.shutdown)
    shutdown_thread.start()
    blocking_sync.release.set()
    shutdown_thread.join(timeout=2)
    assert shutdown_thread.is_alive() is False
    assert targeted_calls == []

    settings = Settings(api_key="test-secret", vault_path=tmp_path)
    service = SemanticSearchService(
        vault_root=tmp_path,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        embedder=ConstantEmbedder(),
    )
    application = main.create_app(settings=settings, semantic_search_service=service)

    with TestClient(application):
        assert application.state.semantic_indexer.wait(timeout=2) == {
            "indexed": 1,
            "unchanged": 0,
            "removed": 0,
        }
        assert service.repository.load_chunks()[0].path == "Recovered.md"


def test_background_sync_failure_sets_error_and_can_be_retried(tmp_path):
    class FailingEmbedder:
        def embed(self, texts):
            raise RuntimeError("background embedding failure")

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "note.md").write_text("Semantic content.", encoding="utf-8")
    service = SemanticSearchService(
        vault_root=vault_root,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        embedder=FailingEmbedder(),
    )
    indexer = BackgroundSemanticIndexer(service.sync)
    try:
        assert indexer.start() is True
        with pytest.raises(RuntimeError, match="background embedding failure"):
            indexer.wait(timeout=2)
        assert service.state is IndexState.ERROR
        assert isinstance(indexer.last_error, RuntimeError)

        service.embedder = ConstantEmbedder()
        assert indexer.start() is True
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}
        assert service.state is IndexState.READY
        assert indexer.last_error is None
    finally:
        indexer.shutdown()


def test_shutdown_stops_multi_batch_sync_after_active_batch(tmp_path):
    class BoundaryBlockingEmbedder(ConstantEmbedder):
        def __init__(self):
            self.calls = 0
            self.second_call_started = threading.Event()
            self.release = threading.Event()

        def embed(self, texts):
            self.calls += 1
            if self.calls == 2:
                self.second_call_started.set()
                if not self.release.wait(timeout=5):
                    raise RuntimeError("test batch was not released")
            return super().embed(texts)

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    for index in range(5):
        (vault_root / f"note-{index}.md").write_text(
            f"Semantic content {index}.",
            encoding="utf-8",
        )
    embedder = BoundaryBlockingEmbedder()
    service = SemanticSearchService(
        vault_root=vault_root,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        embedder=embedder,
        index_batch_size=2,
    )
    observed_cancel_events = []

    def sync(cancel_event):
        observed_cancel_events.append(cancel_event)
        return service.sync(cancel_event)

    indexer = BackgroundSemanticIndexer(sync)
    indexer.start()
    assert embedder.second_call_started.wait(timeout=2)
    shutdown_thread = threading.Thread(target=indexer.shutdown)
    shutdown_thread.start()
    try:
        assert observed_cancel_events[0].wait(timeout=2)
        assert shutdown_thread.is_alive() is True
        embedder.release.set()
        shutdown_thread.join(timeout=2)
        assert shutdown_thread.is_alive() is False
        assert embedder.calls == 2
        assert len(service.repository.load_chunks()) == 2
        assert service.state is IndexState.ERROR
        assert isinstance(indexer.last_error, SynchronizationCancelledError)
    finally:
        embedder.release.set()
        shutdown_thread.join(timeout=2)
