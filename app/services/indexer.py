from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any


class BackgroundSemanticIndexer:
    """Own one in-process semantic synchronization worker."""

    def __init__(self, sync: Callable[[threading.Event], Any]) -> None:
        self._sync = sync
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vaultbridge-index")
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._future: Future[Any] | None = None
        self._last_error: BaseException | None = None
        self._closed = False

    def start(self) -> bool:
        """Submit synchronization unless one is already running."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Background semantic indexer is shut down")
            if self._future is not None and not self._future.done():
                return False
            self._last_error = None
            self._cancel_event.clear()
            future = self._executor.submit(self._sync, self._cancel_event)
            self._future = future
        future.add_done_callback(self._record_completion)
        return True

    def _record_completion(self, future: Future[Any]) -> None:
        try:
            future.result()
        except BaseException as exc:
            with self._lock:
                if self._future is future:
                    self._last_error = exc

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._future is not None and not self._future.done()

    @property
    def last_error(self) -> BaseException | None:
        with self._lock:
            return self._last_error

    def wait(self, timeout: float | None = None) -> Any:
        with self._lock:
            future = self._future
        return future.result(timeout=timeout) if future is not None else None

    def shutdown(self) -> None:
        """Request a stop, then wait for the active batch boundary or blocking call."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
