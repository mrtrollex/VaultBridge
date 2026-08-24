from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from app.core.logging import log_event

logger = logging.getLogger("vaultbridge.indexer")


class BackgroundSemanticIndexer:
    """Own one in-process semantic synchronization worker."""

    def __init__(
        self,
        sync: Callable[[threading.Event], Any],
        sync_paths: Callable[[Sequence[str], threading.Event], Any] | None = None,
    ) -> None:
        self._sync = sync
        self._sync_paths = sync_paths
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vaultbridge-index")
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._future: Future[Any] | None = None
        self._last_error: BaseException | None = None
        self._full_sync_requested = False
        self._full_sync_required = False
        self._pending_paths: set[str] = set()
        self._follow_up_requested = False
        self._closed = False

    def start(self) -> bool:
        """Submit synchronization unless one is already running."""
        future = None
        with self._lock:
            if self._closed:
                raise RuntimeError("Background semantic indexer is shut down")
            if self._future is not None and not self._future.done():
                return False
            self._full_sync_requested = True
            future = self._submit_locked()
            if future is not None:
                log_event(
                    logger,
                    logging.INFO,
                    "semantic_full_sync_scheduled",
                    "Full semantic synchronization was scheduled",
                    operation="full",
                    full_sync_required=(
                        self._full_sync_required or self._full_sync_requested
                    ),
                )
        if future is not None:
            future.add_done_callback(self._record_completion)
            return True
        return False

    def enqueue(self, path: str) -> bool:
        """Queue one vault-relative note path, coalescing duplicate pending work."""
        normalized = path.strip().replace("\\", "/")
        posix_path = PurePosixPath(normalized)
        if (
            not normalized
            or posix_path.is_absolute()
            or PureWindowsPath(normalized).is_absolute()
            or ".." in posix_path.parts
        ):
            raise ValueError("Queued semantic path must be vault-relative")
        normalized = posix_path.as_posix()

        future = None
        queued_notes = 0
        log_queued = False
        with self._lock:
            if self._closed:
                return False
            added = normalized not in self._pending_paths
            self._pending_paths.add(normalized)
            queued_notes = len(self._pending_paths)
            if self._future is not None and not self._future.done():
                self._follow_up_requested = True
                log_queued = added
            else:
                future = self._submit_locked()
                log_queued = future is not None
            if log_queued:
                log_event(
                    logger,
                    logging.INFO,
                    "targeted_reindex_queued",
                    "Targeted semantic reindex was queued",
                    operation="targeted",
                    note_path=normalized,
                    queued_notes=queued_notes,
                )
        if future is not None:
            future.add_done_callback(self._record_completion)
        return added

    def _submit_locked(self) -> Future[Any] | None:
        self._cancel_event.clear()
        try:
            future = self._executor.submit(self._run)
        except Exception as exc:
            self._last_error = exc
            if self._future is not None and self._future.done():
                self._future = None
            log_event(
                logger,
                logging.ERROR,
                "semantic_worker_submission_failed",
                "Semantic worker submission failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                error_type=type(exc).__name__,
            )
            return None
        self._last_error = None
        self._future = future
        return future

    def _run(self) -> Any:
        last_result = None
        while True:
            with self._lock:
                if self._closed:
                    return last_result
                if self._full_sync_requested or self._full_sync_required:
                    self._full_sync_requested = False
                    self._full_sync_required = True
                    paths = tuple(sorted(self._pending_paths))
                    self._pending_paths.clear()
                    self._follow_up_requested = False
                    operation = "full"
                elif self._pending_paths:
                    paths = tuple(sorted(self._pending_paths))
                    self._pending_paths.clear()
                    self._follow_up_requested = False
                    operation = "targeted" if self._sync_paths is not None else "full"
                    if operation == "full":
                        self._full_sync_required = True
                else:
                    return last_result

            try:
                if operation == "full":
                    last_result = self._sync(self._cancel_event)
                    with self._lock:
                        self._full_sync_required = False
                else:
                    assert self._sync_paths is not None
                    last_result = self._sync_paths(paths, self._cancel_event)
            except BaseException:
                with self._lock:
                    if not self._closed:
                        self._pending_paths.update(paths)
                raise

    def _record_completion(self, future: Future[Any]) -> None:
        next_future = None
        try:
            future.result()
        except BaseException as exc:
            with self._lock:
                if self._future is future:
                    self._last_error = exc
                    if not self._closed and self._follow_up_requested:
                        next_future = self._submit_locked()
            if next_future is not None:
                next_future.add_done_callback(self._record_completion)
            return

        with self._lock:
            if (
                self._future is future
                and not self._closed
                and (self._full_sync_requested or self._full_sync_required or self._pending_paths)
            ):
                next_future = self._submit_locked()
        if next_future is not None:
            next_future.add_done_callback(self._record_completion)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._future is not None and not self._future.done()

    @property
    def last_error(self) -> BaseException | None:
        with self._lock:
            return self._last_error

    @property
    def queued_paths(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._pending_paths))

    @property
    def requires_full_sync(self) -> bool:
        with self._lock:
            return self._full_sync_required or self._full_sync_requested

    def wait(self, timeout: float | None = None) -> Any:
        deadline = None if timeout is None else time.monotonic() + timeout
        last_result = None
        while True:
            with self._lock:
                future = self._future
            if future is None:
                return last_result

            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            last_result = future.result(timeout=remaining)

            next_future = None
            with self._lock:
                if self._future is not future:
                    continue
                if not self._closed and (
                    self._full_sync_requested or self._full_sync_required or self._pending_paths
                ):
                    next_future = self._submit_locked()
            if next_future is not None:
                next_future.add_done_callback(self._record_completion)
                continue
            return last_result

    def shutdown(self) -> None:
        """Request a stop, then wait for the active batch boundary or blocking call."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_event.set()
            self._full_sync_requested = False
            self._follow_up_requested = False
            self._pending_paths.clear()
        log_event(
            logger,
            logging.INFO,
            "semantic_indexer_shutdown_requested",
            "Semantic indexer shutdown was requested",
        )
        self._executor.shutdown(wait=True, cancel_futures=True)
        log_event(
            logger,
            logging.INFO,
            "semantic_indexer_stopped",
            "Semantic indexer stopped",
        )
