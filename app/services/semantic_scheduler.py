"""Private E4 execution primitive. Production named composition remains deferred.

The composing process owns this scheduler, its one executor and the injected E3
owners' synchronization lifetimes. It closes the E2 resource separately, after
scheduler shutdown. No per-space worker, model, watcher or public adapter is built.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.services.indexer import BackgroundSemanticIndexer
from app.services.knowledge_spaces import (
    AuthorizedSpaceBinding,
    QualifiedPath,
    SpaceError,
    SpaceOwners,
    SpaceRegistry,
)
from app.services.semantic_search import SemanticSearchService, SynchronizationCancelledError
from app.services.semantic_sync import SyncContinuation, SyncStep, SyncStepState, directory_identity
from app.services.vault import SEMANTIC_EXCLUDED_DIRECTORIES

GLOBAL_TARGET_LIMIT = 10_000
SPACE_TARGET_LIMIT = 2_000


@dataclass(frozen=True)
class SchedulerStatus:
    """Private bounded diagnostic snapshot; no exception tracebacks or host paths."""

    queued_paths: tuple[str, ...]
    requires_full_sync: bool
    running: bool
    paused: bool
    error_type: str | None
    progress: SyncStep | None


@dataclass
class _Owner:
    binding: AuthorizedSpaceBinding
    service: SemanticSearchService
    repository: object
    resource: object
    cancel: threading.Event = field(default_factory=threading.Event)
    pending: set[str] = field(default_factory=set)
    inflight: set[str] = field(default_factory=set)
    continuation: SyncContinuation | None = None
    full_needed: bool = False
    full_again: bool = False
    full_active: bool = False
    running: bool = False
    paused: bool = False
    wake_pending: bool = False
    error_type: str | None = None
    progress: SyncStep | None = None


class SharedSemanticScheduler:
    """One bounded E3 turn per sorted-ID round-robin selection.

    Construction takes only registry-issued authorized bindings with trusted,
    independently constructed E2-backed semantic owners. It does not compose or
    activate them. Enqueues may be staged before start(); start(startup=True)
    atomically requests one full sync for each enabled binding before dispatch.
    The condition sleeps when no owner is ready; failures never self-resubmit.
    """

    def __init__(self, registry: SpaceRegistry, bindings: Iterable[AuthorizedSpaceBinding]):
        self._registry = registry
        self._condition = threading.Condition()
        self._owners = {}
        self._total = 0
        self._last_id = None
        self._closed = False
        self._stopped = threading.Event()
        self._executor = None
        self._future = None
        resource = None
        for count, binding in enumerate(bindings, 1):
            if count > 8 or not registry._accepts(binding):
                raise SpaceError("invalid_scope")
            if binding.policy.read != "allow" or binding.policy.indexing != "enabled":
                # Strict no-work: do not inspect a disabled owner's references.
                continue
            if binding.space_id in self._owners:
                raise SpaceError("invalid_scope")
            owners = binding._owners
            if type(owners) is not SpaceOwners or owners._definition is not registry._by_id[binding.space_id]:
                raise SpaceError("unavailable_space")
            service = owners.semantic
            shared = getattr(service, "_shared_resource", None)
            if shared is None or (resource is not None and shared is not resource):
                raise SpaceError("unavailable_space")
            resource = shared
            entry = _Owner(binding, service, service.repository, shared)
            self._validate_owner(entry)
            self._owners[binding.space_id] = entry

    def _entry(self, binding):
        if not self._registry._accepts(binding):
            raise SpaceError("invalid_scope")
        if binding.policy.read != "allow" or binding.policy.indexing != "enabled":
            raise SpaceError("indexing_disabled")
        entry = self._owners.get(binding.space_id)
        if entry is None or entry.binding._owners is not binding._owners:
            raise SpaceError("invalid_scope")
        return entry

    def _validate_owner(self, entry):
        binding = entry.binding
        if not self._registry._accepts(binding):
            raise SpaceError("invalid_scope")
        owners = binding._owners
        definition = owners._definition
        service = entry.service
        root = definition.root_binding
        expected = entry.repository._expected_binding
        if (
            owners.semantic is not service or service.repository is not entry.repository
            or service.vault_root != root or owners.vault.vault_root != root
            or service._shared_resource is not entry.resource
            or root.resolve(strict=True) != root
            or directory_identity(root) != owners._root_identity
            or not service._indexing_enabled
            or service.db_path != definition.semantic_data_binding / "semantic-index.sqlite3"
            or entry.repository.db_path != service.db_path
            or (binding.space_id.value != "default" and (
                expected is None or expected.space_id != binding.space_id.value or str(expected._root) != str(root)
            ))
        ):
            raise SpaceError("unavailable_space")
        # E1 validates index identity/compatibility before preparation; E3 repeats
        # root/database/signature/generation validation at every resumed step.

    def start(self, *, startup: bool = False) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("Semantic scheduler is shut down")
            if self._executor is not None or not self._owners:
                return
            if startup:
                for entry in self._owners.values():
                    entry.full_needed = True
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vaultbridge-shared-sync")
            self._future = self._executor.submit(self._run)

    def enqueue(self, binding: AuthorizedSpaceBinding, path: QualifiedPath) -> bool:
        """Admit one qualified identity; overflow is acknowledged as full debt.

        False means duplicate, overflow, or closed admission. Full debt is visible
        through status(). No iterable is copied before applying the hard limits.
        """
        entry = self._entry(binding)  # Authorization precedes path/owner access.
        if type(path) is not QualifiedPath or path.space_id != binding.space_id:
            raise SpaceError("invalid_scope")
        normalized = BackgroundSemanticIndexer._normalize_path(path.relative_path)
        relative = PurePosixPath(normalized)
        if relative.suffix.lower() != ".md" or any(p in SEMANTIC_EXCLUDED_DIRECTORIES for p in relative.parts):
            raise SpaceError("invalid_scope")
        with self._condition:
            if self._closed:
                return False
        # Filesystem probes never hold the dispatcher/queue lock. E3 revalidates
        # containment, ordinary absence, size and identities before any mutation.
        self._validate_owner(entry)
        canonical = entry.binding._owners.vault.resolve_path(normalized).relative_to(
            entry.service.vault_root,
        ).as_posix()
        if (PurePosixPath(canonical).suffix.lower() != ".md"
                or any(p in SEMANTIC_EXCLUDED_DIRECTORIES for p in PurePosixPath(canonical).parts)):
            raise SpaceError("invalid_scope")
        with self._condition:
            if self._closed:
                return False
            if canonical in entry.pending:
                self._wake(entry)
                return False
            # In-flight targets remain charged until completion. A concurrent
            # write to that identity needs follow-up but does not consume a new
            # distinct-identity slot.
            if canonical not in entry.inflight:
                if self._total >= GLOBAL_TARGET_LIMIT or len(entry.pending | entry.inflight) >= SPACE_TARGET_LIMIT:
                    self._request_full(entry)
                    self._wake(entry)
                    return False
                self._total += 1
            entry.pending.add(canonical)
            self._wake(entry)
            return True

    @staticmethod
    def _request_full(entry):
        entry.full_needed = True
        if entry.full_active or entry.running:
            entry.full_again = True

    def _wake(self, entry):
        # Caller holds the condition. Admission and scheduling are separate:
        # even a duplicate write is a new explicit recovery trigger.
        if self._closed:
            return
        if entry.running or (entry.cancel.is_set() and entry.continuation is not None):
            # Failure publication must not overwrite a concurrent submission.
            # A cancelled turn keeps its token until disposal has fully drained.
            entry.wake_pending = True
        if entry.cancel.is_set():
            if entry.running or entry.continuation is not None:
                self._condition.notify_all()
                return
            entry.cancel = threading.Event()
        entry.paused = False
        self._condition.notify_all()

    def request_full_sync(self, binding: AuthorizedSpaceBinding) -> bool:
        entry = self._entry(binding)
        with self._condition:
            if self._closed:
                return False
            self._request_full(entry)
            self._wake(entry)
            return True

    def retry(self, binding: AuthorizedSpaceBinding) -> bool:
        entry = self._entry(binding)
        with self._condition:
            if self._closed or entry.running or entry.continuation is not None:
                return False
            self._wake(entry)
            return True

    def cancel(self, binding: AuthorizedSpaceBinding) -> None:
        entry = self._entry(binding)
        with self._condition:
            entry.cancel.set()
            entry.paused = True
            entry.wake_pending = False  # This cancellation supersedes older triggers.
            # Cancelled accepted targets become full debt, never silent loss.
            if entry.pending or entry.inflight or entry.continuation is not None or entry.running:
                entry.full_needed = True
            self._total -= len(entry.pending - entry.inflight)
            entry.pending.clear()
            self._condition.notify_all()

    @staticmethod
    def _ready(entry):
        if entry.continuation is not None and entry.cancel.is_set():
            return True  # Dispose a yielded continuation even though paused.
        return not entry.paused and bool(entry.continuation or entry.full_needed or entry.pending)

    def _select(self):
        ready = sorted(sid for sid, entry in self._owners.items() if self._ready(entry))
        if not ready:
            return None
        sid = next((sid for sid in ready if self._last_id is None or sid > self._last_id), ready[0])
        self._last_id = sid
        entry = self._owners[sid]
        entry.running = True
        return entry

    def _release_inflight(self, entry):
        self._total -= len(entry.inflight - entry.pending)
        entry.inflight.clear()

    def _turn(self, entry):
        service = entry.service
        handle = entry.continuation
        try:
            if entry.cancel.is_set():
                raise SynchronizationCancelledError("Scheduled synchronization cancelled")
            self._validate_owner(entry)
            with self._condition:
                preempt = handle is not None and not entry.full_active and entry.full_needed
            if preempt:
                service.cancel_sync(handle)
                handle = None
                with self._condition:
                    entry.continuation = None
                    self._release_inflight(entry)
                # Cancellation is this turn's boundary; full work starts on a
                # later rotation so cancellation cannot become an extra step.
                return
            if handle is None:
                with self._condition:
                    if self._closed or entry.cancel.is_set():
                        raise SynchronizationCancelledError("Scheduled synchronization cancelled")
                    full = entry.full_needed
                    paths = None if full else tuple(sorted(entry.pending))
                    if full:
                        self._total -= len(entry.pending)
                    else:
                        entry.inflight = entry.pending
                    entry.pending = set()
                    entry.full_active = full
                    entry.full_again = False
                handle = service.begin_sync(relative_paths=paths, cancel_event=entry.cancel)
                with self._condition:
                    entry.continuation = handle
                    entry.full_active = handle.full
                    if handle.full:
                        entry.full_needed = True
                        self._release_inflight(entry)
            result = service.sync_step(handle, entry.cancel)
            with self._condition:
                entry.progress = result
                if result.state is SyncStepState.COMPLETE:
                    if entry.full_active and not entry.cancel.is_set():
                        entry.full_needed = entry.full_again
                    self._release_inflight(entry)
                    entry.continuation = None
                    entry.full_active = False
                    entry.error_type = None
                elif result.state is not SyncStepState.INCOMPLETE:
                    raise RuntimeError("Unsuccessful synchronization step")
        except BaseException as error:
            # Do not retain backend/owner tracebacks in a Future or diagnostics.
            # E3 normally terminalizes on failure; pre-step validation failures
            # still need explicit continuation disposal.
            if handle is not None and handle.state is SyncStepState.INCOMPLETE:
                try:
                    service.cancel_sync(handle)
                except BaseException:
                    pass
            with self._condition:
                entry.error_type = type(error).__name__
                entry.full_needed = True
                entry.paused = True
                self._release_inflight(entry)
                entry.continuation = None
                entry.full_active = False
        finally:
            with self._condition:
                entry.running = False
                if entry.continuation is None:
                    if entry.wake_pending:
                        self._wake(entry)
                    entry.wake_pending = False
                elif not entry.cancel.is_set():
                    # Healthy yielded turns already retain pending writes. Do
                    # not carry their triggers into a later unrelated failure.
                    entry.wake_pending = False
                self._condition.notify_all()

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or any(
                        self._ready(entry) for entry in self._owners.values()
                    ))
                    if self._closed:
                        break
                    entry = self._select()
                self._turn(entry)
        finally:
            # All tokens were signalled before this drain. No queue lock is held
            # while E3 closes temporary workspaces or waits for a safe boundary.
            self._close_owners()

    def _close_owners(self):
        for entry in self._owners.values():
            try:
                if entry.continuation is not None and entry.continuation.state is SyncStepState.INCOMPLETE:
                    entry.service.cancel_sync(entry.continuation)
            except BaseException:
                entry.error_type = "SynchronizationCleanupError"
            try:
                entry.service.close_synchronization()
            except BaseException:
                entry.error_type = "SynchronizationCleanupError"
            with self._condition:
                self._release_inflight(entry)
                entry.continuation = None
                entry.running = False
                self._condition.notify_all()

    def status(self, binding: AuthorizedSpaceBinding) -> SchedulerStatus:
        entry = self._entry(binding)
        with self._condition:
            return SchedulerStatus(tuple(sorted(entry.pending | entry.inflight)), entry.full_needed,
                                   entry.running or entry.continuation is not None, entry.paused,
                                   entry.error_type, entry.progress)

    @property
    def pending_count(self) -> int:
        with self._condition:
            return self._total

    def wait(self, timeout: float | None = None) -> None:
        """Wait for quiescence, including paused failures; does not trigger retry."""
        with self._condition:
            if not self._condition.wait_for(lambda: all(
                not entry.running and not self._ready(entry) for entry in self._owners.values()
            ), timeout):
                raise TimeoutError("Semantic scheduler did not become idle")

    def shutdown(self) -> None:
        with self._condition:
            first = not self._closed
            self._closed = True  # Fence submissions before signalling every owner.
            if first:
                for entry in self._owners.values():
                    entry.cancel.set()
                    entry.paused = True
                    if entry.pending or entry.inflight or entry.continuation is not None or entry.running:
                        entry.full_needed = True
                    self._total -= len(entry.pending - entry.inflight)
                    entry.pending.clear()
                self._condition.notify_all()
        if not first:
            self._stopped.wait()
            return
        try:
            if self._executor is not None:
                self._executor.shutdown(wait=True, cancel_futures=True)
                # A dispatcher cancelled before starting has no finally block.
                if self._future.cancelled():
                    self._close_owners()
            else:
                self._close_owners()
        finally:
            self._stopped.set()
