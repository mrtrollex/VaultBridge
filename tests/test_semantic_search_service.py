import os
import sqlite3
from contextlib import contextmanager

import numpy as np
import pytest

from app.repositories.semantic import SemanticRepository
from app.services.semantic_search import IndexState, SemanticSearchService


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
        chunk_chars=300,
        chunk_overlap=50,
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

    results = service.search("TrueNAS backup", limit=5, min_score=0.0)

    assert [result.path for result in results] == ["Infrastructure Notes/TrueNAS backup strategy.md"]
    assert results[0].score == 1.0
    assert results[0].semantic_score == 1.0
    assert results[0].lexical_score == 0.75
    assert results[0].heading is None


def test_chunking_preserves_frontmatter_removal_and_heading_metadata(tmp_path):
    service = semantic_service(tmp_path)

    chunks = service._chunk_markdown(
        "Fallback title",
        "---\ntags: [test]\n---\n# Heading\n\nBody text.",
    )

    assert chunks == [("Heading", "# Heading\n\nBody text.")]
