from __future__ import annotations

from fastapi import Depends, Header, Request

from app.core.config import Settings
from app.core.http_security import enforce_peer_rate_limit, verify_bearer_authorization
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.indexer import BackgroundSemanticIndexer
from app.services.rate_limiter import FixedWindowRateLimiter
from app.services.relationships import RelationshipService
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


def get_relationship_service(request: Request) -> RelationshipService:
    return request.app.state.relationship_service


def get_rate_limiter(request: Request) -> FixedWindowRateLimiter:
    return request.app.state.rate_limiter


def enforce_rate_limit(
    request: Request,
    settings: Settings = Depends(get_settings),
    rate_limiter: FixedWindowRateLimiter = Depends(get_rate_limiter),
) -> None:
    client_id = request.client.host if request.client is not None else "unknown-peer"
    enforce_peer_rate_limit(client_id, settings, rate_limiter)


def require_auth(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    verify_bearer_authorization(authorization, settings)


PROTECTED_ROUTE_DEPENDENCIES = (
    Depends(enforce_rate_limit),
    Depends(require_auth),
)
