from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.vault import NoteNotFoundError, VaultService
from app.services.wikilinks import Wikilink, WikilinkResolutionSnapshot, WikilinkResolver


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


@dataclass(frozen=True)
class Backlink:
    """One distinct verified link from a canonical source note to a requested note."""

    source_path: str
    target: str
    heading: str | None
    alias: str | None


class RelationshipService:
    """Derive read-only note relationships from live vault Markdown."""

    def __init__(self, vault_service: VaultService) -> None:
        self._vault_service = vault_service
        self._wikilink_resolver = WikilinkResolver(vault_service)

    def outgoing_relationships(self, source_path: str) -> tuple[OutgoingRelationship, ...]:
        """Return outgoing relationship occurrences in source order."""
        source = self._vault_service.read_note(source_path)
        return self._derive_outgoing_relationships(source.content)

    def _derive_outgoing_relationships(
        self,
        markdown: str,
        *,
        snapshot: WikilinkResolutionSnapshot | None = None,
    ) -> tuple[OutgoingRelationship, ...]:
        return tuple(
            OutgoingRelationship.from_wikilink(link)
            for link in self._wikilink_resolver.resolve_markdown(
                markdown,
                snapshot=snapshot,
            )
        )

    def backlinks(self, target_path: str) -> tuple[Backlink, ...]:
        """Return distinct verified links to one exact live Markdown target.

        Results use canonical source-path order and source relationship order. Exact duplicates
        with the same source path, written target, heading, and alias are returned once.
        """
        canonical_target = self._vault_service.verify_existing_markdown_path(
            target_path,
            exact_spelling=True,
        )
        if canonical_target is None:
            # Preserve VaultService's public missing/invalid-path errors. A read can succeed only
            # for a case-insensitive spelling mismatch on filesystems that permit one.
            self._vault_service.read_note(target_path)
            raise NoteNotFoundError("Note not found")

        snapshot = self._wikilink_resolver.resolution_snapshot()
        source_paths = sorted(
            set(self._vault_service.live_markdown_paths()),
            key=lambda path: (path.casefold(), path),
        )
        results: list[Backlink] = []
        seen: set[tuple[str, str, str | None, str | None]] = set()
        for source_path in source_paths:
            source = self._vault_service.read_note(source_path)
            relationships = self._derive_outgoing_relationships(
                source.content,
                snapshot=snapshot,
            )
            for relationship in relationships:
                if relationship.resolved_path != canonical_target:
                    continue
                key = (
                    source.path,
                    relationship.target,
                    relationship.heading,
                    relationship.alias,
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    Backlink(
                        source_path=source.path,
                        target=relationship.target,
                        heading=relationship.heading,
                        alias=relationship.alias,
                    )
                )
        return tuple(results)
