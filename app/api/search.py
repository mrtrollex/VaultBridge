from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import (
    PROTECTED_ROUTE_DEPENDENCIES,
    get_duplicate_candidate_service,
    get_semantic_search_service,
    get_settings,
    get_vault_service,
)
from app.api.versioning import versioned_api_route
from app.core.config import Settings
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.semantic_search import SemanticSearchService, SemanticSearchUnavailableError
from app.services.vault import VaultService

router = APIRouter()

RELATED_CANDIDATE_OVERFETCH_FACTOR = 3
RELATED_CANDIDATE_LIMIT = 50


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


class DuplicateCandidatesRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180, description="Prospective note title")
    text: str = Field(default="", max_length=4000, description="Optional concise content excerpt")
    folder: str = Field(default="", max_length=500, description="Optional vault-relative folder")
    limit: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(
        default=0.28,
        ge=-1.0,
        le=1.0,
        description="Minimum semantic cosine similarity before hybrid reranking",
    )

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class DuplicateCandidateResult(BaseModel):
    path: str
    title: str
    match_type: Literal["exact_title", "semantic"]
    score: float | None
    semantic_score: float | None
    lexical_score: float | None
    snippet: str | None
    heading: str | None


class DuplicateCandidatesResponse(BaseModel):
    title: str
    results: list[DuplicateCandidateResult]


@versioned_api_route(
    router,
    "/notes/search",
    operation_id="searchNotes",
    methods=["POST"],
    dependencies=PROTECTED_ROUTE_DEPENDENCIES,
    tags=["notes"],
    summary="Search Markdown notes in the vault",
)
def search_notes(
    req: SearchRequest,
    vault_service: VaultService = Depends(get_vault_service),
) -> dict:
    results = vault_service.search_notes(query=req.query, folder=req.folder, limit=req.limit)
    return {
        "query": req.query,
        "results": [
            {"path": result.path, "title": result.title, "snippet": result.snippet} for result in results
        ],
    }


@versioned_api_route(
    router,
    "/notes/related",
    operation_id="findRelatedNotes",
    methods=["POST"],
    dependencies=PROTECTED_ROUTE_DEPENDENCIES,
    tags=["notes"],
    summary="Find conceptually related Obsidian notes",
)
def find_related_notes(
    req: RelatedNotesRequest,
    settings: Settings = Depends(get_settings),
    semantic_search_service: SemanticSearchService = Depends(get_semantic_search_service),
    vault_service: VaultService = Depends(get_vault_service),
) -> dict:
    folder = ""
    if req.folder:
        existing_folder = vault_service.existing_relative_path(req.folder)
        if existing_folder is None:
            return {"text": req.text, "results": []}
        folder = existing_folder

    semantic_search_service.reconfigure(
        vault_root=settings.vault_path,
        max_note_bytes=settings.max_note_bytes,
    )
    # Keep backfill bounded while allowing a small stale prefix to be filtered without
    # needlessly shortening the caller-visible result list.
    candidate_limit = min(
        RELATED_CANDIDATE_LIMIT,
        req.limit * RELATED_CANDIDATE_OVERFETCH_FACTOR,
    )
    try:
        results = semantic_search_service.search(
            req.text,
            folder=folder,
            limit=candidate_limit,
            min_score=req.min_score,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Semantic search unavailable: {exc}") from exc

    verified_results = []
    for result in results:
        verified_path = vault_service.verify_existing_markdown_path(result.path, folder=folder)
        if verified_path is None:
            continue
        verified_results.append(
            {
                "path": verified_path,
                "title": PurePosixPath(verified_path).stem,
                "score": result.score,
                "semantic_score": result.semantic_score,
                "lexical_score": result.lexical_score,
                "snippet": result.snippet,
                "heading": result.heading,
            }
        )
        if len(verified_results) >= req.limit:
            break

    return {"text": req.text, "results": verified_results}


@versioned_api_route(
    router,
    "/notes/duplicates",
    operation_id="findDuplicateCandidates",
    methods=["POST"],
    dependencies=PROTECTED_ROUTE_DEPENDENCIES,
    response_model=DuplicateCandidatesResponse,
    tags=["notes"],
    summary="Find advisory duplicate candidates for a prospective note",
)
def find_duplicate_candidates(
    req: DuplicateCandidatesRequest,
    duplicate_candidate_service: DuplicateCandidateService = Depends(
        get_duplicate_candidate_service
    ),
) -> dict:
    try:
        candidates = duplicate_candidate_service.find_candidates(
            title=req.title,
            text=req.text,
            folder=req.folder,
            limit=req.limit,
            min_score=req.min_score,
        )
    except SemanticSearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"Semantic search unavailable: {exc}") from exc
    return {"title": req.title, "results": candidates}
