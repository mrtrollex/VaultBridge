import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings
from app.repositories.semantic import SemanticRepository, StoredChunk, StoredNote
from app.services.semantic_search import (
    INDEX_STATE_METADATA_KEY,
    LAST_SUCCESSFUL_SYNC_METADATA_KEY,
    SemanticSearchService,
    SynchronizationCancelledError,
)
from app.services.vault import VaultService


class ConstantEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [np.array([1.0, 0.1], dtype=np.float32) for _ in texts]


class FailingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, _texts):
        self.calls += 1
        raise RuntimeError("health test embedding failure")


class BlockingEmbedder(ConstantEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def embed(self, texts):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("health test embedding was not released")
        return super().embed(texts)


class StatusOnlyIndexer:
    def __init__(self, *, running: bool = False, full_sync_required: bool = False) -> None:
        self._running = running
        self._full_sync_required = full_sync_required
        self.start_calls = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def requires_full_sync(self) -> bool:
        return self._full_sync_required

    def start(self) -> bool:
        self.start_calls += 1
        return False

    def shutdown(self) -> None:
        return None


def health_client(
    tmp_path: Path,
    *,
    vault_root: Path | None = None,
    embedder=None,
    semantic_indexer=None,
    index_batch_size: int = 25,
    max_note_bytes: int = 1_000_000,
) -> tuple[TestClient, SemanticSearchService]:
    default_vault_root = tmp_path / "vault"
    vault_root = vault_root or default_vault_root
    if vault_root == default_vault_root:
        vault_root.mkdir(exist_ok=True)
    settings = Settings(
        api_key="test-secret",
        vault_path=vault_root,
        semantic_data_path=tmp_path / "data",
        max_note_bytes=max_note_bytes,
    )
    repository = SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3")
    service = SemanticSearchService(
        vault_root=settings.vault_path,
        repository=repository,
        model_name="example/model",
        max_note_bytes=settings.max_note_bytes,
        chunk_chars=300,
        chunk_overlap=50,
        index_batch_size=index_batch_size,
        embedder=embedder or ConstantEmbedder(),
    )
    application = main.create_app(
        settings=settings,
        semantic_search_service=service,
        semantic_indexer=semantic_indexer,
        vault_service=VaultService(
            vault_root=settings.vault_path,
            max_note_bytes=settings.max_note_bytes,
        ),
    )
    return TestClient(application), service


def assert_legacy_fields(payload, *, vault_exists, ready):
    assert payload["ok"] is True
    assert payload["vault_exists"] is vault_exists
    assert payload["semantic_index_ready"] is ready


def test_health_reports_uninitialized_state_without_embedder_sync_or_storage_mutation(tmp_path):
    embedder = FailingEmbedder()
    indexer = StatusOnlyIndexer()
    client, service = health_client(tmp_path, embedder=embedder, semantic_indexer=indexer)
    (service.vault_root / "note.md").write_text("Markdown source.", encoding="utf-8")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "vault_exists": True,
        "semantic_index_ready": False,
        "semantic_index_state": "uninitialized",
        "semantic_search_available": False,
        "semantic_indexer_running": False,
        "full_sync_required": False,
        "indexed_notes": 0,
        "semantic_chunks": 0,
        "vault_notes": 1,
        "last_successful_sync": None,
    }
    assert embedder.calls == 0
    assert indexer.start_calls == 0
    assert service.repository.db_path.exists() is False
    assert service._state_initialized is False


def test_health_reports_initial_indexing_as_unavailable(tmp_path):
    embedder = BlockingEmbedder()
    client, service = health_client(tmp_path, embedder=embedder)
    (service.vault_root / "note.md").write_text("Initial semantic content.", encoding="utf-8")

    with client:
        try:
            assert embedder.started.wait(timeout=2)
            payload = client.get("/health").json()

            assert_legacy_fields(payload, vault_exists=True, ready=False)
            assert payload["semantic_index_state"] == "indexing"
            assert payload["semantic_search_available"] is False
            assert payload["semantic_indexer_running"] is True
            assert payload["full_sync_required"] is True
            readiness = client.get("/health/ready")
            assert readiness.status_code == 503
            assert readiness.json() == {"ready": False}
            assert client.get("/health/live").json() == {"ok": True}
        finally:
            embedder.release.set()
        client.app.state.semantic_indexer.wait(timeout=2)


def test_health_reports_ready_index_counts_and_last_successful_full_sync(tmp_path):
    client, service = health_client(tmp_path)
    (service.vault_root / "short.md").write_text("Short semantic content.", encoding="utf-8")
    (service.vault_root / "long.md").write_text(
        f"{'a' * 200}\n\n{'b' * 200}\n\n{'c' * 200}",
        encoding="utf-8",
    )

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        payload = client.get("/health").json()
        readiness = client.get("/health/ready")
        liveness = client.get("/health/live")

    assert_legacy_fields(payload, vault_exists=True, ready=True)
    assert payload["semantic_index_state"] == "ready"
    assert payload["semantic_search_available"] is True
    assert payload["semantic_indexer_running"] is False
    assert payload["full_sync_required"] is False
    assert payload["indexed_notes"] == 2
    assert payload["semantic_chunks"] == len(service.repository.load_chunks())
    assert payload["semantic_chunks"] > 0
    assert payload["vault_notes"] == 2
    assert payload["last_successful_sync"].endswith("+00:00")
    assert readiness.status_code == 200
    assert readiness.json() == {"ready": True}
    assert liveness.status_code == 200
    assert liveness.json() == {"ok": True}


def test_vault_notes_matches_full_sync_eligibility_without_reading_contents(tmp_path):
    client, service = health_client(tmp_path, max_note_bytes=20)
    normal = service.vault_root / "normal.md"
    normal.write_text("Eligible.", encoding="utf-8")
    for directory_name in (".obsidian", ".trash", ".git", ".obsidian-chatgpt-data"):
        internal = service.vault_root / directory_name
        internal.mkdir()
        (internal / "internal.md").write_text("Excluded.", encoding="utf-8")
    (service.vault_root / "oversized.md").write_text("x" * 21, encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("Outside.", encoding="utf-8")
    try:
        (service.vault_root / "escaping.md").symlink_to(outside)
    except OSError:
        pass

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        payload = client.get("/health").json()

    assert payload["vault_notes"] == 1
    assert payload["indexed_notes"] == 1
    assert {chunk.path for chunk in service.repository.load_chunks()} == {"normal.md"}


def test_health_reports_initial_index_failure_and_full_sync_debt(tmp_path):
    embedder = FailingEmbedder()
    client, service = health_client(tmp_path, embedder=embedder)
    (service.vault_root / "note.md").write_text("Initial semantic content.", encoding="utf-8")

    with client:
        with pytest.raises(RuntimeError, match="health test embedding failure"):
            client.app.state.semantic_indexer.wait(timeout=2)
        payload = client.get("/health").json()
        readiness = client.get("/health/ready")
        liveness = client.get("/health/live")

    assert_legacy_fields(payload, vault_exists=True, ready=False)
    assert payload["semantic_index_state"] == "error"
    assert payload["semantic_search_available"] is False
    assert payload["semantic_indexer_running"] is False
    assert payload["full_sync_required"] is True
    assert payload["last_successful_sync"] is None
    assert readiness.status_code == 503
    assert readiness.json() == {"ready": False}
    assert liveness.status_code == 200
    assert liveness.json() == {"ok": True}


def test_health_reports_failed_full_refresh_as_searchable_with_recovery_debt(tmp_path):
    client, service = health_client(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("Original semantic content.", encoding="utf-8")

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        successful_sync = "2026-08-23T12:00:00+00:00"
        service.repository.set_metadata(LAST_SUCCESSFUL_SYNC_METADATA_KEY, successful_sync)
        note.write_text("Changed semantic content.", encoding="utf-8")
        service.embedder = FailingEmbedder()

        assert client.app.state.semantic_indexer.start() is True
        with pytest.raises(RuntimeError, match="health test embedding failure"):
            client.app.state.semantic_indexer.wait(timeout=2)
        payload = client.get("/health").json()
        readiness = client.get("/health/ready")

    assert payload["semantic_index_state"] == "error"
    assert payload["semantic_index_ready"] is False
    assert payload["semantic_search_available"] is True
    assert payload["semantic_indexer_running"] is False
    assert payload["full_sync_required"] is True
    assert payload["indexed_notes"] == 1
    assert payload["semantic_chunks"] == 1
    assert payload["last_successful_sync"] == successful_sync
    assert readiness.status_code == 200
    assert readiness.json() == {"ready": True}


def test_health_reports_active_full_refresh_with_previous_index_available(tmp_path):
    client, service = health_client(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("Original semantic content.", encoding="utf-8")

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        embedder = BlockingEmbedder()
        service.embedder = embedder
        note.write_text("Changed semantic content for a full refresh.", encoding="utf-8")
        assert client.app.state.semantic_indexer.start() is True
        try:
            assert embedder.started.wait(timeout=2)
            payload = client.get("/health").json()

            assert payload["semantic_index_state"] == "indexing"
            assert payload["semantic_index_ready"] is False
            assert payload["semantic_search_available"] is True
            assert payload["semantic_indexer_running"] is True
            assert payload["full_sync_required"] is True
            readiness = client.get("/health/ready")
            assert readiness.status_code == 200
            assert readiness.json() == {"ready": True}
        finally:
            embedder.release.set()
        client.app.state.semantic_indexer.wait(timeout=2)


def test_health_reports_targeted_indexing_without_full_sync_debt(tmp_path):
    client, service = health_client(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("Original semantic content.", encoding="utf-8")

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        embedder = BlockingEmbedder()
        service.embedder = embedder
        note.write_text("Targeted semantic content.", encoding="utf-8")
        assert client.app.state.semantic_indexer.enqueue("note.md") is True
        try:
            assert embedder.started.wait(timeout=2)
            payload = client.get("/health").json()

            assert payload["semantic_index_state"] == "indexing"
            assert payload["semantic_index_ready"] is False
            assert payload["semantic_search_available"] is True
            assert payload["semantic_indexer_running"] is True
            assert payload["full_sync_required"] is False
            readiness = client.get("/health/ready")
            assert readiness.status_code == 200
            assert readiness.json() == {"ready": True}
        finally:
            embedder.release.set()
        client.app.state.semantic_indexer.wait(timeout=2)


def test_targeted_refresh_does_not_change_last_successful_full_sync(tmp_path):
    client, service = health_client(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("Original semantic content.", encoding="utf-8")

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        service.repository.set_metadata(LAST_SUCCESSFUL_SYNC_METADATA_KEY, "2026-08-23T12:00:00+00:00")
        note.write_text("Targeted semantic content.", encoding="utf-8")
        client.app.state.semantic_indexer.enqueue("note.md")
        client.app.state.semantic_indexer.wait(timeout=2)

        assert client.get("/health").json()["last_successful_sync"] == (
            "2026-08-23T12:00:00+00:00"
        )


def test_health_during_shutdown_and_cancelled_partial_full_sync_preserves_timestamp(tmp_path):
    client, service = health_client(tmp_path, index_batch_size=1)
    first = service.vault_root / "first.md"
    second = service.vault_root / "second.md"
    first.write_text("First original semantic content.", encoding="utf-8")
    second.write_text("Second original semantic content.", encoding="utf-8")

    with client:
        indexer = client.app.state.semantic_indexer
        indexer.wait(timeout=2)
        previous_sync = "2026-08-23T12:00:00+00:00"
        service.repository.set_metadata(LAST_SUCCESSFUL_SYNC_METADATA_KEY, previous_sync)
        first.write_text("First changed semantic content for cancellation.", encoding="utf-8")
        second.write_text("Second changed semantic content for cancellation.", encoding="utf-8")
        embedder = BlockingEmbedder()
        service.embedder = embedder
        assert indexer.start() is True
        assert embedder.started.wait(timeout=2)

        shutdown_thread = threading.Thread(target=indexer.shutdown)
        shutdown_thread.start()
        try:
            assert indexer._cancel_event.wait(timeout=2)
            during_shutdown = client.get("/health").json()
            assert during_shutdown["semantic_index_state"] == "indexing"
            assert during_shutdown["semantic_search_available"] is True
            assert during_shutdown["semantic_indexer_running"] is True
            assert during_shutdown["full_sync_required"] is True
        finally:
            embedder.release.set()
        shutdown_thread.join(timeout=2)

        assert shutdown_thread.is_alive() is False
        assert isinstance(indexer.last_error, SynchronizationCancelledError)
        after_shutdown = client.get("/health").json()
        assert after_shutdown["semantic_index_state"] == "error"
        assert after_shutdown["semantic_search_available"] is True
        assert after_shutdown["last_successful_sync"] == previous_sync


def test_health_reports_unavailable_vault_without_changing_application_health(tmp_path):
    missing_vault = tmp_path / "missing-vault"
    client, _service = health_client(
        tmp_path,
        vault_root=missing_vault,
        semantic_indexer=StatusOnlyIndexer(),
    )

    payload = client.get("/health").json()
    readiness = client.get("/health/ready")
    liveness = client.get("/health/live")

    assert_legacy_fields(payload, vault_exists=False, ready=False)
    assert payload["vault_notes"] == 0
    assert payload["semantic_index_state"] == "uninitialized"
    assert readiness.status_code == 503
    assert readiness.json() == {"ready": False}
    assert liveness.status_code == 200
    assert liveness.json() == {"ok": True}


def test_health_and_readiness_do_not_mutate_persisted_or_in_memory_lifecycle_state(tmp_path):
    client, service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())
    service.repository.prepare_index(service.index_signature)
    service.repository.set_metadata(INDEX_STATE_METADATA_KEY, "uninitialized")
    service.repository.set_metadata(
        LAST_SUCCESSFUL_SYNC_METADATA_KEY,
        "2026-08-23T12:00:00+00:00",
    )

    assert service._state_initialized is False
    assert client.get("/health").json()["semantic_index_state"] == "uninitialized"
    readiness = client.get("/health/ready")

    assert readiness.status_code == 503
    assert readiness.json() == {"ready": False}
    assert service.repository.get_metadata(INDEX_STATE_METADATA_KEY) == "uninitialized"
    assert service.repository.get_metadata(LAST_SUCCESSFUL_SYNC_METADATA_KEY) == (
        "2026-08-23T12:00:00+00:00"
    )
    assert service._state_initialized is False


def test_health_treats_incompatible_stored_counts_as_uninitialized_without_invalidating_them(tmp_path):
    client, service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())
    service.repository.prepare_index("v1|different/model|300|50")
    service.repository.set_metadata(INDEX_STATE_METADATA_KEY, "ready")
    service.repository.set_metadata(LAST_SUCCESSFUL_SYNC_METADATA_KEY, "2026-08-23T12:00:00+00:00")
    with service.repository.transaction() as session:
        session.replace_note(
            StoredNote(
                path="old.md",
                mtime_ns=1,
                size=3,
                sha256="old",
                indexed_at="2026-08-23T12:00:00+00:00",
            ),
            [
                StoredChunk(
                    path="old.md",
                    chunk_index=0,
                    heading=None,
                    content="Old",
                    embedding=np.array([1.0, 0.1], dtype=np.float32).tobytes(),
                    dimensions=2,
                )
            ],
        )

    payload = client.get("/health").json()

    assert payload["semantic_index_state"] == "uninitialized"
    assert payload["semantic_search_available"] is False
    assert payload["indexed_notes"] == 0
    assert payload["semantic_chunks"] == 0
    assert payload["last_successful_sync"] is None
    assert service.repository.read_status().indexed_notes == 1
    assert service._state_initialized is False


def test_ready_health_becomes_unavailable_when_semantic_database_is_removed(tmp_path):
    client, service = health_client(tmp_path)

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        assert client.get("/health").json()["semantic_search_available"] is True
        service.repository.db_path.unlink()

        payload = client.get("/health").json()
        readiness = client.get("/health/ready")

    assert payload["semantic_index_state"] == "uninitialized"
    assert payload["semantic_search_available"] is False
    assert payload["semantic_index_ready"] is False
    assert readiness.status_code == 503
    assert readiness.json() == {"ready": False}


def test_ready_health_becomes_unavailable_when_semantic_database_is_corrupt(tmp_path):
    client, service = health_client(tmp_path)

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        assert client.get("/health").json()["semantic_search_available"] is True
        service.repository.db_path.write_bytes(b"not a sqlite database")

        response = client.get("/health")
        readiness = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["semantic_index_state"] == "error"
    assert response.json()["semantic_search_available"] is False
    assert response.json()["semantic_index_ready"] is False
    assert "not a database" not in response.text
    assert readiness.status_code == 503
    assert readiness.json() == {"ready": False}
    assert "not a database" not in readiness.text


def test_ready_health_becomes_unavailable_when_index_signature_is_incompatible(tmp_path):
    client, service = health_client(tmp_path)

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        assert client.get("/health").json()["semantic_search_available"] is True
        service.repository.set_metadata("index_signature", "v1|different/model|300|50")

        payload = client.get("/health").json()
        readiness = client.get("/health/ready")

    assert payload["semantic_index_state"] == "uninitialized"
    assert payload["semantic_search_available"] is False
    assert payload["semantic_index_ready"] is False
    assert payload["indexed_notes"] == 0
    assert payload["semantic_chunks"] == 0
    assert payload["last_successful_sync"] is None
    assert readiness.status_code == 503
    assert readiness.json() == {"ready": False}


def test_health_handles_unreadable_uninitialized_storage_without_exposing_errors(tmp_path):
    client, service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())
    service.repository.db_path.parent.mkdir(parents=True)
    service.repository.db_path.write_bytes(b"not a sqlite database")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["semantic_index_state"] == "error"
    assert response.json()["semantic_search_available"] is False
    assert "not a database" not in response.text


def test_health_operation_id_and_path_remain_compatible():
    operation = main.app.openapi()["paths"]["/health"]["get"]

    assert operation["operationId"] == "healthCheck"


def test_liveness_is_public_minimal_and_does_not_touch_runtime_dependencies(tmp_path, monkeypatch):
    client, service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())

    def unexpected_call():
        raise AssertionError("liveness touched a runtime dependency")

    monkeypatch.setattr(service, "health_status", unexpected_call)
    monkeypatch.setattr(service, "probe_search_availability", unexpected_call)
    monkeypatch.setattr(client.app.state.vault_service, "vault_exists", unexpected_call)
    monkeypatch.setattr(client.app.state.vault_service, "vault_available", unexpected_call)
    monkeypatch.setattr(client.app.state.vault_service, "count_notes", unexpected_call)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert service.repository.db_path.exists() is False
    assert service._state_initialized is False


def test_readiness_is_public_minimal_and_side_effect_free_when_uninitialized(tmp_path, monkeypatch):
    embedder = FailingEmbedder()
    indexer = StatusOnlyIndexer()
    client, service = health_client(tmp_path, embedder=embedder, semantic_indexer=indexer)
    vault_service = client.app.state.vault_service

    def unexpected_call():
        raise AssertionError("readiness used an expensive status path")

    monkeypatch.setattr(service.repository, "read_status", unexpected_call)
    monkeypatch.setattr(vault_service, "count_notes", unexpected_call)
    monkeypatch.setattr(service, "sync", unexpected_call)
    monkeypatch.setattr(service, "sync_paths", unexpected_call)
    monkeypatch.setattr(service, "search", unexpected_call)
    monkeypatch.setattr(Path, "rglob", unexpected_call)
    monkeypatch.setattr(Path, "read_text", unexpected_call)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"ready": False}
    assert embedder.calls == 0
    assert indexer.start_calls == 0
    assert service.repository.db_path.exists() is False
    assert service._state_initialized is False


def test_readiness_short_circuits_semantic_storage_when_vault_is_missing(tmp_path, monkeypatch):
    missing_vault = tmp_path / "missing-vault"
    client, service = health_client(
        tmp_path,
        vault_root=missing_vault,
        semantic_indexer=StatusOnlyIndexer(),
    )

    def unexpected_call():
        raise AssertionError("readiness checked semantic storage for a missing vault")

    monkeypatch.setattr(service, "probe_search_availability", unexpected_call)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"ready": False}


def test_readiness_accepts_compatible_legacy_index_without_persisted_state(tmp_path):
    source_client, service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())
    (service.vault_root / "note.md").write_text("TrueNAS backup storage.", encoding="utf-8")
    service.sync()
    with sqlite3.connect(service.repository.db_path) as connection:
        connection.execute("DELETE FROM meta WHERE key=?", (INDEX_STATE_METADATA_KEY,))
        connection.commit()

    restarted = SemanticSearchService(
        vault_root=service.vault_root,
        repository=SemanticRepository(service.repository.db_path),
        model_name=service.model_name,
        max_note_bytes=service.max_note_bytes,
        chunk_chars=service.chunk_chars,
        chunk_overlap=service.chunk_overlap,
        index_batch_size=service.index_batch_size,
        embedder=ConstantEmbedder(),
    )
    application = main.create_app(
        settings=source_client.app.state.settings,
        semantic_search_service=restarted,
        semantic_indexer=StatusOnlyIndexer(),
        vault_service=source_client.app.state.vault_service,
    )
    client = TestClient(application)

    health = client.get("/health")
    readiness = client.get("/health/ready")

    assert health.status_code == 200
    assert health.json()["semantic_search_available"] is True
    assert readiness.status_code == 200
    assert readiness.json() == {"ready": True}
    assert restarted.repository.get_metadata(INDEX_STATE_METADATA_KEY) is None
    assert restarted._state_initialized is False


def test_readiness_rejects_regular_file_vault_without_changing_rich_health(tmp_path):
    vault_file = tmp_path / "vault-file"
    vault_file.write_text("not a directory", encoding="utf-8")
    client, service = health_client(
        tmp_path,
        vault_root=vault_file,
        semantic_indexer=StatusOnlyIndexer(),
    )
    service.repository.prepare_index(service.index_signature)
    service.repository.set_metadata(INDEX_STATE_METADATA_KEY, "ready")

    health = client.get("/health")
    readiness = client.get("/health/ready")
    liveness = client.get("/health/live")

    assert health.status_code == 200
    assert health.json()["vault_exists"] is True
    assert readiness.status_code == 503
    assert readiness.json() == {"ready": False}
    assert liveness.status_code == 200
    assert liveness.json() == {"ok": True}


@pytest.mark.parametrize(
    "error",
    [PermissionError("vault denied"), OSError("vault metadata unavailable")],
)
def test_readiness_returns_503_for_vault_availability_errors(tmp_path, monkeypatch, error):
    client, _service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())
    vault_root = client.app.state.vault_service.vault_root
    original_is_dir = type(vault_root).is_dir

    def unavailable(path):
        if path == vault_root:
            raise error
        return original_is_dir(path)

    monkeypatch.setattr(type(vault_root), "is_dir", unavailable)

    readiness = client.get("/health/ready")
    liveness = client.get("/health/live")

    assert readiness.status_code == 503
    assert readiness.json() == {"ready": False}
    assert liveness.status_code == 200
    assert liveness.json() == {"ok": True}


@pytest.mark.parametrize(
    "error",
    [PermissionError("semantic path denied"), OSError("semantic path unavailable")],
)
def test_readiness_returns_503_for_semantic_path_errors(tmp_path, monkeypatch, error):
    client, service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())
    service.repository.prepare_index(service.index_signature)
    service.repository.set_metadata(INDEX_STATE_METADATA_KEY, "ready")
    db_path = service.repository.db_path
    original_exists = type(db_path).exists

    def unavailable(path):
        if path == db_path:
            raise error
        return original_exists(path)

    monkeypatch.setattr(type(db_path), "exists", unavailable)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"ready": False}


def test_readiness_returns_503_for_sqlite_operational_error(tmp_path, monkeypatch):
    client, service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())
    service.repository.prepare_index(service.index_signature)

    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("semantic database unavailable")

    monkeypatch.setattr("app.repositories.semantic.sqlite3.connect", unavailable)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"ready": False}


def test_readiness_preserves_unexpected_sqlite_programming_error_boundary(tmp_path, monkeypatch):
    client, service = health_client(tmp_path, semantic_indexer=StatusOnlyIndexer())
    service.repository.prepare_index(service.index_signature)

    def fail(*_args, **_kwargs):
        raise sqlite3.ProgrammingError("invalid readiness implementation")

    monkeypatch.setattr("app.repositories.semantic.sqlite3.connect", fail)

    with pytest.raises(sqlite3.ProgrammingError, match="invalid readiness implementation"):
        client.get("/health/ready")


def test_probe_operation_ids_and_response_contracts_are_stable():
    schema = main.app.openapi()

    assert schema["paths"]["/health/live"]["get"]["operationId"] == "livenessCheck"
    assert schema["paths"]["/health/ready"]["get"]["operationId"] == "readinessCheck"
    assert set(schema["paths"]["/health/ready"]["get"]["responses"]) == {"200", "503"}
