"""Owner-local synchronization turns. No scheduler or persistent continuation format."""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import threading
import time
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.repositories.semantic_binding import ordinary_identity
from app.services.vault import SEMANTIC_EXCLUDED_DIRECTORIES, _markdown_discovery_name


class SyncStepState(str, Enum):
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SyncStep:
    state: SyncStepState
    indexed: int
    unchanged: int
    removed: int

    def counts(self):
        return {"indexed": self.indexed, "unchanged": self.unchanged, "removed": self.removed}


class SyncContinuation:
    """Opaque identity handle; terminal handles cannot be resumed or reused."""

    __slots__ = ("_state", "_full")

    def __init__(self, *, full=False):
        self._state = SyncStepState.INCOMPLETE
        self._full = full

    @property
    def state(self):
        return self._state

    @property
    def full(self):
        """Actual admitted mode, including owner-required full recovery."""
        return self._full


def directory_identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise OSError("Synchronization directory changed")
    return info.st_dev, info.st_ino


class SyncWork:
    """Disk-backed discovery frontier/seen set; bounded Python payloads per turn.

    One scandir cursor survives discovery turns, with its directory identity
    rechecked before consumption. No SQLite connection/transaction survives a
    turn. The temporary database contains paths only, never note bodies/vectors.
    Restart discards this private scan and rediscovers against durable note rows.
    """

    def __init__(self, service, *, paths: Iterable[str] | None, full: bool):
        self.handle = SyncContinuation(full=full)
        self.started_at = time.perf_counter()
        self.turn_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.configuration = self.configuration_of(service)
        self.repository = service.repository
        self.root = service.vault_root.resolve(strict=True)
        self.root_identity = directory_identity(self.root)
        self.database = service.db_path.resolve(strict=True)
        self.database_identity = ordinary_identity(service.db_path)
        self.generation = service._resource_generation
        self.signature = service.index_signature
        self.full = full
        self.phase = "discover" if full else "targets"
        self.targets = iter(paths) if paths is not None else None
        self.cursor = None
        self.directory = None
        self.directory_identity = None
        self.after = ("", "")
        self.stale_after = ""
        self.counts = {"indexed": 0, "unchanged": 0, "removed": 0}
        self.temporary = tempfile.TemporaryDirectory(prefix="vaultbridge-sync-")
        try:
            with self.spool() as connection:
                connection.executescript(
                    "CREATE TABLE files(path TEXT PRIMARY KEY, sort_key TEXT NOT NULL, "
                    "missing INTEGER NOT NULL);"
                    "CREATE INDEX file_order ON files(sort_key, path);"
                    "CREATE TABLE directories(path TEXT PRIMARY KEY);"
                )
                if full:
                    connection.execute("INSERT INTO directories VALUES('')")
        except BaseException:
            self.temporary.cleanup()
            raise

    @staticmethod
    def configuration_of(service):
        return (str(service.vault_root), service.max_note_bytes, service.model_name,
                service.chunk_chars, service.chunk_overlap, service.index_batch_size,
                service.embedder, service.repository, str(service.db_path),
                service._indexing_enabled, service.repository._expected_binding,
                service._resolved_index_signature, service._sync_configuration_revision)

    def validate(self, service):
        if self.configuration != self.configuration_of(service):
            raise ValueError("Synchronization configuration changed")
        self.validate_storage(service)

    def validate_storage(self, service):
        """Failure reporting may mark ERROR after a same-root config change."""
        service._validate_source_root(service.vault_root)
        if (service.vault_root.resolve(strict=True) != self.root
                or directory_identity(self.root) != self.root_identity):
            raise OSError("Synchronization root changed")
        self.repository.validate_expected_binding(allow_initialization=False)
        if (service.db_path.resolve(strict=True) != self.database
                or ordinary_identity(service.db_path) != self.database_identity):
            raise OSError("Synchronization database changed")
        if self.repository.get_metadata("index_signature") != self.signature:
            raise ValueError("Synchronization index signature changed")

    @contextmanager
    def spool(self):
        connection = sqlite3.connect(Path(self.temporary.name) / "scan.sqlite3")
        try:
            # Keep SQLite's page cache fixed as the frontier grows on disk.
            connection.execute("PRAGMA cache_size=-256")
            with connection:
                yield connection
        finally:
            connection.close()

    def _remember(self, connection, path, *, missing=False):
        connection.execute("INSERT OR IGNORE INTO files VALUES(?, ?, ?)",
                           (path, path.casefold(), int(missing)))

    def discover(self, service, connection, budget, check):
        # Charge directory opens, exhausted cursors and every entry, including
        # rejected/non-Markdown entries. No directory is materialized in memory.
        for _ in range(budget):
            check()
            if self.cursor is None:
                row = connection.execute(
                    "SELECT path FROM directories ORDER BY path LIMIT 1"
                ).fetchone()
                if row is None:
                    self.phase = "process"
                    return
                self.directory = row[0]
                directory = self.root / self.directory
                resolved = directory.resolve(strict=True)
                resolved.relative_to(self.root)
                self.directory_identity = directory_identity(directory)
                self.cursor = os.scandir(directory)
                continue
            directory = self.root / self.directory
            directory.resolve(strict=True).relative_to(self.root)
            if directory_identity(directory) != self.directory_identity:
                raise OSError("Synchronization directory changed")
            try:
                entry = next(self.cursor)
            except StopIteration:
                self.cursor.close()
                self.cursor = None
                connection.execute("DELETE FROM directories WHERE path=?", (self.directory,))
                continue
            if entry.is_dir(follow_symlinks=False):
                connection.execute("INSERT OR IGNORE INTO directories VALUES(?)",
                                   ((Path(self.directory) / entry.name).as_posix(),))
                continue
            if not _markdown_discovery_name(entry.name):
                continue
            try:
                path = Path(entry.path).resolve(strict=True)
                try:
                    relative = path.relative_to(self.root)
                except ValueError:
                    # A positively resolved escape is ineligible, not absence
                    # evidence obtained from a failed filesystem inspection.
                    continue
                if (path.suffix.lower() != ".md"
                        or any(part in SEMANTIC_EXCLUDED_DIRECTORIES for part in relative.parts)):
                    continue
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > service.max_note_bytes:
                    continue
            except FileNotFoundError:
                # A race with genuine deletion is safe only after confirming
                # ordinary absence through every relevant ancestor. All other
                # inspection errors fail this scan before stale reconciliation.
                spelling = Path(entry.path).relative_to(self.root).as_posix()
                _, missing = service._target_files(self.root, service.max_note_bytes, (spelling,))
                if not missing:
                    raise OSError("Synchronization entry changed during discovery") from None
                continue
            self._remember(connection, relative.as_posix())

    def admit_targets(self, service, connection, budget, check):
        for _ in range(budget):
            check()
            try:
                raw = next(self.targets)
            except StopIteration:
                self.targets = None
                self.phase = "process"
                return
            files, missing = service._target_files(self.root, service.max_note_bytes, (raw,))
            for path in files:
                self._remember(connection, path.relative_to(self.root).as_posix())
            for path in missing:
                self._remember(connection, path, missing=True)

    def process(self, service, connection, check, cancel_event):
        rows = connection.execute(
            "SELECT path, sort_key, missing FROM files WHERE (sort_key, path) > (?, ?) "
            "ORDER BY sort_key, path LIMIT ?", (*self.after, service.index_batch_size),
        ).fetchall()
        if not rows:
            self.phase = "stale" if self.full else "finish"
            return
        check()
        # Missing targeted paths and replacements share the same active batch.
        # An embedding/read failure rolls back both kinds of mutation together.
        with self.repository.transaction() as session:
            removed = 0
            files = []
            for path, _, missing in rows:
                check()
                if missing:
                    # Admission's missing flag is provisional across yields.
                    # Reappeared/unsafe/ambiguous targets cannot authorize a
                    # deletion; use the same strict targeted path policy again.
                    current, still_missing = service._target_files(
                        self.root, service.max_note_bytes, (path,),
                    )
                    check()
                    if still_missing:
                        if session.get_note(path) is not None:
                            session.delete_note(path)
                            removed += 1
                    else:
                        files.extend(current)
                else:
                    files.append(self.root / path)
            indexed, unchanged = service._index_files(
                files=files, vault_root=self.root, known=None, cancel_event=cancel_event,
                strict_reads=not self.full, _session=session,
            )
        self.counts["indexed"] += indexed
        self.counts["unchanged"] += unchanged
        self.counts["removed"] += removed
        self.after = rows[-1][1], rows[-1][0]

    def reconcile(self, service, connection, check):
        check()
        with self.repository.transaction() as session:
            paths = session.note_paths_after(self.stale_after, service.index_batch_size)
            removed = 0
            for path in paths:
                check()
                if connection.execute("SELECT 1 FROM files WHERE path=?", (path,)).fetchone() is None:
                    session.delete_note(path)
                    removed += 1
        self.counts["removed"] += removed
        if paths:
            self.stale_after = paths[-1]
        else:
            self.phase = "finish"

    def close(self):
        if self.cursor is not None:
            self.cursor.close()
            self.cursor = None
        self.targets = None
        self.temporary.cleanup()

    def result(self):
        return SyncStep(self.handle.state, **self.counts)
