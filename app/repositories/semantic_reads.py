"""Bounded internal SQLite reads, deliberately separate from legacy full loaders.

Live mode=ro honors WAL snapshots and may use SQLite-managed SHM coordination.
It is not the filesystem-immutable stopped-service inspection API. The caller
owns authorization and canonical vault eligibility; no note filesystem is read.
Only cooperating writers are supported, not hostile concurrent file replacement.
"""

from __future__ import annotations

import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from app.repositories.semantic import StoredChunk
from app.services.knowledge_spaces import SpaceId
from app.services.scoped_budget import BudgetExhausted, CancellationToken, ScopedBudget


class SemanticReadUnavailableError(RuntimeError):
    def __init__(self):
        super().__init__("semantic_unavailable")


@dataclass(frozen=True, slots=True)
class EligibleSemanticSnapshot:
    """Complete eligible universe and metadata from the same read transaction.

    Payloads are private and excluded from repr. Signature interpretation and
    ranking belong to the semantic owner in D2, not this storage primitive.
    """

    index_signature: str | None = field(repr=False)
    index_state: str | None
    chunks: tuple[StoredChunk, ...] = field(repr=False)
    has_chunks: bool = False


def _identity(path, *, budget, space_id, cancel, directory=False):
    cancel.check()
    budget.charge(space_id, "probes")
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise SemanticReadUnavailableError
    return info.st_dev, info.st_ino


def _storage_identity(path, *, budget, space_id, cancel):
    # Ancestors first: never intentionally traverse a redirected parent/sidecar.
    directories = tuple(
        (parent, _identity(parent, budget=budget, space_id=space_id, cancel=cancel, directory=True))
        for parent in reversed(path.parents)
    )
    identity = _identity(path, budget=budget, space_id=space_id, cancel=cancel)
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            _identity(path.with_name(path.name + suffix), budget=budget, space_id=space_id, cancel=cancel)
        except FileNotFoundError:
            pass
    return directories, identity


def read_eligible_snapshot(
    db_path: Path, eligible_paths: tuple[str, ...] | frozenset[str], *,
    budget: ScopedBudget, space_id: SpaceId, cancel: CancellationToken,
) -> EligibleSemanticSnapshot:
    """Return all admitted rows or fail; never return an incomplete contribution.

    Existing idx_chunks_path and INTEGER PRIMARY KEY id are used. Scalar length
    preflights charge rows (including overflow detection) and UTF-8/blob bytes
    before payload queries. Metadata bytes also consume semantic_bytes. Bounded
    Python sorting avoids any SQLite temporary sort of unrestricted path rows.
    VM callbacks run every instruction, including BEGIN/compatibility/EOF work.
    """
    cancel.check()
    # Supplied owner snapshots are already bounded; reject unbounded iterables.
    if not isinstance(eligible_paths, (tuple, frozenset)):
        raise ValueError("expected bounded eligible paths")
    if len(eligible_paths) > budget.allocations[space_id]["paths"]:
        raise BudgetExhausted("paths")
    for path in eligible_paths:
        cancel.check()
        if not isinstance(path, str) or not path or len(path.encode("utf-8")) > 1024:
            raise ValueError("invalid eligible path")
    paths = sorted(set(eligible_paths), key=lambda path: (path.casefold(), path))
    connection = None
    interrupted = None

    def progress():
        nonlocal interrupted
        try:
            cancel.check()
            budget.charge(space_id, "sqlite_steps")
        except BaseException as error:
            interrupted = error
            return 1
        return 0

    def one(sql, parameters=()):
        cancel.check()
        with closing(connection.cursor()) as cursor:
            cursor.execute(sql, parameters)
            result = cursor.fetchone()
            cancel.check()
            return result

    try:
        # Do not resolve symlinks into a different index. Recheck observed native
        # identities before accepting results, including replacement while open.
        target = Path(db_path).absolute()
        before = _storage_identity(target, budget=budget, space_id=space_id, cancel=cancel)
        cancel.check()
        budget.charge(space_id, "probes")  # Explicit database descriptor open.
        connection = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True, timeout=0)
        connection.set_progress_handler(progress, 1)
        one("BEGIN")
        # Only real existing tables, not views executing unexpected SQL.
        tables = one(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('meta','notes','chunks')"
        )
        if tables != (3,):
            raise SemanticReadUnavailableError
        # Force the existing indexed path access and verify its leading column.
        if one("SELECT name FROM pragma_index_info('idx_chunks_path') WHERE seqno=0") != ("path",):
            raise SemanticReadUnavailableError
        metadata = []
        for key in ("index_signature", "index_state"):
            size = one("SELECT length(CAST(value AS BLOB)) FROM meta WHERE key=?", (key,))
            if size is None:
                metadata.append(None)
            else:
                if type(size[0]) is not int or size[0] < 0:
                    raise SemanticReadUnavailableError
                budget.charge(space_id, "semantic_bytes", size[0])
                value = one("SELECT value FROM meta WHERE key=?", (key,))[0]
                if not isinstance(value, str):
                    raise SemanticReadUnavailableError
                metadata.append(value)
        has_chunks = one("SELECT EXISTS(SELECT 1 FROM chunks LIMIT 1)") == (1,)
        chunks = []
        for path in paths:
            cancel.check()
            descriptors = []
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    "SELECT CASE WHEN typeof(id)='integer' THEN id END, "
                    "CASE WHEN typeof(chunk_index)='integer' THEN chunk_index END, "
                    "length(CAST(path AS BLOB)), coalesce(length(CAST(heading AS BLOB)),0), "
                    "length(CAST(content AS BLOB)), length(CAST(embedding AS BLOB)), "
                    "CASE WHEN typeof(dimensions)='integer' THEN dimensions END "
                    "FROM chunks INDEXED BY idx_chunks_path WHERE path=?", (path,),
                )
                while True:
                    cancel.check()
                    row = cursor.fetchone()
                    if row is None:
                        break
                    cancel.check()
                    budget.charge(space_id, "semantic_rows")
                    if (
                        any(type(value) is not int for value in row)
                        or any(value < 0 for value in row[2:6]) or row[6] <= 0
                    ):
                        raise SemanticReadUnavailableError
                    budget.charge(space_id, "semantic_bytes", sum(row[2:6]))
                    descriptors.append((row[1], row[0]))
            # At most the admitted row quota, never unrestricted SQL ORDER BY.
            descriptors.sort()
            for _, row_id in descriptors:
                row = one(
                    "SELECT path, chunk_index, heading, content, embedding, dimensions "
                    "FROM chunks WHERE rowid=? AND id=?", (row_id, row_id),
                )
                if (
                    row is None or row[0] != path or type(row[1]) is not int
                    or (row[2] is not None and not isinstance(row[2], str))
                    or not isinstance(row[3], str) or not isinstance(row[4], bytes)
                    or type(row[5]) is not int or row[5] <= 0
                ):
                    raise SemanticReadUnavailableError
                chunks.append(StoredChunk(*row))
        if _storage_identity(target, budget=budget, space_id=space_id, cancel=cancel) != before:
            raise SemanticReadUnavailableError
        cancel.check()
        return EligibleSemanticSnapshot(metadata[0], metadata[1], tuple(chunks), has_chunks)
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.DatabaseError:
        if interrupted is not None:
            raise interrupted from None
        raise SemanticReadUnavailableError from None
    except OSError:
        raise SemanticReadUnavailableError from None
    finally:
        if connection is not None:
            # Disable an exhausted/cancelled callback before rollback itself.
            try:
                connection.set_progress_handler(None, 0)
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()
