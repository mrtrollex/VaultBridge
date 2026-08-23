import os
import sqlite3

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


def semantic_service(
    tmp_path,
    *,
    embedder=None,
    model_name: str = "example/model",
) -> SemanticSearchService:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(exist_ok=True)
    return SemanticSearchService(
        vault_root=vault_root,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        model_name=model_name,
        max_note_bytes=1_000_000,
        chunk_chars=300,
        chunk_overlap=50,
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


def test_sync_removes_stale_notes_and_repository_chunks(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "deleted.md"
    note.write_text("TrueNAS backup storage.", encoding="utf-8")
    service.sync()

    note.unlink()

    assert service.sync() == {"indexed": 0, "unchanged": 0, "removed": 1}
    assert service.repository.load_chunks() == []


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
