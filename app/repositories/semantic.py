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

    def _connect(self, index_signature: str | None = None) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
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
        if index_signature is not None:
            current = connection.execute(
                "SELECT value FROM meta WHERE key='index_signature'"
            ).fetchone()
            if current and current["value"] != index_signature:
                connection.execute("DELETE FROM chunks")
                connection.execute("DELETE FROM notes")
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('index_signature', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (index_signature,),
            )
            connection.commit()
        return connection

    @contextmanager
    def transaction(self, index_signature: str) -> Iterator[SemanticRepositorySession]:
        connection = self._connect(index_signature)
        try:
            yield SemanticRepositorySession(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_chunks(self, index_signature: str) -> list[StoredChunk]:
        connection = self._connect(index_signature)
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

    def is_initialized(self) -> bool:
        try:
            if not self.db_path.exists():
                return False
            connection = sqlite3.connect(self.db_path, timeout=3)
            try:
                row = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()
                return bool(row and row[0] > 0)
            finally:
                connection.close()
        except sqlite3.Error:
            return False
