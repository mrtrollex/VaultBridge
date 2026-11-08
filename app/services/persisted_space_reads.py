"""Private stopped-service profile: no scheduler, watcher or writable storage.

The registry remains complete. Only policy-authorized operations can ask a lazy
owner for persisted chunks/model evidence. Federation, budgets and admission are
the same as the serving runtime. The profile owns cleanup on the same generation.
"""

import sqlite3
from contextlib import contextmanager
from types import MappingProxyType

from app.repositories.semantic import ImmutableIndexInspectionUnavailableError
from app.repositories.semantic_binding import ExpectedIndexBinding, ordinary_identity
from app.repositories.semantic_reads import SemanticReadUnavailableError, read_eligible_snapshot
from app.services import semantic_resources
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.knowledge_hygiene import KnowledgeHygieneService
from app.services.knowledge_query import KnowledgeQueryService
from app.services.knowledge_spaces import SpaceOwners, SpacePolicyResolver, SpaceRegistry
from app.services.promotion import PromotionService
from app.services.relationships import RelationshipService
from app.services.semantic_resources import SharedEmbeddingResource
from app.services.semantic_search import SemanticSearchService, SemanticSearchUnavailableError
from app.services.space_operations import SpaceOperations
from app.services.space_owners import ImmutableSpaceInspection, _ImmutableRepository
from app.services.space_runtime import SpaceRuntime
from app.services.vault import VaultService


class _PersistedEmbeddingResource(SharedEmbeddingResource):
    def _get_backend(self):
        if self._backend is None:
            self._backend = semantic_resources.FastEmbedder(
                self.model_name, self.cache_dir, batch_size=self._batch_size,
                enable_cpu_mem_arena=self._cpu_mem_arena, _read_only=True,
            )
        return self._backend


class _PersistedRepository(_ImmutableRepository):
    def _legacy_target(self):
        if self._readers.failed:
            raise ImmutableIndexInspectionUnavailableError()
        self._binding.check_root()
        target = self._validated_target()
        if target is None:
            raise ImmutableIndexInspectionUnavailableError()
        return self._repository(target)

    def validate_expected_binding(self):
        with self._legacy_connection():
            pass

    @contextmanager
    def _legacy_connection(self):
        """Same immutable screening and namespace rules, without writable/default owners."""
        try:
            repository = self._legacy_target()
            before = ordinary_identity(repository.db_path)
            uri = repository.db_path.as_uri() + "?mode=ro&immutable=1"
            with self._readers.connection(uri, uri=True, timeout=0) as connection:
                connection.execute("BEGIN")
                repository._validate_namespace(lambda sql, args=(): connection.execute(sql, args).fetchone())
                yield connection
                self._legacy_target()
                if ordinary_identity(repository.db_path) != before:
                    raise ImmutableIndexInspectionUnavailableError()
        except sqlite3.ProgrammingError:
            raise
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError):
            raise ImmutableIndexInspectionUnavailableError() from None

    def load_chunks_read_only(self):
        from app.repositories.semantic import SemanticRepository

        with self._legacy_connection() as connection:
            return SemanticRepository._load_chunks_connection(connection)

    @property
    def db_path(self):
        return self._path

    @property
    def _namespace_binding(self):
        return self._binding

    def close_initializations(self, *, retry=True):
        self.close_resources(retry=retry)

    def read_eligible_snapshot(self, eligible_paths, *, budget, space_id, cancel):
        return read_eligible_snapshot(
            self._path, eligible_paths, budget=budget, space_id=space_id, cancel=cancel,
            expected_binding=self._binding, allow_legacy_binding=self._binding.space_id == "default",
            connection_owner=self._readers, immutable=True, storage_guard=self._guard,
        )

    def _guard(self, *, budget, space_id, cancel):
        def probe():
            cancel.check()
            budget.charge(space_id, "probes")
        if self._readers.failed:
            raise SemanticReadUnavailableError()
        try:
            self._binding.check_root(probe=probe)
            if self._validated_target(probe=probe) is None:
                raise SemanticReadUnavailableError()
        except (OSError, RuntimeError, ValueError):
            raise SemanticReadUnavailableError() from None


class _CompatibilitySemantic:
    """Lazy legacy search/Query against the same immutable persisted owner."""

    def __init__(self, service):
        self.service = service

    def query_basis(self):
        try:
            self.service.use_persisted_index_for_read_only_search()
        except ImmutableIndexInspectionUnavailableError:
            return None
        return self.service.query_basis()

    def search(self, *args, **kwargs):
        try:
            self.service.use_persisted_index_for_read_only_search()
        except ImmutableIndexInspectionUnavailableError:
            raise SemanticSearchUnavailableError("Persisted index unavailable") from None
        return self.service.search(*args, **kwargs)


def compose_persisted_space_reads(settings, *, operation=None, scope=None, request=None, compatibility=False,
                                  promotion_review=False):
    """Stopped-service reads, with complete policy preflight before owner construction."""
    registry = SpaceRegistry.from_settings(settings)
    if operation is not None:
        operations = SpaceOperations(SpacePolicyResolver(registry))
        if compatibility:
            from app.services._space_compatibility import authorize

            authorize(operations, operation, {"request": request})
        else:
            operations.authorize_read_requirements(operation, scope, request=request)
    default = next(d for d in registry._definitions if d.space_id.value == "default")
    resource = (_PersistedEmbeddingResource(settings.model_copy(update={
        "semantic_data_path": default.semantic_data_binding,
    })) if any(d.policy.indexing == "enabled" for d in registry._definitions) else None)
    owners = {}
    try:
        for definition in registry._definitions:
            vault = VaultService(vault_root=definition.root_binding, max_note_bytes=settings.max_note_bytes)
            relationships = RelationshipService(vault)
            semantic = ImmutableSpaceInspection(definition, settings, _runtime=True)
            if definition.policy.indexing == "enabled":
                semantic = SemanticSearchService(
                    vault_root=definition.root_binding,
                    repository=_PersistedRepository(
                        definition.semantic_data_binding / "semantic-index.sqlite3", named=True,
                        binding=(ExpectedIndexBinding._for_default(definition.root_binding)
                                 if definition.space_id.value == "default" else
                                 ExpectedIndexBinding(definition.space_id.value, definition.root_binding)),
                    ),
                    shared_resource=resource, model_name=settings.semantic_model,
                    max_note_bytes=settings.max_note_bytes, chunk_chars=settings.semantic_chunk_chars,
                    chunk_overlap=settings.semantic_chunk_overlap,
                )
            duplicates = DuplicateCandidateService(vault_service=vault,
                semantic_search_service=_CompatibilitySemantic(semantic) if promotion_review else semantic)
            owners[definition.space_id] = SpaceOwners(
                definition=definition, root_identity=vault.root_binding_identity(), vault=vault,
                relationships=relationships, semantic=semantic, scheduler=None, capture=None,
                promotion=PromotionService(vault, candidates=duplicates) if promotion_review else None,
                duplicates=duplicates,
                query=KnowledgeQueryService(vault_service=vault, relationship_service=relationships,
                    semantic_search_service=_CompatibilitySemantic(semantic) if compatibility else semantic),
                hygiene=KnowledgeHygieneService(vault_service=vault, relationship_service=relationships,
                                                duplicate_candidate_service=duplicates,
                                                semantic_search_service=semantic),
            )
        object.__setattr__(registry, "_owners", MappingProxyType(owners))
        return SpaceRuntime(registry, resource, None, None)
    except BaseException:
        for owner in owners.values():
            if isinstance(owner.semantic, SemanticSearchService):
                owner.semantic.repository.close_resources()
            owner.vault.close_scoped_resources()
        if resource is not None:
            resource.close()
        raise
