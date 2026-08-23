import os

import numpy as np

from app.repositories.semantic import SemanticRepository
from app.services.semantic_search import SemanticSearchService


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


def semantic_service(tmp_path) -> SemanticSearchService:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    return SemanticSearchService(
        vault_root=vault_root,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        model_name="example/model",
        max_note_bytes=1_000_000,
        chunk_chars=300,
        chunk_overlap=50,
        embedder=FakeEmbedder(),
    )


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
    assert service.repository.load_chunks(service.index_signature) == []


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
