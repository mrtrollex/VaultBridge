"""Private E5 watcher; public named-space composition remains deferred to E6.

The scheduler owns all targeted reservations, including monotonic debounce
deadlines. There is no second path queue. Its condition protects only bounded
in-memory coordination, never filesystem validation, SQLite or model calls.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import PurePath

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.core.logging import log_event
from app.repositories.semantic_binding import ordinary_identity
from app.services.filesystem_watcher import (
    _OBSERVER_JOIN_TIMEOUT_SECONDS,
    FilesystemEventInterpreter,
    _Observer,
)
from app.services.knowledge_spaces import AuthorizedSpaceBinding, QualifiedPath, SpaceError, SpaceRegistry
from app.services.semantic_scheduler import SharedSemanticScheduler
from app.services.vault import SEMANTIC_EXCLUDED_DIRECTORIES
from app.services.watcher_ingress import install_bounded_ingress, native_watch_alive, wait_native_watch_ready

logger = logging.getLogger("vaultbridge.filesystem_watcher")

# One existing dispatcher checks <=8 roots and native pipelines without events.
# Idle detection takes at most one second plus filesystem/check scheduling time.
ROOT_VALIDATION_INTERVAL_SECONDS = 1.0


class _BoundEventHandler(FileSystemEventHandler):
    def __init__(self, watcher, binding):
        self._watcher = watcher
        self._binding = binding
        definition = binding._owners._definition
        self._interpreter = FilesystemEventInterpreter(
            binding._owners.vault, semantic_data_path=definition.semantic_data_binding,
        )

    def on_any_event(self, event: FileSystemEvent) -> None:
        self._watcher._offer(self._binding, self._interpreter, event)


class SharedFilesystemWatcher:
    """One process observer and one dispatcher for <=8 exact enabled bindings.

    Construction is inert. Start validates all bindings before allocating the
    observer or dispatcher. Stop fences admission, joins the observer, then joins
    the dispatcher and converts residual reservations to coalesced full debt;
    it never flushes syncs or waits for a full reconciliation. This differs from
    the unchanged legacy watcher's targeted flush contract.

    Process shutdown order: scheduler.begin_shutdown(), watcher.stop(), then
    scheduler.shutdown() and E2 resource close. E6 owns that composition. Stopping
    this watcher alone leaves recovery debt in the live scheduler.
    """

    def __init__(
        self, registry: SpaceRegistry, bindings: Iterable[AuthorizedSpaceBinding], *,
        scheduler: SharedSemanticScheduler, debounce_seconds: float,
        observer_factory: Callable[[], _Observer] = Observer,
        clock: Callable[[], float] = time.monotonic,
    ):
        if debounce_seconds <= 0 or not math.isfinite(debounce_seconds):
            raise ValueError("debounce_seconds must be positive")
        if scheduler._registry is not registry:
            raise SpaceError("invalid_scope")
        self._scheduler = scheduler
        self._bindings = {}
        for count, binding in enumerate(bindings, 1):
            if count > 8 or not registry._accepts(binding):
                raise SpaceError("invalid_scope")
            if binding.policy.read != "allow" or binding.policy.indexing != "enabled":
                continue
            if binding.space_id in self._bindings:
                raise SpaceError("invalid_scope")
            scheduler._entry(binding)
            self._bindings[binding.space_id] = binding
        self._debounce_seconds = debounce_seconds
        self._clock = clock
        self._observer_factory = observer_factory
        self._observer = None
        self._thread = None
        self._handlers = []
        self._watches = []
        self._producers = ()  # <=8 native emitters retained until verified cleanup.
        self._root_directories = {}
        self._ingress = None
        self._token = object()
        self._lifecycle_lock = threading.RLock()
        self._attempted = False
        self._accepting = False
        self._stopping = False

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._attempted or self._stopping:
                raise RuntimeError("Shared filesystem watcher was already started or stopped")
            self._attempted = True
            try:
                for binding in self._bindings.values():
                    root = binding._owners.vault.vault_root
                    # Default owners have no named E1 ExpectedIndexBinding.
                    # Reuse E1's ordinary-directory/ancestor identity checks.
                    if self._scheduler._entry(binding).repository._expected_binding is None:
                        self._root_directories[binding.space_id] = tuple(
                            (parent, ordinary_identity(parent, directory=True))
                            for parent in reversed((root, *root.parents))
                        )
                    self._validate_binding(binding)
                if not self._bindings:
                    return
                self._scheduler._attach_watcher(self._token)
                with self._scheduler._condition:
                    health_revisions = {
                        sid: (entry.monitoring_revision, entry.monitoring_failure_revision, entry.coverage_revision)
                        for sid in self._bindings for entry in (self._scheduler._owners[sid],)
                    }
                    cancellations = {sid: (self._scheduler._owners[sid].cancel,
                                           self._scheduler._owners[sid].cancel.is_set())
                                     for sid in self._bindings}

                def active():
                    with self._scheduler._condition:
                        return (not self._stopping and not self._scheduler._closed
                                and self._scheduler._watcher_token is self._token
                                and all(entry.cancel is cancellations[sid][0]
                                        and (cancellations[sid][1] or not entry.cancel.is_set())
                                        for sid, entry in self._scheduler._owners.items()
                                        if sid in self._bindings))
                self._observer = self._observer_factory()
                self._ingress = install_bounded_ingress(
                    self._observer, self._ingress_loss, self._ingress_failure,
                    validate=self._validate_watch, gap=self._ingress_gap, restored=self._ingress_restored,
                    departures=self._ingress_departures,
                )
                if self._ingress is not None:
                    # FIRST native attachment is an uncovered interval too.
                    # Record debt before scheduling/starting producers; only
                    # verified readiness may request its authoritative full.
                    with self._scheduler._condition:
                        for sid, binding in self._bindings.items():
                            self._scheduler._watcher_gap(self._token, binding, reconcile=False)
                            entry = self._scheduler._owners[sid]
                            health_revisions[sid] = (entry.monitoring_revision,
                                                     entry.monitoring_failure_revision, entry.coverage_revision)
                for sid, binding in sorted(self._bindings.items()):
                    handler = _BoundEventHandler(self, binding)
                    self._handlers.append(handler)
                    watch = self._observer.schedule(handler, str(binding._owners.vault.vault_root), recursive=True)
                    self._watches.append((watch, binding))
                # Registration is injectable and may have raced a root change.
                for binding in self._bindings.values():
                    self._validate_binding(binding)
                with self._scheduler._condition:
                    if not active():
                        raise RuntimeError("Shared filesystem native arming was cancelled")
                    self._accepting = True
                self._thread = threading.Thread(
                    target=self._run, name="vaultbridge-shared-watch-dispatch", daemon=True,
                )
                self._thread.start()
                if self._ingress is not None:
                    # Retain even a failed emitter that watchdog removes from
                    # its set when on_thread_start raises during allocation.
                    self._producers = tuple(self._observer.emitters)
                self._observer.start()
                if self._ingress is not None:
                    for watch, binding in self._watches:
                        try:
                            wait_native_watch_ready(self._observer, watch, active)
                        except TimeoutError:
                            self._ingress_failure(watch, "NativeArmingTimeout")
                            raise
                        except RuntimeError:
                            if not self._thread.is_alive():
                                raise RuntimeError("Shared filesystem dispatcher did not start") from None
                            if not self._observer.is_alive():
                                self._ingress_failure(None, "NativeObserverUnavailable")
                                raise RuntimeError("Shared filesystem observer did not start") from None
                            raise
                        if not native_watch_alive(self._observer, watch):
                            self._ingress_failure(watch, "NativeProducerUnavailable")
                            raise RuntimeError("Shared filesystem producer did not start")
                        try:
                            self._validate_binding(binding)  # Root identity AFTER native acknowledgment.
                        except Exception as error:
                            self._ingress_failure(watch, type(error).__name__)
                            raise
                    with self._scheduler._condition:
                        if not self._thread.is_alive() or not self._accepting or not active():
                            raise RuntimeError("Shared filesystem dispatcher did not start")
                        if not self._observer.is_alive():
                            self._ingress_failure(None, "NativeObserverUnavailable")
                            raise RuntimeError("Shared filesystem observer did not start")
                        # Existing cleanup references also identify completed
                        # startup for periodic liveness checks. Before this,
                        # recursive registration/native arming can still be busy.
                        self._producers = tuple(self._observer.emitters)
                        for watch, binding in self._watches:
                            if not native_watch_alive(self._observer, watch):
                                self._ingress_failure(watch, "NativeProducerUnavailable")
                                raise RuntimeError("Shared filesystem producer did not start")
                            self._scheduler._watcher_reconstructed(
                                self._token, binding, health_revisions[binding.space_id],
                            )
            except BaseException:
                self.stop()
                raise

    def stop_admission(self) -> None:
        with self._scheduler._condition:
            self._accepting = False
            self._stopping = True
            if self._scheduler._watcher_token is self._token:
                for sid, binding in self._bindings.items():
                    if self._scheduler._owners[sid].watcher_callbacks:
                        self._scheduler._watcher_loss(binding)
            self._scheduler._fence_watcher(self._token, self._bindings.values())
            self._scheduler._condition.notify_all()

    def stop(self) -> None:
        # Fence a start waiting for native readiness without waiting for its
        # lifecycle lock. Cleanup itself remains serialized by that lock.
        self.stop_admission()
        with self._lifecycle_lock:
            if self._ingress is not None:
                self._ingress.close()
            error = None
            observer = self._observer
            if observer is not None:
                try:
                    # watchdog removes its emitter set during stop. Retain the
                    # bounded original objects across failures and retries.
                    if self._ingress is not None and not self._producers:
                        self._producers = tuple(observer.emitters)
                    observer.stop()
                    try:
                        observer.join(timeout=_OBSERVER_JOIN_TIMEOUT_SECONDS)
                    except RuntimeError:
                        if observer.is_alive():
                            raise
                    if observer.is_alive():
                        raise RuntimeError("Shared filesystem observer did not stop")
                    for producer in self._producers:
                        if producer.ident is not None:
                            producer.join(timeout=_OBSERVER_JOIN_TIMEOUT_SECONDS)
                        native_read = getattr(producer, "_native_read", None)
                        if native_read is not None and not producer.is_alive():
                            # watchdog may have removed a failed-start emitter.
                            # Its retained native handles still require retry.
                            producer.stop()
                        reader = getattr(producer, "_inotify", None)
                        if reader is not None and reader.ident is not None:
                            reader.join(timeout=_OBSERVER_JOIN_TIMEOUT_SECONDS)
                        if reader is not None and not reader.is_alive():
                            reader.close()  # Retry cleanup, including partial startup's queue.
                        if (producer.is_alive() or (reader is not None and reader.is_alive())
                                or getattr(producer, "_whandle", None) is not None
                                or (native_read is not None
                                    and (native_read.pending or native_read.event is not None))
                                or (reader is not None and not reader._inotify._resources_closed)):
                            raise RuntimeError("Shared filesystem producer cleanup is incomplete")
                    observer.unschedule_all()
                    self._observer = None
                    self._handlers.clear()
                    self._watches.clear()
                    self._ingress = None
                    self._producers = ()
                except BaseException as exc:
                    error = exc
            thread = self._thread
            if thread is not None and thread.ident is None:
                self._thread = None
            if thread is not None and thread.ident is not None:
                thread.join(timeout=_OBSERVER_JOIN_TIMEOUT_SECONDS)
                if thread.is_alive():
                    error = error or RuntimeError("Shared filesystem dispatcher did not stop")
                else:
                    self._thread = None
            if self._thread is None or self._thread.ident is None:
                self._scheduler._retire_watcher_work(self._token)
                if self._observer is None and error is None:
                    self._scheduler._detach_watcher(self._token)
            if error is not None:
                raise error

    def _ingress_loss(self, watch):
        # Only exact scheduled watch objects supply ownership evidence. Event
        # src/dest paths (including lost rename ends) never select an owner.
        for binding in self._affected_bindings(watch):
            self._scheduler._watcher_loss(binding, token=self._token)

    def _affected_bindings(self, watch):
        binding = next((binding for registered, binding in self._watches if registered is watch), None)
        return (binding,) if binding is not None else self._bindings.values()

    def _ingress_failure(self, watch, error_type):
        for binding in self._affected_bindings(watch):
            self._scheduler._watcher_failed(binding, error_type, token=self._token)

    def _validate_watch(self, watch):
        # Filesystem validation runs on native/startup paths without the E4
        # condition held. An unknown watch cannot authorize probing any root.
        binding = next((binding for registered, binding in self._watches if registered is watch), None)
        if binding is None:
            raise SpaceError("invalid_scope")
        self._scheduler._validate_owner(self._scheduler._entry(binding))

    def _validate_binding(self, binding):
        entry = self._scheduler._entry(binding)
        expected = entry.repository._expected_binding
        if expected is not None:
            expected.check_root()
        else:
            for parent, identity in self._root_directories[binding.space_id]:
                if ordinary_identity(parent, directory=True) != identity:
                    raise SpaceError("unavailable_space")
        self._scheduler._validate_owner(entry)

    def _check_roots(self):
        for sid, binding in self._bindings.items():
            with self._scheduler._condition:
                if (self._stopping or self._scheduler._closed
                        or self._scheduler._watcher_token is not self._token):
                    return
                entry = self._scheduler._owners[sid]
                if (entry.monitoring_error_type is not None
                        and not self._scheduler._can_reconcile_coverage(entry)):
                    continue  # Fatal health requires explicit reconstruction.
                revision = entry.monitoring_revision
                verified = self._scheduler._can_reconcile_coverage(entry)
                observer, producers = self._observer, self._producers
                watch = next((watch for watch, owner in self._watches if owner is binding), None)
            error_type = None
            try:
                if self._ingress is not None and producers and verified:
                    # No scheduler lock over thread probes or filesystem I/O.
                    # A dead shared dispatch invalidates each served binding;
                    # a dead emitter/reader invalidates only its exact watch.
                    if not observer.is_alive() or not observer.should_keep_running():
                        error_type = "NativeObserverUnavailable"
                    elif not native_watch_alive(observer, watch, producers=producers):
                        error_type = "NativeProducerUnavailable"
                if error_type is None:
                    self._validate_binding(binding)  # No scheduler lock over I/O.
            except Exception as error:
                error_type = type(error).__name__
            if error_type is not None:
                with self._scheduler._condition:
                    if (self._scheduler._watcher_token is self._token and not self._stopping
                            and not self._scheduler._closed and entry.monitoring_revision == revision):
                        self._scheduler._watcher_failed(binding, error_type, token=self._token)

    def _ingress_gap(self, watch):
        # At most eight scalar revision tickets; no retained event payloads.
        return tuple((binding, self._scheduler._watcher_gap(self._token, binding))
                     for binding in self._affected_bindings(watch))

    def _ingress_restored(self, watch, tickets):
        for binding, ticket in tickets:
            self._scheduler._watcher_restored(self._token, binding, ticket)

    def _departure_relevant(self, binding, raw_path):
        # Pure lexical exclusions only: a removed name can have been a directory,
        # even with a .md suffix or a temporary-looking name. Never probe/follow
        # a replacement or an outside path to classify the former object.
        try:
            root = PurePath(binding._owners.vault.vault_root)
            relative = PurePath(os.fsdecode(raw_path))
            if relative.is_absolute():
                relative = relative.relative_to(root)
            if relative.drive or ".." in relative.parts or not relative.parts:
                return True
            if any(part in SEMANTIC_EXCLUDED_DIRECTORIES for part in relative.parts):
                return False
            data = PurePath(binding._owners._definition.semantic_data_binding)
            return not data.is_relative_to(root) or not relative.is_relative_to(data.relative_to(root))
        except (OSError, TypeError, ValueError):
            return True  # Uncertain metadata never proves that descendants were irrelevant.

    def _ingress_departures(self, watch, paths):
        binding = next((binding for registered, binding in self._watches if registered is watch), None)
        if binding is None:
            self._ingress_loss(watch)
        elif any(self._departure_relevant(binding, path) for path in paths):
            self._scheduler._watcher_loss(binding, token=self._token)

    def _offer(self, binding, interpreter, event):
        with self._scheduler._condition:
            if not self._accepting or self._bindings.get(binding.space_id) is not binding:
                return
            # Bounded per-owner counters only; no event/callback payload buffer.
            # Identity is recorded before owner probes or interpreter filesystem
            # work can block, so stop covers events already dequeued by watchdog.
            entry = self._scheduler._owners[binding.space_id]
            if self._ingress is not None and not self._scheduler._can_reconcile_coverage(entry):
                self._scheduler._watcher_loss(binding, token=self._token)
                return
            if entry.monitoring_error_type is not None:
                if self._scheduler._can_reconcile_coverage(entry):
                    self._scheduler._watcher_loss(binding, token=self._token)
                return  # Failed monitoring cannot acknowledge targeted delivery.
            entry.watcher_callbacks += 1
        rejected = False
        try:
            self._scheduler._validate_owner(self._scheduler._entry(binding))
            if (os.name == "nt" and self._ingress is not None
                    and (event.event_type in {"deleted", "moved"}
                         or (event.is_directory and event.event_type == "created"))
                    and (self._departure_relevant(binding, event.src_path)
                         or (event.event_type == "moved"
                             and self._departure_relevant(binding, getattr(event, "dest_path", ""))))):
                # watchdog turns FILE_ACTION_REMOVED into FileDeletedEvent for
                # BOTH files and directories. Record debt before the unchanged
                # Markdown interpreter can ignore it. E4 coalesces pending/active
                # full work and preserves a trigger arriving during an older full.
                self._scheduler._watcher_loss(binding, token=self._token)
            # The interpreter returns at most two paths. It never crawls roots.
            for relative in interpreter.paths_for_event(event):
                if not self._scheduler._debounce(
                    self._token, binding, QualifiedPath(binding.space_id, relative),
                    deadline=self._clock() + self._debounce_seconds,
                ):
                    rejected = True
        except Exception as error:
            # Uncertain evidence is recovery debt, never authorization to delete.
            # Failure categories only: no host paths, note names or tracebacks.
            rejected = True
            self._log_failure(error)
        finally:
            with self._scheduler._condition:
                try:
                    if rejected or not self._accepting:
                        self._scheduler._watcher_loss(binding)
                finally:
                    entry.watcher_callbacks -= 1
                    self._scheduler._condition.notify_all()

    @staticmethod
    def _log_failure(error):
        log_event(logger, logging.ERROR, "semantic_watcher_failed",
                  "Shared semantic filesystem event delivery failed", error_type=type(error).__name__)

    def _next_locked(self):
        # A bounded scan chooses one identity; never copies an event batch. Stable
        # deadline/ID/path ordering prevents activity in B from starving ready A.
        return min((
            (deadline, sid, path)
            for sid in self._bindings
            for path, deadline in self._scheduler._owners[sid].deferred.items()
        ), default=None)

    def _run(self):
        try:
            self._dispatch_loop()
            if not self._stopping:
                raise RuntimeError("Shared filesystem dispatcher stopped unexpectedly")
        except BaseException as error:
            # This dispatcher serves every enabled binding; its failure has no
            # narrower provable owner scope. Publish fatal health BEFORE fencing
            # or ordinary full-sync completion can appear sufficient.
            with self._scheduler._condition:
                self._ingress_failure(None, type(error).__name__)
                self.stop_admission()
                self._scheduler._retire_watcher_work(self._token)
            self._log_failure(error)

    def _dispatch_loop(self):
        integrity_due = time.monotonic() + ROOT_VALIDATION_INTERVAL_SECONDS
        while True:
            with self._scheduler._condition:
                if self._stopping:
                    return
                check_roots = time.monotonic() >= integrity_due
                if not check_roots:
                    item = self._next_locked()
                    until_check = max(0, integrity_due - time.monotonic())
                    if item is None:
                        self._scheduler._condition.wait(timeout=until_check)
                        continue
                    deadline, sid, relative = item
                    remaining = deadline - self._clock()
                    if remaining > 0:
                        self._scheduler._condition.wait(timeout=min(remaining, until_check))
                        continue
                    binding = self._bindings[sid]
                    path = QualifiedPath(sid, relative)
            if check_roots:
                self._check_roots()
                integrity_due = time.monotonic() + ROOT_VALIDATION_INTERVAL_SECONDS
                continue
            try:
                self._scheduler._deliver_debounced(self._token, binding, path, deadline)
            except Exception as error:
                self._scheduler._debounce_failed(binding, path, deadline)
                self._log_failure(error)
