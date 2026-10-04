"""Strict transport values for read-only Knowledge Hygiene adapters."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services import knowledge_hygiene as domain

_DEFAULTS = domain.KnowledgeHygieneRequest()


class KnowledgeHygieneScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    groups: list[domain.Group] = Field(
        default=[group for group in ("relationships", "isolation", "frontmatter", "aliases")
                 if group in _DEFAULTS.groups],
    )
    finding_limit: int = Field(default=_DEFAULTS.finding_limit, ge=1, le=500)
    duplicate_source_limit: int = Field(default=_DEFAULTS.duplicate_source_limit, ge=0, le=20)
    semantic_candidates: bool = Field(
        default=_DEFAULTS.semantic_candidates,
        description="Requires duplicate_source_limit > 0; semantic evidence is currently unavailable.",
    )
    inspect_derived_index: bool = _DEFAULTS.inspect_derived_index

    def to_domain(self) -> domain.KnowledgeHygieneRequest:
        return domain.KnowledgeHygieneRequest(
            groups=frozenset(self.groups), finding_limit=self.finding_limit,
            duplicate_source_limit=self.duplicate_source_limit,
            semantic_candidates=self.semantic_candidates, inspect_derived_index=self.inspect_derived_index,
        )

    @model_validator(mode="after")
    def validate_domain_request(self) -> KnowledgeHygieneScanRequest:
        # Domain remains the final authority, before any scan owner work.
        try:
            domain.KnowledgeHygieneService._validate(self.to_domain())
        except domain.KnowledgeHygieneError:
            raise ValueError("invalid_request") from None
        return self


class TransportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelationshipEvidence(TransportModel):
    origin: Literal["obsidian_wikilink", "markdown_link"]
    source_order: int = Field(ge=0)


class FrontmatterEvidence(TransportModel):
    line: int | None = Field(ge=1)
    column: int | None = Field(ge=1)


class PortableFieldEvidence(TransportModel):
    field: Literal["aliases", "tags"]
    source_index: int | None = Field(ge=0, le=255)


class DuplicateAliasEvidence(TransportModel):
    source_indices: tuple[Annotated[int, Field(ge=0, le=255)], Annotated[int, Field(ge=0, le=255)]]


class CollidingAliasEvidence(TransportModel):
    source_index: int = Field(ge=0, le=255)
    peer_count: int = Field(ge=1, le=9999)
    related_paths_truncated: bool


class RuleEvidence(TransportModel):
    pass


class Finding(TransportModel):
    primary_path: str | None
    related_paths: list[str] = Field(max_length=10)
    evidence_source: Literal["live_markdown", "derived_index"]
    category: str


class RelationshipFinding(Finding):
    kind: Literal["missing_relationship_target", "unsafe_relationship_target", "ambiguous_relationship_target"]
    evidence: RelationshipEvidence


class FrontmatterFinding(Finding):
    kind: Literal["invalid_frontmatter"]
    evidence: FrontmatterEvidence


class PortableFieldFinding(Finding):
    kind: Literal["invalid_portable_field", "empty_portable_field_value"]
    evidence: PortableFieldEvidence


class DuplicateAliasFinding(Finding):
    kind: Literal["duplicate_alias_in_note"]
    evidence: DuplicateAliasEvidence


class CollidingAliasFinding(Finding):
    kind: Literal["colliding_alias"]
    evidence: CollidingAliasEvidence


class RuleFinding(Finding):
    kind: Literal[
        "isolated_note", "empty_authored_body", "duplicate_candidate", "near_duplicate_candidate",
        "previous_compatible_index", "derived_index_unavailable",
    ]
    evidence: RuleEvidence


class ScanCompleteness(TransportModel):
    state: Literal["complete", "partial"]
    reasons: list[domain.ScanReason]
    eligible_paths: int = Field(ge=0, le=10000)
    inspected_notes: int = Field(ge=0, le=10000)
    unavailable_notes: int = Field(ge=0, le=10000)


class CandidateCoverage(TransportModel):
    state: Literal["not_requested", "complete", "partial", "unavailable"]
    source_notes: int = Field(ge=0, le=20)
    reasons: list[domain.CandidateReason]


class DerivedIndexEvidence(TransportModel):
    status: domain.DerivedStatus


class KnowledgeHygieneScanResponse(TransportModel):
    findings: list[Annotated[
        RelationshipFinding | FrontmatterFinding | PortableFieldFinding | DuplicateAliasFinding
        | CollidingAliasFinding | RuleFinding, Field(discriminator="kind"),
    ]] = Field(max_length=500)
    scan: ScanCompleteness
    candidates: CandidateCoverage
    derived_index: DerivedIndexEvidence
    findings_truncated: bool


def serialize_hygiene_result(result: domain.KnowledgeHygieneResult) -> KnowledgeHygieneScanResponse:
    """Map immutable domain evidence mechanically, preserving every returned order."""
    return KnowledgeHygieneScanResponse.model_validate(asdict(result))
