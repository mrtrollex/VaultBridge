from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import get_semantic_indexer, get_vault_service, require_auth
from app.api.versioning import versioned_api_route
from app.core.logging import log_event
from app.services.indexer import BackgroundSemanticIndexer
from app.services.vault import VaultService

router = APIRouter()
logger = logging.getLogger("vaultbridge.api.notes")


def _enqueue_after_committed_write(
    semantic_indexer: BackgroundSemanticIndexer,
    path: str,
) -> None:
    try:
        semantic_indexer.enqueue(path)
    except Exception as exc:
        # Markdown is authoritative and the next startup full sync can recover the write.
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
        return


class CreateNoteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180, description="Human-readable note title without .md")
    folder: str = Field(default="Inbox", max_length=500, description="Vault-relative folder, e.g. Technical Notes/Example Project")
    content: str = Field(default="", max_length=800_000, description="Markdown body. Do not include YAML front matter.")
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title cannot be empty")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tag in values:
            tag = tag.strip().lstrip("#")
            if tag and tag not in cleaned:
                cleaned.append(tag[:80])
        return cleaned


class AppendNoteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=700, description="Vault-relative path ending in .md")
    content: str = Field(min_length=1, max_length=800_000, description="Markdown text to append")
    dedupe_key: str | None = Field(
        default=None,
        max_length=120,
        description="Optional stable key. If already present, the append is treated as already completed.",
    )


class NoteResult(BaseModel):
    success: bool
    path: str
    status: Literal["created", "unchanged", "appended", "already_applied", "read"] | None = None


@versioned_api_route(
    router,
    "/notes",
    operation_id="createNote",
    methods=["POST"],
    response_model=NoteResult,
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Create an Obsidian Markdown note",
)
def create_note(
    note: CreateNoteRequest,
    vault_service: VaultService = Depends(get_vault_service),
    semantic_indexer: BackgroundSemanticIndexer = Depends(get_semantic_indexer),
) -> NoteResult:
    result = vault_service.create_note(
        title=note.title,
        folder=note.folder,
        content=note.content,
        tags=note.tags,
    )
    if result.status == "created":
        log_event(
            logger,
            logging.INFO,
            "note_created",
            "Markdown note was created",
            operation="create",
            note_path=result.path,
        )
        _enqueue_after_committed_write(semantic_indexer, result.path)
    return NoteResult(success=True, path=result.path, status=result.status)


@versioned_api_route(
    router,
    "/notes/append",
    operation_id="appendNote",
    methods=["POST"],
    response_model=NoteResult,
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Append Markdown to an existing Obsidian note",
)
def append_note(
    req: AppendNoteRequest,
    vault_service: VaultService = Depends(get_vault_service),
    semantic_indexer: BackgroundSemanticIndexer = Depends(get_semantic_indexer),
) -> NoteResult:
    result = vault_service.append_note(path=req.path, content=req.content, dedupe_key=req.dedupe_key)
    if result.status == "appended":
        log_event(
            logger,
            logging.INFO,
            "note_appended",
            "Markdown content was appended to a note",
            operation="append",
            note_path=result.path,
        )
        _enqueue_after_committed_write(semantic_indexer, result.path)
    return NoteResult(success=True, path=result.path, status=result.status)


@versioned_api_route(
    router,
    "/notes/read",
    operation_id="readNote",
    methods=["GET"],
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Read one Obsidian note",
)
def read_note(
    path: str = Query(description="Vault-relative .md path"),
    vault_service: VaultService = Depends(get_vault_service),
) -> dict:
    result = vault_service.read_note(path)
    return {"path": result.path, "content": result.content}


@versioned_api_route(
    router,
    "/notes/list",
    operation_id="listNotes",
    methods=["GET"],
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="List Markdown notes in a vault folder",
)
def list_notes(
    folder: str = Query(default="", description="Vault-relative folder; empty means whole vault"),
    limit: int = Query(default=50, ge=1, le=200),
    vault_service: VaultService = Depends(get_vault_service),
) -> dict:
    notes = vault_service.list_notes(folder=folder, limit=limit)
    return {
        "folder": folder,
        "notes": [
            {"path": note.path, "title": note.title, "modified": note.modified} for note in notes
        ],
    }
