"""Named-only, strictly projected discovery and selected semantic status."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from app.api.dependencies import PROTECTED_ROUTE_DEPENDENCIES, get_space_operations
from app.read_execution import run_read
from app.services.knowledge_spaces import SpaceError
from app.space_read_transport import InvalidReadRequest, public_error, selection


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpaceChoice(_Output):
    space_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    read_capabilities: list[Literal["read_note", "list_notes", "literal_search", "semantic_retrieval",
                                    "knowledge_query", "relationships"]]
    relationship_dialects: list[Literal["obsidian_wikilink", "markdown_link"]]
    indexing: Literal["enabled", "disabled"]


class SpaceChoices(_Output):
    spaces: list[SpaceChoice] = Field(max_length=8)


class SpaceStatus(_Output):
    space_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    index_state: Literal["disabled", "not_permitted", "indexing", "ready", "unavailable"]
    search_available: bool
    refresh_required: bool


class SpaceStatuses(_Output):
    spaces: list[SpaceStatus] = Field(min_length=1, max_length=8)


router = APIRouter(dependencies=list(PROTECTED_ROUTE_DEPENDENCIES))


async def _execute(request, *, status):
    try:
        if await request.body():
            raise InvalidReadRequest()
        operations = get_space_operations(request)
        if not status:
            if request.query_params:
                raise InvalidReadRequest()
            return SpaceChoices(spaces=operations.readable_spaces()).model_dump()
        if any(key != "spaces" for key in request.query_params):
            if any(key in {"space", "scope", "space_id"} for key in request.query_params):
                raise SpaceError("invalid_scope")
            raise InvalidReadRequest()
        scope = selection("list", {"spaces": request.query_params.getlist("spaces")}) if request.query_params else None
        rows = await run_read(lambda token: operations.selected_space_status(scope, cancel=token),
                              disconnected=request.is_disconnected)
        return SpaceStatuses(spaces=rows).model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        code, status_code, _ = public_error(exc)
        return JSONResponse({"detail": code}, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/knowledge/spaces", operation_id="listKnowledgeSpacesV1", response_model=SpaceChoices)
async def list_spaces(request: Request):
    return await _execute(request, status=False)


@router.get("/api/v1/knowledge/spaces/status", operation_id="getKnowledgeSpaceStatusV1", response_model=SpaceStatuses)
async def selected_status(request: Request):
    return await _execute(request, status=True)
