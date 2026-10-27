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
    monitoring_error_type: str | None = None
    active_watcher_callbacks: int = 0
    monitoring_recovering: bool = False

    @property
    def monitoring_failed(self) -> bool:
        return self.monitoring_error_type is not None


@dataclass
class _Owner:
    binding: AuthorizedSpaceBinding
    service: SemanticSearchService
    repository: object
    resource: object
    cancel: threading.Event = field(default_factory=threading.Event)
    pending: set[str] = field(default_factory=set)
    inflight: set[str] = field(default_factory=set)
    deferred: dict[str, float] = field(default_factory=dict)
    debounce_revision: int = 0
    continuation: SyncContinuation | None = None
    full_needed: bool = False
    full_again: bool = False
    full_active: bool = False
    running: bool = False
    paused: bool = False
    wake_pending: bool = False
    error_type: str | None = None
    progress: SyncStep | None = None
    monitoring_error_type: str | None = None
    monitoring_revision: int = 0
    monitoring_failure_revision: int = 0
    watcher_verified: tuple[object, int] | None = None
    watcher_callbacks: int = 0
    coverage_revision: int = 0
    coverage_restored: int = 0
    coverage_reconciled: int = 0
    full_coverage: tuple[object, int] | None = None


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
        self._shutdown_started = False
        self._watcher_token = None
        self._watcher_accepting = False
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

    def _canonical_entry(self, binding, path):
        entry = self._entry(binding)  # Authorization precedes path/owner access.
        if type(path) is not QualifiedPath or path.space_id != binding.space_id:
            raise SpaceError("invalid_scope")
        normalized = BackgroundSemanticIndexer._normalize_path(path.relative_path)
        relative = PurePosixPath(normalized)
        if relative.suffix.lower() != ".md" or any(p in SEMANTIC_EXCLUDED_DIRECTORIES for p in relative.parts):
            raise SpaceError("invalid_scope")
        with self._condition:
            if self._closed:
                return entry, None
        # Filesystem probes never hold the dispatcher/queue lock. E3 revalidates
        # containment, ordinary absence, size and identities before any mutation.
        self._validate_owner(entry)
        canonical = entry.binding._owners.vault.resolve_path(normalized).relative_to(
            entry.service.vault_root,
        ).as_posix()
        if (PurePosixPath(canonical).suffix.lower() != ".md"
                or any(p in SEMANTIC_EXCLUDED_DIRECTORIES for p in PurePosixPath(canonical).parts)):
            raise SpaceError("invalid_scope")
        return entry, canonical

    @staticmethod
    def _identities(entry):
        return entry.pending | entry.inflight | entry.deferred.keys()

    def _admit(self, entry, canonical):
        # All three stages share ONE reservation under the scheduler condition.
        if canonical not in self._identities(entry):
            if self._total >= GLOBAL_TARGET_LIMIT or len(self._identities(entry)) >= SPACE_TARGET_LIMIT:
                self._request_full(entry)
                self._wake(entry)
                return False
            self._total += 1
        return True

    def enqueue(self, binding: AuthorizedSpaceBinding, path: QualifiedPath) -> bool:
        """Admit one identity; False denotes duplicate, overflow or closed admission."""
        entry, canonical = self._canonical_entry(binding, path)
        with self._condition:
            if self._closed:
                return False
            if canonical in entry.pending:
                self._wake(entry)
                return False
            if not self._admit(entry, canonical):
                return False
            entry.pending.add(canonical)
            if entry.deferred.pop(canonical, None) is not None:
                entry.debounce_revision += 1
            self._wake(entry)
            return True

    def _attach_watcher(self, token):
        with self._condition:
            if (self._closed or self._watcher_token is not None
                    or any(entry.watcher_callbacks for entry in self._owners.values())):
                raise RuntimeError("Semantic watcher admission is unavailable")
            self._watcher_token = token
            self._watcher_accepting = True

    def _debounce(self, token, binding, path, *, deadline):
        entry = self._entry(binding)
        with self._condition:
            revision = entry.debounce_revision
        entry, canonical = self._canonical_entry(binding, path)
        with self._condition:
            if (self._closed or self._watcher_token is not token or not self._watcher_accepting
                    or entry.monitoring_error_type is not None):
                return False
            if canonical not in entry.deferred and revision != entry.debounce_revision:
                # Validation crossed expiry, direct transfer, cancellation or
                # full absorption. Do not resurrect an old targeted reservation.
                # An unrelated identity may have changed the owner revision;
                # conservative full debt preserves that event without a second
                # per-path history map. Existing duplicates remain wakeups.
                self._request_full(entry)
                self._wake(entry)
                return False
            if not self._admit(entry, canonical):
                return False
            entry.deferred[canonical] = max(entry.deferred.get(canonical, deadline), deadline)
            # A duplicate is an explicit recovery trigger, including while a
            # failed/cancelled turn is publishing its terminal state.
            self._wake(entry)
            return True

    def _deliver_debounced(self, token, binding, path, deadline):
        # No condition held while rechecking root identity and containment.
        entry, canonical = self._canonical_entry(binding, path)
        with self._condition:
            if self._closed or self._watcher_token is not token:
                return
            if entry.deferred.get(path.relative_path) != deadline:
                return  # Updated, directly submitted, cancelled or absorbed.
            if canonical != path.relative_path:
                raise SpaceError("unavailable_space")
            if entry.monitoring_error_type is not None:
                self._debounce_failed(binding, path, deadline)
                return
            entry.pending.add(canonical)
            del entry.deferred[canonical]  # Atomic transfer; count is unchanged.
            entry.debounce_revision += 1
            self._wake(entry)

    def _debounce_failed(self, binding, path, deadline):
        # Bookkeeping for an already accepted reservation must still complete
        # if the registry was invalidated. Never access a revoked filesystem or
        # let one invalid owner kill the other owners' dispatcher.
        entry = self._owners.get(binding.space_id)
        if (entry is None or entry.binding._owners is not binding._owners
                or entry.binding._registry_token is not binding._registry_token):
            return
        with self._condition:
            if entry.deferred.get(path.relative_path) == deadline:
                before = len(self._identities(entry))
                del entry.deferred[path.relative_path]
                entry.debounce_revision += 1
                self._total += len(self._identities(entry)) - before
                self._request_full(entry)
                if not self._closed:
                    self._wake(entry)

    def _fence_watcher(self, token, bindings=()):
        with self._condition:
            if self._watcher_token is token:
                if self._watcher_accepting:
                    # Coverage ends BEFORE any producer is stopped. This is
                    # evidence, not an automatic sync/retry trigger: only a
                    # verified reconstruction can restore this revision.
                    for binding in bindings:
                        entry = self._owners[binding.space_id]
                        entry.coverage_revision += 1
                        entry.monitoring_revision += 1
                        entry.watcher_verified = None
                self._watcher_accepting = False
                self._condition.notify_all()

    def _retire_watcher_work(self, token):
        with self._condition:
            if self._watcher_token is not token:
                return
            for entry in self._owners.values():
                if entry.deferred:
                    before = len(self._identities(entry))
                    entry.deferred.clear()
                    entry.debounce_revision += 1
                    self._total += len(self._identities(entry)) - before
                    self._request_full(entry)
            self._condition.notify_all()

    def _detach_watcher(self, token):
        # Caller must have verified native AND dispatcher cleanup. Fencing or
        # retiring work alone never relinquishes exclusive generation ownership.
        with self._condition:
            if self._watcher_token is not token:
                return
            self._retire_watcher_work(token)
            self._watcher_accepting = False
            self._watcher_token = None
            self._condition.notify_all()

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

    def _watcher_loss(self, binding, *, token=None):
        # Ingress callbacks have no filesystem work and retain no event data.
        # Repeated loss only updates existing E4 flags, including full_again
        # during an active turn. It cannot hot-retry a paused failed owner.
        try:
            entry = self._entry(binding)
        except SpaceError:
            return  # Revoked/foreign bindings authorize no work.
        with self._condition:
            if token is not None and self._watcher_token is not token:
                return
            already_needed = entry.full_needed
            self._request_full(entry)
            if not already_needed:
                self._wake(entry)

    def _watcher_failed(self, binding, error_type, *, token=None):
        try:
            entry = self._entry(binding)
        except SpaceError:
            return
        with self._condition:
            if token is not None and self._watcher_token is not token:
                return
            first = entry.monitoring_error_type is None
            entry.monitoring_error_type = error_type
            entry.monitoring_revision += 1
            entry.monitoring_failure_revision = entry.monitoring_revision
            entry.watcher_verified = None
            if first:
                entry.coverage_revision += 1  # Fatal monitoring invalidates coverage, too.
                self._watcher_loss(binding)
            self._condition.notify_all()

    def _watcher_gap(self, token, binding, *, reconcile=True):
        try:
            entry = self._entry(binding)
        except SpaceError:
            return None
        with self._condition:
            if self._closed or self._watcher_token is not token or not self._watcher_accepting:
                return None
            entry.coverage_revision += 1
            entry.monitoring_revision += 1
            if reconcile and self._can_reconcile_coverage(entry):
                self._watcher_loss(binding)
            self._condition.notify_all()
            return entry.coverage_revision, entry.monitoring_failure_revision

    def _watcher_restored(self, token, binding, ticket):
        try:
            entry = self._entry(binding)
        except SpaceError:
            return
        if not isinstance(ticket, tuple) or len(ticket) != 2:
            return
        revision, failure_revision = ticket
        with self._condition:
            if (revision is None or self._closed or self._watcher_token is not token
                    or not self._watcher_accepting or entry.coverage_revision != revision
                    or entry.coverage_restored >= revision
                    or entry.monitoring_failure_revision != failure_revision):
                return
            entry.coverage_restored = revision
            if not self._can_reconcile_coverage(entry):
                return  # Retain native proof until this generation is verified.
            # This trigger is ordered AFTER native registration and validation.
            # If a full turn already began, full_again forces a later full turn;
            # only that later turn can capture the restored coverage revision.
            self._request_full(entry)
            self._wake(entry)

    def _can_reconcile_coverage(self, entry):
        # Verification is distinct from coverage. A receipt survives legitimate
        # subtree gaps, but any new fatal failure or shutdown invalidates it.
        if self._watcher_token is None:
            return entry.monitoring_error_type is None
        return entry.watcher_verified == (self._watcher_token, entry.monitoring_failure_revision)

    def _watcher_reconstructed(self, token, binding, snapshot):
        # Called only after a fresh native Observer and its required producer
        # are alive. A concurrent failure invalidates that liveness observation.
        entry = self._entry(binding)
        with self._condition:
            revision, failure_revision, coverage_revision = snapshot
            if (self._closed or self._watcher_token is not token or not self._watcher_accepting
                    or entry.monitoring_revision < revision
                    or entry.monitoring_failure_revision != failure_revision):
                return
            if entry.watcher_verified == (token, failure_revision):
                return  # Repeated readiness is not another recovery trigger.
            entry.watcher_verified = (token, failure_revision)
            if (entry.monitoring_error_type is not None
                    or entry.coverage_reconciled != entry.coverage_revision):
                if entry.coverage_revision == coverage_revision:
                    # No subtree work in this startup: verified recursive setup
                    # restores the stopped interval. Otherwise only its matching
                    # native fence may establish the newer coverage revision.
                    entry.coverage_revision += 1
                    entry.monitoring_revision += 1
                    entry.coverage_restored = entry.coverage_revision
                if entry.coverage_restored != entry.coverage_revision:
                    return
                # Reconstruction is one explicit recovery trigger even when
                # earlier gap work failed or was cancelled and remains paused.
                self._request_full(entry)
                self._wake(entry)

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
            if self._identities(entry) or entry.continuation is not None or entry.running:
                entry.full_needed = True
            self._total -= len(self._identities(entry) - entry.inflight)
            entry.pending.clear()
            entry.deferred.clear()
            entry.debounce_revision += 1
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
        self._total -= len(entry.inflight - (entry.pending | entry.deferred.keys()))
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
                        self._total -= len(self._identities(entry))
                        entry.deferred.clear()
                        entry.debounce_revision += 1
                    else:
                        entry.inflight = entry.pending
                    entry.pending = set()
                    entry.full_active = full
                    entry.full_again = False
                    # Capture BEFORE begin_sync: discovery may start there.
                    entry.full_coverage = (
                        (self._watcher_token, entry.coverage_revision)
                        if entry.coverage_restored == entry.coverage_revision
                        and self._can_reconcile_coverage(entry) else None
                    )
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
                        if (not self._closed and self._watcher_accepting
                                and self._can_reconcile_coverage(entry)
                                and entry.full_coverage == (self._watcher_token, entry.coverage_revision)
                                and entry.coverage_restored == entry.coverage_revision):
                            entry.coverage_reconciled = entry.coverage_revision
                            entry.monitoring_error_type = None
                    entry.full_coverage = None
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
                entry.full_coverage = None
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
            return SchedulerStatus(tuple(sorted(self._identities(entry))),
                                   entry.full_needed or entry.monitoring_error_type is not None
                                   or entry.coverage_reconciled != entry.coverage_revision
                                   or (bool(entry.watcher_callbacks)
                                       and (self._closed or not self._watcher_accepting)),
                                   entry.running or entry.continuation is not None, entry.paused,
                                   entry.error_type, entry.progress, entry.monitoring_error_type,
                                   entry.watcher_callbacks,
                                   entry.coverage_reconciled != entry.coverage_revision)

    @property
    def pending_count(self) -> int:
        """Distinct reservations across debounce, queued and in-flight stages."""
        with self._condition:
            return self._total

    def wait(self, timeout: float | None = None) -> None:
        """Wait for quiescence, including paused failures; does not trigger retry."""
        with self._condition:
            if not self._condition.wait_for(lambda: all(
                not entry.running and not self._ready(entry) for entry in self._owners.values()
            ), timeout):
                raise TimeoutError("Semantic scheduler did not become idle")

    def begin_shutdown(self) -> None:
        """Fence and signal all owners before E6 stops observer/debounce delivery.

        Does not drain. Call shutdown() after stopping the process-owned watcher.
        """
        with self._condition:
            first = not self._closed
            self._closed = True  # Fence submissions before signalling every owner.
            if first:
                for entry in self._owners.values():
                    entry.cancel.set()
                    entry.paused = True
                    if self._identities(entry) or entry.continuation is not None or entry.running:
                        entry.full_needed = True
                    self._total -= len(self._identities(entry) - entry.inflight)
                    entry.pending.clear()
                    entry.deferred.clear()
                    entry.debounce_revision += 1
                self._condition.notify_all()

    def shutdown(self) -> None:
        self.begin_shutdown()
        with self._condition:
            first = not self._shutdown_started
            self._shutdown_started = True
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
