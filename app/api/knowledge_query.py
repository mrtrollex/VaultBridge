"""Versioned Knowledge Query, executed by the shared strict read route."""

from fastapi import APIRouter, Body

from app.api.dependencies import PROTECTED_ROUTE_DEPENDENCIES
from app.api.space_reads import SpaceReadRoute
from app.space_read_transport import NoteMatchOutput, ScopedReadOutput, SemanticMatchOutput

router = APIRouter(route_class=SpaceReadRoute)


@router.post(
    "/api/v1/knowledge/query", operation_id="queryKnowledgeV1",
    dependencies=PROTECTED_ROUTE_DEPENDENCIES,
    response_model=ScopedReadOutput[NoteMatchOutput] | ScopedReadOutput[SemanticMatchOutput],
)
def query_knowledge(request: dict = Body(...)):
    """Query selected spaces; semantic_text selects SemanticMatch output.

    SpaceReadRoute validates raw JSON before any framework coercion and dispatches
    through SpaceOperations. The generated schema is installed by read_openapi.
    """
    raise RuntimeError("Knowledge Query must use the strict read route")
