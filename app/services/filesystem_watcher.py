from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileSystemMovedEvent
from watchdog.observers import Observer

from app.core.logging import log_event
from app.services.indexer import BackgroundSemanticIndexer
from app.services.vault import SEMANTIC_EXCLUDED_DIRECTORIES, VaultService

logger = logging.getLogger("vaultbridge.filesystem_watcher")

_OBSERVER_JOIN_TIMEOUT_SECONDS = 5.0
_TEMPORARY_NAME_PREFIXES = (".#", ".~", "~$")


class _Observer(Protocol):
    def schedule(
        self,
        event_handler: FileSystemEventHandler,
        path: str,
        *,
        recursive: bool,
    ) -> Any: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


class FilesystemEventInterpreter:
    """Translate watchdog events into safe vault-relative Markdown paths."""

    def __init__(self, vault_service: VaultService, *, semantic_data_path: Path) -> None:
        self._vault_service = vault_service
        self._vault_root = vault_service.vault_root
        self._semantic_data_relative = self._contained_relative_path(semantic_data_path)

    def paths_for_event(self, event: FileSystemEvent) -> tuple[str, ...]:
        if event.is_directory:
            return ()
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return ()

        raw_paths: tuple[str | bytes, ...]
        if isinstance(event, FileSystemMovedEvent) or event.event_type == "moved":
            destination = getattr(event, "dest_path", "")
            raw_paths = (event.src_path, destination)
        else:
            raw_paths = (event.src_path,)

        paths: dict[str, None] = {}
        for raw_path in raw_paths:
            relative_path = self._safe_markdown_path(raw_path)
            if relative_path is not None:
                paths[relative_path] = None
        return tuple(paths)

    def _contained_relative_path(self, path: Path) -> PurePosixPath | None:
        try:
            resolved_path = Path(path).expanduser().resolve(strict=False)
            relative_path = resolved_path.relative_to(self._vault_root)
        except (OSError, ValueError):
            return None
        relative = PurePosixPath(relative_path.as_posix())
        return relative if relative.parts else None

    def _safe_markdown_path(self, raw_path: str | bytes) -> str | None:
        try:
            absolute_path = Path(os.path.abspath(os.fsdecode(raw_path)))
            relative_path = absolute_path.relative_to(self._vault_root)
        except (OSError, TypeError, ValueError):
            return None

        relative = PurePosixPath(relative_path.as_posix())
        if relative.suffix.lower() != ".md":
            return None
        if any(part in SEMANTIC_EXCLUDED_DIRECTORIES for part in relative.parts):
            return None
        if self._semantic_data_relative is not None and relative.is_relative_to(
            self._semantic_data_relative
        ):
            return None
        if relative.name.startswith(_TEMPORARY_NAME_PREFIXES):
            return None

        candidate = self._vault_root / Path(relative.as_posix())
        try:
            live_or_symlink = candidate.exists() or candidate.is_symlink()
        except OSError:
            live_or_symlink = True
        if live_or_symlink:
            return self._vault_service.verify_existing_markdown_path(relative.as_posix())
        return relative.as_posix()


class DebouncedPathQueue:
    """Pure monotonic deadline queue used by the watcher dispatcher."""

    def __init__(self, debounce_seconds: float) -> None:
        if debounce_seconds <= 0 or not math.isfinite(debounce_seconds):
            raise ValueError("debounce_seconds must be positive")
        self._debounce_seconds = debounce_seconds
        self._deadlines: dict[str, float] = {}

    def add(self, paths: Sequence[str], *, now: float) -> bool:
        added = False
        deadline = now + self._debounce_seconds
        for path in paths:
            added = path not in self._deadlines or added
            self._deadlines[path] = deadline
        return added

    @property
    def next_deadline(self) -> float | None:
        return min(self._deadlines.values(), default=None)

    def pop_ready(self, *, now: float) -> tuple[str, ...]:
        ready = sorted(path for path, deadline in self._deadlines.items() if deadline <= now)
        for path in ready:
            del self._deadlines[path]
        return tuple(ready)

    def pop_all(self) -> tuple[str, ...]:
        paths = tuple(sorted(self._deadlines))
        self._deadlines.clear()
        return paths


class DebouncedPathDispatcher:
    """Dispatch coalesced paths from one bounded worker thread."""

    def __init__(
        self,
        *,
        debounce_seconds: float,
        dispatch: Callable[[Sequence[str]], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._queue = DebouncedPathQueue(debounce_seconds)
        self._dispatch = dispatch
        self._clock = clock
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._stopping = False
        self._flush_on_stop = False

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("Filesystem watcher dispatcher was already started")
            self._accepting = True
            self._thread = threading.Thread(
                target=self._run,
                name="vaultbridge-watch-dispatch",
                daemon=True,
            )
            self._thread.start()

    def submit(self, paths: Sequence[str]) -> bool:
        if not paths:
            return False
        with self._condition:
            if not self._accepting:
                return False
            added = self._queue.add(paths, now=self._clock())
            self._condition.notify()
            return added

    def stop(self, *, flush: bool) -> None:
        with self._condition:
            thread = self._thread
            if thread is None:
                return
            self._accepting = False
            self._stopping = True
            self._flush_on_stop = flush
            self._condition.notify_all()
        thread.join(timeout=_OBSERVER_JOIN_TIMEOUT_SECONDS)
        if thread.is_alive():
            raise RuntimeError("Filesystem watcher dispatcher did not stop")

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    paths = self._queue.pop_all() if self._flush_on_stop else ()
                    should_stop = True
                else:
                    deadline = self._queue.next_deadline
                    if deadline is None:
                        self._condition.wait()
                        continue
                    remaining = deadline - self._clock()
                    if remaining > 0:
                        self._condition.wait(timeout=remaining)
                        continue
                    paths = self._queue.pop_ready(now=self._clock())
                    should_stop = False
            if paths:
                self._dispatch(paths)
            if should_stop:
                return


class _WatcherEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        interpreter: FilesystemEventInterpreter,
        dispatcher: DebouncedPathDispatcher,
    ) -> None:
        self._interpreter = interpreter
        self._dispatcher = dispatcher

    def on_created(self, event: FileSystemEvent) -> None:
        self._offer(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._offer(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._offer(event)

    def on_moved(self, event: FileSystemMovedEvent) -> None:
        self._offer(event)

    def _offer(self, event: FileSystemEvent) -> None:
        self._dispatcher.submit(self._interpreter.paths_for_event(event))


class SemanticFilesystemWatcher:
    """Observe external Markdown changes and enqueue existing targeted refreshes."""

    def __init__(
        self,
        *,
        vault_service: VaultService,
        semantic_indexer: BackgroundSemanticIndexer,
        semantic_data_path: Path,
        debounce_seconds: float,
        observer_factory: Callable[[], _Observer] = Observer,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._vault_root = vault_service.vault_root
        self._semantic_indexer = semantic_indexer
        self._observer_factory = observer_factory
        self._observer: _Observer | None = None
        interpreter = FilesystemEventInterpreter(
            vault_service,
            semantic_data_path=semantic_data_path,
        )
        self._dispatcher = DebouncedPathDispatcher(
            debounce_seconds=debounce_seconds,
            dispatch=self._dispatch_paths,
            clock=clock,
        )
        self._event_handler = _WatcherEventHandler(interpreter, self._dispatcher)

    def start(self) -> None:
        observer: _Observer | None = None
        try:
            self._dispatcher.start()
            observer = self._observer_factory()
            observer.schedule(self._event_handler, str(self._vault_root), recursive=True)
            observer.start()
            self._observer = observer
        except BaseException as exc:
            if observer is not None:
                try:
                    observer.stop()
                    observer.join(timeout=_OBSERVER_JOIN_TIMEOUT_SECONDS)
                except Exception:
                    pass
            self._dispatcher.stop(flush=False)
            log_event(
                logger,
                logging.ERROR,
                "semantic_watcher_failed",
                "Semantic filesystem watcher failed to start",
                exc_info=(type(exc), exc, exc.__traceback__),
                error_type=type(exc).__name__,
            )
            raise
        log_event(
            logger,
            logging.INFO,
            "semantic_watcher_started",
            "Semantic filesystem watcher started",
        )

    def stop(self) -> None:
        observer = self._observer
        if observer is None:
            return
        self._observer = None
        error: BaseException | None = None
        try:
            observer.stop()
            observer.join(timeout=_OBSERVER_JOIN_TIMEOUT_SECONDS)
            if observer.is_alive():
                error = RuntimeError("Semantic filesystem observer did not stop")
        except BaseException as exc:
            error = exc
        try:
            self._dispatcher.stop(flush=True)
        except BaseException as exc:
            error = error or exc
        if error is not None:
            log_event(
                logger,
                logging.ERROR,
                "semantic_watcher_failed",
                "Semantic filesystem watcher failed to stop cleanly",
                error_type=type(error).__name__,
            )
            raise error
        log_event(
            logger,
            logging.INFO,
            "semantic_watcher_stopped",
            "Semantic filesystem watcher stopped",
        )

    def _dispatch_paths(self, paths: Sequence[str]) -> None:
        try:
            self._semantic_indexer.enqueue_paths(paths)
        except Exception as failure:
            log_event(
                logger,
                logging.ERROR,
                "semantic_watcher_failed",
                "Semantic filesystem watcher could not enqueue targeted work",
                exc_info=(type(failure), failure, failure.__traceback__),
                error_type=type(failure).__name__,
            )
