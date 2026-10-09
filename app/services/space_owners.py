"""Explicit private composition for VB-151's nonsemantic one-space seam.

No public adapter calls this factory. Named semantic execution waits for D/E;
scoped capture/promotion and their bound enqueue callbacks wait for E/F.
"""

from __future__ import annotations

import os
import stat
from types import MappingProxyType

from app.core.config import Settings
from app.repositories.semantic import ImmutableIndexInspectionUnavailableError, SemanticRepository
from app.services.capture import CaptureService
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.knowledge_hygiene import KnowledgeHygieneService
from app.services.knowledge_query import KnowledgeQueryService
from app.services.knowledge_spaces import (
    KnowledgeSpaceDefinition,
    SpaceError,
    SpaceOwners,
    SpaceRegistry,
    _PrivateImmutable,
)
from app.services.promotion import PromotionService
from app.services.relationships import RelationshipService
from app.services.semantic_search import SemanticSearchService, SemanticSearchUnavailableError
from app.services.vault import VaultService


class _NoSemanticExecution:
    """Fail closed for advice without touching repository, model or stale rows."""

    __slots__ = ()

    def search(self, *args, **kwargs):
        raise SemanticSearchUnavailableError("Semantic search unavailable")

    def query_basis(self):
        return None


class _ImmutableRepository:
    __slots__ = ("_path", "_named", "_directories")

    def __init__(self, path, *, named):
        self._path = path
        self._named = named
        self._directories = None
        if named:
            # Capture only existing directory identities. Missing suffixes may be
            # created later by their owner; inspection itself never creates them.
            try:
                self._directories = tuple(
                    (directory, self._directory_identity(directory))
                    for directory in reversed((path.parent, *path.parent.parents))
                )
            except (OSError, ImmutableIndexInspectionUnavailableError):
                # Inaccessible disabled storage is availability, not startup fatal.
                pass

    @staticmethod
    def _ordinary_entry(path, *, directory):
        info = path.lstat()  # Never follow the entry being validated.
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400  # Windows reparse point, including junctions.
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        ):
            raise ImmutableIndexInspectionUnavailableError
        return info.st_dev, info.st_ino

    @classmethod
    def _directory_identity(cls, path):
        try:
            return cls._ordinary_entry(path, directory=True)
        except FileNotFoundError:
            return None

    def _validated_target(self):
        if self._directories is None:
            raise ImmutableIndexInspectionUnavailableError
        # Validate ancestors first, so probing a child cannot traverse a redirect.
        for directory, expected in self._directories:
            current = self._directory_identity(directory)
            if current is None:
                return None
            if expected is not None and current != expected:
                raise ImmutableIndexInspectionUnavailableError
            if not os.access(directory, os.R_OK | os.X_OK):
                raise ImmutableIndexInspectionUnavailableError
        directory = self._path.parent
        if directory.resolve(strict=True) != directory:
            raise ImmutableIndexInspectionUnavailableError
        # Immutable SQLite must not consume active or redirected sidecars. A
        # regular rollback journal retains the existing immutable-reader behavior.
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                self._ordinary_entry(self._path.with_name(self._path.name + suffix), directory=False)
            except FileNotFoundError:
                continue
            if suffix != "-journal":
                raise ImmutableIndexInspectionUnavailableError
        try:
            identity = self._ordinary_entry(self._path, directory=False)
        except FileNotFoundError:
            return None
        target = self._path.resolve(strict=True)
        if (
            target != self._path or target.parent != directory
            or self._ordinary_entry(target, directory=False) != identity
        ):
            raise ImmutableIndexInspectionUnavailableError
        return target

    def read_immutable_status(self):
        if not self._named:
            return SemanticRepository(self._path).read_immutable_status()
        try:
            target = self._validated_target()
        except (OSError, RuntimeError, ValueError):
            # Existing facade classification; never expose paths or raw I/O text.
            raise ImmutableIndexInspectionUnavailableError from None
        if target is None:
            return SemanticRepository._missing_status()
        # Preserve the validated canonical target; no independent second resolve.
        # This is cooperative-filesystem validation, not hostile-writer pinning.
        return SemanticRepository(target)._read_immutable_status(validated_path=target)


class ImmutableSpaceInspection(_PrivateImmutable):
    """Only immutable inspection, including when indexing is disabled.

    Reuse the semantic owner's classification without a FastEmbedder or writable
    repository interface. An unresolved fingerprint remains inspection_unavailable;
    inspection never resolves it or claims compatibility from model name alone.
    """

    __slots__ = ("_inspect",)

    def __init__(self, definition: KnowledgeSpaceDefinition, settings: Settings):
        inspector = SemanticSearchService(
            vault_root=definition.root_binding,
            repository=_ImmutableRepository(
                definition.semantic_data_binding / "semantic-index.sqlite3",
                named=settings.knowledge_spaces_json is not None,
            ),
            cache_dir=definition.semantic_data_binding / "models",
            model_name=settings.semantic_model,
            max_note_bytes=settings.max_note_bytes,
            chunk_chars=settings.semantic_chunk_chars,
            chunk_overlap=settings.semantic_chunk_overlap,
            index_batch_size=settings.semantic_index_batch_size,
            embedder=_NoSemanticExecution(),
        )
        object.__setattr__(self, "_inspect", inspector.inspect_hygiene_index)

    def inspect_hygiene_index(self) -> str:
        return self._inspect()

    def __repr__(self):
        return "ImmutableSpaceInspection()"


class DeferredSpaceIndexLifecycle(_PrivateImmutable):
    """Structural handle only: no executor, jobs, model, sync or enqueue API.

    Slice E must supply registry-bound scheduling and resource leases before any
    enabled semantic runtime can execute. Disabled spaces have no lifecycle handle.
    """

    __slots__ = ()

    def __repr__(self):
        return "DeferredSpaceIndexLifecycle()"


def _owners_for(definition: KnowledgeSpaceDefinition, settings: Settings, *, named: bool) -> SpaceOwners:
    vault = VaultService(vault_root=definition.root_binding, max_note_bytes=settings.max_note_bytes)
    relationships = RelationshipService(vault)
    inspection = ImmutableSpaceInspection(definition, settings)
    advice = _NoSemanticExecution()
    duplicates = DuplicateCandidateService(vault_service=vault, semantic_search_service=advice)
    return SpaceOwners(
        definition=definition,
        root_identity=vault.root_binding_identity() if named else None,
        vault=vault,
        relationships=relationships,
        query=KnowledgeQueryService(
            vault_service=vault, relationship_service=relationships, semantic_search_service=advice,
        ),
        duplicates=duplicates,
        capture=CaptureService(vault),
        promotion=PromotionService(vault, candidates=duplicates),
        hygiene=KnowledgeHygieneService(
            vault_service=vault, relationship_service=relationships,
            duplicate_candidate_service=duplicates, semantic_search_service=inspection,
        ),
        semantic=inspection,
        scheduler=DeferredSpaceIndexLifecycle() if definition.policy.indexing == "enabled" else None,
    )


def compose_space_registry(settings: Settings) -> SpaceRegistry:
    """Validate ALL definitions first, then compose each exact local owner set once.

    The Slice-A registry-only factory remains nonprobing in implicit legacy mode.
    This explicit factory constructs ordinary VaultService shells; public legacy
    startup/composition continues to use its existing owners and lifecycle.
    """
    registry = SpaceRegistry.from_settings(settings)
    try:
        owners = {
            definition.space_id: _owners_for(definition, settings, named=settings.knowledge_spaces_json is not None)
            for definition in registry._definitions
        }
    except (OSError, ValueError, RuntimeError):
        raise SpaceError("invalid_configuration") from None
    object.__setattr__(registry, "_owners", MappingProxyType(owners))
    return registry
