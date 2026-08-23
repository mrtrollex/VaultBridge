from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Settings
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_vault_service(request: Request) -> VaultService:
    return request.app.state.vault_service


def get_semantic_search_service(request: Request) -> SemanticSearchService:
    return request.app.state.semantic_search_service


def require_auth(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    api_key = settings.api_key.get_secret_value()
    if not api_key:
        raise HTTPException(status_code=500, detail="Server API_KEY is not configured")
    expected = f"Bearer {api_key}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
