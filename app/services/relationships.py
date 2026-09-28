from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.markdown_links import MarkdownLink, MarkdownLinkResolver
from app.services.vault import (
    MarkdownPathCandidateSnapshot,
    NoteNotFoundError,
    VaultService,
)
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


@dataclass(frozen=True)
class OutgoingMarkdownRelationship:
    """One outgoing standard Markdown note-link occurrence."""

    destination: str
    fragment: str | None
    label: str
    resolved_path: str | None

    @property
    def state(self) -> Literal["resolved", "unresolved"]:
        return "resolved" if self.resolved_path is not None else "unresolved"

    @classmethod
    def from_markdown_link(cls, link: MarkdownLink) -> OutgoingMarkdownRelationship:
        return cls(
            destination=link.destination,
            fragment=link.fragment,
            label=link.label,
            resolved_path=link.resolved_path,
        )


@dataclass(frozen=True)
class MarkdownBacklink:
    """One distinct verified standard Markdown link to a requested note."""

    source_path: str
    destination: str
    fragment: str | None
    label: str


@dataclass(frozen=True)
class RelationshipOccurrence:
    """One immutable normalized note-link occurrence from a verified source."""

    source_path: str
    written_target: str
    resolved_path: str | None
    resolution: Literal["resolved", "missing", "ambiguous", "unsafe"]
    origin: Literal["obsidian_wikilink", "markdown_link"]
    relationship_type: Literal["note_link"]
    fragment: str | None
    label: str | None
    source_order: int
    origin_metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RelationshipResolutionSnapshot:
    """Opaque immutable query-level resolution facts owned by RelationshipService."""

    _wikilinks: WikilinkResolutionSnapshot


class RelationshipService:
    """Derive read-only note relationships from live vault Markdown."""

    def __init__(self, vault_service: VaultService) -> None:
        self._vault_service = vault_service
        self._wikilink_resolver = WikilinkResolver(vault_service)
        self._markdown_link_resolver = MarkdownLinkResolver(vault_service)

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

    def outgoing_markdown_relationships(
        self,
        source_path: str,
    ) -> tuple[OutgoingMarkdownRelationship, ...]:
        """Return source-relative standard Markdown relationships in source order."""
        source = self._vault_service.read_note(source_path)
        return self._derive_outgoing_markdown_relationships(
            source.content,
            source_path=source.path,
        )

    def _derive_outgoing_markdown_relationships(
        self,
        markdown: str,
        *,
        source_path: str,
    ) -> tuple[OutgoingMarkdownRelationship, ...]:
        return tuple(
            OutgoingMarkdownRelationship.from_markdown_link(link)
            for link in self._markdown_link_resolver.resolve_markdown(
                markdown,
                source_path=source_path,
            )
        )

    def markdown_backlinks(self, target_path: str) -> tuple[MarkdownBacklink, ...]:
        """Return distinct verified standard Markdown links to one exact live target."""
        canonical_target = self._vault_service.verify_existing_markdown_path(
            target_path,
            exact_spelling=True,
        )
        if canonical_target is None:
            self._vault_service.read_note(target_path)
            raise NoteNotFoundError("Note not found")

        source_paths = sorted(
            set(self._vault_service.live_markdown_paths()),
            key=lambda path: (path.casefold(), path),
        )
        results: list[MarkdownBacklink] = []
        seen: set[tuple[str, str, str | None, str]] = set()
        for source_path in source_paths:
            source = self._vault_service.read_note(source_path)
            relationships = self._derive_outgoing_markdown_relationships(
                source.content,
                source_path=source.path,
            )
            for relationship in relationships:
                if relationship.resolved_path != canonical_target:
                    continue
                key = (
                    source.path,
                    relationship.destination,
                    relationship.fragment,
                    relationship.label,
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    MarkdownBacklink(
                        source_path=source.path,
                        destination=relationship.destination,
                        fragment=relationship.fragment,
                        label=relationship.label,
                    )
                )
        return tuple(results)

    def normalized_outgoing_relationships(
        self,
        source_path: str,
    ) -> tuple[RelationshipOccurrence, ...]:
        """Return both supported relationship dialects in true document order."""
        source = self._vault_service.read_note(source_path)
        return self._derive_normalized_relationships(
            source.content,
            source_path=source.path.replace("\\", "/"),
        )

    def normalized_resolution_snapshot(
        self,
        candidates: MarkdownPathCandidateSnapshot,
    ) -> RelationshipResolutionSnapshot:
        """Build one reusable normalized-resolution snapshot from one vault enumeration."""
        return RelationshipResolutionSnapshot(
            WikilinkResolutionSnapshot.from_candidates(
                candidates.live_candidates,
                unsafe_unqualified_names=candidates.unsafe_unqualified_names,
            )
        )

    def normalized_relationships_from_content(
        self,
        content: str,
        *,
        source_path: str,
        snapshot: RelationshipResolutionSnapshot,
    ) -> tuple[RelationshipOccurrence, ...]:
        """Derive normalized relationships from one already verified content snapshot."""
        return self._derive_normalized_relationships(
            content,
            source_path=source_path.replace("\\", "/"),
            wikilink_snapshot=snapshot._wikilinks,
        )

    def _derive_normalized_relationships(
        self,
        markdown: str,
        *,
        source_path: str,
        wikilink_snapshot: WikilinkResolutionSnapshot | None = None,
    ) -> tuple[RelationshipOccurrence, ...]:
        snapshot = (
            self._wikilink_resolver.resolution_snapshot(include_unsafe=True)
            if wikilink_snapshot is None
            else wikilink_snapshot
        )
        positioned: list[
            tuple[
                int,
                Literal["obsidian_wikilink", "markdown_link"],
                Wikilink | MarkdownLink,
                Literal["resolved", "missing", "ambiguous", "unsafe"],
            ]
        ] = []

        for link in self._wikilink_resolver.parse(markdown):
            resolved, resolution = self._wikilink_resolver.resolve_with_reason(
                link,
                snapshot=snapshot,
            )
            positioned.append(
                (link._source_position, "obsidian_wikilink", resolved, resolution)
            )

        for link in self._markdown_link_resolver.parse(markdown):
            resolved, resolution = self._markdown_link_resolver.resolve_with_reason(
                link,
                source_path=source_path,
            )
            positioned.append(
                (link._source_position, "markdown_link", resolved, resolution)
            )

        positioned.sort(key=lambda item: item[0])
        relationships: list[RelationshipOccurrence] = []
        for source_order, (_, origin, resolved, resolution) in enumerate(positioned):
            if isinstance(resolved, Wikilink):
                written_target = resolved.target
                fragment = resolved.heading
                label = resolved.alias
            else:
                written_target = resolved.destination
                fragment = resolved.fragment
                label = resolved.label
            relationships.append(
                RelationshipOccurrence(
                    source_path=source_path,
                    written_target=written_target,
                    resolved_path=resolved.resolved_path,
                    resolution=resolution,
                    origin=origin,
                    relationship_type="note_link",
                    fragment=fragment,
                    label=label,
                    source_order=source_order,
                )
            )
        return tuple(relationships)

    def normalized_backlinks(
        self,
        target_path: str,
    ) -> tuple[RelationshipOccurrence, ...]:
        """Return exact distinct normalized occurrences resolving to one live target."""
        canonical_target = self._vault_service.verify_existing_markdown_path(
            target_path,
            exact_spelling=True,
        )
        if canonical_target is None:
            self._vault_service.read_note(target_path)
            raise NoteNotFoundError("Note not found")

        snapshot = self._wikilink_resolver.resolution_snapshot()
        source_paths = sorted(
            set(self._vault_service.live_markdown_paths()),
            key=lambda path: (path.casefold(), path),
        )
        results: list[RelationshipOccurrence] = []
        seen: set[
            tuple[
                str,
                str,
                str | None,
                str,
                str,
                str,
                str | None,
                str | None,
                tuple[tuple[str, str], ...],
            ]
        ] = set()
        for source_path in source_paths:
            source = self._vault_service.read_note(source_path)
            relationships = self._derive_normalized_relationships(
                source.content,
                source_path=source.path.replace("\\", "/"),
                wikilink_snapshot=snapshot,
            )
            for relationship in relationships:
                if relationship.resolved_path != canonical_target:
                    continue
                key = (
                    relationship.source_path,
                    relationship.written_target,
                    relationship.resolved_path,
                    relationship.resolution,
                    relationship.origin,
                    relationship.relationship_type,
                    relationship.fragment,
                    relationship.label,
                    relationship.origin_metadata,
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append(relationship)
        return tuple(results)
