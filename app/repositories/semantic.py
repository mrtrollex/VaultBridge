from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


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

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

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


class SemanticRepository:
    """Own SQLite schema management and semantic index persistence."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
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
            )
        except Exception:
            connection.close()
            raise
        return connection

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
        signature_changed = bool(current and current["value"] != index_signature)
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
            connection.commit()
            return info
        finally:
            connection.close()

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
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reset_index(self, index_signature: str, index_state: str) -> None:
        """Discard derived semantic data and reset lifecycle metadata atomically."""
        try:
            self._reset_index_storage(index_signature, index_state)
        except sqlite3.ProgrammingError:
            raise
        except sqlite3.DatabaseError as exc:
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
        try:
            yield SemanticRepositorySession(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_chunks(self) -> list[StoredChunk]:
        connection = self._connect()
        try:
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
        finally:
            connection.close()

    def get_metadata(self, key: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            connection.close()

    def set_metadata(self, key: str, value: str) -> None:
        connection = self._connect()
        try:
            self._write_metadata(connection, key, value)
            connection.commit()
        finally:
            connection.close()

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
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def storage_initialized(self) -> bool:
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
    def _storage_error_status(*, storage_exists: bool | None) -> SemanticIndexStatus:
        return SemanticIndexStatus(
            storage_exists=storage_exists,
            storage_initialized=False,
            storage_error=True,
            index_signature=None,
            index_state=None,
            indexed_notes=0,
            semantic_chunks=0,
            last_successful_sync=None,
        )

    @staticmethod
    def _read_status_connection(connection: sqlite3.Connection) -> SemanticIndexStatus:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
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
        connection = sqlite3.connect(database_uri, uri=True, timeout=1)
        try:
            return self._read_status_connection(connection)
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()

    def read_status(self) -> SemanticIndexStatus:
        """Read live-compatible index metadata and counts without changing lifecycle state."""
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
        except (OSError, sqlite3.DatabaseError):
            return self._storage_error_status(storage_exists=True)

    def read_immutable_status(self) -> SemanticIndexStatus:
        """Inspect stopped/offline SQLite storage without creating or changing sidecars."""
        try:
            if not self.db_path.exists():
                return self._missing_status()
            sidecars = (Path(f"{self.db_path}-wal"), Path(f"{self.db_path}-shm"))
            if any(path.exists() for path in sidecars):
                raise ImmutableIndexInspectionUnavailableError
            database_uri = f"{self.db_path.resolve().as_uri()}?mode=ro&immutable=1"
            status = self._read_status_uri(database_uri)
            if any(path.exists() for path in sidecars):
                raise ImmutableIndexInspectionUnavailableError
            return status
        except ImmutableIndexInspectionUnavailableError:
            raise
        except sqlite3.ProgrammingError:
            raise
        except (OSError, sqlite3.DatabaseError):
            return self._storage_error_status(storage_exists=True)

    def read_availability_status(self) -> SemanticAvailabilityStatus:
        """Read only the storage metadata needed to decide search availability."""
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
        except (OSError, sqlite3.DatabaseError):
            return SemanticAvailabilityStatus(
                storage_initialized=False,
                storage_error=True,
                index_signature=None,
                index_state=None,
                has_chunks=False,
            )
