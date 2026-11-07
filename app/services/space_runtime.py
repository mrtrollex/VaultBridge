"""Explicit private E6 composition. No public adapter activates this runtime."""

from __future__ import annotations

import threading
from types import MappingProxyType

from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.repositories.semantic_binding import ExpectedIndexBinding, StorageDirectoryIdentity
from app.services.capture import CaptureService
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.knowledge_hygiene import KnowledgeHygieneService
from app.services.knowledge_query import KnowledgeQueryService
from app.services.knowledge_spaces import (
    SpaceError,
    SpaceOwners,
    SpacePolicyResolver,
    SpaceRegistry,
    _PrivateImmutable,
)
from app.services.promotion import PromotionService
from app.services.relationships import RelationshipService
from app.services.semantic_resources import SharedEmbeddingResource
from app.services.semantic_scheduler import SharedSemanticScheduler
from app.services.semantic_search import SemanticSearchService
from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
from app.services.space_admission import ScopedOperationAdmission
from app.services.space_operations import SpaceOperations
from app.services.space_owners import ImmutableSpaceInspection, _NoSemanticExecution
from app.services.vault import VaultService


class RuntimeSpaceIndexLifecycle(_PrivateImmutable):
    """Single-assignment capability, bound only AFTER final registry ownership.

    This inert shell breaks the owners -> scheduler -> binding -> owners cycle.
    Its identity never changes; finalization adds dispatch state, never replaces
    a bundle or changes authorization. No grants exist before the final owner map.
    """

    __slots__ = ("_attachment",)

    def __init__(self):
        object.__setattr__(self, "_attachment", None)

    def _attach(self, runtime, binding):
        if self._attachment is not None:
            raise RuntimeError("Lifecycle handle already finalized")
        if (not runtime.registry._accepts(binding) or binding._owners.scheduler is not self
                or runtime.scheduler is None):
            raise SpaceError("invalid_scope")
        runtime.scheduler._entry(binding)
        object.__setattr__(self, "_attachment", (runtime, binding))

    def _dispatch(self, method, *args):
        if self._attachment is None:
            raise SpaceError("unavailable_space")
        runtime, binding = self._attachment
        if not runtime.registry._accepts(binding) or binding._owners.scheduler is not self:
            raise SpaceError("invalid_scope")
        # Never take scoped admission while acquiring a scheduler lock. These
        # lifecycle calls are separate internal control operations, not reads.
        return getattr(runtime.scheduler, method)(binding, *args)

    def enqueue(self, path):
        return self._dispatch("enqueue", path)

    def request_full_sync(self):
        return self._dispatch("request_full_sync")

    def retry(self):
        return self._dispatch("retry")

    def cancel(self):
        return self._dispatch("cancel")

    def status(self):
        return self._dispatch("status")


def _runtime_owners(definition, settings, resource, default_storage):
    vault = VaultService(vault_root=definition.root_binding, max_note_bytes=settings.max_note_bytes)
    relationships = RelationshipService(vault)
    enabled = definition.policy.indexing == "enabled"
    semantic = (SemanticSearchService(
        vault_root=definition.root_binding,
        repository=SemanticRepository(
            definition.semantic_data_binding / "semantic-index.sqlite3",
            _expected_binding=(ExpectedIndexBinding(definition.space_id.value, definition.root_binding)
                               if definition.space_id.value != "default" else None),
            _storage_directory=default_storage if definition.space_id.value == "default" else None,
            _default_binding=(ExpectedIndexBinding._for_default(definition.root_binding)
                              if definition.space_id.value == "default" else None),
        ),
        shared_resource=resource,
        model_name=settings.semantic_model,
        max_note_bytes=settings.max_note_bytes,
        chunk_chars=settings.semantic_chunk_chars,
        chunk_overlap=settings.semantic_chunk_overlap,
        index_batch_size=settings.semantic_index_batch_size,
    ) if enabled else ImmutableSpaceInspection(definition, settings, _runtime=True))
    # Explicit duplicates disable semantic advice at SpaceOperations. Legacy
    # default dispatch retains its existing optional semantic owner behavior.
    advice = semantic if enabled else _NoSemanticExecution()
    duplicates = DuplicateCandidateService(vault_service=vault, semantic_search_service=advice)
    return SpaceOwners(
        definition=definition, root_identity=vault.root_binding_identity(), vault=vault,
        relationships=relationships,
        query=KnowledgeQueryService(vault_service=vault, relationship_service=relationships,
                                    semantic_search_service=advice),
        duplicates=duplicates, capture=CaptureService(vault), promotion=PromotionService(vault, candidates=duplicates),
        hygiene=KnowledgeHygieneService(vault_service=vault, relationship_service=relationships,
                                       duplicate_candidate_service=duplicates, semantic_search_service=semantic),
        semantic=semantic, scheduler=RuntimeSpaceIndexLifecycle() if enabled else None,
    )


class SpaceRuntime(_PrivateImmutable):
    """One-shot private startup; serialized, retryable shutdown on the same owners.

    compose_space_runtime is inert. start() opens admission after E5 native
    readiness and E4 submission, not after every index becomes ready. Post-shell
    E1/E3 index/content failures remain E4 owner-local paused failures. Structural
    validation/constructor failures are composition-fatal. Native failures roll
    back, retaining the same E5 generation if verified disposal fails.
    """

    __slots__ = ("registry", "resolver", "operations", "admission", "resource", "scheduler", "watcher",
                 "_lifecycle", "_state")

    def __init__(self, registry, resource, scheduler, watcher):
        admission = ScopedOperationAdmission()
        resolver = SpacePolicyResolver(registry)
        for name, value in (
            ("registry", registry), ("resolver", resolver), ("admission", admission),
            ("operations", SpaceOperations(resolver, admission)), ("resource", resource),
            ("scheduler", scheduler), ("watcher", watcher), ("_lifecycle", threading.RLock()),
            ("_state", "composed"),
        ):
            object.__setattr__(self, name, value)

    def start(self):
        with self._lifecycle:
            if self._state != "composed":
                raise RuntimeError("Space runtime cannot be restarted")
            object.__setattr__(self, "_state", "starting")
            try:
                if self.watcher is not None:
                    self.watcher.start()
                if self.scheduler is not None:
                    self.scheduler.start(startup=True)
                self.admission.open()
                object.__setattr__(self, "_state", "running")
            except BaseException:
                # shutdown retries the SAME native generation; it never creates
                # a replacement, even if watcher.start's own rollback failed.
                try:
                    self.shutdown()
                except BaseException:
                    pass  # Retain stopping/owners; preserve startup's interruption.
                raise
        return self

    def shutdown(self):
        # Fence without waiting for start's lifecycle lock/native acknowledgment.
        # No admission lock is held across any scheduler or native call.
        self.admission.begin_shutdown()
        if self.scheduler is not None:
            self.scheduler.begin_shutdown()
        if self.watcher is not None:
            self.watcher.stop_admission()
        with self._lifecycle:
            if self._state == "stopped":
                return
            object.__setattr__(self, "_state", "stopping")
            error = None
            try:
                if self.watcher is not None:
                    self.watcher.stop()
            except BaseException as exc:
                error = exc
            scheduler_failed = False
            try:
                if self.scheduler is not None:
                    self.scheduler.shutdown()
            except BaseException as exc:
                scheduler_failed = True
                error = error or exc
            try:
                self.admission.drain()
                # Admitted reads may finish after E4's cached owner cleanup.
                # Verify their retained repository resources before E2/stopped.
                repository_error = None
                for owners in self.registry._owners.values():
                    cleanups = (
                        (getattr(getattr(owners.semantic, "repository", None), "close_initializations", None),
                         {"retry": not scheduler_failed}),
                        (getattr(owners.semantic, "close_resources", None), {}),
                        (getattr(owners.vault, "close_scoped_resources", None), {}),
                    )
                    for cleanup, options in cleanups:
                        if cleanup is None:
                            continue
                        try:
                            cleanup(**options)
                        except BaseException as exc:
                            repository_error = repository_error or exc
                if repository_error is not None:
                    raise repository_error
                if error is None and self.resource is not None:
                    self.resource.close()
                    if not self.resource._closed or self.resource._cleanup_failed:
                        raise RuntimeError("Shared embedding resource cleanup is incomplete")
            except BaseException as exc:
                error = error or exc
            if error is not None:
                raise error
            object.__setattr__(self, "_state", "stopped")

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.shutdown()


def compose_space_runtime(settings: Settings) -> SpaceRuntime:
    """Validate before allocation; finalize ownership once, then issue grants.

    E2 construction is a lazy shell (no FastEmbedder, cache or inference). E4/E5
    construction also starts no threads. No semantic shell at all if disabled.
    Constructors may reject inaccessible/unsafe E1 paths BEFORE E4 can isolate
    failures: such failures abort composition and close already-built shells.
    """
    registry = SpaceRegistry.from_settings(settings)
    resource = None
    owners = {}
    try:
        default_storage = None
        if any(d.policy.indexing == "enabled" for d in registry._definitions):
            # Canonical validated default storage, also for relative data settings.
            default = next(d for d in registry._definitions if d.space_id.value == "default")
            default_storage = StorageDirectoryIdentity(default.semantic_data_binding)
            cache_storage = StorageDirectoryIdentity(default.semantic_data_binding / "models")

            def prepare_cache():
                default_storage.prepare()
                cache_storage.prepare()

            resource = SharedEmbeddingResource(settings.model_copy(update={
                "semantic_data_path": default.semantic_data_binding,
            }), _cache_guard=prepare_cache)
        for definition in registry._definitions:
            owners[definition.space_id] = _runtime_owners(definition, settings, resource, default_storage)
        # The only finalization: registry has not escaped and NO binding has
        # been issued. From here onward neither map nor bundles are replaced.
        object.__setattr__(registry, "_owners", MappingProxyType(owners))
        bindings = tuple(registry._binding(sid) for sid, owner in owners.items() if owner.scheduler is not None)
        scheduler = SharedSemanticScheduler(registry, bindings) if bindings else None
        watcher = (SharedFilesystemWatcher(registry, bindings, scheduler=scheduler,
                                          debounce_seconds=settings.semantic_watch_debounce_seconds)
                   if scheduler is not None and settings.semantic_watch_enabled else None)
        runtime = SpaceRuntime(registry, resource, scheduler, watcher)
        for binding in bindings:
            binding._owners.scheduler._attach(runtime, binding)
        return runtime
    except BaseException as error:
        for owner in owners.values():
            if isinstance(owner.semantic, SemanticSearchService):
                owner.semantic.close_synchronization()
        if resource is not None:
            resource.close()
        if isinstance(error, (OSError, ValueError, RuntimeError)):
            # Like the inspection factory, structural constructor failures are
            # configuration errors, never raw host-path-bearing I/O diagnostics.
            raise SpaceError("invalid_configuration") from None
        raise
