from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.dependencies import PROTECTED_ROUTE_DEPENDENCIES, get_relationship_service
from app.services.relationships import RelationshipService

router = APIRouter()


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
    relationship_service: RelationshipService = Depends(get_relationship_service),
) -> NoteLinksResponse:
    relationships = relationship_service.outgoing_relationships(path)
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
    relationship_service: RelationshipService = Depends(get_relationship_service),
) -> NoteBacklinksResponse:
    backlinks = relationship_service.backlinks(path)
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
