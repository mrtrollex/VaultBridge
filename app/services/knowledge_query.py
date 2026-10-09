from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal, TypeAlias

from app.services.frontmatter import (
    FrontmatterParser,
    FrontmatterResult,
    PortableScalar,
    PortableValue,
    project_portable_fields,
)
from app.services.relationships import (
    RelationshipOccurrence,
    RelationshipResolutionSnapshot,
    RelationshipService,
)
from app.services.semantic_search import (
    SemanticQueryBasis,
    SemanticSearchService,
    SemanticSearchUnavailableError,
)
from app.services.vault import NoteReadResult, VaultService, VaultServiceError

MetadataOperator: TypeAlias = Literal[
    "exists",
    "equals",
    "not_equals",
    "sequence_contains",
]
RelationshipDirection: TypeAlias = Literal["outgoing", "incoming"]
RelationshipOrigin: TypeAlias = Literal["obsidian_wikilink", "markdown_link"]
KnowledgeQueryOrdering: TypeAlias = Literal["semantic", "canonical_path"]
SemanticIndexBasis: TypeAlias = Literal[
    "none",
    "compatible_ready",
    "compatible_previous_refresh",
    "compatible_previous_error",
]

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_PATHS = 64
MAX_TAGS = 16
MAX_METADATA_PREDICATES = 16
MAX_RELATIONSHIP_PREDICATES = 16
MAX_SEMANTIC_TEXT_BYTES = 4_096
MAX_LITERAL_TEXT_BYTES = 8_192
MAX_PATH_BYTES = 1_024
MAX_METADATA_KEY_BYTES = 256
MAX_VALUE_BYTES = 1_024
MIN_INT64 = -(2**63)
MAX_INT64 = 2**63 - 1


class _OmittedValue(Enum):
    OMITTED = "omitted"


OMITTED = _OmittedValue.OMITTED
MetadataRequestValue: TypeAlias = PortableScalar | _OmittedValue


@dataclass(frozen=True, slots=True)
class MetadataPredicate:
    key: str
    operator: MetadataOperator
    value: MetadataRequestValue = OMITTED


@dataclass(frozen=True, slots=True)
class RelationshipPredicate:
    direction: RelationshipDirection
    other_path: str
    origin: RelationshipOrigin | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeQuery:
    semantic_text: str | None = None
    literal_text: str | None = None
    folder: str | None = None
    paths: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: tuple[MetadataPredicate, ...] = ()
    relationships: tuple[RelationshipPredicate, ...] = ()
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True, slots=True)
class KnowledgeQueryMatch:
    canonical_path: str
    final_score: float | None = None
    semantic_score: float | None = None
    lexical_score: float | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeQueryResult:
    matches: tuple[KnowledgeQueryMatch, ...]
    ordering: KnowledgeQueryOrdering
    semantic_index_basis: SemanticIndexBasis


class KnowledgeQueryError(RuntimeError):
    reason: Literal["invalid_request", "unsafe_scope", "semantic_unavailable"]

    def __init__(self) -> None:
        super().__init__(self.reason.replace("_", " "))


class InvalidKnowledgeQueryError(KnowledgeQueryError):
    reason = "invalid_request"


class UnsafeKnowledgeQueryScopeError(KnowledgeQueryError):
    reason = "unsafe_scope"


class KnowledgeQuerySemanticUnavailableError(KnowledgeQueryError):
    reason = "semantic_unavailable"


@dataclass(frozen=True, slots=True)
class _ValidatedRequest:
    tags: tuple[str, ...]


class KnowledgeQueryService:
    """Compose bounded live domain facts without taking ownership from their services."""

    def __init__(
        self,
        *,
        vault_service: VaultService,
        relationship_service: RelationshipService,
        semantic_search_service: SemanticSearchService,
    ) -> None:
        self._vault = vault_service
        self._relationships = relationship_service
        self._semantic = semantic_search_service

    def evaluate_supplied_nonsemantic(self, request, *, snapshot, session, paths,
                                     relationships, budget, cancel, reasons, validated):
        """Evaluate one authorized projection using one budgeted supplied universe.

        Request validation remains shared with query(); this seam does no discovery,
        semantic work, eager relationship derivation or legacy backlink scans.
        Content is released after retaining scalar eligibility and target/origin sets.
        """
        from app.services.relationships import ScopedRelationshipSnapshot
        from app.services.scoped_budget import BudgetExhausted
        from app.services.vault import NoteUnavailableError

        if request.semantic_text is not None:
            raise KnowledgeQuerySemanticUnavailableError()
        universe = ScopedRelationshipSnapshot(snapshot, session)
        if relationships and not snapshot.resolution_complete:
            reasons.add("relationship_limit")
            return (), 0, False
        selected = None
        if paths is not None:
            selected = set()
            for path in paths:
                try:
                    resolved, outcome = universe.exact(path)
                except (NoteUnavailableError, OSError):
                    reasons.add("note_unavailable")
                    continue
                if outcome == "unsafe":
                    raise UnsafeKnowledgeQueryScopeError()
                if resolved is not None:
                    selected.add(resolved)
        if selected == set():
            return (), 0, True
        targets = []
        for predicate in relationships:
            target, outcome = universe.exact(predicate.other.relative_path)
            if outcome == "unsafe":
                raise UnsafeKnowledgeQueryScopeError()
            targets.append(target)
        facts = {fact.path: fact for fact in snapshot.facts}
        eligible = {}
        edges = {}
        evaluated = 0
        view = budget.relationships(session.sid, cancel)
        outgoing = any(predicate.direction == "outgoing" for predicate in relationships)

        def candidate_scope(path):
            return ((selected is None or path in selected)
                    and (request.folder is None or _path_is_below(path, request.folder.replace("\\", "/"))))

        def inspect(path):
            nonlocal evaluated
            cancel.check()
            if path in eligible:
                return True
            fact = facts[path]
            try:
                note = session.read(fact, incoming=not candidate_scope(path))
            except (NoteUnavailableError, UnicodeError, OSError):
                reasons.add("note_unavailable")
                eligible[path] = False
                edges[path] = frozenset()
                return True
            evaluated += 1
            frontmatter = FrontmatterParser.parse(note.content) if request.tags or request.metadata else None
            eligible[path] = (
                (request.literal_text is None or request.literal_text in note.content)
                and (not request.tags or _matches_tags(frontmatter, validated.tags))
                and (not request.metadata or _matches_metadata(frontmatter, request.metadata))
            )
            if relationships and (outgoing or path in incoming_targets):
                try:
                    derivation = self._relationships.derive_normalized_bounded(
                        note.content, source_path=path, snapshot=universe, budget=view, cancel=cancel,
                    )
                except (NoteUnavailableError, OSError):
                    reasons.add("note_unavailable")
                    eligible[path] = False
                    edges[path] = frozenset()
                    return True
                if derivation.state == "limited":
                    reasons.add("relationship_limit")
                    return False
                edges[path] = frozenset((row.resolved_path, row.origin) for row in derivation.occurrences
                                        if row.resolved_path is not None)
            return True

        if any(target is None for target in targets):
            return (), 0, True
        incoming_targets = {target for predicate, target in zip(relationships, targets, strict=True)
                            if predicate.direction == "incoming"}
        try:
            for predicate, target in zip(relationships, targets, strict=True):
                if predicate.direction == "incoming" and not inspect(target):
                    return (), evaluated, False
            matches = []
            for fact in snapshot.facts:
                cancel.check()
                path = fact.path
                if request.folder is not None and not _path_is_below(path, request.folder.replace("\\", "/")):
                    continue
                if selected is not None and path not in selected:
                    continue
                if not inspect(path):
                    return (), evaluated, False
                if not eligible[path]:
                    continue
                if relationships and outgoing and path not in edges:
                    raise AssertionError("missing bounded derivation")
                matched = True
                for predicate, target in zip(relationships, targets, strict=True):
                    budget.charge(session.sid, "comparisons")
                    source, destination = (path, target) if predicate.direction == "outgoing" else (target, path)
                    origins = (predicate.origin.value,) if predicate.origin is not None else (
                        "obsidian_wikilink", "markdown_link",
                    )
                    if not any((destination, origin) in edges.get(source, ()) for origin in origins):
                        matched = False
                        break
                if matched:
                    matches.append(path)
        except BudgetExhausted as exc:
            reasons.add("content_limit" if exc.resource == "bytes" else "relationship_limit")
            if relationships:
                reasons.add("relationship_limit")
                return (), evaluated, False
        return tuple(matches), evaluated, True

    def query(self, request: KnowledgeQuery) -> KnowledgeQueryResult:
        validated = self._validate_request(request)

        folder_path: str | None = None
        folder_missing = False
        if request.folder is not None:
            folder = self._vault.verify_existing_folder_scope_result(request.folder)
            if folder.resolution == "unsafe":
                raise UnsafeKnowledgeQueryScopeError()
            folder_missing = folder.resolution == "missing"
            folder_path = folder.resolved_folder

        scoped_paths: set[str] | None = None
        if request.paths:
            scoped_paths = set()
            for raw_path in dict.fromkeys(request.paths):
                verification = self._vault.verify_existing_markdown_path_result(
                    raw_path,
                    exact_spelling=True,
                )
                if verification.resolution == "unsafe":
                    raise UnsafeKnowledgeQueryScopeError()
                if verification.resolved_path is not None:
                    scoped_paths.add(verification.resolved_path)

        relationship_targets: list[str | None] = []
        target_cache: dict[str, str | None] = {}
        for predicate in request.relationships:
            if predicate.other_path not in target_cache:
                verification = self._vault.verify_existing_markdown_path_result(
                    predicate.other_path,
                    exact_spelling=True,
                )
                if verification.resolution == "unsafe":
                    raise UnsafeKnowledgeQueryScopeError()
                target_cache[predicate.other_path] = verification.resolved_path
            relationship_targets.append(target_cache[predicate.other_path])

        path_snapshot = self._vault.markdown_path_candidate_snapshot()
        resolution_snapshot = self._relationships.normalized_resolution_snapshot(path_snapshot)
        candidates = sorted(
            {candidate.canonical_path for candidate in path_snapshot.live_candidates},
            key=lambda path: (path.casefold(), path),
        )
        if folder_missing:
            candidates = []
        elif folder_path is not None:
            candidates = [path for path in candidates if _path_is_below(path, folder_path)]
        if scoped_paths is not None:
            candidates = [path for path in candidates if path in scoped_paths]
        if any(target is None for target in relationship_targets):
            candidates = []

        reads: dict[str, NoteReadResult | None] = {}

        def read_once(path: str) -> NoteReadResult | None:
            if path not in reads:
                reads[path] = self._read_current(path)
            return reads[path]

        incoming_sources: dict[str, tuple[RelationshipOccurrence, ...]] = {}
        for predicate, target_path in zip(
            request.relationships,
            relationship_targets,
            strict=True,
        ):
            if predicate.direction != "incoming" or target_path is None:
                continue
            if target_path not in incoming_sources:
                source = read_once(target_path)
                incoming_sources[target_path] = (
                    ()
                    if source is None
                    else self._derive_relationships(source, resolution_snapshot)
                )

        eligible: list[str] = []
        needs_frontmatter = bool(request.tags or request.metadata)
        needs_outgoing = any(
            predicate.direction == "outgoing" for predicate in request.relationships
        )
        for candidate_path in candidates:
            note = read_once(candidate_path)
            if note is None:
                continue
            if request.literal_text is not None and request.literal_text not in note.content:
                continue

            frontmatter: FrontmatterResult | None = None
            if needs_frontmatter:
                frontmatter = FrontmatterParser.parse(note.content)
            if request.tags and not _matches_tags(frontmatter, validated.tags):
                continue
            if request.metadata and not _matches_metadata(frontmatter, request.metadata):
                continue

            outgoing: tuple[RelationshipOccurrence, ...] = ()
            if needs_outgoing:
                outgoing = self._derive_relationships(note, resolution_snapshot)
            if not _matches_relationships(
                candidate_path,
                request.relationships,
                relationship_targets,
                outgoing,
                incoming_sources,
            ):
                continue
            eligible.append(candidate_path)

        if request.semantic_text is None:
            matches: list[KnowledgeQueryMatch] = []
            for path in eligible:
                if self._is_still_live(path):
                    matches.append(KnowledgeQueryMatch(canonical_path=path))
                if len(matches) >= request.limit:
                    break
            return KnowledgeQueryResult(
                matches=tuple(matches),
                ordering="canonical_path",
                semantic_index_basis="none",
            )

        basis = self._semantic.query_basis()
        if basis is None:
            raise KnowledgeQuerySemanticUnavailableError()
        candidate_limit = min(500, max(request.limit * 5, request.limit))
        try:
            ranked = self._semantic.search(
                request.semantic_text,
                limit=candidate_limit,
                eligible_paths=frozenset(eligible),
            )
        except SemanticSearchUnavailableError:
            raise KnowledgeQuerySemanticUnavailableError() from None

        semantic_matches: list[KnowledgeQueryMatch] = []
        for result in ranked:
            if result.path not in eligible or not self._is_still_live(result.path):
                continue
            semantic_matches.append(
                KnowledgeQueryMatch(
                    canonical_path=result.path,
                    final_score=result.score,
                    semantic_score=result.semantic_score,
                    lexical_score=result.lexical_score,
                )
            )
            if len(semantic_matches) >= request.limit:
                break
        return KnowledgeQueryResult(
            matches=tuple(semantic_matches),
            ordering="semantic",
            semantic_index_basis=_semantic_basis(basis),
        )

    def _derive_relationships(
        self,
        note: NoteReadResult,
        snapshot: RelationshipResolutionSnapshot,
    ) -> tuple[RelationshipOccurrence, ...]:
        return self._relationships.normalized_relationships_from_content(
            note.content,
            source_path=note.path,
            snapshot=snapshot,
        )

    def _read_current(self, path: str) -> NoteReadResult | None:
        verification = self._vault.verify_existing_markdown_path_result(
            path,
            exact_spelling=True,
        )
        if verification.resolved_path != path:
            return None
        try:
            return self._vault.read_note(path)
        except (OSError, RuntimeError, UnicodeError, VaultServiceError):
            return None

    def _is_still_live(self, path: str) -> bool:
        verification = self._vault.verify_existing_markdown_path_result(
            path,
            exact_spelling=True,
        )
        return verification.resolved_path == path

    @staticmethod
    def _validate_request(request: KnowledgeQuery) -> _ValidatedRequest:
        if not isinstance(request, KnowledgeQuery):
            raise InvalidKnowledgeQueryError()
        if type(request.limit) is not int or request.limit <= 0 or request.limit > MAX_LIMIT:
            raise InvalidKnowledgeQueryError()
        _validate_collection(request.paths, MAX_PATHS, str)
        _validate_collection(request.tags, MAX_TAGS, str)
        _validate_collection(request.metadata, MAX_METADATA_PREDICATES, MetadataPredicate)
        _validate_collection(
            request.relationships,
            MAX_RELATIONSHIP_PREDICATES,
            RelationshipPredicate,
        )

        if request.semantic_text is not None:
            _bounded_text(request.semantic_text, MAX_SEMANTIC_TEXT_BYTES, allow_empty=False)
        if request.literal_text is not None:
            _bounded_text(request.literal_text, MAX_LITERAL_TEXT_BYTES, allow_empty=False)
        if request.folder is not None:
            _bounded_scope_path(request.folder)
        for path in request.paths:
            _bounded_scope_path(path)

        normalized_tags: list[str] = []
        seen_tags: set[str] = set()
        for tag in request.tags:
            _bounded_text(tag, MAX_VALUE_BYTES, allow_empty=False, trim_empty=True)
            normalized = tag.strip()
            if normalized not in seen_tags:
                normalized_tags.append(normalized)
                seen_tags.add(normalized)

        for predicate in request.metadata:
            _validate_metadata_predicate(predicate)
        for predicate in request.relationships:
            if not isinstance(predicate.direction, str):
                raise InvalidKnowledgeQueryError()
            if predicate.direction not in {"outgoing", "incoming"}:
                raise InvalidKnowledgeQueryError()
            if predicate.origin is not None and not isinstance(predicate.origin, str):
                raise InvalidKnowledgeQueryError()
            if predicate.origin not in {None, "obsidian_wikilink", "markdown_link"}:
                raise InvalidKnowledgeQueryError()
            _bounded_scope_path(predicate.other_path)

        if not any(
            (
                request.semantic_text is not None,
                request.literal_text is not None,
                request.folder is not None,
                bool(request.paths),
                bool(request.tags),
                bool(request.metadata),
                bool(request.relationships),
            )
        ):
            raise InvalidKnowledgeQueryError()
        return _ValidatedRequest(tags=tuple(normalized_tags))


def _validate_collection(value: object, maximum: int, member_type: type[object]) -> None:
    if not isinstance(value, tuple) or len(value) > maximum:
        raise InvalidKnowledgeQueryError()
    if any(not isinstance(item, member_type) for item in value):
        raise InvalidKnowledgeQueryError()


def _bounded_text(
    value: object,
    maximum: int,
    *,
    allow_empty: bool,
    trim_empty: bool = False,
) -> None:
    if not isinstance(value, str):
        raise InvalidKnowledgeQueryError()
    if not allow_empty and (not value or (trim_empty and not value.strip())):
        raise InvalidKnowledgeQueryError()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise InvalidKnowledgeQueryError() from None
    if size > maximum:
        raise InvalidKnowledgeQueryError()


def _bounded_scope_path(value: object) -> None:
    """Validate one exact query path before legacy VaultService normalization."""
    _bounded_text(value, MAX_PATH_BYTES, allow_empty=False, trim_empty=True)
    assert isinstance(value, str)
    if value != value.strip():
        raise InvalidKnowledgeQueryError()


def _validate_metadata_predicate(predicate: MetadataPredicate) -> None:
    _bounded_text(predicate.key, MAX_METADATA_KEY_BYTES, allow_empty=True)
    if not isinstance(predicate.operator, str):
        raise InvalidKnowledgeQueryError()
    if predicate.operator not in {"exists", "equals", "not_equals", "sequence_contains"}:
        raise InvalidKnowledgeQueryError()
    if predicate.operator == "exists":
        if predicate.value is not OMITTED:
            raise InvalidKnowledgeQueryError()
        return
    if predicate.value is OMITTED or not _is_portable_scalar(predicate.value):
        raise InvalidKnowledgeQueryError()
    if type(predicate.value) is str:
        _bounded_text(predicate.value, MAX_VALUE_BYTES, allow_empty=True)
    elif type(predicate.value) is int and not MIN_INT64 <= predicate.value <= MAX_INT64:
        raise InvalidKnowledgeQueryError()
    elif type(predicate.value) is float and not math.isfinite(predicate.value):
        raise InvalidKnowledgeQueryError()


def _is_portable_scalar(value: object) -> bool:
    return value is None or type(value) in {str, bool, int, float}


def _same_scalar_type(left: object, right: object) -> bool:
    return _is_portable_scalar(left) and type(left) is type(right)


def _path_is_below(path: str, folder: str) -> bool:
    return PurePosixPath(folder) in PurePosixPath(path).parents


def _matches_tags(frontmatter: FrontmatterResult | None, required: tuple[str, ...]) -> bool:
    if frontmatter is None:
        return False
    projection = project_portable_fields(frontmatter)
    if projection is None or projection.tags.state != "valid":
        return False
    stored = {occurrence.value.strip() for occurrence in projection.tags.occurrences}
    return all(tag in stored for tag in required)


def _matches_metadata(
    frontmatter: FrontmatterResult | None,
    predicates: tuple[MetadataPredicate, ...],
) -> bool:
    if frontmatter is None or frontmatter.state != "valid" or frontmatter.metadata is None:
        return False
    metadata = frontmatter.metadata
    return all(_matches_metadata_predicate(metadata, predicate) for predicate in predicates)


def _matches_metadata_predicate(
    metadata: Mapping[str, PortableValue],
    predicate: MetadataPredicate,
) -> bool:
    present = predicate.key in metadata
    if predicate.operator == "exists":
        return present
    if not present:
        return False
    stored = metadata[predicate.key]
    requested = predicate.value
    if predicate.operator == "equals":
        return _same_scalar_type(stored, requested) and stored == requested
    if predicate.operator == "not_equals":
        return _same_scalar_type(stored, requested) and stored != requested
    if not isinstance(stored, tuple):
        return False
    return any(
        _same_scalar_type(member, requested) and member == requested
        for member in stored
    )


def _matches_relationships(
    candidate_path: str,
    predicates: tuple[RelationshipPredicate, ...],
    targets: list[str | None],
    outgoing: tuple[RelationshipOccurrence, ...],
    incoming_sources: Mapping[str, tuple[RelationshipOccurrence, ...]],
) -> bool:
    for predicate, target_path in zip(predicates, targets, strict=True):
        if target_path is None:
            return False
        occurrences = outgoing if predicate.direction == "outgoing" else incoming_sources[target_path]
        expected_path = target_path if predicate.direction == "outgoing" else candidate_path
        if not any(
            occurrence.relationship_type == "note_link"
            and occurrence.resolution == "resolved"
            and occurrence.resolved_path == expected_path
            and (predicate.origin is None or occurrence.origin == predicate.origin)
            for occurrence in occurrences
        ):
            return False
    return True


def _semantic_basis(basis: SemanticQueryBasis) -> SemanticIndexBasis:
    return basis
