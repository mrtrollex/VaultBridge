"""Internal immutable federation contracts; no transport projection."""

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar, get_args

from app.services.knowledge_query import KnowledgeQuery, SemanticIndexBasis
from app.services.knowledge_spaces import Dialect, QualifiedNoteIdentity, QualifiedPath, ReadScope, SpaceId

T = TypeVar("T")
CoverageReason = Literal[
    "root_unavailable", "index_unavailable", "discovery_limit", "path_limit",
    "alias_limit", "content_limit", "relationship_limit", "semantic_limit", "note_unavailable",
]


@dataclass(frozen=True, slots=True)
class SpaceCoverage:
    space_id: SpaceId
    state: Literal["complete", "partial", "unavailable"]
    reasons: tuple[CoverageReason, ...]
    semantic_index_basis: SemanticIndexBasis
    enumerated_paths: int
    evaluated_paths: int
    returned_candidates: int

    def __post_init__(self):
        if (type(self.space_id) is not SpaceId or self.state not in {"complete", "partial", "unavailable"}
                or type(self.reasons) is not tuple or self.reasons != tuple(sorted(set(self.reasons)))
                or not set(self.reasons) <= set(get_args(CoverageReason))
                or self.semantic_index_basis not in get_args(SemanticIndexBasis)
                or (self.state == "complete") != (not self.reasons)):
            raise ValueError("invalid coverage")
        for count, maximum in ((self.enumerated_paths, 10_000), (self.evaluated_paths, 10_016),
                               (self.returned_candidates, 10_000)):
            if type(count) is not int or not 0 <= count <= maximum:
                raise ValueError("invalid coverage count")


@dataclass(frozen=True, slots=True)
class ScopedReadResult(Generic[T]):
    items: tuple[T, ...]
    coverage: tuple[SpaceCoverage, ...]
    state: Literal["complete", "partial"]
    ordering: Literal["canonical_path", "modified", "semantic"]
    result_limited: bool

    def __post_init__(self):
        ids = tuple(row.space_id.value for row in self.coverage)
        if (type(self.items) is not tuple or len(self.items) > 100
                or type(self.coverage) is not tuple or not 1 <= len(ids) <= 8
                or ids != tuple(sorted(set(ids))) or type(self.result_limited) is not bool
                or self.ordering not in {"canonical_path", "modified", "semantic"}
                or self.state != ("partial" if any(row.state != "complete" for row in self.coverage) else "complete")):
            raise ValueError("invalid scoped result")


@dataclass(frozen=True, slots=True)
class QualifiedRelationshipPredicate:
    direction: Literal["incoming", "outgoing"]
    other: QualifiedPath
    origin: Dialect | None = None


@dataclass(frozen=True, slots=True)
class ScopedKnowledgeQuery:
    scope: ReadScope | None
    query: KnowledgeQuery
    paths: tuple[QualifiedPath, ...] = ()
    relationships: tuple[QualifiedRelationshipPredicate, ...] = ()


@dataclass(frozen=True, slots=True)
class ScopedNoteMatch:
    identity: QualifiedNoteIdentity
    title: str
    snippet: str | None = None
    modified: str | None = None
