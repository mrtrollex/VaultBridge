from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, get_ident

from app.core.resource_cleanup import DescriptorDisposal, DescriptorState, SQLiteConnections
from app.repositories.semantic_binding import (
    BINDING_KEYS,
    ExpectedIndexBinding,
    IndexBindingUnavailableError,
    StorageDirectoryIdentity,
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
        self._check_runtime_storage()
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
        self._check_runtime_storage()
        self._connection.execute(
            "UPDATE notes SET mtime_ns=?, size=? WHERE path=?",
            (mtime_ns, size, path),
        )

    def replace_note(self, note: StoredNote, chunks: Sequence[StoredChunk]) -> None:
        self._check_runtime_storage()
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

    def _check_runtime_storage(self):
        if self._repository is not None and self._repository._storage_directory is not None:
            self._repository._validate_transaction_binding(self)


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


class RepositoryCleanupIncompleteError(RuntimeError):
    def __init__(self):
        super().__init__("Semantic repository cleanup is incomplete")


@dataclass(eq=False)
class _InitializationOwnership(DescriptorState):
    connection: sqlite3.Connection | None = None
    connection_closed: bool = False
    connection_failed: bool = False
    committed: bool = False


class SemanticRepository(DescriptorDisposal):
    """Own SQLite schema management and semantic index persistence."""

    _cleanup_error = RepositoryCleanupIncompleteError

    def __init__(self, db_path: Path, *, _expected_binding: ExpectedIndexBinding | None = None,
                 _storage_directory: StorageDirectoryIdentity | None = None,
                 _default_binding: ExpectedIndexBinding | None = None, _reader_connections=None,
                 _inspection_binding: ExpectedIndexBinding | None = None) -> None:
        if _default_binding is not None and (
            _default_binding.space_id != "default" or _expected_binding is not None or _storage_directory is None
        ):
            raise ValueError("invalid default namespace expectation")
        if _inspection_binding is not None and (
            _inspection_binding.space_id != "default" or _expected_binding is not None
            or _default_binding is not None or _storage_directory is not None
        ):
            raise ValueError("invalid immutable namespace expectation")
        self.__inspection_binding = _inspection_binding
        self.__default_binding = _default_binding
        self._storage_directory = _storage_directory
        self._storage_protected = _expected_binding is not None or _storage_directory is not None
        self.db_path = db_path if _expected_binding is None else db_path.absolute()
        self.__expected_binding = _expected_binding
        self._bound_directories = ()
        self._bound_database_identity = None
        self._initializations = {}
        self._initialization_lock = Lock()
        self._reader_connections = _reader_connections if _reader_connections is not None else SQLiteConnections()
        self._sessions = {}
        if self._storage_protected:
            if self._namespace_binding is not None:
                self._namespace_binding.check_root()
            if _storage_directory is not None:
                if self.db_path.absolute().parent != _storage_directory.path:
                    raise IndexBindingUnavailableError
                _storage_directory.check()
            directories = []
            for parent in reversed(self.db_path.absolute().parents):
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

    @property
    def _namespace_binding(self):
        return self._expected_binding or self.__default_binding or self.__inspection_binding

    def _validate_namespace(self, one, *, allow_empty=False):
        if self._namespace_binding is None:
            return False
        return validate_binding(
            one, self._namespace_binding, allow_empty=allow_empty,
            allow_legacy=(self.__default_binding is not None or self.__inspection_binding is not None),
        )

    def _binding_storage_identity(self, *, immutable=True):
        expected = self._namespace_binding
        if not self._storage_protected:
            return None
        if expected is not None:
            expected.check_root()
        if self._storage_directory is not None:
            if self.db_path.absolute().parent != self._storage_directory.path:
                raise IndexBindingUnavailableError
            self._storage_directory.check()
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
        if self._namespace_binding is None:
            if self._storage_protected:
                identity = self._binding_storage_identity(immutable=False)
                if identity is None and not allow_initialization:
                    raise IndexBindingUnavailableError
            return
        try:
            legacy = self.__default_binding is not None
            before = self._binding_storage_identity(immutable=not legacy)
            if before is None:
                if allow_initialization:
                    return
                raise IndexBindingUnavailableError
            uri = f"{self.db_path.absolute().as_uri()}?mode=ro"
            if not legacy or not any(Path(f"{self.db_path}{suffix}").exists()
                                     for suffix in ("-wal", "-shm", "-journal")):
                uri += "&immutable=1"
            with self._reader_connections.connection(uri, uri=True, timeout=0) as connection:
                connection.execute("BEGIN")
                self._validate_namespace(
                    lambda sql, args=(): connection.execute(sql, args).fetchone(),
                    allow_empty=allow_initialization,
                )
                if self._binding_storage_identity(immutable=not legacy) != before:
                    raise IndexBindingUnavailableError
        except (OSError, sqlite3.DatabaseError, IndexBindingUnavailableError):
            raise IndexBindingUnavailableError from None

    def _connect_bound(self) -> sqlite3.Connection:
        self._check_cleanup_available()
        self.validate_expected_binding()
        immutable = self._expected_binding is not None
        before = self._binding_storage_identity(immutable=immutable)
        if self._storage_directory is not None:
            self._storage_directory.prepare()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._binding_storage_identity(immutable=immutable)
        self._bound_directories = tuple(
            (parent, ordinary_identity(parent, directory=True))
            for parent, _ in self._bound_directories
        )
        created = before is None
        attempt = _InitializationOwnership(self.db_path)
        connection = None
        try:
            if created:
                # Register before allocation; even interrupted fstat/connect has
                # an exact reachable owner. No registry lock spans SQLite/I/O.
                self._remember_initialization(attempt)
                self._allocate_descriptor(attempt)
                attempt.created = os.fstat(attempt.descriptor)
                before = attempt.created.st_dev, attempt.created.st_ino
                self._bound_database_identity = before
            connection = sqlite3.connect(f"{self.db_path.absolute().as_uri()}?mode=rw", uri=True, timeout=30,
                                         check_same_thread=False)
            attempt.connection = connection
            if created:
                self._remember_initialization(attempt)
            connection.row_factory = sqlite3.Row
            if self._binding_storage_identity(immutable=False) != before:
                raise IndexBindingUnavailableError
            if self._expected_binding is None:
                # Default keeps its schema/WAL behavior and has no binding keys.
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            # Acquire the writer transaction BEFORE revalidation/schema/key writes.
            connection.execute("BEGIN IMMEDIATE")
            if self._binding_storage_identity(immutable=False) != before:
                raise IndexBindingUnavailableError
            if not created and self.__default_binding is not None:
                self._validate_namespace(lambda sql, args=(): connection.execute(sql, args).fetchone())
            if created or self._expected_binding is None:
                if created and connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                    raise IndexBindingUnavailableError
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            initialize = self._validate_namespace(
                lambda sql, args=(): connection.execute(sql, args).fetchone(),
                allow_empty=True,
            )
            if initialize:
                connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)",
                                       zip(BINDING_KEYS, self._expected_binding.values))
            self._bound_database_identity = before
            return connection
        except BaseException:
            if connection is not None:
                attempt.connection = connection
                try:
                    connection.rollback()
                except BaseException:
                    pass
                try:
                    self._dispose_initialization(attempt)
                except BaseException:
                    pass  # The close helper retains any unresolved ownership.
            else:
                try:
                    self._dispose_initialization(attempt)
                except BaseException:
                    pass
            raise

    def _remember_initialization(self, attempt):
        with self._initialization_lock:
            key = attempt.connection if attempt.connection is not None else attempt
            self._initializations[key] = attempt
            if key is not attempt:
                self._initializations.pop(attempt, None)


    def _allocate_descriptor(self, attempt):
        from app.core.resource_cleanup import CleanupState, NoAllocation, descriptor_allocation

        if os.name != "nt":
            attempt.descriptor_release.state = CleanupState.AMBIGUOUS
            result = descriptor_allocation(os.open, attempt.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                                           0o600, _exclusive=True)
            if isinstance(result, NoAllocation):
                attempt.descriptor_release.state = CleanupState.VERIFIED_RELEASED
                raise result.error
            attempt.descriptor = result
            attempt.descriptor_release.state = CleanupState.OWNED_OPEN
            return
        import ctypes
        import msvcrt

        # CREATE_NEW preserves exclusive creation. Sharing delete permits the
        # existing post-close replacement validation even while its proof handle
        # briefly retains the original file object. Identity checks stay intact.
        kernel = self._windows_kernel()
        attempt.native_release.state = CleanupState.AMBIGUOUS
        handle = kernel.CreateFileW(str(attempt.path), 0x40000000, 0x7, None, 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            attempt.native_release.state = CleanupState.VERIFIED_RELEASED
            raise ctypes.WinError(ctypes.get_last_error())
        attempt.native_handle = handle
        # Opaque conversion may transfer ownership before an exception escapes.
        attempt.native_transfer_ambiguous = True
        attempt.native_release.state = CleanupState.AMBIGUOUS
        attempt.descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_NOINHERIT)
        attempt.native_transfer_ambiguous = False
        attempt.native_release.state = CleanupState.VERIFIED_RELEASED  # Handoff, not a second native close.
        attempt.native_handle = None  # The CRT descriptor now owns this handle.

    def _forget_initialization(self, attempt):
        with self._initialization_lock:
            key = attempt.connection if attempt.connection is not None else attempt
            if self._initializations.get(key) is attempt:
                del self._initializations[key]
            if self._initializations.get(attempt) is attempt:
                del self._initializations[attempt]

    @property
    def cleanup_failed(self):
        with self._initialization_lock:
            failed = any(item.retired or item.descriptor_failed or item.connection_failed
                         for item in self._initializations.values())
        return failed or self._reader_connections.failed

    def _check_cleanup_available(self):
        if self.cleanup_failed:
            raise RepositoryCleanupIncompleteError





    def _cleanup_initialization(self, attempt):
        """After SQLite closes, remove only our still-empty exclusively created file.

        Retain storage if rollback leaves any data/sidecars or an identity/owner
        cannot be proven. Never delete sidecars or a peer's replacement. This
        retains the cooperating-filesystem contract, not cross-process locking.
        """
        path, descriptor, created = attempt
        remove_empty = False
        try:
            if created is None or attempt.preserve_storage:
                return
            identity = created.st_dev, created.st_ino
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
            remove_empty = True
        except (OSError, IndexBindingUnavailableError):
            pass
        finally:
            # Release failure propagates and remains owned; it is not an
            # uncertain-file-deletion error to suppress or retry in this pass.
            self._release_descriptor(attempt)
        if not remove_empty or attempt.preserve_storage:
            return
        try:
            # Windows requires closing our creation descriptor before unlink.
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

    def _close_connection(self, connection):
        with self._initialization_lock:
            attempt = self._initializations.get(connection)
        if attempt is None:
            attempt = _InitializationOwnership(self.db_path, connection=connection)
        self._dispose_initialization(attempt)

    @staticmethod
    def _connection_closed(connection):
        try:
            if isinstance(connection, sqlite3.Connection):
                connection.in_transaction
            else:
                # Preserve the existing repository connection-wrapper seam.
                # This literal probe reads no semantic rows and writes nothing.
                connection.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            return True
        return False

    def _dispose_initialization(self, attempt, *, retry=False):
        # This dedicated per-attempt disposal lock never guards normal SQLite
        # work. Registry, semantic owner, scheduler and resource locks are absent.
        with attempt.lock:
            attempt.retired = True
            error = None
            connection = attempt.connection
            if connection is None:
                attempt.connection_closed = True
            elif not attempt.connection_closed and (retry or not attempt.connection_failed):
                try:
                    connection.close()
                    if not self._connection_closed(connection):
                        raise RepositoryCleanupIncompleteError
                    attempt.connection_closed = True
                    attempt.connection_failed = False
                except BaseException as exc:
                    try:
                        attempt.connection_closed = self._connection_closed(connection)
                    except BaseException:
                        attempt.connection_closed = False
                    attempt.connection_failed = not attempt.connection_closed
                    error = exc
            try:
                if not attempt.descriptor_released or not attempt.guard_released:
                    if attempt.descriptor_failed and not retry:
                        raise RepositoryCleanupIncompleteError
                    if attempt.descriptor is not None and attempt.connection_closed and not attempt.committed:
                        self._cleanup_initialization(attempt)
                    else:
                        self._release_descriptor(attempt)
            except BaseException as exc:
                error = error or exc
            if attempt.connection_closed and attempt.descriptor_released and attempt.guard_released:
                self._forget_initialization(attempt)
            else:
                self._remember_initialization(attempt)
            if error is not None:
                raise error
            if not attempt.connection_closed or not attempt.descriptor_released or not attempt.guard_released:
                raise RepositoryCleanupIncompleteError

    def close_initializations(self, *, retry=True, _defer_readers=False):
        """Drain retained failed disposal, never steal an active connection.

        New writable work stays fenced while failed ownership is retained. The
        exact records are bounded by already accepted work, not a cleanup queue.
        """
        with self._initialization_lock:
            attempts = tuple(dict.fromkeys(self._initializations.values()))
        failed = False
        error = None
        for attempt in attempts:
            if not attempt.retired:
                failed = True
                continue
            try:
                self._dispose_initialization(attempt, retry=retry)
            except BaseException as exc:
                failed = True
                error = error or exc
        with self._initialization_lock:
            failed = failed or bool(self._initializations)
        try:
            self._reader_connections.drain(retry=retry, defer_active=_defer_readers)
        except BaseException as exc:
            error = error or exc
        if error is not None:
            raise error
        if failed:
            raise RepositoryCleanupIncompleteError

    def _validate_transaction_binding(self, session):
        if not self._storage_protected:
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
        self._validate_namespace(lambda sql, args=(): session._connection.execute(sql, args).fetchone())

    def _connect(self) -> sqlite3.Connection:
        if self.__inspection_binding is not None:
            raise ImmutableIndexInspectionUnavailableError  # No writable surface for this expectation.
        self._check_cleanup_available()
        if self._storage_protected:
            return self._connect_bound()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(_SCHEMA)
        except BaseException:
            try:
                connection.rollback()
            except BaseException:
                pass
            try:
                self._close_connection(connection)
            except BaseException:
                pass
            raise
        return connection

    def _commit(self, connection):
        if self._storage_protected:
            self._binding_storage_identity(immutable=False)
        self._validate_namespace(lambda sql, args=(): connection.execute(sql, args).fetchone())
        connection.commit()
        with self._initialization_lock:
            attempt = self._initializations.get(connection)
        if attempt is not None:
            attempt.committed = True
            self._release_descriptor(attempt)

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
            if self._storage_protected:
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
        if self._storage_protected:
            return self.load_chunks_read_only()
        connection = self._connect()
        try:
            return self._load_chunks_connection(connection)
        finally:
            self._close_connection(connection)

    def read_eligible_snapshot(self, eligible_paths, *, budget, space_id, cancel):
        """Internal bounded live reader; no preparation, scoring or binding writes.

        Paths must already be authorized/canonicalized by the vault owner. Returns
        only a complete snapshot; quota exhaustion never exposes a partial prefix.
        """
        from app.repositories.semantic_reads import read_eligible_snapshot

        return read_eligible_snapshot(
            self.db_path, eligible_paths, budget=budget, space_id=space_id, cancel=cancel,
            expected_binding=self._namespace_binding, storage_guard=self._bounded_binding_guard,
            allow_legacy_binding=self.__default_binding is not None, connection_owner=self._reader_connections,
        )

    def _bounded_binding_guard(self, *, budget, space_id, cancel):
        if not self._storage_protected:
            return
        def probe():
            cancel.check()
            budget.charge(space_id, "probes")
        if self._namespace_binding is not None:
            self._namespace_binding.check_root(probe=probe)
        if self._storage_directory is not None:
            self._storage_directory.check(probe=probe)
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
            uri = f"{self.db_path.absolute().as_uri()}?mode=ro"
            if immutable:
                uri += "&immutable=1"
            with self._reader_connections.connection(uri, uri=True, timeout=0) as connection:
                connection.execute("BEGIN")
                self._validate_namespace(lambda sql, args=(): connection.execute(sql, args).fetchone(),
                                         allow_empty=allow_empty)
                yield connection
                if self._binding_storage_identity(immutable=immutable) != before:
                    raise IndexBindingUnavailableError
                for path, identity in sidecars.items():
                    if ordinary_identity(path) != identity:
                        raise IndexBindingUnavailableError
        except (OSError, sqlite3.DatabaseError, IndexBindingUnavailableError):
            raise IndexBindingUnavailableError from None


    def load_chunks_read_only(self) -> list[StoredChunk]:
        """Read bound live chunks; preserve legacy immutable offline inspection."""
        if self._storage_protected:
            with self._bound_read_connection() as connection:
                return self._load_chunks_connection(connection)
        sidecars = (Path(f"{self.db_path}-wal"), Path(f"{self.db_path}-shm"))
        if any(path.exists() for path in sidecars):
            raise ImmutableIndexInspectionUnavailableError
        target = self.db_path.resolve()
        database_uri = f"{target.as_uri()}?mode=ro&immutable=1"
        with self._reader_connections.connection(database_uri, uri=True, timeout=1) as connection:
            chunks = self._load_chunks_connection(connection)
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
            self._close_connection(connection)

    def set_metadata(self, key: str, value: str) -> None:
        if self._namespace_binding is not None and key in BINDING_KEYS:
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
        if self._storage_protected:
            return self.read_status().storage_initialized
        try:
            if not self.db_path.exists():
                return False
            with self._reader_connections.connection(self.db_path, timeout=3) as connection:
                rows = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name IN ('meta', 'notes', 'chunks')"
                ).fetchall()
                return {row[0] for row in rows} == {"meta", "notes", "chunks"}
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
        self._validate_namespace(lambda sql, args=(): connection.execute(sql, args).fetchone(), allow_empty=True)
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
        if self._storage_protected:
            with self._bound_read_connection(
                allow_empty=True, immutable="immutable=1" in database_uri,
            ) as connection:
                return self._read_status_connection(connection)
        with self._reader_connections.connection(database_uri, uri=True, timeout=1) as connection:
            return self._read_status_connection(connection)


    def read_status(self) -> SemanticIndexStatus:
        """Read live-compatible index metadata and counts without changing lifecycle state."""
        if self._storage_protected:
            try:
                if self._binding_storage_identity(immutable=False) is None:
                    return self._missing_status()
                return self._read_status_uri(f"{self.db_path.absolute().as_uri()}?mode=ro")
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
            if self._storage_protected:
                if self._binding_storage_identity() is None:
                    return self._missing_status()
                return self._read_status_uri(f"{self.db_path.absolute().as_uri()}?mode=ro&immutable=1")
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
        if self._storage_protected:
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
            with self._reader_connections.connection(database_uri, uri=True, timeout=1) as connection:
                connection.row_factory = sqlite3.Row
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
