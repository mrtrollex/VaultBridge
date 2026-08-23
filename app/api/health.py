from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.api.dependencies import get_semantic_search_service, get_settings, get_vault_service
from app.core.config import Settings
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService

router = APIRouter()


@router.get("/health", operation_id="healthCheck", tags=["system"])
def health(
    settings: Settings = Depends(get_settings),
    semantic_search_service: SemanticSearchService = Depends(get_semantic_search_service),
    vault_service: VaultService = Depends(get_vault_service),
) -> dict:
    semantic_search_service.reconfigure(
        vault_root=settings.vault_path,
        max_note_bytes=settings.max_note_bytes,
    )
    return {
        "ok": True,
        "vault_exists": vault_service.vault_exists(),
        "semantic_index_ready": semantic_search_service.is_ready(),
    }


@router.get("/privacy", response_class=PlainTextResponse, include_in_schema=False)
def privacy() -> str:
    return (
        "VaultBridge stores request content only as Markdown files in the configured vault. "
        "It does not intentionally send vault data to third parties. Access is protected by an API key."
    )
