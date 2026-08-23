import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from app.repositories.semantic import SemanticRepository
from app.services.indexer import BackgroundSemanticIndexer
from app.services.semantic_search import (
    IndexState,
    SemanticSearchService,
    SemanticSearchUnavailableError,
    TargetedSynchronizationError,
)


class FakeEmbedder:
    def embed(self, texts):
        vectors = []
        for text in texts:
            value = text.casefold()
            vectors.append(
                np.array(
                    [
                        sum(
                            word in value
                            for word in ("truenas", "nas", "storage", "backup", "server")
                        ),
                        sum(
                            word in value
                            for word in ("oracle", "apex", "pl/sql", "database", "ords")
                        ),
                        sum(word in value for word in ("music", "audio", "navidrome", "song")),
                        0.1,
                    ],
                    dtype=np.float32,
                )
            )
        return vectors


class TrackingSession:
    def __init__(self, session):
        self._session = session
        self.mutations = 0

    def __getattr__(self, name):
        return getattr(self._session, name)

    def delete_note(self, path):
        self.mutations += 1
        return self._session.delete_note(path)

    def update_note_metadata(self, **kwargs):
        self.mutations += 1
        return self._session.update_note_metadata(**kwargs)

    def replace_note(self, note, chunks):
        self.mutations += 1
        return self._session.replace_note(note, chunks)


class TrackingRepository(SemanticRepository):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.committed_batch_sizes = []

    @contextmanager
    def transaction(self):
        with super().transaction() as session:
            tracking_session = TrackingSession(session)
            yield tracking_session
        if tracking_session.mutations:
            self.committed_batch_sizes.append(tracking_session.mutations)


def semantic_service(
    tmp_path,
    *,
    embedder=None,
    model_name: str = "example/model",
    chunk_chars: int = 300,
    chunk_overlap: int = 50,
    index_batch_size: int = 25,
    repository=None,
) -> SemanticSearchService:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(exist_ok=True)
    return SemanticSearchService(
        vault_root=vault_root,
        repository=repository or SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        model_name=model_name,
        max_note_bytes=1_000_000,
        chunk_chars=chunk_chars,
        chunk_overlap=chunk_overlap,
        index_batch_size=index_batch_size,
        embedder=embedder or FakeEmbedder(),
    )


def test_state_distinguishes_missing_storage_from_ready_empty_index(tmp_path):
    service = semantic_service(tmp_path)

    assert service.state is IndexState.UNINITIALIZED
    assert service.is_storage_initialized() is False
    assert service.is_ready() is False

    assert service.sync() == {"indexed": 0, "unchanged": 0, "removed": 0}
    assert service.is_storage_initialized() is True
    assert service.state is IndexState.READY
    assert service.is_ready() is True

    restarted = semantic_service(tmp_path)
    assert restarted.state is IndexState.READY


def test_sync_exposes_indexing_state_and_persists_ready(tmp_path):
    observed_states = []
    service = semantic_service(tmp_path)

    class ObservingEmbedder(FakeEmbedder):
        def embed(self, texts):
            observed_states.append(service.state)
            return super().embed(texts)

    service.embedder = ObservingEmbedder()
    (service.vault_root / "note.md").write_text("TrueNAS backup storage.", encoding="utf-8")

    service.sync()

    assert observed_states == [IndexState.INDEXING]
    assert service.state is IndexState.READY
    assert service.repository.get_metadata("index_state") == "ready"


def test_sync_failure_persists_error_and_a_later_sync_can_recover(tmp_path):
    class FailingEmbedder:
        def embed(self, texts):
            raise RuntimeError("deterministic embedding failure")

    service = semantic_service(tmp_path, embedder=FailingEmbedder())
    (service.vault_root / "note.md").write_text("TrueNAS backup storage.", encoding="utf-8")

    with pytest.raises(RuntimeError, match="deterministic embedding failure"):
        service.sync()

    assert service.state is IndexState.ERROR
    assert service.repository.get_metadata("index_state") == "error"

    restarted = semantic_service(tmp_path)
    assert restarted.state is IndexState.ERROR
    restarted.sync()
    assert restarted.state is IndexState.READY


def test_restart_converts_interrupted_indexing_state_to_error(tmp_path):
    service = semantic_service(tmp_path)
    service.repository.prepare_index(service.index_signature)
    service.repository.set_metadata("index_state", "indexing")

    restarted = semantic_service(tmp_path)

    assert restarted.state is IndexState.ERROR
    assert restarted.repository.get_metadata("index_state") == "error"


def test_signature_mismatch_invalidates_index_and_resets_state(tmp_path):
    service = semantic_service(tmp_path)
    (service.vault_root / "note.md").write_text("TrueNAS backup storage.", encoding="utf-8")
    service.sync()
    assert service.state is IndexState.READY

    changed = semantic_service(tmp_path, model_name="different/model")

    assert changed.state is IndexState.UNINITIALIZED
    assert changed.is_storage_initialized() is True
    assert changed.repository.load_chunks() == []

    changed.sync()
    assert changed.state is IndexState.READY


def test_legacy_compatible_index_without_state_is_inferred_ready_once(tmp_path):
    service = semantic_service(tmp_path)
    (service.vault_root / "note.md").write_text("TrueNAS backup storage.", encoding="utf-8")
    service.sync()
    connection = sqlite3.connect(service.db_path)
    try:
        connection.execute("DELETE FROM meta WHERE key='index_state'")
        connection.commit()
    finally:
        connection.close()

    restarted = semantic_service(tmp_path)

    assert restarted.state is IndexState.READY
    assert restarted.repository.get_metadata("index_state") == "ready"


def test_sync_indexes_incrementally_without_loading_real_model(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "TrueNAS backup.md"
    note.write_text("Backup storage on my NAS.", encoding="utf-8")

    assert service.sync() == {"indexed": 1, "unchanged": 0, "removed": 0}
    assert service.sync() == {"indexed": 0, "unchanged": 1, "removed": 0}

    original = note.stat()
    os.utime(note, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000))
    assert service.sync() == {"indexed": 0, "unchanged": 1, "removed": 0}

    note.write_text("Backup storage on my NAS and server.", encoding="utf-8")
    assert service.sync() == {"indexed": 1, "unchanged": 0, "removed": 0}


def test_sync_paths_refreshes_only_requested_notes(tmp_path):
    service = semantic_service(tmp_path)
    first = service.vault_root / "first.md"
    second = service.vault_root / "second.md"
    first.write_text("TrueNAS backup original first.", encoding="utf-8")
    second.write_text("Oracle APEX original second.", encoding="utf-8")
    service.sync()

    first.write_text("Navidrome music refreshed first.", encoding="utf-8")
    second.write_text("Navidrome music not requested second.", encoding="utf-8")

    assert service.sync_paths(["first.md"]) == {
        "indexed": 1,
        "unchanged": 0,
        "removed": 0,
    }
    chunks = {chunk.path: chunk.content for chunk in service.repository.load_chunks()}
    assert chunks["first.md"] == "Navidrome music refreshed first."
    assert chunks["second.md"] == "Oracle APEX original second."


def test_sync_paths_rejects_vault_escape(tmp_path):
    service = semantic_service(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("Content outside the vault.", encoding="utf-8")
    service.sync()

    with pytest.raises(TargetedSynchronizationError, match="Invalid targeted note path"):
        service.sync_paths(["../outside.md"])
    assert service.state is IndexState.ERROR
    assert service.repository.load_chunks() == []


def test_queued_target_failure_keeps_previous_index_and_retries_safely(tmp_path):
    class FailingEmbedder:
        def embed(self, texts):
            raise RuntimeError("targeted embedding failure")

    service = semantic_service(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("TrueNAS backup original.", encoding="utf-8")
    service.sync()
    original_chunks = service.repository.load_chunks()

    note.write_text("Navidrome music replacement.", encoding="utf-8")
    service.embedder = FailingEmbedder()
    indexer = BackgroundSemanticIndexer(service.sync, service.sync_paths)
    try:
        assert indexer.enqueue("note.md") is True
        with pytest.raises(RuntimeError, match="targeted embedding failure"):
            indexer.wait(timeout=2)

        assert service.state is IndexState.ERROR
        assert service.is_search_available() is True
        assert service.repository.load_chunks() == original_chunks
        assert indexer.queued_paths == ("note.md",)

        service.embedder = FakeEmbedder()
        assert indexer.enqueue("note.md") is False
        assert indexer.wait(timeout=2) == {
            "indexed": 1,
            "unchanged": 0,
            "removed": 0,
        }
        assert service.state is IndexState.READY
        assert "Navidrome music replacement." in service.repository.load_chunks()[0].content
    finally:
        indexer.shutdown()


def test_full_sync_failure_debt_forces_full_retry_before_ready(tmp_path, monkeypatch):
    service = semantic_service(tmp_path, index_batch_size=1)
    baseline = service.vault_root / "baseline.md"
    baseline.write_text("TrueNAS baseline content.", encoding="utf-8")
    service.sync()

    first = service.vault_root / "first.md"
    second = service.vault_root / "second.md"
    first.write_text("Oracle first committed batch.", encoding="utf-8")
    second.write_text("Navidrome second failed batch.", encoding="utf-8")
    ordered_files = [first, second, baseline]
    monkeypatch.setattr(service, "_eligible_files", lambda *_args: list(ordered_files))

    class FailOnSecondEmbedding(FakeEmbedder):
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("full synchronization failed")
            return super().embed(texts)

    targeted_calls = []

    def tracked_sync_paths(paths, cancel_event):
        targeted_calls.append(tuple(paths))
        return service.sync_paths(paths, cancel_event)

    service.embedder = FailOnSecondEmbedding()
    indexer = BackgroundSemanticIndexer(service.sync, tracked_sync_paths)
    try:
        assert indexer.start() is True
        with pytest.raises(RuntimeError, match="full synchronization failed"):
            indexer.wait(timeout=2)

        chunks = {chunk.path: chunk.content for chunk in service.repository.load_chunks()}
        assert "first committed batch" in chunks["first.md"]
        assert "second.md" not in chunks
        assert service.state is IndexState.ERROR
        assert service.is_search_available() is True
        assert indexer.requires_full_sync is True

        retry_started = threading.Event()
        release_retry = threading.Event()

        class BlockingRetryEmbedder(FakeEmbedder):
            def embed(self, texts):
                retry_started.set()
                if not release_retry.wait(timeout=5):
                    raise RuntimeError("full retry was not released")
                return super().embed(texts)

        service.embedder = BlockingRetryEmbedder()
        queued = service.vault_root / "queued.md"
        queued.write_text("Queued write during recovery debt.", encoding="utf-8")
        ordered_files.append(queued)
        assert indexer.enqueue("queued.md") is True
        assert retry_started.wait(timeout=2)
        assert service.state is IndexState.INDEXING
        assert indexer.requires_full_sync is True
        assert targeted_calls == []

        release_retry.set()
        result = indexer.wait(timeout=2)
        assert result == {"indexed": 2, "unchanged": 2, "removed": 0}
        assert targeted_calls == []
        assert service.state is IndexState.READY
        assert indexer.requires_full_sync is False
        assert {chunk.path for chunk in service.repository.load_chunks()} == {
            "baseline.md",
            "first.md",
            "queued.md",
            "second.md",
        }
    finally:
        if "release_retry" in locals():
            release_retry.set()
        indexer.shutdown()


def test_write_during_active_target_refresh_eventually_indexes_newest_content(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("Original content.", encoding="utf-8")
    service.sync()

    embed_started = threading.Event()
    release_embed = threading.Event()

    class FirstRefreshBlockingEmbedder(FakeEmbedder):
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 1:
                embed_started.set()
                if not release_embed.wait(timeout=5):
                    raise RuntimeError("target refresh was not released")
            return super().embed(texts)

    targeted_calls = []

    def tracked_sync_paths(paths, cancel_event):
        targeted_calls.append(tuple(paths))
        return service.sync_paths(paths, cancel_event)

    service.embedder = FirstRefreshBlockingEmbedder()
    indexer = BackgroundSemanticIndexer(service.sync, tracked_sync_paths)
    try:
        note.write_text("First targeted revision.", encoding="utf-8")
        assert indexer.enqueue("note.md") is True
        assert embed_started.wait(timeout=2)

        note.write_text("Newest targeted revision after active work.", encoding="utf-8")
        assert indexer.enqueue("note.md") is True
        release_embed.set()
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}

        assert targeted_calls == [("note.md",), ("note.md",)]
        chunks = service.repository.load_chunks()
        assert len(chunks) == 1
        assert chunks[0].content == "Newest targeted revision after active work."
    finally:
        release_embed.set()
        indexer.shutdown()


def test_write_during_failing_target_refresh_automatically_indexes_newest_content(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("Original content.", encoding="utf-8")
    service.sync()

    first_started = threading.Event()
    release_first = threading.Event()
    retry_started = threading.Event()

    class FailFirstRefreshEmbedder(FakeEmbedder):
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 1:
                first_started.set()
                if not release_first.wait(timeout=5):
                    raise RuntimeError("first target refresh was not released")
                raise RuntimeError("first target refresh failed")
            retry_started.set()
            return super().embed(texts)

    targeted_calls = []

    def tracked_sync_paths(paths, cancel_event):
        targeted_calls.append(tuple(paths))
        return service.sync_paths(paths, cancel_event)

    service.embedder = FailFirstRefreshEmbedder()
    indexer = BackgroundSemanticIndexer(service.sync, tracked_sync_paths)
    try:
        note.write_text("First targeted revision.", encoding="utf-8")
        assert indexer.enqueue("note.md") is True
        assert first_started.wait(timeout=2)

        note.write_text("Newest revision queued during the failing refresh.", encoding="utf-8")
        assert indexer.enqueue("note.md") is True
        release_first.set()

        assert retry_started.wait(timeout=2)
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}
        assert targeted_calls == [("note.md",), ("note.md",)]
        assert service.state is IndexState.READY
        assert service.repository.load_chunks()[0].content == (
            "Newest revision queued during the failing refresh."
        )
    finally:
        release_first.set()
        indexer.shutdown()


def test_write_after_full_processed_path_gets_follow_up_target_refresh(tmp_path, monkeypatch):
    service = semantic_service(tmp_path, index_batch_size=1)
    note = service.vault_root / "note.md"
    note.write_text("Original content.", encoding="utf-8")
    service.sync()

    blocker = service.vault_root / "zz-blocker.md"
    blocker.write_text("Block full synchronization here.", encoding="utf-8")
    note.write_text("Revision processed by full synchronization.", encoding="utf-8")
    monkeypatch.setattr(service, "_eligible_files", lambda *_args: [note, blocker])

    blocker_started = threading.Event()
    release_blocker = threading.Event()

    class FullBlockingEmbedder(FakeEmbedder):
        def embed(self, texts):
            if any("Block full synchronization here" in text for text in texts):
                blocker_started.set()
                if not release_blocker.wait(timeout=5):
                    raise RuntimeError("full synchronization was not released")
            return super().embed(texts)

    targeted_calls = []

    def tracked_sync_paths(paths, cancel_event):
        targeted_calls.append(tuple(paths))
        return service.sync_paths(paths, cancel_event)

    service.embedder = FullBlockingEmbedder()
    indexer = BackgroundSemanticIndexer(service.sync, tracked_sync_paths)
    try:
        assert indexer.start() is True
        assert blocker_started.wait(timeout=2)

        note.write_text("Newest revision written after full processed the path.", encoding="utf-8")
        assert indexer.enqueue("note.md") is True
        release_blocker.set()
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}

        assert targeted_calls == [("note.md",)]
        chunks = {chunk.path: chunk.content for chunk in service.repository.load_chunks()}
        assert chunks["note.md"] == "Newest revision written after full processed the path."
    finally:
        release_blocker.set()
        indexer.shutdown()


def test_targeted_read_oserror_requeues_and_preserves_previous_index(tmp_path, monkeypatch):
    service = semantic_service(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("TrueNAS original searchable content.", encoding="utf-8")
    service.sync()
    original_chunks = service.repository.load_chunks()
    note.write_text("Navidrome replacement content.", encoding="utf-8")

    original_open = Path.open

    def fail_note_read(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == note and mode == "r":
            raise OSError("simulated targeted read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_note_read)
    indexer = BackgroundSemanticIndexer(service.sync, service.sync_paths)
    try:
        assert indexer.enqueue("note.md") is True
        with pytest.raises(TargetedSynchronizationError, match="could not be read"):
            indexer.wait(timeout=2)
        assert service.state is IndexState.ERROR
        assert service.repository.load_chunks() == original_chunks
        assert service.search("TrueNAS", min_score=0.0)
        assert indexer.queued_paths == ("note.md",)

        monkeypatch.setattr(Path, "open", original_open)
        assert indexer.enqueue("note.md") is False
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}
        assert service.repository.load_chunks()[0].content == "Navidrome replacement content."
    finally:
        indexer.shutdown()


def test_missing_target_requeues_and_preserves_previous_index(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("Original indexed content.", encoding="utf-8")
    service.sync()
    original_chunks = service.repository.load_chunks()
    note.unlink()

    indexer = BackgroundSemanticIndexer(service.sync, service.sync_paths)
    try:
        assert indexer.enqueue("note.md") is True
        with pytest.raises(TargetedSynchronizationError, match="unavailable"):
            indexer.wait(timeout=2)
        assert service.state is IndexState.ERROR
        assert service.repository.load_chunks() == original_chunks
        assert indexer.queued_paths == ("note.md",)

        note.write_text("Recovered newest content.", encoding="utf-8")
        assert indexer.enqueue("note.md") is False
        assert indexer.wait(timeout=2) == {"indexed": 1, "unchanged": 0, "removed": 0}
        assert service.repository.load_chunks()[0].content == "Recovered newest content."
    finally:
        indexer.shutdown()


def test_targeted_decode_failure_keeps_prior_batches_and_retries_remainder(tmp_path):
    service = semantic_service(tmp_path, index_batch_size=1)
    first = service.vault_root / "first.md"
    second = service.vault_root / "second.md"
    first.write_text("First original.", encoding="utf-8")
    second.write_text("Second original.", encoding="utf-8")
    service.sync()

    first.write_text("First committed replacement.", encoding="utf-8")
    second.write_bytes(b"\xff\xfe")
    with pytest.raises(TargetedSynchronizationError, match="could not be read"):
        service.sync_paths(["first.md", "second.md"])

    chunks = {chunk.path: chunk.content for chunk in service.repository.load_chunks()}
    assert chunks == {
        "first.md": "First committed replacement.",
        "second.md": "Second original.",
    }
    assert service.state is IndexState.ERROR
    assert service.is_search_available() is True

    second.write_text("Second recovered replacement.", encoding="utf-8")
    assert service.sync_paths(["first.md", "second.md"]) == {
        "indexed": 1,
        "unchanged": 1,
        "removed": 0,
    }
    assert service.state is IndexState.READY
    chunks = {chunk.path: chunk.content for chunk in service.repository.load_chunks()}
    assert chunks == {
        "first.md": "First committed replacement.",
        "second.md": "Second recovered replacement.",
    }


def test_sync_paths_rejects_symlink_escape(tmp_path):
    service = semantic_service(tmp_path)
    service.sync()
    outside = tmp_path / "outside.md"
    outside.write_text("Outside semantic content.", encoding="utf-8")
    link = service.vault_root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(TargetedSynchronizationError, match="inaccessible"):
        service.sync_paths(["linked.md"])
    assert service.state is IndexState.ERROR
    assert service.repository.load_chunks() == []


def test_full_sync_never_indexes_symlinks_outside_the_vault(tmp_path):
    class RecordingEmbedder(FakeEmbedder):
        def __init__(self):
            self.indexed_texts = []

        def embed(self, texts):
            self.indexed_texts.extend(texts)
            return super().embed(texts)

    embedder = RecordingEmbedder()
    service = semantic_service(tmp_path, embedder=embedder)
    normal = service.vault_root / "normal.md"
    normal.write_text("TrueNAS safe in-vault content.", encoding="utf-8")
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("External file secret must not be indexed.", encoding="utf-8")
    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    (outside_directory / "directory-secret.md").write_text(
        "External directory secret must not be indexed.",
        encoding="utf-8",
    )
    external_file_link = service.vault_root / "external-file.md"
    external_directory_link = service.vault_root / "external-directory"
    internal_link = service.vault_root / "internal-link.md"
    try:
        external_file_link.symlink_to(outside_file)
        external_directory_link.symlink_to(outside_directory, target_is_directory=True)
        internal_link.symlink_to(normal)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    assert service.sync() == {"indexed": 1, "unchanged": 0, "removed": 0}
    chunks = service.repository.load_chunks()
    assert [(chunk.path, chunk.content) for chunk in chunks] == [
        ("normal.md", "TrueNAS safe in-vault content.")
    ]
    assert len(embedder.indexed_texts) == 1
    assert "External file secret" not in embedder.indexed_texts[0]
    assert "External directory secret" not in embedder.indexed_texts[0]

    assert service.sync_paths(["internal-link.md"]) == {
        "indexed": 0,
        "unchanged": 1,
        "removed": 0,
    }
    results = service.search("external secret", min_score=0.0)
    assert all("External" not in result.snippet for result in results)


def test_sync_commits_successful_work_in_configured_batches(tmp_path):
    repository = TrackingRepository(tmp_path / "data" / "semantic-index.sqlite3")
    service = semantic_service(
        tmp_path,
        index_batch_size=2,
        repository=repository,
    )
    for index in range(5):
        (service.vault_root / f"note-{index}.md").write_text(
            f"TrueNAS backup storage {index}.",
            encoding="utf-8",
        )

    assert service.sync() == {"indexed": 5, "unchanged": 0, "removed": 0}
    assert repository.committed_batch_sizes == [2, 2, 1]
    assert len(repository.load_chunks()) == 5


def test_sync_paths_preserves_configured_batch_commits(tmp_path):
    repository = TrackingRepository(tmp_path / "data" / "semantic-index.sqlite3")
    service = semantic_service(
        tmp_path,
        index_batch_size=2,
        repository=repository,
    )
    paths = []
    for index in range(5):
        path = service.vault_root / f"note-{index}.md"
        path.write_text(f"TrueNAS backup original {index}.", encoding="utf-8")
        paths.append(path)
    service.sync()
    repository.committed_batch_sizes.clear()
    for index, path in enumerate(paths):
        path.write_text(f"Navidrome music refreshed {index}.", encoding="utf-8")

    assert service.sync_paths([path.name for path in paths]) == {
        "indexed": 5,
        "unchanged": 0,
        "removed": 0,
    }
    assert repository.committed_batch_sizes == [2, 2, 1]


def test_interrupted_sync_keeps_completed_batches_and_retry_finishes(tmp_path):
    class InterruptingEmbedder(FakeEmbedder):
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 4:
                raise RuntimeError("simulated interruption")
            return super().embed(texts)

    service = semantic_service(
        tmp_path,
        embedder=InterruptingEmbedder(),
        index_batch_size=2,
    )
    for index in range(5):
        (service.vault_root / f"note-{index}.md").write_text(
            f"TrueNAS backup storage {index}.",
            encoding="utf-8",
        )

    with pytest.raises(RuntimeError, match="simulated interruption"):
        service.sync()

    assert service.state is IndexState.ERROR
    assert len(service.repository.load_chunks()) == 2

    restarted = semantic_service(tmp_path, index_batch_size=2)
    assert restarted.sync() == {"indexed": 3, "unchanged": 2, "removed": 0}
    assert restarted.state is IndexState.READY
    assert len(restarted.repository.load_chunks()) == 5


def test_sync_removes_stale_notes_and_repository_chunks(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "deleted.md"
    note.write_text("TrueNAS backup storage.", encoding="utf-8")
    service.sync()

    note.unlink()

    assert service.sync() == {"indexed": 0, "unchanged": 0, "removed": 1}
    assert service.repository.load_chunks() == []


def test_interrupted_stale_deletion_keeps_completed_batches_and_retry_finishes(tmp_path):
    class InterruptingDeleteSession:
        def __init__(self, session, repository):
            self._session = session
            self._repository = repository

        def __getattr__(self, name):
            return getattr(self._session, name)

        def delete_note(self, path):
            self._repository.delete_calls += 1
            self._session.delete_note(path)
            if self._repository.delete_calls == 4:
                raise RuntimeError("simulated stale deletion interruption")

    class InterruptingDeleteRepository(SemanticRepository):
        def __init__(self, db_path):
            super().__init__(db_path)
            self.delete_calls = 0

        @contextmanager
        def transaction(self):
            with super().transaction() as session:
                yield InterruptingDeleteSession(session, self)

    service = semantic_service(tmp_path, index_batch_size=2)
    note_paths = []
    for index in range(5):
        note_path = service.vault_root / f"stale-{index}.md"
        note_path.write_text(f"TrueNAS backup storage {index}.", encoding="utf-8")
        note_paths.append(note_path)
    service.sync()

    for note_path in note_paths:
        note_path.unlink()

    interrupted_repository = InterruptingDeleteRepository(service.db_path)
    interrupted = semantic_service(
        tmp_path,
        index_batch_size=2,
        repository=interrupted_repository,
    )
    with pytest.raises(RuntimeError, match="simulated stale deletion interruption"):
        interrupted.sync()

    with interrupted_repository.transaction() as session:
        remaining_notes = set(session.load_notes())
    remaining_chunks = interrupted_repository.load_chunks()
    assert interrupted.state is IndexState.ERROR
    assert interrupted_repository.delete_calls == 4
    assert len(remaining_notes) == 3
    assert {chunk.path for chunk in remaining_chunks} == remaining_notes

    restarted = semantic_service(tmp_path, index_batch_size=2)
    assert restarted.sync() == {"indexed": 0, "unchanged": 0, "removed": 3}
    with restarted.repository.transaction() as session:
        assert session.load_notes() == {}
    assert restarted.state is IndexState.READY
    assert restarted.repository.load_chunks() == []


def test_search_preserves_hybrid_ranking_scores_and_order(tmp_path):
    service = semantic_service(tmp_path)
    folder = service.vault_root / "Infrastructure Notes"
    folder.mkdir()
    (folder / "TrueNAS backup strategy.md").write_text(
        "My NAS stores backups and Docker application data on mirrored storage.",
        encoding="utf-8",
    )
    (service.vault_root / "Oracle APEX.md").write_text(
        "An Oracle database application using APEX and ORDS.",
        encoding="utf-8",
    )
    service.sync()

    results = service.search("TrueNAS backup", limit=5, min_score=0.0)

    assert [result.path for result in results] == ["Infrastructure Notes/TrueNAS backup strategy.md"]
    assert results[0].score == 1.0
    assert results[0].semantic_score == 1.0
    assert results[0].lexical_score == 0.75
    assert results[0].heading is None


def test_search_returns_empty_during_initial_background_indexing(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingEmbedder(FakeEmbedder):
        def embed(self, texts):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test embedding was not released")
            return super().embed(texts)

    service = semantic_service(tmp_path, embedder=BlockingEmbedder())
    (service.vault_root / "note.md").write_text("TrueNAS backup storage.", encoding="utf-8")
    indexer = BackgroundSemanticIndexer(service.sync)
    try:
        indexer.start()
        assert started.wait(timeout=2)
        assert service.state is IndexState.INDEXING
        assert service.search("TrueNAS backup", min_score=0.0) == []
    finally:
        release.set()
        indexer.shutdown()


def test_previous_ready_index_remains_searchable_during_background_refresh(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "TrueNAS backup.md"
    note.write_text("TrueNAS backup storage.", encoding="utf-8")
    service.sync()
    started = threading.Event()
    release = threading.Event()

    class RefreshBlockingEmbedder(FakeEmbedder):
        def embed(self, texts):
            if any("refresh marker" in text for text in texts):
                started.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("test refresh was not released")
            return super().embed(texts)

    service.embedder = RefreshBlockingEmbedder()
    note.write_text("TrueNAS backup storage with refresh marker.", encoding="utf-8")
    indexer = BackgroundSemanticIndexer(service.sync)
    search_finished = threading.Event()
    search_results = []

    def run_search():
        search_results.extend(service.search("TrueNAS backup", min_score=0.0))
        search_finished.set()

    try:
        indexer.start()
        assert started.wait(timeout=2)
        assert service.state is IndexState.INDEXING
        search_thread = threading.Thread(target=run_search)
        search_thread.start()
        assert search_finished.wait(timeout=0.1) is False
        release.set()
        search_thread.join(timeout=2)
        assert search_thread.is_alive() is False
        assert [result.path for result in search_results] == ["TrueNAS backup.md"]
    finally:
        release.set()
        indexer.shutdown()


def test_partial_initial_batches_remain_unavailable_after_failure(tmp_path):
    class FailOnThirdEmbedding(FakeEmbedder):
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("initial indexing failed after one batch")
            return super().embed(texts)

    service = semantic_service(
        tmp_path,
        embedder=FailOnThirdEmbedding(),
        index_batch_size=2,
    )
    for index in range(4):
        (service.vault_root / f"note-{index}.md").write_text(
            f"TrueNAS backup storage {index}.",
            encoding="utf-8",
        )

    with pytest.raises(RuntimeError, match="initial indexing failed after one batch"):
        service.sync()

    assert service.state is IndexState.ERROR
    assert len(service.repository.load_chunks()) == 2
    with pytest.raises(SemanticSearchUnavailableError, match="Semantic index is unavailable"):
        service.search("TrueNAS backup", min_score=0.0)


def test_failed_refresh_keeps_compatible_committed_index_searchable(tmp_path):
    service = semantic_service(tmp_path, index_batch_size=2)
    notes = []
    for index in range(4):
        note = service.vault_root / f"note-{index}.md"
        note.write_text(f"TrueNAS backup storage {index}.", encoding="utf-8")
        notes.append(note)
    service.sync()

    class FailOnThirdRefreshEmbedding(FakeEmbedder):
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("refresh failed after one batch")
            return super().embed(texts)

    service.embedder = FailOnThirdRefreshEmbedding()
    for note in notes:
        note.write_text(f"{note.read_text(encoding='utf-8')} refresh marker", encoding="utf-8")

    with pytest.raises(RuntimeError, match="refresh failed after one batch"):
        service.sync()

    assert service.state is IndexState.ERROR
    chunks = service.repository.load_chunks()
    assert len(chunks) == 4
    assert sum("refresh marker" in chunk.content for chunk in chunks) == 2
    assert service.search("TrueNAS backup", min_score=0.0)


def test_search_reads_previous_snapshot_while_sqlite_write_transaction_is_open(tmp_path):
    class BlockingWriteSession:
        def __init__(self, session, repository):
            self._session = session
            self._repository = repository

        def __getattr__(self, name):
            return getattr(self._session, name)

        def replace_note(self, note, chunks):
            self._session.replace_note(note, chunks)
            if self._repository.block_writes:
                self._repository.write_started.set()
                if not self._repository.release_write.wait(timeout=5):
                    raise RuntimeError("test write transaction was not released")

    class BlockingWriteRepository(SemanticRepository):
        def __init__(self, db_path):
            super().__init__(db_path)
            self.block_writes = False
            self.write_started = threading.Event()
            self.release_write = threading.Event()

        @contextmanager
        def transaction(self):
            with super().transaction() as session:
                yield BlockingWriteSession(session, self)

    repository = BlockingWriteRepository(tmp_path / "data" / "semantic-index.sqlite3")
    service = semantic_service(tmp_path, repository=repository)
    note = service.vault_root / "TrueNAS backup.md"
    note.write_text("TrueNAS backup original snapshot.", encoding="utf-8")
    service.sync()
    note.write_text("TrueNAS backup replacement snapshot.", encoding="utf-8")
    repository.block_writes = True
    indexer = BackgroundSemanticIndexer(service.sync)
    try:
        indexer.start()
        assert repository.write_started.wait(timeout=2)
        results = service.search("TrueNAS backup", min_score=0.0)
        assert results
        assert "original snapshot" in results[0].snippet
    finally:
        repository.release_write.set()
        indexer.shutdown()


def test_embedder_calls_are_serialized_during_refresh_and_search(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "TrueNAS backup.md"
    note.write_text("TrueNAS backup original.", encoding="utf-8")
    service.sync()

    class ConcurrencyDetectingEmbedder(FakeEmbedder):
        def __init__(self):
            self._lock = threading.Lock()
            self.active_calls = 0
            self.maximum_active_calls = 0
            self.refresh_started = threading.Event()
            self.release_refresh = threading.Event()

        def embed(self, texts):
            with self._lock:
                self.active_calls += 1
                self.maximum_active_calls = max(self.maximum_active_calls, self.active_calls)
            try:
                if any("refresh marker" in text for text in texts):
                    self.refresh_started.set()
                    if not self.release_refresh.wait(timeout=5):
                        raise RuntimeError("test refresh embedding was not released")
                return super().embed(texts)
            finally:
                with self._lock:
                    self.active_calls -= 1

    embedder = ConcurrencyDetectingEmbedder()
    service.embedder = embedder
    note.write_text("TrueNAS backup refresh marker.", encoding="utf-8")
    indexer = BackgroundSemanticIndexer(service.sync)
    search_started = threading.Event()
    search_finished = threading.Event()
    search_results = []

    def run_search():
        search_started.set()
        search_results.extend(service.search("TrueNAS backup", min_score=0.0))
        search_finished.set()

    try:
        indexer.start()
        assert embedder.refresh_started.wait(timeout=2)
        search_thread = threading.Thread(target=run_search)
        search_thread.start()
        assert search_started.wait(timeout=2)
        assert search_finished.wait(timeout=0.1) is False
        embedder.release_refresh.set()
        search_thread.join(timeout=2)
        assert search_thread.is_alive() is False
        assert search_results
        assert embedder.maximum_active_calls == 1
    finally:
        embedder.release_refresh.set()
        indexer.shutdown()


def test_chunking_preserves_frontmatter_removal_and_heading_metadata(tmp_path):
    service = semantic_service(tmp_path)

    chunks = service._chunk_markdown(
        "Fallback title",
        "---\ntags: [test]\n---\n# Heading\n\nBody text.",
    )

    assert chunks == [("Heading", "# Heading\n\nBody text.")]


def test_heading_aware_chunking_prefers_sections_and_preserves_hierarchy(tmp_path):
    service = semantic_service(tmp_path)
    overview = "Overview of the server. " * 6
    storage = "TrueNAS datasets and snapshots. " * 5
    backups = "Nočná replikácia chráni dáta. " * 5
    media = "Jellyfin library. " * 8
    markdown = (
        f"# Infrastructure Notes\n\n{overview}\n\n"
        f"## Storage\n\n{storage}\n\n"
        f"### Zálohy\n\n{backups}\n\n"
        f"## Media\n\n{media}\n"
    )

    chunks = service._chunk_markdown("Infrastructure Notes", markdown)

    assert [heading for heading, _ in chunks] == [
        "Infrastructure Notes",
        "Infrastructure Notes > Storage",
        "Infrastructure Notes > Storage > Zálohy",
        "Infrastructure Notes > Media",
    ]
    assert all(len(content) <= service.chunk_chars for _, content in chunks)
    assert "".join(content for _, content in chunks) == markdown


def test_chunking_handles_no_headings_heading_only_and_empty_notes(tmp_path):
    service = semantic_service(tmp_path)

    assert service._chunk_markdown("Plain", "A plain note without headings.") == [
        (None, "A plain note without headings.")
    ]
    assert service._chunk_markdown("Empty", "---\ntags: [empty]\n---\n") == [
        (None, "Empty")
    ]
    assert service._chunk_markdown(
        "Sparse",
        "# Root\n\n## Empty section\n\n## Populated\n\nUseful body.",
    ) == [
        (
            "Root > Empty section … Root > Populated",
            "# Root\n\n## Empty section\n\n## Populated\n\nUseful body.",
        ),
    ]
    assert service._chunk_markdown(
        "Nested",
        "# Root\n\n## Child\n\nChild body.",
    ) == [("Root > Child", "# Root\n\n## Child\n\nChild body.")]


def test_oversized_section_is_bounded_deterministic_and_uses_limited_overlap(tmp_path):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=40)
    markdown = "# Long section\n\n" + "alpha beta gamma delta " * 40

    first = service._chunk_markdown("Long", markdown)
    second = service._chunk_markdown("Long", markdown)

    assert first == second
    assert len(first) > 1
    assert {heading for heading, _ in first} == {"Long section"}
    assert all(len(content) <= service.chunk_chars for _, content in first)
    assert min(len(content) for _, content in first) >= service.chunk_chars // 3
    duplicated_characters = sum(len(content) for _, content in first) - len(markdown.strip())
    assert 0 < duplicated_characters <= (service.chunk_overlap + 5) * (len(first) - 1)


@pytest.mark.parametrize(
    "body",
    [
        "```python\n" + "".join(f"    value_{index} = operation_{index}()\n" for index in range(30)) + "```",
        "".join(f"    - nested item {index} with details\n" for index in range(30)),
        "".join(f"    indented plain text line {index}\n" for index in range(30)),
        "".join(f"    CRLF indented line {index}\r\n" for index in range(30)),
    ],
    ids=["fenced-python", "nested-list", "indented-plain-text", "crlf-text"],
)
def test_oversized_structured_content_preserves_every_source_character(tmp_path, body):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=40)
    markdown = f"# Structured\n\n{body}"

    chunks = service._chunk_markdown("Structured", markdown)

    assert len(chunks) > 1
    assert all(len(content) <= service.chunk_chars for _, content in chunks)
    assert "".join(content for _, content in chunks) == markdown


def test_oversized_unbreakable_token_uses_exact_source_overlap(tmp_path):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=40)
    token = "".join(chr(0x1000 + index) for index in range(800))

    chunks = [content for _, content in service._chunk_markdown("Token", token)]

    assert len(chunks) > 1
    assert all(len(content) <= service.chunk_chars for content in chunks)
    assert all(content in token for content in chunks)
    reconstructed = chunks[0]
    for content in chunks[1:]:
        overlap = min(service.chunk_overlap, len(reconstructed), len(content))
        while overlap and not reconstructed.endswith(content[:overlap]):
            overlap -= 1
        reconstructed += content[overlap:]
    assert reconstructed == token


@pytest.mark.parametrize("size", [249, 250, 251])
def test_chunk_size_boundaries_preserve_non_overlap_content(tmp_path, size):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=0)
    content = "x" * size

    chunks = service._chunk_markdown("Boundary", content)

    assert all(len(chunk) <= service.chunk_chars for _, chunk in chunks)
    assert "".join(chunk for _, chunk in chunks) == content
    assert len(chunks) == (1 if size <= service.chunk_chars else 2)


def test_many_short_sibling_sections_are_coalesced_without_losing_order(tmp_path):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=0)
    markdown = "\n\n".join(f"## Section {index}\nvalue {index}" for index in range(100))

    chunks = service._chunk_markdown("Many sections", markdown)

    assert 1 < len(chunks) < 20
    assert all(len(content) <= service.chunk_chars for _, content in chunks)
    assert all(heading is None or len(heading) <= 200 for heading, _ in chunks)
    assert "".join(content for _, content in chunks) == markdown
    assert chunks[0][0] == "Section 0 … Section 10"
    assert "Section 99" in (chunks[-1][0] or "")


def test_adjacent_heading_only_and_short_nested_sections_coalesce_truthfully(tmp_path):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=0)
    markdown = (
        "# Root\n\n## Empty A\n\n## Empty B\n\n"
        "## Parent\n\n### Child A\n\none\n\n### Child B\ntwo"
    )

    chunks = service._chunk_markdown("Nested", markdown)

    assert len(chunks) == 1
    assert chunks[0][0] == "Root > Empty A … Root > Parent > Child B"
    assert chunks[0][1] == markdown


def test_short_sections_coalesce_around_normal_sections_without_crossing_limit(tmp_path):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=0)
    normal_body = "normal-sized body " * 12
    markdown = (
        "## Short A\na\n\n"
        f"## Normal\n{normal_body}\n\n"
        "## Short B\nb\n\n"
        "## Short C\nc"
    )

    chunks = service._chunk_markdown("Mixed", markdown)

    assert 1 < len(chunks) < 4
    assert all(len(content) <= service.chunk_chars for _, content in chunks)
    assert "".join(content for _, content in chunks) == markdown


def test_heading_metadata_is_leaf_first_bounded_and_separator_safe(tmp_path):
    service = semantic_service(tmp_path)

    assert service._format_hierarchy(("Root", "Child")) == "Root > Child"
    near_limit = ("A" * 190, "Leaf")
    assert service._format_hierarchy(near_limit) == f"{'A' * 190} > Leaf"

    long_hierarchy = ("A" * 110, "B" * 110, "CHILD-UNIQUE")
    formatted = service._format_hierarchy(long_hierarchy)
    assert formatted is not None
    assert len(formatted) == 200
    assert formatted.endswith(" > CHILD-UNIQUE")
    assert "…" in formatted
    assert not formatted.endswith(" > ")

    deep = service._format_hierarchy(tuple(f"Level-{index}" * 8 for index in range(6)))
    assert deep is not None
    assert len(deep) <= 200
    assert deep.endswith("Level-5" * 8)


def test_long_leaf_headings_remain_bounded_and_siblings_distinguishable(tmp_path):
    service = semantic_service(tmp_path)
    prefix = "A" * 100
    suffix = "Z" * 100
    first_hierarchy = ("Ancestor" * 30, f"{prefix}FIRST-UNIQUE{suffix}")
    second_hierarchy = ("Ancestor" * 30, f"{prefix}SECOND-UNIQUE{suffix}")
    first = service._format_hierarchy(first_hierarchy)
    second = service._format_hierarchy(second_hierarchy)

    assert first is not None and second is not None
    assert len(first) == len(second) == 200
    assert first != second
    assert service._format_hierarchy(first_hierarchy) == first
    assert first.startswith("A") and second.startswith("A")
    assert "Z" * 20 in first and "Z" * 20 in second
    assert "…" in first and "…" in second
    assert re.search(r" \[[0-9a-f]{12}\]", first)
    assert re.search(r" \[[0-9a-f]{12}\]", second)
    assert service._format_hierarchy(("Root", "Child")) == "Root > Child"


def test_coalesced_long_heading_metadata_is_bounded_stable_and_distinguishable(tmp_path):
    service = semantic_service(tmp_path)
    prefix = "shared-prefix-" * 12
    suffix = "-shared-suffix" * 12
    last = (f"{prefix}LAST{suffix}",)
    first_group = ((f"{prefix}FIRST-A{suffix}",), ("Middle",), last)
    second_group = ((f"{prefix}FIRST-B{suffix}",), ("Middle",), last)

    first = service._format_group_heading(first_group)
    second = service._format_group_heading(second_group)

    assert first is not None and second is not None
    assert len(first) == len(second) == 200
    assert first != second
    assert service._format_group_heading(first_group) == first
    assert first.startswith("shared-prefix-") and second.startswith("shared-prefix-")
    assert "shared-suffix" in first and "shared-suffix" in second
    assert re.search(r" \[[0-9a-f]{12}\]", first)
    assert re.search(r" \[[0-9a-f]{12}\]", second)


def test_full_and_targeted_sync_preserve_source_newlines(tmp_path):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=0)
    crlf_note = service.vault_root / "crlf.md"
    lf_note = service.vault_root / "lf.md"
    initial_crlf = b"# CRLF\r\n\r\n    indented code\r\nbody\r\n"
    initial_lf = b"# LF\n\n    indented code\nbody\n"
    crlf_note.write_bytes(initial_crlf)
    lf_note.write_bytes(initial_lf)

    assert service.sync() == {"indexed": 2, "unchanged": 0, "removed": 0}
    chunks = service.repository.load_chunks()
    assert "".join(chunk.content for chunk in chunks if chunk.path == "crlf.md") == (
        initial_crlf.decode("utf-8")
    )
    assert "".join(chunk.content for chunk in chunks if chunk.path == "lf.md") == (
        initial_lf.decode("utf-8")
    )

    updated_crlf = b"# CRLF\r\n\r\n    updated indented code\r\nupdated body\r\n"
    crlf_note.write_bytes(updated_crlf)

    assert service.sync_paths(["crlf.md"]) == {
        "indexed": 1,
        "unchanged": 0,
        "removed": 0,
    }
    chunks = service.repository.load_chunks()
    assert "".join(chunk.content for chunk in chunks if chunk.path == "crlf.md") == (
        updated_crlf.decode("utf-8")
    )
    assert "".join(chunk.content for chunk in chunks if chunk.path == "lf.md") == (
        initial_lf.decode("utf-8")
    )


def test_chunking_keeps_lists_and_fenced_code_intact_when_they_fit(tmp_path):
    service = semantic_service(tmp_path, chunk_chars=250, chunk_overlap=40)
    list_block = "\n".join(f"- complete list item {index}" for index in range(14))
    code_block = "```python\nfor item in values:\n    print(item)\n```"
    markdown = f"# Runbook\n\n{list_block}\n\n{code_block}"

    chunks = service._chunk_markdown("Runbook", markdown)

    assert all(len(content) <= service.chunk_chars for _, content in chunks)
    assert all(heading == "Runbook" for heading, _ in chunks)
    assert any(code_block in content for _, content in chunks)
    list_lines = [
        line
        for _, content in chunks
        for line in content.splitlines()
        if line.startswith("-")
    ]
    assert list_lines == list_block.splitlines()

    large_fitting_fence = "```text\n" + ("structured code line\n" * 11) + "```"
    fence_chunks = service._chunk_markdown(
        "Code",
        f"# A sufficiently long heading\n\n{large_fitting_fence}",
    )
    assert len(large_fitting_fence) <= service.chunk_chars
    assert any(content == large_fitting_fence for _, content in fence_chunks)


def test_headings_inside_fenced_code_do_not_create_sections(tmp_path):
    service = semantic_service(tmp_path)
    markdown = """# Actual heading

```markdown
## This is code, not a section
content
```

Body after the fence.
"""

    chunks = service._chunk_markdown("Code", markdown)

    assert chunks == [("Actual heading", markdown)]


def test_full_and_targeted_sync_use_heading_aware_chunker(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "server.md"
    note.write_text(
        "# Server\n\n" + ("Intro. " * 20) + "\n\n## Storage\n\n" + ("Datasets. " * 16),
        encoding="utf-8",
    )

    assert service.sync() == {"indexed": 1, "unchanged": 0, "removed": 0}
    assert [chunk.heading for chunk in service.repository.load_chunks()] == [
        "Server",
        "Server > Storage",
    ]

    note.write_text(
        "# Server\n\n"
        + ("Updated. " * 18)
        + "\n\n## Storage\n\n"
        + ("Datasets. " * 16)
        + "\n\n### Snapshots\n\n"
        + ("Nightly. " * 18),
        encoding="utf-8",
    )
    assert service.sync_paths(["server.md"]) == {
        "indexed": 1,
        "unchanged": 0,
        "removed": 0,
    }
    assert [chunk.heading for chunk in service.repository.load_chunks()] == [
        "Server",
        "Server > Storage",
        "Server > Storage > Snapshots",
    ]


def test_v1_chunker_signature_is_invalidated_without_schema_migration(tmp_path):
    service = semantic_service(tmp_path)
    (service.vault_root / "note.md").write_text("# Note\n\nBody.", encoding="utf-8")
    service.sync()
    service.repository.set_metadata("index_signature", "v1|example/model|300|50")

    restarted = semantic_service(tmp_path)

    assert restarted.index_signature == "v2-heading-aware|example/model|300|50"
    assert restarted.state is IndexState.UNINITIALIZED
    assert restarted.repository.load_chunks() == []


def test_compatible_v2_heading_aware_index_is_reused(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "note.md"
    note.write_text("# Note\n\nBody.", encoding="utf-8")
    service.sync()
    original_chunks = service.repository.load_chunks()

    restarted = semantic_service(tmp_path)

    assert restarted.state is IndexState.READY
    assert restarted.repository.load_chunks() == original_chunks
    assert restarted.sync() == {"indexed": 0, "unchanged": 1, "removed": 0}


@pytest.mark.parametrize(
    "changed_configuration",
    [
        {"model_name": "different/model"},
        {"chunk_chars": 350},
        {"chunk_overlap": 60},
    ],
    ids=["model", "chunk-size", "overlap"],
)
def test_v2_index_configuration_changes_invalidate_all_chunks(
    tmp_path,
    changed_configuration,
):
    service = semantic_service(tmp_path)
    (service.vault_root / "note.md").write_text("# Note\n\nBody.", encoding="utf-8")
    service.sync()

    changed = semantic_service(tmp_path, **changed_configuration)

    assert changed.state is IndexState.UNINITIALIZED
    assert changed.repository.load_chunks() == []


def test_targeted_refresh_against_v1_index_runs_safe_full_v2_rebuild(tmp_path):
    service = semantic_service(tmp_path)
    first = service.vault_root / "first.md"
    second = service.vault_root / "second.md"
    first.write_text("# First\n\nOriginal first.", encoding="utf-8")
    second.write_text("# Second\n\nOriginal second.", encoding="utf-8")
    service.sync()

    connection = sqlite3.connect(service.db_path)
    try:
        connection.execute(
            "UPDATE meta SET value=? WHERE key='index_signature'",
            ("v1|example/model|300|50",),
        )
        connection.execute("UPDATE chunks SET content='legacy-v1-chunk'")
        connection.commit()
    finally:
        connection.close()

    first.write_text("# First\n\nUpdated first.", encoding="utf-8")
    restarted = semantic_service(tmp_path)

    assert restarted.sync_paths(["first.md"]) == {
        "indexed": 2,
        "unchanged": 0,
        "removed": 0,
    }
    chunks = restarted.repository.load_chunks()
    assert {chunk.path for chunk in chunks} == {"first.md", "second.md"}
    assert all(chunk.content != "legacy-v1-chunk" for chunk in chunks)
    assert restarted.repository.get_metadata("index_signature") == restarted.index_signature
