"""Bounded, read-only facts about one live Markdown vault."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.frontmatter import FrontmatterParser, project_portable_fields
from app.services.relationships import RelationshipService
from app.services.semantic_search import SemanticSearchService, SemanticSearchUnavailableError
from app.services.vault import NoteReadResult, VaultService, VaultServiceError

Group: TypeAlias = Literal["relationships", "isolation", "frontmatter", "aliases"]
DiagnosticKind: TypeAlias = Literal[
    "missing_relationship_target", "unsafe_relationship_target",
    "ambiguous_relationship_target", "isolated_note", "duplicate_alias_in_note",
    "colliding_alias", "invalid_frontmatter", "invalid_portable_field",
    "empty_portable_field_value", "empty_authored_body", "duplicate_candidate",
    "near_duplicate_candidate", "previous_compatible_index", "derived_index_unavailable",
]
ScanReason: TypeAlias = Literal[
    "path_ceiling", "enumeration_unavailable", "note_unavailable", "relationship_unavailable"
]
CandidateReason: TypeAlias = Literal[
    "scan_partial", "source_unavailable", "candidate_unavailable", "semantic_unavailable"
]
DerivedStatus: TypeAlias = Literal[
    "not_requested", "compatible_ready", "compatible_previous_refresh",
    "compatible_previous_error", "missing", "incompatible", "invalid_metadata",
    "corrupt_storage", "storage_unavailable", "inspection_unavailable",
]

_GROUPS = frozenset(("relationships", "isolation", "frontmatter", "aliases"))
_SCAN_REASONS = (
    "path_ceiling", "enumeration_unavailable", "note_unavailable", "relationship_unavailable",
)
_CANDIDATE_REASONS = (
    "scan_partial", "source_unavailable", "candidate_unavailable", "semantic_unavailable",
)


@dataclass(frozen=True, slots=True)
class KnowledgeHygieneRequest:
    groups: frozenset[Group] = _GROUPS
    finding_limit: int = 500
    duplicate_source_limit: int = 0
    semantic_candidates: bool = False
    inspect_derived_index: bool = False


@dataclass(frozen=True, slots=True)
class RelationshipEvidence:
    origin: Literal["obsidian_wikilink", "markdown_link"]
    source_order: int


@dataclass(frozen=True, slots=True)
class FrontmatterEvidence:
    line: int | None = None
    column: int | None = None


@dataclass(frozen=True, slots=True)
class PortableFieldEvidence:
    field: Literal["aliases", "tags"]
    source_index: int | None = None


@dataclass(frozen=True, slots=True)
class DuplicateAliasEvidence:
    source_indices: tuple[int, int]


@dataclass(frozen=True, slots=True)
class CollidingAliasEvidence:
    source_index: int
    peer_count: int
    related_paths_truncated: bool


@dataclass(frozen=True, slots=True)
class RuleEvidence:
    """A fixed classification requiring no additional public evidence fields."""


FindingEvidence: TypeAlias = (
    RelationshipEvidence | FrontmatterEvidence | PortableFieldEvidence
    | DuplicateAliasEvidence | CollidingAliasEvidence | RuleEvidence
)


@dataclass(frozen=True, slots=True)
class DiagnosticFinding:
    kind: DiagnosticKind
    primary_path: str | None
    related_paths: tuple[str, ...]
    evidence_source: Literal["live_markdown", "derived_index"]
    category: str
    evidence: FindingEvidence


@dataclass(frozen=True, slots=True)
class ScanCompleteness:
    state: Literal["complete", "partial"]
    reasons: tuple[ScanReason, ...]
    eligible_paths: int
    inspected_notes: int
    unavailable_notes: int


@dataclass(frozen=True, slots=True)
class CandidateCoverage:
    state: Literal["not_requested", "complete", "partial", "unavailable"]
    source_notes: int
    reasons: tuple[CandidateReason, ...] = ()


@dataclass(frozen=True, slots=True)
class DerivedIndexEvidence:
    status: DerivedStatus


@dataclass(frozen=True, slots=True)
class KnowledgeHygieneResult:
    findings: tuple[DiagnosticFinding, ...]
    scan: ScanCompleteness
    candidates: CandidateCoverage
    derived_index: DerivedIndexEvidence
    findings_truncated: bool


class KnowledgeHygieneError(RuntimeError):
    """Expected privacy-safe service failure."""

    def __init__(self, reason: Literal["invalid_request", "scan_unavailable"]):
        super().__init__(reason)
        self.reason = reason


def _path_key(path: str) -> tuple[str, str]:
    return path.casefold(), path


class KnowledgeHygieneService:
    def __init__(
        self,
        *,
        vault_service: VaultService,
        relationship_service: RelationshipService,
        duplicate_candidate_service: DuplicateCandidateService,
        semantic_search_service: SemanticSearchService,
    ) -> None:
        self._vault = vault_service
        self._relationships = relationship_service
        self._duplicates = duplicate_candidate_service
        self._semantic = semantic_search_service

    @staticmethod
    def _validate(request: KnowledgeHygieneRequest) -> None:
        if (
            type(request) is not KnowledgeHygieneRequest
            or type(request.groups) is not frozenset
            or not request.groups <= _GROUPS
            or type(request.finding_limit) is not int
            or not 1 <= request.finding_limit <= 500
            or type(request.duplicate_source_limit) is not int
            or not 0 <= request.duplicate_source_limit <= 20
            or type(request.semantic_candidates) is not bool
            or (request.semantic_candidates and request.duplicate_source_limit == 0)
            or type(request.inspect_derived_index) is not bool
        ):
            raise KnowledgeHygieneError("invalid_request")

    def scan(self, request: KnowledgeHygieneRequest = KnowledgeHygieneRequest()) -> KnowledgeHygieneResult:
        self._validate(request)
        try:
            snapshot = self._vault.bounded_markdown_snapshot()
        except (VaultServiceError, OSError):
            raise KnowledgeHygieneError("scan_unavailable") from None

        reasons: set[str] = set()
        if not snapshot.complete:
            reasons.add("path_ceiling" if not snapshot.enumeration_unavailable else "enumeration_unavailable")
        paths = snapshot.paths
        path_facts = {fact.path: fact for fact in snapshot.path_facts}
        path_set = frozenset(paths)
        need_relationships = bool(request.groups & {"relationships", "isolation"})
        relationship_snapshot = None
        if need_relationships and snapshot.complete and snapshot.resolution_complete:
            try:
                relationship_snapshot = self._relationships.normalized_resolution_snapshot(
                    snapshot.candidates,
                )
            except (VaultServiceError, OSError):
                reasons.add("relationship_unavailable")
        elif need_relationships and snapshot.complete:
            reasons.add("relationship_unavailable")

        raw: dict[tuple[object, ...], tuple[DiagnosticFinding, str]] = {}

        def add(identity: tuple[object, ...], finding: DiagnosticFinding, alias_key: str = "") -> None:
            raw.setdefault(identity, (finding, alias_key))

        inspected_count = 0
        selected_sources: list[NoteReadResult] = []
        note_states: dict[str, bool] = {}
        unavailable: set[str] = set()
        outgoing: set[str] = set()
        incoming: set[str] = set()
        aliases: dict[str, dict[str, tuple[int, int | None]]] = {}
        for path in paths:
            try:
                source = self._vault.read_verified_markdown_snapshot(path, fact=path_facts[path])
            except (VaultServiceError, OSError, UnicodeError):
                unavailable.add(path)
                reasons.add("note_unavailable")
                continue
            inspected_count += 1
            if len(selected_sources) < request.duplicate_source_limit:
                selected_sources.append(source)
            frontmatter = FrontmatterParser.parse(source.content)
            capture_reliable = frontmatter.state != "invalid"
            if frontmatter.state == "valid":
                assert frontmatter.metadata is not None
                # The portable capture states are intake states. Any other explicit
                # value is not reliable evidence of a curated note.
                capture_reliable = "capture_state" not in frontmatter.metadata
                projection = project_portable_fields(frontmatter)
                assert projection is not None
            else:
                projection = None
            note_states[path] = capture_reliable

            if "frontmatter" in request.groups:
                if frontmatter.state == "invalid":
                    assert frontmatter.diagnostic is not None
                    diagnostic = frontmatter.diagnostic
                    add((path, "invalid_frontmatter"), DiagnosticFinding(
                        "invalid_frontmatter", path, (), "live_markdown", diagnostic.reason,
                        FrontmatterEvidence(diagnostic.line, diagnostic.column),
                    ))
                else:
                    if frontmatter.state == "valid":
                        assert frontmatter.body_offset is not None
                    body = (
                        source.content[frontmatter.body_offset:]
                        if frontmatter.state == "valid" else (
                            source.content[1:] if source.content.startswith("\ufeff") else source.content
                        )
                    )
                    if not any(not char.isspace() for char in body):
                        add((path, "empty_authored_body"), DiagnosticFinding(
                            "empty_authored_body", path, (), "live_markdown", "empty_body", RuleEvidence(),
                        ))
                    if projection is not None:
                        for field_result in (projection.aliases, projection.tags):
                            for diagnostic in field_result.diagnostics:
                                kind = (
                                    "empty_portable_field_value" if diagnostic.reason == "empty_value"
                                    else "invalid_portable_field"
                                )
                                add((path, kind, diagnostic.field, diagnostic.reason, diagnostic.source_index),
                                    DiagnosticFinding(
                                        kind, path, (), "live_markdown", diagnostic.reason,
                                        PortableFieldEvidence(diagnostic.field, diagnostic.source_index),
                                    ))

            if "aliases" in request.groups and projection is not None and projection.aliases.state == "valid":
                for occurrence in projection.aliases.occurrences:
                    key = occurrence.value.strip()
                    by_path = aliases.setdefault(key, {})
                    first, second = by_path.get(path, (occurrence.source_index, None))
                    if occurrence.source_index != first and (
                        second is None or occurrence.source_index < second
                    ):
                        second = occurrence.source_index
                    by_path[path] = (min(first, occurrence.source_index), second)

            if relationship_snapshot is not None:
                try:
                    relationships = self._relationships.normalized_relationships_from_content(
                        source.content, source_path=path, snapshot=relationship_snapshot,
                    )
                except (VaultServiceError, OSError):
                    reasons.add("relationship_unavailable")
                else:
                    for relationship in relationships:
                        if relationship.relationship_type != "note_link":
                            continue
                        if relationship.resolution == "resolved" and relationship.resolved_path is not None:
                            outgoing.add(path)
                            if relationship.resolved_path in path_set:
                                incoming.add(relationship.resolved_path)
                        elif "relationships" in request.groups and relationship.resolution in {
                            "missing", "unsafe", "ambiguous",
                        }:
                            kind = f"{relationship.resolution}_relationship_target"
                            add((path, kind, relationship.origin, relationship.source_order),
                                DiagnosticFinding(
                                    kind, path, (), "live_markdown", relationship.resolution,
                                    RelationshipEvidence(relationship.origin, relationship.source_order),
                                ))

        # A final owner verification removes paths that visibly raced after their read.
        for path in tuple(note_states):
            if not self._vault.verify_bounded_markdown_path(path_facts[path]):
                unavailable.add(path)
                reasons.add("note_unavailable")
                note_states.pop(path)
        if unavailable:
            raw = {
                identity: row for identity, row in raw.items()
                if row[0].primary_path not in unavailable
            }
        complete = not reasons

        if "aliases" in request.groups:
            for key, by_path in aliases.items():
                live = {path: indices for path, indices in by_path.items() if path in note_states}
                peers = tuple(sorted(live, key=_path_key))
                for path, (first, second) in live.items():
                    if second is not None:
                        indices = tuple(sorted((first, second)))
                        add((path, "duplicate_alias_in_note", key), DiagnosticFinding(
                            "duplicate_alias_in_note", path, (), "live_markdown", "exact_alias",
                            DuplicateAliasEvidence(source_indices=indices),
                        ), key)
                if complete and len(peers) > 1:
                    for path in peers:
                        peer_count = len(peers) - 1
                        related = tuple(peer for peer in peers[:11] if peer != path)[:10]
                        add((path, "colliding_alias", key), DiagnosticFinding(
                            "colliding_alias", path, related, "live_markdown", "exact_alias",
                            CollidingAliasEvidence(
                                source_index=live[path][0], peer_count=peer_count,
                                related_paths_truncated=peer_count > 10,
                            ),
                        ), key)

        if "isolation" in request.groups and complete:
            for path in paths:
                if path in note_states and note_states[path] and path not in outgoing and path not in incoming:
                    add((path, "isolated_note"), DiagnosticFinding(
                        "isolated_note", path, (), "live_markdown", "no_resolved_edges", RuleEvidence(),
                    ))

        late_unavailable: set[str] = set()
        if request.duplicate_source_limit:
            sources = tuple(source for source in selected_sources if source.path in note_states)
            candidate_reasons: set[str] = set()
            if not complete:
                candidate_reasons.add("scan_partial")
            if any(source.path in unavailable for source in selected_sources):
                candidate_reasons.add("source_unavailable")
            try:
                batch = self._duplicates.find_bounded_batch(
                    universe=paths, sources=sources,
                    semantic_candidates=request.semantic_candidates,
                    path_facts=path_facts,
                )
            except (VaultServiceError, OSError):
                candidate_reasons.add("candidate_unavailable")
                candidates = CandidateCoverage(
                    "unavailable", len(sources),
                    tuple(reason for reason in _CANDIDATE_REASONS if reason in candidate_reasons),
                )
            else:
                late_unavailable.update(batch.raced_paths)
                if batch.source_unavailable:
                    candidate_reasons.add("source_unavailable")
                if batch.candidate_unavailable:
                    candidate_reasons.add("candidate_unavailable")
                if batch.semantic_unavailable:
                    candidate_reasons.add("semantic_unavailable")
                for source_path, matches in batch.by_source:
                    for match in matches:
                        if match.path == source_path or match.path not in path_set:
                            continue
                        if not self._vault.verify_bounded_markdown_path(path_facts[source_path]):
                            late_unavailable.add(source_path)
                            candidate_reasons.add("source_unavailable")
                            continue
                        if not self._vault.verify_bounded_markdown_path(path_facts[match.path]):
                            late_unavailable.add(match.path)
                            candidate_reasons.add("candidate_unavailable")
                            continue
                        kind = (
                            "duplicate_candidate" if match.match_type == "exact_title"
                            else "near_duplicate_candidate"
                        )
                        add(("candidate", frozenset((source_path, match.path)), match.match_type),
                            DiagnosticFinding(
                                kind, source_path, (match.path,),
                                "live_markdown" if kind == "duplicate_candidate" else "derived_index",
                                match.match_type, RuleEvidence(),
                            ))
                candidates = CandidateCoverage(
                    "partial" if candidate_reasons else "complete", len(sources),
                    tuple(reason for reason in _CANDIDATE_REASONS if reason in candidate_reasons),
                )
        else:
            candidates = CandidateCoverage("not_requested", 0)

        if late_unavailable:
            unavailable.update(late_unavailable)
            reasons.add("note_unavailable")
            raw = {
                identity: row for identity, row in raw.items()
                if row[0].primary_path not in unavailable
                and not any(path in unavailable for path in row[0].related_paths)
                and row[0].kind not in {"isolated_note", "colliding_alias"}
            }
            candidates = CandidateCoverage(
                candidates.state if candidates.state == "unavailable" else "partial",
                candidates.source_notes,
                tuple(reason for reason in _CANDIDATE_REASONS if reason in set(candidates.reasons) | {"scan_partial"}),
            )
        complete = not reasons

        if request.inspect_derived_index:
            try:
                status = self._semantic.inspect_hygiene_index()
            except (OSError, SemanticSearchUnavailableError):
                status = "inspection_unavailable"
            derived = DerivedIndexEvidence(status)
            if status != "compatible_ready":
                kind = (
                    "previous_compatible_index" if status in {
                        "compatible_previous_refresh", "compatible_previous_error",
                    } else "derived_index_unavailable"
                )
                add((kind, status), DiagnosticFinding(
                    kind, None, (), "derived_index", status, RuleEvidence(),
                ))
        else:
            derived = DerivedIndexEvidence("not_requested")

        def order(row: tuple[DiagnosticFinding, str]) -> tuple[object, ...]:
            finding, alias_key = row
            evidence = finding.evidence
            path = finding.primary_path
            source_index = getattr(evidence, "source_index", None)
            source_indices = getattr(evidence, "source_indices", None)
            if source_index is None and source_indices:
                source_index = source_indices[0]
            return (
                path is None, "" if path is None else path.casefold(), path or "",
                finding.kind, finding.category, finding.evidence_source,
                getattr(evidence, "source_order", -1),
                -1 if source_index is None else source_index, alias_key, finding.related_paths,
                getattr(evidence, "origin", ""), getattr(evidence, "field", ""),
                getattr(evidence, "line", -1) or -1,
            )

        ordered = sorted(raw.values(), key=order)
        scan = ScanCompleteness(
            "complete" if complete else "partial",
            tuple(reason for reason in _SCAN_REASONS if reason in reasons),
            len(paths), inspected_count, len(unavailable),
        )
        return KnowledgeHygieneResult(
            tuple(finding for finding, _ in ordered[:request.finding_limit]),
            scan, candidates, derived, len(ordered) > request.finding_limit,
        )
