"""Private stopped-service profile: no scheduler, watcher or writable storage.

The registry remains complete. Only policy-authorized operations can ask a lazy
owner for persisted chunks/model evidence. Federation, budgets and admission are
the same as the serving runtime. The profile owns cleanup on the same generation.
"""

from types import MappingProxyType

from app.repositories.semantic_binding import ExpectedIndexBinding
from app.repositories.semantic_reads import SemanticReadUnavailableError, read_eligible_snapshot
from app.services import semantic_resources
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.knowledge_hygiene import KnowledgeHygieneService
from app.services.knowledge_query import KnowledgeQueryService
from app.services.knowledge_spaces import SpaceOwners, SpacePolicyResolver, SpaceRegistry
from app.services.relationships import RelationshipService
from app.services.semantic_resources import SharedEmbeddingResource
from app.services.semantic_search import SemanticSearchService
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


def compose_persisted_space_reads(settings, *, operation=None, scope=None, request=None):
    """Explicit private profile. Public CLI activation remains guarded."""
    registry = SpaceRegistry.from_settings(settings)
    if operation is not None:
        SpaceOperations(SpacePolicyResolver(registry)).authorize_read_requirements(operation, scope, request=request)
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
            duplicates = DuplicateCandidateService(vault_service=vault, semantic_search_service=semantic)
            owners[definition.space_id] = SpaceOwners(
                definition=definition, root_identity=vault.root_binding_identity(), vault=vault,
                relationships=relationships, semantic=semantic, scheduler=None, capture=None, promotion=None,
                duplicates=duplicates,
                query=KnowledgeQueryService(vault_service=vault, relationship_service=relationships,
                                            semantic_search_service=semantic),
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
