"""Typed, read-only REST transport for the existing hygiene domain result."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from app.api.dependencies import PROTECTED_ROUTE_DEPENDENCIES, get_knowledge_hygiene_service
from app.knowledge_hygiene_transport import (
    KnowledgeHygieneScanRequest,
    KnowledgeHygieneScanResponse,
    TransportModel,
    serialize_hygiene_result,
)
from app.services import knowledge_hygiene as domain


class HygieneRoute(APIRoute):
    """Sanitize this operation, including parsing, dependency and serialization failures."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "invalid_request"})
            except domain.KnowledgeHygieneError as exc:
                if exc.reason == "invalid_request":
                    return JSONResponse(status_code=422, content={"detail": "invalid_request"})
                if exc.reason == "scan_unavailable":
                    return JSONResponse(status_code=503, content={"detail": "scan_unavailable"})
                return JSONResponse(status_code=500, content={"detail": "internal_error"})
            except HTTPException as exc:
                # FastAPI wraps JSON encoding failures in this Starlette parsing error.
                # Require both its marker and decoding cause; other HTTP errors retain
                # their existing status, detail and headers.
                if (
                    exc.status_code == 400
                    and exc.detail == "There was an error parsing the body"
                    and isinstance(exc.__cause__, UnicodeDecodeError)
                ):
                    return JSONResponse(status_code=422, content={"detail": "invalid_request"})
                # Existing authentication/configuration/rate-limit responses and headers.
                raise
            except Exception:
                return JSONResponse(status_code=500, content={"detail": "internal_error"})

        return private_handler


router = APIRouter(route_class=HygieneRoute)
class HygieneErrorResponse(TransportModel):
    detail: Literal["invalid_request", "scan_unavailable", "internal_error"]


@router.post(
    "/api/v1/knowledge/hygiene/scan",
    operation_id="scanKnowledgeHygieneV1",
    response_model=KnowledgeHygieneScanResponse,
    dependencies=PROTECTED_ROUTE_DEPENDENCIES,
    tags=["knowledge"],
    summary="Run bounded read-only Knowledge Hygiene diagnostics",
    description=(
        "Partial scan, candidate and derived-index evidence returns a successful result. "
        "No repairs or index refresh."
    ),
    responses={
        422: {"model": HygieneErrorResponse, "description": "Invalid caller request"},
        503: {"model": HygieneErrorResponse, "description": "No safe bounded scan view"},
        500: {"model": HygieneErrorResponse, "description": "Unexpected internal error"},
    },
)
def scan_knowledge_hygiene(
    request: KnowledgeHygieneScanRequest,
    service: domain.KnowledgeHygieneService = Depends(get_knowledge_hygiene_service),
) -> KnowledgeHygieneScanResponse:
    result = service.scan(request.to_domain())
    return serialize_hygiene_result(result)
