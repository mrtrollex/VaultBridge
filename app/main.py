from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings
from app.semantic import semantic_index_from_settings
from app.services.vault import (
    NoteConflictError,
    NoteNotFoundError,
    NoteTooLargeError,
    VaultService,
    VaultServiceError,
    VaultValidationError,
)

APP_TITLE = "VaultBridge"
APP_VERSION = "0.1.0"
APP_DESCRIPTION = "Self-hosted REST and semantic search API for an Obsidian vault."
settings = Settings.from_env()
SEMANTIC_INDEX = semantic_index_from_settings(settings)
VAULT_SERVICE = VaultService(vault_root=settings.vault_path, max_note_bytes=settings.max_note_bytes)

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=APP_DESCRIPTION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(VaultServiceError)
async def handle_vault_service_error(_request: Request, exc: VaultServiceError) -> JSONResponse:
    status_codes = {
        VaultValidationError: status.HTTP_400_BAD_REQUEST,
        NoteNotFoundError: status.HTTP_404_NOT_FOUND,
        NoteConflictError: status.HTTP_409_CONFLICT,
        NoteTooLargeError: 413,
    }
    return JSONResponse(status_code=status_codes[type(exc)], content={"detail": str(exc)})


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


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    folder: str = Field(default="", max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


class RelatedNotesRequest(BaseModel):
    text: str = Field(min_length=2, max_length=4000, description="Text or concept to compare with the vault")
    folder: str = Field(default="", max_length=500, description="Optional vault-relative folder")
    limit: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(
        default=0.28,
        ge=-1.0,
        le=1.0,
        description="Minimum semantic cosine similarity before hybrid reranking",
    )


class NoteResult(BaseModel):
    success: bool
    path: str
    status: Literal["created", "unchanged", "appended", "already_applied", "read"] | None = None


def require_auth(authorization: str | None = Header(default=None)) -> None:
    api_key = settings.api_key.get_secret_value()
    if not api_key:
        raise HTTPException(status_code=500, detail="Server API_KEY is not configured")
    expected = f"Bearer {api_key}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


@app.get("/health", operation_id="healthCheck", tags=["system"])
def health() -> dict:
    SEMANTIC_INDEX.reconfigure(vault_root=settings.vault_path, max_note_bytes=settings.max_note_bytes)
    return {
        "ok": True,
        "vault_exists": VAULT_SERVICE.vault_exists(),
        "semantic_index_ready": SEMANTIC_INDEX.is_initialized(),
    }


@app.post(
    "/notes",
    operation_id="createNote",
    response_model=NoteResult,
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Create an Obsidian Markdown note",
)
def create_note(note: CreateNoteRequest) -> NoteResult:
    result = VAULT_SERVICE.create_note(
        title=note.title,
        folder=note.folder,
        content=note.content,
        tags=note.tags,
    )
    return NoteResult(success=True, path=result.path, status=result.status)


@app.post(
    "/notes/append",
    operation_id="appendNote",
    response_model=NoteResult,
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Append Markdown to an existing Obsidian note",
)
def append_note(req: AppendNoteRequest) -> NoteResult:
    result = VAULT_SERVICE.append_note(path=req.path, content=req.content, dedupe_key=req.dedupe_key)
    return NoteResult(success=True, path=result.path, status=result.status)


@app.get(
    "/notes/read",
    operation_id="readNote",
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Read one Obsidian note",
)
def read_note(path: str = Query(description="Vault-relative .md path")) -> dict:
    result = VAULT_SERVICE.read_note(path)
    return {"path": result.path, "content": result.content}


@app.post(
    "/notes/search",
    operation_id="searchNotes",
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Search Markdown notes in the vault",
)
def search_notes(req: SearchRequest) -> dict:
    results = VAULT_SERVICE.search_notes(query=req.query, folder=req.folder, limit=req.limit)
    return {
        "query": req.query,
        "results": [
            {"path": result.path, "title": result.title, "snippet": result.snippet} for result in results
        ],
    }


@app.post(
    "/notes/related",
    operation_id="findRelatedNotes",
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Find conceptually related Obsidian notes",
)
def find_related_notes(req: RelatedNotesRequest) -> dict:
    folder = ""
    if req.folder:
        existing_folder = VAULT_SERVICE.existing_relative_path(req.folder)
        if existing_folder is None:
            return {"text": req.text, "results": []}
        folder = existing_folder

    SEMANTIC_INDEX.reconfigure(vault_root=settings.vault_path, max_note_bytes=settings.max_note_bytes)
    try:
        results = SEMANTIC_INDEX.search(
            req.text,
            folder=folder,
            limit=req.limit,
            min_score=req.min_score,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Semantic search unavailable: {exc}") from exc

    return {
        "text": req.text,
        "results": [
            {
                "path": result.path,
                "title": result.title,
                "score": result.score,
                "semantic_score": result.semantic_score,
                "lexical_score": result.lexical_score,
                "snippet": result.snippet,
                "heading": result.heading,
            }
            for result in results
        ],
    }


@app.get(
    "/notes/list",
    operation_id="listNotes",
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="List Markdown notes in a vault folder",
)
def list_notes(
    folder: str = Query(default="", description="Vault-relative folder; empty means whole vault"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    notes = VAULT_SERVICE.list_notes(folder=folder, limit=limit)
    return {
        "folder": folder,
        "notes": [
            {"path": note.path, "title": note.title, "modified": note.modified} for note in notes
        ],
    }


@app.get("/privacy", response_class=PlainTextResponse, include_in_schema=False)
def privacy() -> str:
    return (
        "VaultBridge stores request content only as Markdown files in the configured vault. "
        "It does not intentionally send vault data to third parties. Access is protected by an API key."
    )
