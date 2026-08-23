from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from app.semantic import semantic_index_from_env

APP_TITLE = "Obsidian ChatGPT Bridge"
API_KEY = os.getenv("API_KEY", "")
VAULT_ROOT = Path(os.getenv("VAULT_PATH", "/vault")).expanduser().resolve()
MAX_NOTE_BYTES = int(os.getenv("MAX_NOTE_BYTES", "1000000"))
SEMANTIC_INDEX = semantic_index_from_env(VAULT_ROOT, MAX_NOTE_BYTES)

app = FastAPI(
    title=APP_TITLE,
    version="1.1.1",
    description="Obsidian REST bridge with local hybrid semantic search",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


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
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Server API_KEY is not configured")
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def _safe_relative(raw: str) -> Path:
    raw = raw.strip().replace("\\", "/")
    path = Path(raw)
    if path.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be vault-relative")
    candidate = (VAULT_ROOT / path).resolve()
    try:
        candidate.relative_to(VAULT_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escapes the vault") from exc
    return candidate


def _safe_filename(title: str) -> str:
    # Safe on Windows/Linux/macOS and avoids accidental nested paths.
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).strip().rstrip(". ")
    value = re.sub(r"\s+", " ", value)
    if not value:
        raise HTTPException(status_code=400, detail="Title does not produce a valid filename")
    return f"{value[:180]}.md"


def _ensure_markdown(path: Path) -> None:
    if path.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="Only .md files are allowed")


def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Note not found")
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise HTTPException(status_code=413, detail="Note is too large")
    return path.read_text(encoding="utf-8")


@app.get("/health", operation_id="healthCheck", tags=["system"])
def health() -> dict:
    SEMANTIC_INDEX.reconfigure(vault_root=VAULT_ROOT, max_note_bytes=MAX_NOTE_BYTES)
    return {
        "ok": True,
        "vault_exists": VAULT_ROOT.exists(),
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
    folder_raw = (note.folder or "Inbox").strip().replace("\\", "/")
    folder = _safe_relative(folder_raw)
    folder.mkdir(parents=True, exist_ok=True)
    path = _safe_relative(str(Path(folder_raw) / _safe_filename(note.title)))
    _ensure_markdown(path)

    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tags_yaml = json.dumps(note.tags, ensure_ascii=False)
    markdown = (
        "---\n"
        f"created: {created}\n"
        f"tags: {tags_yaml}\n"
        "source: chatgpt\n"
        "---\n\n"
        f"{note.content.rstrip()}\n"
    )

    if len(markdown.encode("utf-8")) > MAX_NOTE_BYTES:
        raise HTTPException(status_code=413, detail="Generated note is too large")

    if path.exists():
        existing = _read_text(path)
        # Make retries idempotent even though the generated `created` timestamp changes.
        expected_tail = f"{note.content.rstrip()}\n"
        if existing.endswith(expected_tail):
            return NoteResult(success=True, path=str(path.relative_to(VAULT_ROOT)), status="unchanged")
        raise HTTPException(
            status_code=409,
            detail="A note with this title already exists. Use appendNote or choose another title.",
        )

    path.write_text(markdown, encoding="utf-8")
    return NoteResult(success=True, path=str(path.relative_to(VAULT_ROOT)), status="created")


@app.post(
    "/notes/append",
    operation_id="appendNote",
    response_model=NoteResult,
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Append Markdown to an existing Obsidian note",
)
def append_note(req: AppendNoteRequest) -> NoteResult:
    path = _safe_relative(req.path)
    _ensure_markdown(path)
    existing = _read_text(path)

    marker = None
    if req.dedupe_key:
        safe_key = re.sub(r"[^A-Za-z0-9._:-]", "_", req.dedupe_key)
        marker = f"<!-- chatgpt-append:{safe_key} -->"
        if marker in existing:
            return NoteResult(success=True, path=str(path.relative_to(VAULT_ROOT)), status="already_applied")

    addition = "\n\n" + req.content.strip() + "\n"
    if marker:
        addition += marker + "\n"

    if len((existing + addition).encode("utf-8")) > MAX_NOTE_BYTES:
        raise HTTPException(status_code=413, detail="Resulting note would be too large")

    with path.open("a", encoding="utf-8") as file:
        file.write(addition)

    return NoteResult(success=True, path=str(path.relative_to(VAULT_ROOT)), status="appended")


@app.get(
    "/notes/read",
    operation_id="readNote",
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Read one Obsidian note",
)
def read_note(path: str = Query(description="Vault-relative .md path")) -> dict:
    note_path = _safe_relative(path)
    _ensure_markdown(note_path)
    content = _read_text(note_path)
    return {"path": str(note_path.relative_to(VAULT_ROOT)), "content": content}


@app.post(
    "/notes/search",
    operation_id="searchNotes",
    dependencies=[Depends(require_auth)],
    tags=["notes"],
    summary="Search Markdown notes in the vault",
)
def search_notes(req: SearchRequest) -> dict:
    root = _safe_relative(req.folder) if req.folder else VAULT_ROOT
    if not root.exists():
        return {"query": req.query, "results": []}

    needle = req.query.casefold()
    results = []
    for path in root.rglob("*.md"):
        if not path.is_file() or path.stat().st_size > MAX_NOTE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        title_match = needle in path.stem.casefold()
        text_folded = text.casefold()
        pos = text_folded.find(needle)
        if pos < 0 and not title_match:
            continue
        if pos >= 0:
            snippet_start = max(0, pos - 120)
            snippet_end = min(len(text), pos + len(req.query) + 220)
            snippet = text[snippet_start:snippet_end].replace("\n", " ").strip()
        else:
            snippet = text[:320].replace("\n", " ").strip()
        results.append(
            {
                "path": str(path.relative_to(VAULT_ROOT)),
                "title": path.stem,
                "snippet": snippet[:400],
            }
        )
        if len(results) >= req.limit:
            break

    return {"query": req.query, "results": results}


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
        folder_path = _safe_relative(req.folder)
        if not folder_path.exists():
            return {"text": req.text, "results": []}
        folder = str(folder_path.relative_to(VAULT_ROOT)).replace("\\", "/")

    SEMANTIC_INDEX.reconfigure(vault_root=VAULT_ROOT, max_note_bytes=MAX_NOTE_BYTES)
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
    root = _safe_relative(folder) if folder else VAULT_ROOT
    if not root.exists():
        return {"folder": folder, "notes": []}
    notes = []
    for path in sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        notes.append(
            {
                "path": str(path.relative_to(VAULT_ROOT)),
                "title": path.stem,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            }
        )
        if len(notes) >= limit:
            break
    return {"folder": folder, "notes": notes}


@app.get("/privacy", response_class=PlainTextResponse, include_in_schema=False)
def privacy() -> str:
    return (
        "This private Obsidian bridge stores request content only as Markdown files in the configured vault. "
        "It does not intentionally send vault data to third parties. Access is protected by an API key."
    )
