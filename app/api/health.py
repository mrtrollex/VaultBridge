from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.api.dependencies import get_semantic_indexer, get_semantic_search_service, get_vault_service
from app.services.indexer import BackgroundSemanticIndexer
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService

router = APIRouter()


class HealthResponse(BaseModel):
    ok: bool
    vault_exists: bool
    semantic_index_ready: bool
    semantic_index_state: Literal["uninitialized", "indexing", "ready", "error"]
    semantic_search_available: bool
    semantic_indexer_running: bool
    full_sync_required: bool
    indexed_notes: int
    semantic_chunks: int
    vault_notes: int
    last_successful_sync: str | None


@router.get(
    "/health",
    operation_id="healthCheck",
    tags=["system"],
    response_model=HealthResponse,
)
def health(
    semantic_search_service: SemanticSearchService = Depends(get_semantic_search_service),
    semantic_indexer: BackgroundSemanticIndexer = Depends(get_semantic_indexer),
    vault_service: VaultService = Depends(get_vault_service),
) -> HealthResponse:
    semantic_status = semantic_search_service.health_status()
    return HealthResponse(
        ok=True,
        vault_exists=vault_service.vault_exists(),
        semantic_index_ready=semantic_status.state.value == "ready",
        semantic_index_state=semantic_status.state.value,
        semantic_search_available=semantic_status.search_available,
        semantic_indexer_running=semantic_indexer.is_running,
        full_sync_required=semantic_indexer.requires_full_sync,
        indexed_notes=semantic_status.indexed_notes,
        semantic_chunks=semantic_status.semantic_chunks,
        vault_notes=vault_service.count_notes(),
        last_successful_sync=semantic_status.last_successful_sync,
    )


@router.get("/privacy", response_class=PlainTextResponse, include_in_schema=False)
def privacy() -> str:
    return (
        "VaultBridge stores request content only as Markdown files in the configured vault. "
        "It does not intentionally send vault data to third parties. Access is protected by an API key."
    )
