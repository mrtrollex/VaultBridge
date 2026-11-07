from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.api.dependencies import get_semantic_indexer, get_semantic_search_service, get_vault_service

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


class NamedHealthResponse(BaseModel):
    ok: bool


class LivenessResponse(BaseModel):
    ok: Literal[True] = True


class ReadinessResponse(BaseModel):
    ready: bool


@router.get(
    "/health/live",
    operation_id="livenessCheck",
    tags=["system"],
    response_model=LivenessResponse,
)
def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/health/ready",
    operation_id="readinessCheck",
    tags=["system"],
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
def readiness(
    response: Response,
    request: Request,
) -> ReadinessResponse:
    if request.app.state.settings.knowledge_spaces_json is not None:
        operations = request.app.state.space_operations
        ready = (operations._admission is not None and operations._admission.running
                 and operations.probe_default_readiness())
    else:
        ready = (
            get_vault_service(request).vault_available()
            and get_semantic_search_service(request).probe_search_availability()
        )
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(ready=ready)


@router.get(
    "/health",
    operation_id="healthCheck",
    tags=["system"],
    response_model=HealthResponse | NamedHealthResponse,
)
def health(
    request: Request,
) -> HealthResponse | NamedHealthResponse:
    if request.app.state.settings.knowledge_spaces_json is not None:
        admission = request.app.state.space_operations._admission
        return NamedHealthResponse(ok=admission is not None and admission.running)
    semantic_search_service = get_semantic_search_service(request)
    semantic_indexer = get_semantic_indexer(request)
    vault_service = get_vault_service(request)
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
