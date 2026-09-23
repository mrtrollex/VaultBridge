from __future__ import annotations

import logging

from app.core.logging import log_event
from app.services.indexer import BackgroundSemanticIndexer

logger = logging.getLogger("vaultbridge.note_writes")


def clean_note_title(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("title cannot be empty")
    return value


def clean_note_tags(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in values:
        tag = tag.strip().lstrip("#")
        if tag and tag not in cleaned:
            cleaned.append(tag[:80])
    return cleaned


def enqueue_after_committed_write(
    semantic_indexer: BackgroundSemanticIndexer,
    path: str,
) -> None:
    try:
        semantic_indexer.enqueue(path)
    except Exception as exc:
        # Markdown is authoritative and the next full sync can recover the write.
        log_event(
            logger,
            logging.WARNING,
            "targeted_reindex_queue_failed",
            "Targeted semantic reindex could not be queued after a committed note write",
            exc_info=(type(exc), exc, exc.__traceback__),
            operation="targeted",
            note_path=path,
            error_type=type(exc).__name__,
        )
