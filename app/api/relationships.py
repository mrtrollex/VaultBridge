from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.dependencies import PROTECTED_ROUTE_DEPENDENCIES, get_space_operations
from app.api.space_reads import SpaceReadRoute
from app.services.space_operations import SpaceOperations

router = APIRouter(route_class=SpaceReadRoute)


class NoteLink(BaseModel):
    target: str
    heading: str | None
    alias: str | None
    state: Literal["resolved", "unresolved"]
    resolved_path: str | None


class NoteLinksResponse(BaseModel):
    links: list[NoteLink]


class NoteBacklink(BaseModel):
    source_path: str
    target: str
    heading: str | None
    alias: str | None


class NoteBacklinksResponse(BaseModel):
    backlinks: list[NoteBacklink]


@router.get(
    "/api/v1/notes/links",
    operation_id="listNoteLinksV1",
    response_model=NoteLinksResponse,
    dependencies=PROTECTED_ROUTE_DEPENDENCIES,
    tags=["notes"],
    summary="List outgoing relationships from an Obsidian note",
)
def list_note_links(
    path: str = Query(description="Vault-relative .md path"),
    operations: SpaceOperations = Depends(get_space_operations),
) -> NoteLinksResponse:
    relationships = operations.compatibility_read("links", path=path)
    return NoteLinksResponse(
        links=[
            NoteLink(
                target=relationship.target,
                heading=relationship.heading,
                alias=relationship.alias,
                state=relationship.state,
                resolved_path=relationship.resolved_path,
            )
            for relationship in relationships
        ]
    )


@router.get(
    "/api/v1/notes/backlinks",
    operation_id="listNoteBacklinksV1",
    response_model=NoteBacklinksResponse,
    dependencies=PROTECTED_ROUTE_DEPENDENCIES,
    tags=["notes"],
    summary="List verified backlinks to an Obsidian note",
)
def list_note_backlinks(
    path: str = Query(description="Vault-relative .md path"),
    operations: SpaceOperations = Depends(get_space_operations),
) -> NoteBacklinksResponse:
    backlinks = operations.compatibility_read("backlinks", path=path)
    return NoteBacklinksResponse(
        backlinks=[
            NoteBacklink(
                source_path=backlink.source_path,
                target=backlink.target,
                heading=backlink.heading,
                alias=backlink.alias,
            )
            for backlink in backlinks
        ]
    )
