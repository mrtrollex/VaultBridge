from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.vault import VaultService
from app.services.wikilinks import Wikilink, WikilinkResolver


@dataclass(frozen=True)
class OutgoingRelationship:
    """One outgoing wikilink occurrence from a verified live Markdown note."""

    target: str
    heading: str | None
    alias: str | None
    resolved_path: str | None

    @property
    def state(self) -> Literal["resolved", "unresolved"]:
        return "resolved" if self.resolved_path is not None else "unresolved"

    @classmethod
    def from_wikilink(cls, link: Wikilink) -> OutgoingRelationship:
        return cls(
            target=link.target,
            heading=link.heading,
            alias=link.alias,
            resolved_path=link.resolved_path,
        )


class RelationshipService:
    """Derive read-only note relationships from live vault Markdown."""

    def __init__(self, vault_service: VaultService) -> None:
        self._vault_service = vault_service
        self._wikilink_resolver = WikilinkResolver(vault_service)

    def outgoing_relationships(self, source_path: str) -> tuple[OutgoingRelationship, ...]:
        """Return outgoing relationship occurrences in source order."""
        source = self._vault_service.read_note(source_path)
        return tuple(
            OutgoingRelationship.from_wikilink(link)
            for link in self._wikilink_resolver.resolve_markdown(source.content)
        )
