from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import get_ident

from app.repositories.semantic_binding import (
    BINDING_KEYS,
    ExpectedIndexBinding,
    IndexBindingUnavailableError,
    ordinary_identity,
    validate_binding,
)


@dataclass(frozen=True)
class StoredNote:
    path: str
    mtime_ns: int
    size: int
    sha256: str
    indexed_at: str


@dataclass(frozen=True)
class StoredChunk:
    path: str
    chunk_index: int
    heading: str | None
    content: str
    embedding: bytes
    dimensions: int


@dataclass(frozen=True)
class IndexStorageInfo:
    signature_changed: bool
    has_chunks: bool
    index_state: str | None


@dataclass(frozen=True)
class SemanticIndexStatus:
    storage_exists: bool | None
    storage_initialized: bool
    storage_error: bool
    index_signature: str | None
    index_state: str | None
    indexed_notes: int
    semantic_chunks: int
    last_successful_sync: str | None
    error_kind: str | None = None


@dataclass(frozen=True)
class SemanticAvailabilityStatus:
    storage_initialized: bool
    storage_error: bool
    index_signature: str | None
    index_state: str | None
    has_chunks: bool


class ImmutableIndexInspectionUnavailableError(RuntimeError):
    """Persisted index cannot be inspected immutably while SQLite sidecars exist."""


class SemanticRepositorySession:
    """Semantic index operations sharing one SQLite transaction."""

    def __init__(self, connection: sqlite3.Connection, *, _repository=None) -> None:
        self._connection = connection
        self._repository = _repository
        self._active = True
        self._thread = get_ident()
        self._database_identity = (
            _repository._binding_storage_identity(immutable=False) if _repository is not None else None
        )

    def load_notes(self) -> dict[str, StoredNote]:
        rows = self._connection.execute(
            "SELECT path, mtime_ns, size, sha256, indexed_at FROM notes"
        ).fetchall()
        return {
            row["path"]: StoredNote(
                path=row["path"],
                mtime_ns=row["mtime_ns"],
                size=row["size"],
                sha256=row["sha256"],
                indexed_at=row["indexed_at"],
            )
            for row in rows
        }

    def delete_note(self, path: str) -> None:
        self._connection.execute("DELETE FROM notes WHERE path=?", (path,))

    def get_note(self, path: str) -> StoredNote | None:
        """One primary-key metadata lookup, without a whole-index map."""
        row = self._connection.execute(
            "SELECT path, mtime_ns, size, sha256, indexed_at FROM notes WHERE path=?", (path,),
        ).fetchone()
        return StoredNote(**dict(row)) if row is not None else None

    def note_paths_after(self, after: str, limit: int) -> list[str]:
        """Bounded primary-key cursor for completed-discovery reconciliation."""
        return [row[0] for row in self._connection.execute(
            "SELECT path FROM notes WHERE path > ? ORDER BY path LIMIT ?", (after, limit),
        ).fetchall()]

    def load_notes_and_remove_stale(
        self,
        current_paths: set[str],
    ) -> tuple[dict[str, StoredNote], int]:
        notes = self.load_notes()
        stale_paths = set(notes) - current_paths
        for path in stale_paths:
            self.delete_note(path)
        return notes, len(stale_paths)

    def update_note_metadata(self, *, path: str, mtime_ns: int, size: int) -> None:
        self._connection.execute(
            "UPDATE notes SET mtime_ns=?, size=? WHERE path=?",
            (mtime_ns, size, path),
        )

    def replace_note(self, note: StoredNote, chunks: Sequence[StoredChunk]) -> None:
        self._connection.execute("DELETE FROM chunks WHERE path=?", (note.path,))
        self._connection.execute(
            "INSERT INTO notes(path, mtime_ns, size, sha256, indexed_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, size=excluded.size, "
            "sha256=excluded.sha256, indexed_at=excluded.indexed_at",
            (note.path, note.mtime_ns, note.size, note.sha256, note.indexed_at),
        )
        self._connection.executemany(
            "INSERT INTO chunks(path, chunk_index, heading, content, embedding, dimensions) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            [
                (
                    chunk.path,
                    chunk.chunk_index,
                    chunk.heading,
                    chunk.content,
                    chunk.embedding,
                    chunk.dimensions,
                )
                for chunk in chunks
            ],
        )


_SCHEMA = """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    heading TEXT,
                    content TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    dimensions INTEGER NOT NULL,
                    FOREIGN KEY(path) REFERENCES notes(path) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
                """


class SemanticRepository:
    """Own SQLite schema management and semantic index persistence."""

    def __init__(self, db_path: Path, *, _expected_binding: ExpectedIndexBinding | None = None) -> None:
        self.db_path = db_path if _expected_binding is None else db_path.absolute()
        self.__expected_binding = _expected_binding
        self._bound_directories = ()
        self._bound_database_identity = None
        self._initializations = {}
        self._sessions = {}
        if _expected_binding is not None:
            _expected_binding.check_root()
            directories = []
            for parent in reversed(self.db_path.parents):
                try:
                    identity = ordinary_identity(parent, directory=True)
                except FileNotFoundError:
                    identity = None
                directories.append((parent, identity))
            self._bound_directories = tuple(directories)
            try:
                self._bound_database_identity = ordinary_identity(self.db_path)
            except FileNotFoundError:
                pass

    @property
    def _expected_binding(self):
        return self.__expected_binding

    def _binding_storage_identity(self, *, immutable=True):
        expected = self._expected_binding
        if expected is None:
            return None
        expected.check_root()
        for parent, previous in self._bound_directories:
            try:
                current = ordinary_identity(parent, directory=True)
            except FileNotFoundError:
                if previous is not None:
                    raise IndexBindingUnavailableError from None
                return None
            if previous is not None and current != previous:
                raise IndexBindingUnavailableError
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                ordinary_identity(Path(f"{self.db_path}{suffix}"))
            except FileNotFoundError:
                continue
            # Offline inspection must never ignore potentially newer binding
            # metadata in a journal/WAL, or change SQLite coordination files.
            if immutable:
                raise IndexBindingUnavailableError
        try:
            current = ordinary_identity(self.db_path)
        except FileNotFoundError:
            if self._bound_database_identity is not None:
                raise IndexBindingUnavailableError from None
            return None
        if self._bound_database_identity is not None and current != self._bound_database_identity:
            raise IndexBindingUnavailableError
        return current

    def validate_expected_binding(self, *, allow_initialization=True) -> None:
        """Non-destructive preflight, before any model or writable connection.

        Bound lifecycle preflight requires stopped storage (no sidecars). D1's
        live reader validates binding in its own WAL-aware bounded transaction.
        """
        if self._expected_binding is None:
            return
        try:
            before = self._binding_storage_identity()
            if before is None:
                if allow_initialization:
                    return
                raise IndexBindingUnavailableError
            connection = sqlite3.connect(
                f"{self.db_path.as_uri()}?mode=ro&immutable=1", uri=True, timeout=0,
            )
            try:
                connection.execute("BEGIN")
                validate_binding(
                    lambda sql, args=(): connection.execute(sql, args).fetchone(),
                    self._expected_binding, allow_empty=allow_initialization,
                )
                if self._binding_storage_identity() != before:
                    raise IndexBindingUnavailableError
            finally:
                connection.close()
        except (OSError, sqlite3.DatabaseError, IndexBindingUnavailableError):
            raise IndexBindingUnavailableError from None

    def _connect_bound(self) -> sqlite3.Connection:
        self.validate_expected_binding()
        before = self._binding_storage_identity()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._binding_storage_identity()
        self._bound_directories = tuple(
            (parent, ordinary_identity(parent, directory=True))
            for parent, _ in self._bound_directories
        )
        created = before is None
        attempt = None
        connection = None
        if created:
            # Exclusive creation prevents adopting a file arriving after preflight.
            descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            info = os.fstat(descriptor)
            before = info.st_dev, info.st_ino
            attempt = self.db_path, descriptor, info
            self._bound_database_identity = before
        try:
            connection = sqlite3.connect(f"{self.db_path.as_uri()}?mode=rw", uri=True, timeout=30)
            if attempt is not None:
                self._initializations[connection] = attempt
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            # Acquire the writer transaction BEFORE revalidation/schema/key writes.
            connection.execute("BEGIN IMMEDIATE")
            if self._binding_storage_identity(immutable=False) != before:
                raise IndexBindingUnavailableError
            if created:
                if connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                    raise IndexBindingUnavailableError
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            initialize = validate_binding(
                lambda sql, args=(): connection.execute(sql, args).fetchone(),
                self._expected_binding, allow_empty=True,
            )
            if initialize:
                connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)",
                                       zip(BINDING_KEYS, self._expected_binding.values))
            self._bound_database_identity = before
            return connection
        except Exception:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    self._close_connection(connection)
            elif attempt is not None:
                self._cleanup_initialization(attempt)
            raise

    def _cleanup_initialization(self, attempt):
        """After SQLite closes, remove only our still-empty exclusively created file.

        Retain storage if rollback leaves any data/sidecars or an identity/owner
        cannot be proven. Never delete sidecars or a peer's replacement. This
        retains the cooperating-filesystem contract, not cross-process locking.
        """
        path, descriptor, created = attempt
        identity = created.st_dev, created.st_ino
        try:
            if self.db_path != path or self._binding_storage_identity() != identity:
                return
            held = os.fstat(descriptor)
            current = path.lstat()
            if (
                (held.st_dev, held.st_ino) != identity
                or (current.st_dev, current.st_ino) != identity
                or held.st_uid != created.st_uid or current.st_uid != created.st_uid
                or held.st_nlink != 1 or current.st_nlink != 1
                or held.st_size != 0 or current.st_size != 0
            ):
                return
            # Windows requires closing our creation descriptor before unlink.
            os.close(descriptor)
            descriptor = None
            if self._binding_storage_identity() != identity:
                return
            current = path.lstat()
            if current.st_size or current.st_uid != created.st_uid or current.st_nlink != 1:
                return
            path.unlink()
            if self._bound_database_identity == identity:
                self._bound_database_identity = None
        except (OSError, IndexBindingUnavailableError):
            # Uncertain cleanup must leave storage in place and fail closed on
            # subsequent lifecycle validation; preserve the original failure.
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _close_connection(self, connection):
        attempt = self._initializations.pop(connection, None)
        closed = False
        try:
            connection.close()
            closed = True
        finally:
            if attempt is not None:
                if closed:
                    self._cleanup_initialization(attempt)
                else:
                    os.close(attempt[1])

    def _validate_transaction_binding(self, session):
        if self._expected_binding is None:
            return
        if (
            type(session) is not SemanticRepositorySession
            or self._sessions.get(session) is not session._connection
            or session._repository is not self
            or not session._active or session._thread != get_ident()
            or not session._connection.in_transaction
            or self._binding_storage_identity(immutable=False) != session._database_identity
        ):
            raise IndexBindingUnavailableError
        validate_binding(
            lambda sql, args=(): session._connection.execute(sql, args).fetchone(),
            self._expected_binding,
        )

    def _connect(self) -> sqlite3.Connection:
        if self._expected_binding is not None:
            return self._connect_bound()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(_SCHEMA)
        except Exception:
            connection.close()
            raise
        return connection

    def _commit(self, connection):
        if self._expected_binding is not None:
            self._binding_storage_identity(immutable=False)
            validate_binding(
                lambda sql, args=(): connection.execute(sql, args).fetchone(),
                self._expected_binding,
            )
        connection.commit()
        attempt = self._initializations.pop(connection, None)
        if attempt is not None:
            os.close(attempt[1])

    @staticmethod
    def _write_metadata(
        connection: sqlite3.Connection,
        key: str,
        value: str,
    ) -> None:
        connection.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    @staticmethod
    def _prepare_connection(
        connection: sqlite3.Connection,
        index_signature: str,
    ) -> IndexStorageInfo:
        current = connection.execute(
            "SELECT value FROM meta WHERE key='index_signature'"
        ).fetchone()
        has_derived_rows = bool(
            connection.execute(
                "SELECT EXISTS(SELECT 1 FROM notes) OR EXISTS(SELECT 1 FROM chunks)"
            ).fetchone()[0]
        )
        signature_changed = bool(
            (current and current["value"] != index_signature)
            or (current is None and has_derived_rows)
        )
        if signature_changed:
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM notes")
            connection.execute(
                "DELETE FROM meta WHERE key IN ('index_state', 'last_successful_sync')"
            )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('index_signature', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (index_signature,),
        )
        chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()
        state = connection.execute(
            "SELECT value FROM meta WHERE key='index_state'"
        ).fetchone()
        return IndexStorageInfo(
            signature_changed=signature_changed,
            has_chunks=bool(chunks and chunks[0] > 0),
            index_state=state["value"] if state else None,
        )

    def prepare_index(self, index_signature: str) -> IndexStorageInfo:
        connection = self._connect()
        try:
            info = self._prepare_connection(connection, index_signature)
            self._commit(connection)
            return info
        finally:
            self._close_connection(connection)

    def _reset_index_storage(self, index_signature: str, index_state: str) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM notes")
            connection.execute(
                "DELETE FROM meta WHERE key IN ('index_signature', 'index_state')"
            )
            connection.executemany(
                "INSERT INTO meta(key, value) VALUES(?, ?)",
                (
                    ("index_signature", index_signature),
                    ("index_state", index_state),
                ),
            )
            self._commit(connection)
        except Exception:
            connection.rollback()
            raise
        finally:
            self._close_connection(connection)

    def reset_index(self, index_signature: str, index_state: str) -> None:
        """Discard derived semantic data and reset lifecycle metadata atomically."""
        try:
            self._reset_index_storage(index_signature, index_state)
        except sqlite3.ProgrammingError:
            raise
        except sqlite3.DatabaseError as exc:
            if self._expected_binding is not None:
                raise IndexBindingUnavailableError from None
            corrupt_codes = {
                getattr(sqlite3, "SQLITE_CORRUPT", 11),
                getattr(sqlite3, "SQLITE_NOTADB", 26),
            }
            if getattr(exc, "sqlite_errorcode", None) not in corrupt_codes:
                raise
            for storage_path in (
                self.db_path,
                Path(f"{self.db_path}-wal"),
                Path(f"{self.db_path}-shm"),
            ):
                storage_path.unlink(missing_ok=True)
            self._reset_index_storage(index_signature, index_state)

    @contextmanager
    def transaction(self) -> Iterator[SemanticRepositorySession]:
        connection = self._connect()
        session = None
        try:
            session = SemanticRepositorySession(connection, _repository=self)
            self._sessions[session] = connection
            yield session
            self._commit(connection)
        except Exception:
            connection.rollback()
            raise
        finally:
            if session is not None:
                session._active = False
                self._sessions.pop(session, None)
            self._close_connection(connection)

    def load_chunks(self) -> list[StoredChunk]:
        if self._expected_binding is not None:
            return self.load_chunks_read_only()
        connection = self._connect()
        try:
            return self._load_chunks_connection(connection)
        finally:
            connection.close()

    def read_eligible_snapshot(self, eligible_paths, *, budget, space_id, cancel):
        """Internal bounded live reader; no preparation, scoring or binding writes.

        Paths must already be authorized/canonicalized by the vault owner. Returns
        only a complete snapshot; quota exhaustion never exposes a partial prefix.
        """
        from app.repositories.semantic_reads import read_eligible_snapshot

        return read_eligible_snapshot(
            self.db_path, eligible_paths, budget=budget, space_id=space_id, cancel=cancel,
            expected_binding=self._expected_binding, storage_guard=self._bounded_binding_guard,
        )

    def _bounded_binding_guard(self, *, budget, space_id, cancel):
        if self._expected_binding is None:
            return
        def probe():
            cancel.check()
            budget.charge(space_id, "probes")
        self._expected_binding.check_root(probe=probe)
        for parent, previous in self._bound_directories:
            probe()
            current = ordinary_identity(parent, directory=True)
            if previous is not None and previous != current:
                raise IndexBindingUnavailableError
        probe()
        current = ordinary_identity(self.db_path)
        if self._bound_database_identity is not None and current != self._bound_database_identity:
            raise IndexBindingUnavailableError
        for suffix in ("-wal", "-shm", "-journal"):
            probe()
            try:
                ordinary_identity(Path(f"{self.db_path}{suffix}"))
            except FileNotFoundError:
                pass

    @staticmethod
    def _load_chunks_connection(connection: sqlite3.Connection) -> list[StoredChunk]:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT path, chunk_index, heading, content, embedding, dimensions FROM chunks"
        ).fetchall()
        return [
            StoredChunk(
                path=row["path"],
                chunk_index=row["chunk_index"],
                heading=row["heading"],
                content=row["content"],
                embedding=row["embedding"],
                dimensions=row["dimensions"],
            )
            for row in rows
        ]

    @contextmanager
    def _bound_read_connection(self, *, allow_empty=False, immutable=False):
        """Keep authorization and payloads in one normally locked live snapshot.

        Immutable screening only rejects stopped storage before SQLite can
        create coordination files. It never authorizes a live result.
        """
        connection = None
        try:
            before = self._binding_storage_identity(immutable=immutable)
            if before is None:
                raise IndexBindingUnavailableError
            sidecars = {}
            for suffix in ("-wal", "-shm", "-journal"):
                path = Path(f"{self.db_path}{suffix}")
                try:
                    sidecars[path] = ordinary_identity(path)
                except FileNotFoundError:
                    pass
            if not sidecars:
                self.validate_expected_binding(allow_initialization=allow_empty)
            uri = f"{self.db_path.as_uri()}?mode=ro"
            if immutable:
                uri += "&immutable=1"
            connection = sqlite3.connect(uri, uri=True, timeout=0)
            connection.execute("BEGIN")
            validate_binding(
                lambda sql, args=(): connection.execute(sql, args).fetchone(),
                self._expected_binding, allow_empty=allow_empty,
            )
            yield connection
            if self._binding_storage_identity(immutable=immutable) != before:
                raise IndexBindingUnavailableError
            for path, identity in sidecars.items():
                if ordinary_identity(path) != identity:
                    raise IndexBindingUnavailableError
        except (OSError, sqlite3.DatabaseError, IndexBindingUnavailableError):
            raise IndexBindingUnavailableError from None
        finally:
            if connection is not None:
                connection.close()

    def load_chunks_read_only(self) -> list[StoredChunk]:
        """Read bound live chunks; preserve legacy immutable offline inspection."""
        if self._expected_binding is not None:
            with self._bound_read_connection() as connection:
                return self._load_chunks_connection(connection)
        sidecars = (Path(f"{self.db_path}-wal"), Path(f"{self.db_path}-shm"))
        if any(path.exists() for path in sidecars):
            raise ImmutableIndexInspectionUnavailableError
        target = self.db_path.resolve()
        database_uri = f"{target.as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(database_uri, uri=True, timeout=1)
        try:
            chunks = self._load_chunks_connection(connection)
        finally:
            connection.close()
        if any(path.exists() for path in sidecars):
            raise ImmutableIndexInspectionUnavailableError
        return chunks

    def get_metadata(self, key: str) -> str | None:
        if self._expected_binding is not None:
            with self._bound_read_connection() as connection:
                row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                return row[0] if row else None
        connection = self._connect()
        try:
            row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            connection.close()

    def set_metadata(self, key: str, value: str) -> None:
        if self._expected_binding is not None and key in BINDING_KEYS:
            raise IndexBindingUnavailableError
        connection = self._connect()
        try:
            self._write_metadata(connection, key, value)
            self._commit(connection)
        finally:
            self._close_connection(connection)

    def finalize_full_sync_success(
        self,
        *,
        last_successful_sync: str,
        ready_state: str,
    ) -> None:
        """Commit full-sync timestamp and ready lifecycle state atomically."""
        connection = self._connect()
        try:
            self._write_metadata(connection, "last_successful_sync", last_successful_sync)
            self._write_metadata(connection, "index_state", ready_state)
            self._commit(connection)
        except Exception:
            connection.rollback()
            raise
        finally:
            self._close_connection(connection)

    def storage_initialized(self) -> bool:
        if self._expected_binding is not None:
            return self.read_status().storage_initialized
        try:
            if not self.db_path.exists():
                return False
            connection = sqlite3.connect(self.db_path, timeout=3)
            try:
                rows = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name IN ('meta', 'notes', 'chunks')"
                ).fetchall()
                return {row[0] for row in rows} == {"meta", "notes", "chunks"}
            finally:
                connection.close()
        except sqlite3.Error:
            return False

    @staticmethod
    def _missing_status() -> SemanticIndexStatus:
        return SemanticIndexStatus(
            storage_exists=False,
            storage_initialized=False,
            storage_error=False,
            index_signature=None,
            index_state=None,
            indexed_notes=0,
            semantic_chunks=0,
            last_successful_sync=None,
        )

    @staticmethod
    def _storage_error_status(
        *, storage_exists: bool | None, error_kind: str | None = None,
    ) -> SemanticIndexStatus:
        return SemanticIndexStatus(
            storage_exists=storage_exists,
            storage_initialized=False,
            storage_error=True,
            index_signature=None,
            index_state=None,
            indexed_notes=0,
            semantic_chunks=0,
            last_successful_sync=None,
            error_kind=error_kind,
        )

    def _read_status_connection(self, connection: sqlite3.Connection) -> SemanticIndexStatus:
        connection.row_factory = sqlite3.Row
        if not connection.in_transaction:
            connection.execute("BEGIN")
        if self._expected_binding is not None:
            validate_binding(
                lambda sql, args=(): connection.execute(sql, args).fetchone(),
                self._expected_binding, allow_empty=True,
            )
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name IN ('meta', 'notes', 'chunks')"
        ).fetchall()
        if {row["name"] for row in rows} != {"meta", "notes", "chunks"}:
            return SemanticIndexStatus(
                storage_exists=True,
                storage_initialized=False,
                storage_error=False,
                index_signature=None,
                index_state=None,
                indexed_notes=0,
                semantic_chunks=0,
                last_successful_sync=None,
            )

        metadata = {
            row["key"]: row["value"]
            for row in connection.execute(
                "SELECT key, value FROM meta "
                "WHERE key IN ('index_signature', 'index_state', 'last_successful_sync')"
            ).fetchall()
        }
        indexed_notes = connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        semantic_chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return SemanticIndexStatus(
            storage_exists=True,
            storage_initialized=True,
            storage_error=False,
            index_signature=metadata.get("index_signature"),
            index_state=metadata.get("index_state"),
            indexed_notes=indexed_notes,
            semantic_chunks=semantic_chunks,
            last_successful_sync=metadata.get("last_successful_sync"),
        )

    def _read_status_uri(self, database_uri: str) -> SemanticIndexStatus:
        if self._expected_binding is not None:
            with self._bound_read_connection(
                allow_empty=True, immutable="immutable=1" in database_uri,
            ) as connection:
                return self._read_status_connection(connection)
        connection = sqlite3.connect(database_uri, uri=True, timeout=1)
        try:
            status = self._read_status_connection(connection)
            return status
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()

    def read_status(self) -> SemanticIndexStatus:
        """Read live-compatible index metadata and counts without changing lifecycle state."""
        if self._expected_binding is not None:
            try:
                if self._binding_storage_identity(immutable=False) is None:
                    return self._missing_status()
                return self._read_status_uri(f"{self.db_path.as_uri()}?mode=ro")
            except sqlite3.ProgrammingError:
                raise
            except (OSError, sqlite3.DatabaseError, IndexBindingUnavailableError):
                return self._storage_error_status(storage_exists=True, error_kind="storage_unavailable")
        try:
            storage_exists = self.db_path.exists()
        except OSError:
            return self._storage_error_status(storage_exists=None)
        if not storage_exists:
            return self._missing_status()

        try:
            database_uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
            return self._read_status_uri(database_uri)
        except sqlite3.ProgrammingError:
            raise
        except (OSError, sqlite3.DatabaseError, IndexBindingUnavailableError):
            return self._storage_error_status(storage_exists=True)

    def read_immutable_status(self) -> SemanticIndexStatus:
        """Inspect stopped/offline SQLite storage without creating or changing sidecars."""
        return self._read_immutable_status()

    def _read_immutable_status(self, *, validated_path: Path | None = None) -> SemanticIndexStatus:
        """Named inspection may supply its already validated canonical DB target.

        Do not resolve that target again. This does not pin descriptors against
        concurrent hostile mutation; ordinary callers keep their existing path.
        """
        try:
            if self._expected_binding is not None:
                if self._binding_storage_identity() is None:
                    return self._missing_status()
                return self._read_status_uri(f"{self.db_path.as_uri()}?mode=ro&immutable=1")
            sidecars = (Path(f"{self.db_path}-wal"), Path(f"{self.db_path}-shm"))
            if any(path.exists() for path in sidecars):
                raise ImmutableIndexInspectionUnavailableError
            if not self.db_path.exists():
                return self._missing_status()
            target = self.db_path.resolve() if validated_path is None else validated_path
            database_uri = f"{target.as_uri()}?mode=ro&immutable=1"
            status = self._read_status_uri(database_uri)
            if any(path.exists() for path in sidecars):
                raise ImmutableIndexInspectionUnavailableError
            return status
        except IndexBindingUnavailableError:
            return self._storage_error_status(storage_exists=True, error_kind="storage_unavailable")
        except ImmutableIndexInspectionUnavailableError:
            raise
        except sqlite3.ProgrammingError:
            raise
        except OSError:
            return self._storage_error_status(
                storage_exists=True, error_kind="storage_unavailable",
            )
        except sqlite3.OperationalError:
            return self._storage_error_status(
                storage_exists=True, error_kind="storage_unavailable",
            )
        except sqlite3.DatabaseError:
            return self._storage_error_status(
                storage_exists=True, error_kind="corrupt_storage",
            )

    def read_availability_status(self) -> SemanticAvailabilityStatus:
        """Read only the storage metadata needed to decide search availability."""
        if self._expected_binding is not None:
            storage = self.read_status()
            return SemanticAvailabilityStatus(
                storage.storage_initialized, storage.storage_error,
                storage.index_signature, storage.index_state, bool(storage.semantic_chunks),
            )
        try:
            if not self.db_path.exists():
                return SemanticAvailabilityStatus(
                    storage_initialized=False,
                    storage_error=False,
                    index_signature=None,
                    index_state=None,
                    has_chunks=False,
                )

            database_uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(database_uri, uri=True, timeout=1)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("BEGIN")
                rows = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name IN ('meta', 'notes', 'chunks')"
                ).fetchall()
                if {row["name"] for row in rows} != {"meta", "notes", "chunks"}:
                    return SemanticAvailabilityStatus(
                        storage_initialized=False,
                        storage_error=False,
                        index_signature=None,
                        index_state=None,
                        has_chunks=False,
                    )

                metadata = {
                    row["key"]: row["value"]
                    for row in connection.execute(
                        "SELECT key, value FROM meta "
                        "WHERE key IN ('index_signature', 'index_state')"
                    ).fetchall()
                }
                has_chunks = bool(
                    connection.execute("SELECT EXISTS(SELECT 1 FROM chunks)").fetchone()[0]
                )
                return SemanticAvailabilityStatus(
                    storage_initialized=True,
                    storage_error=False,
                    index_signature=metadata.get("index_signature"),
                    index_state=metadata.get("index_state"),
                    has_chunks=has_chunks,
                )
            finally:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()
        except sqlite3.ProgrammingError:
            raise
        except (OSError, sqlite3.DatabaseError, IndexBindingUnavailableError):
            return SemanticAvailabilityStatus(
                storage_initialized=False,
                storage_error=True,
                index_signature=None,
                index_state=None,
                has_chunks=False,
            )
