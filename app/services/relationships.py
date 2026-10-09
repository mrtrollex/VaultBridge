from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.markdown_links import MarkdownLink, MarkdownLinkResolver
from app.services.vault import (
    BoundedDirectoryAliasFact,
    LiveMarkdownPathCandidate,
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
    _exact_candidates: dict[str, LiveMarkdownPathCandidate] | None = None
    _directory_alias_facts: dict[str, BoundedDirectoryAliasFact] | None = None
    _selected_paths: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class BoundedRelationshipDerivation:
    occurrences: tuple[RelationshipOccurrence, ...]
    state: Literal["complete", "limited"]
    reason: Literal["relationship_limit"] | None

    def __post_init__(self):
        if (type(self.occurrences) is not tuple
                or (self.state, self.reason) not in {("complete", None), ("limited", "relationship_limit")}):
            raise ValueError("invalid bounded relationship derivation")


class ScopedRelationshipSnapshot:
    """Complete local supplied universe with request-accounted live proof."""

    def __init__(self, snapshot, session):
        self.snapshot, self.session = snapshot, session
        session.resolution_snapshot = snapshot
        self.facts = {fact.path: fact for fact in snapshot.facts}
        candidates = tuple(LiveMarkdownPathCandidate(spelling, canonical)
                           for spelling, canonical in snapshot.spellings.items())
        self.wikilinks = WikilinkResolutionSnapshot.from_candidates(
            candidates, unsafe_unqualified_names=snapshot.unsafe_names,
        )
        self.exact_candidates = {candidate.discovered_path: candidate for candidate in candidates}

    def exact(self, relative):
        from app.services._vault_writes import UnsafeWritePathError
        from app.services.vault import NoteUnavailableError

        try:
            canonical, info = self.session.resolve(relative)
        except FileNotFoundError:
            if relative in self.snapshot.spellings or any(
                    relative == alias or relative.startswith(alias + "/")
                    for alias in self.snapshot.directory_aliases):
                raise NoteUnavailableError("note_unavailable") from None
            return None, "missing"
        except UnsafeWritePathError:
            return None, "unsafe"
        fact = self.facts.get(canonical)
        if fact is None:
            return None, "missing"
        if self.session.identity(info) != fact.identity:
            raise NoteUnavailableError("note_unavailable")
        self.session.verify(fact)
        return canonical, "resolved"

    def verify_snapshot_markdown_spelling(self, relative, candidates, **kwargs):
        from app.services.vault import MarkdownPathVerification

        path, reason = self.exact(relative)
        return MarkdownPathVerification(reason, path)

    def verify_snapshot_source_relative_markdown_path_result(self, source, destination, candidates, **kwargs):
        from pathlib import PurePosixPath

        return self.verify_snapshot_markdown_spelling(
            (PurePosixPath(source).parent / destination).as_posix(), candidates,
        )

    def verify_snapshot_unqualified_markdown_candidates(self, candidates, exact_candidates):
        from app.services.vault import NoteUnavailableError

        for candidate in candidates:
            path, outcome = self.exact(candidate.discovered_path)
            if outcome != "resolved" or path != candidate.canonical_path:
                raise NoteUnavailableError("note_unavailable")

    def resolve(self, source, origin, target):
        # Existing dialect resolvers retain syntax/ambiguity/locality semantics;
        # their verification seam is supplied by this accounted vault snapshot.
        if origin == "obsidian_wikilink":
            link, reason = WikilinkResolver(self).resolve_with_reason(
                Wikilink(target), snapshot=self.wikilinks, exact_candidates=self.exact_candidates,
            )
        else:
            link, reason = MarkdownLinkResolver(self).resolve_with_reason(
                MarkdownLink(target, None, ""), source_path=source, exact_candidates=self.exact_candidates,
            )
        return link.resolved_path, reason


class RelationshipService:
    """Derive read-only note relationships from live vault Markdown."""

    def __init__(self, vault_service: VaultService) -> None:
        self._vault_service = vault_service
        self._wikilink_resolver = WikilinkResolver(vault_service)
        self._markdown_link_resolver = MarkdownLinkResolver(vault_service)

    def derive_normalized_bounded(self, content, *, source_path, snapshot, budget, cancel):
        """Admit a combined source-order occurrence before constructing/resolving it.

        The supplied resolution universe owns accounted containment verification;
        legacy eager resolution snapshots and APIs are deliberately unchanged.
        """
        from app.services._scoped_links import recognize
        from app.services.scoped_budget import BudgetExhausted

        cancel.check()
        if not budget.available:
            return BoundedRelationshipDerivation((), "limited", "relationship_limit")
        budget.source(source_path, len(content.encode("utf-8")))
        occurrences = []
        try:
            for position, origin, spec in recognize(content, budget):
                budget.try_admit()
                cancel.check()
                target, fragment, label = spec
                resolved, reason = snapshot.resolve(source_path, origin, target)
                occurrences.append(RelationshipOccurrence(
                    source_path, target, resolved, reason, origin, "note_link",
                    fragment, label, len(occurrences),
                ))
                if not budget.available:
                    return BoundedRelationshipDerivation(tuple(occurrences), "limited", "relationship_limit")
        except BudgetExhausted:
            return BoundedRelationshipDerivation(tuple(occurrences), "limited", "relationship_limit")
        if content and budget.parse_exhausted:
            return BoundedRelationshipDerivation(tuple(occurrences), "limited", "relationship_limit")
        return BoundedRelationshipDerivation(tuple(occurrences), "complete", None)

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
        exact_candidates = (
            {candidate.discovered_path: candidate
             for candidate in candidates.live_candidates}
            if all(candidate.spelling_fact is not None
                   for candidate in candidates.live_candidates)
            else None
        )
        return RelationshipResolutionSnapshot(
            WikilinkResolutionSnapshot.from_candidates(
                candidates.live_candidates,
                unsafe_unqualified_names=candidates.unsafe_unqualified_names,
            ),
            exact_candidates,
            ({fact.path: fact for fact in candidates.directory_alias_facts}
             if exact_candidates is not None else None),
            (frozenset(candidate.canonical_path for candidate in candidates.live_candidates)
             if exact_candidates is not None else None),
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
            exact_candidates=snapshot._exact_candidates,
            directory_alias_facts=snapshot._directory_alias_facts,
            selected_paths=snapshot._selected_paths,
        )

    def _derive_normalized_relationships(
        self,
        markdown: str,
        *,
        source_path: str,
        wikilink_snapshot: WikilinkResolutionSnapshot | None = None,
        exact_candidates: dict[str, LiveMarkdownPathCandidate] | None = None,
        directory_alias_facts: dict[str, BoundedDirectoryAliasFact] | None = None,
        selected_paths: frozenset[str] | None = None,
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
                exact_candidates=exact_candidates,
                directory_alias_facts=directory_alias_facts,
                selected_paths=selected_paths,
            )
            positioned.append(
                (link._source_position, "obsidian_wikilink", resolved, resolution)
            )

        for link in self._markdown_link_resolver.parse(markdown):
            resolved, resolution = self._markdown_link_resolver.resolve_with_reason(
                link,
                source_path=source_path,
                exact_candidates=exact_candidates,
                directory_alias_facts=directory_alias_facts,
                selected_paths=selected_paths,
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
