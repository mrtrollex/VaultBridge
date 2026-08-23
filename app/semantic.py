"""Compatibility imports for the pre-VB-005 semantic index API.

New application code should use :mod:`app.services.semantic_search` and
:mod:`app.repositories.semantic` directly.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.semantic_search import (
    DEFAULT_MODEL,
    Embedder,
    FastEmbedder,
    IndexState,
    SemanticResult,
    SemanticSearchService,
)


class SemanticIndex(SemanticSearchService):
    """Backward-compatible constructor backed by the extracted repository."""

    def __init__(
        self,
        *,
        vault_root: Path,
        db_path: Path,
        model_name: str = DEFAULT_MODEL,
        cache_dir: Path | None = None,
        max_note_bytes: int = 1_000_000,
        chunk_chars: int = 600,
        chunk_overlap: int = 100,
        index_batch_size: int = 25,
        embedder: Embedder | None = None,
    ) -> None:
        super().__init__(
            vault_root=vault_root,
            repository=SemanticRepository(db_path),
            model_name=model_name,
            cache_dir=cache_dir,
            max_note_bytes=max_note_bytes,
            chunk_chars=chunk_chars,
            chunk_overlap=chunk_overlap,
            index_batch_size=index_batch_size,
            embedder=embedder,
        )


def semantic_index_from_settings(settings: Settings) -> SemanticIndex:
    data_path = settings.semantic_data_path
    return SemanticIndex(
        vault_root=settings.vault_path,
        db_path=data_path / "semantic-index.sqlite3",
        cache_dir=data_path / "models",
        model_name=settings.semantic_model,
        max_note_bytes=settings.max_note_bytes,
        chunk_chars=settings.semantic_chunk_chars,
        chunk_overlap=settings.semantic_chunk_overlap,
        index_batch_size=settings.semantic_index_batch_size,
    )


__all__ = [
    "DEFAULT_MODEL",
    "Embedder",
    "FastEmbedder",
    "IndexState",
    "SemanticIndex",
    "SemanticResult",
    "semantic_index_from_settings",
]
