from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Settings
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.indexer import BackgroundSemanticIndexer
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_vault_service(request: Request) -> VaultService:
    return request.app.state.vault_service


def get_semantic_search_service(request: Request) -> SemanticSearchService:
    return request.app.state.semantic_search_service


def get_semantic_indexer(request: Request) -> BackgroundSemanticIndexer:
    return request.app.state.semantic_indexer


def get_duplicate_candidate_service(request: Request) -> DuplicateCandidateService:
    return request.app.state.duplicate_candidate_service


def require_auth(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    api_key = settings.api_key.get_secret_value()
    if not api_key:
        raise HTTPException(status_code=500, detail="Server API_KEY is not configured")

    presented = (authorization or "").encode("utf-8")
    current_matches = hmac.compare_digest(presented, f"Bearer {api_key}".encode("utf-8"))
    previous_api_key = settings.previous_api_key.get_secret_value()
    previous_matches = bool(previous_api_key) and hmac.compare_digest(
        presented,
        f"Bearer {previous_api_key}".encode("utf-8"),
    )
    if not (current_matches | previous_matches):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
