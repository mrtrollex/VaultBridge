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
