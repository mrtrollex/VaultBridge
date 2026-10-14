"""Synthetic D1 boundaries; no models or public/named-mode activation."""

import sqlite3
from contextlib import closing

import pytest

from app.repositories.semantic import SemanticRepository, StoredChunk, StoredNote
from app.repositories.semantic_reads import SemanticReadUnavailableError
from app.services.knowledge_spaces import SpaceError, SpaceId
from app.services.scoped_budget import CEILINGS, BudgetExhausted, CancellationToken, ScopedBudget

SID = SpaceId("default")


def fixture(tmp_path, paths=("a.md",), indices=(0,)):
    repository = SemanticRepository(tmp_path / "index.sqlite3")
    repository.prepare_index("sig")
    with repository.transaction() as session:
        for path in paths:
            session.replace_note(
                StoredNote(path, 0, 1, "hash", "now"),
                [StoredChunk(path, i, "é", "内容", b"1234", 1) for i in indices],
            )
    return repository


def read(repository, paths=("a.md",), *, limits=None, cancel=None, budget=None):
    budget = budget or ScopedBudget((SID,), limits=limits)
    return repository.read_eligible_snapshot(
        paths, budget=budget, space_id=SID, cancel=cancel or CancellationToken(),
    )


@pytest.mark.parametrize("count", range(1, 9))
def test_fixed_allocations(count):
    ids = tuple(SpaceId(f"s{i}") for i in range(count))
    budget = ScopedBudget(ids)
    for resource in ("semantic_rows", "semantic_bytes", "sqlite_steps"):
        total, local = CEILINGS[resource]
        allocation = min(local, total // count)
        assert all(budget.remaining(sid, resource) == allocation for sid in ids)
        budget.charge(ids[0], resource, allocation)
        with pytest.raises(BudgetExhausted):
            budget.charge(ids[0], resource)
        assert all(budget.remaining(sid, resource) == allocation for sid in ids[1:])


@pytest.mark.parametrize("resource", ("semantic_rows", "semantic_bytes", "sqlite_steps"))
def test_downward_limits(resource):
    budget = ScopedBudget((SID,), limits={resource: (7, 3)})
    assert budget.remaining(SID, resource) == 3
    with pytest.raises(ValueError, match="raise ceiling"):
        ScopedBudget((SID,), limits={resource: (CEILINGS[resource][0] + 1, 0)})


def test_read_only_no_schema_creation_or_legacy_loader(tmp_path, monkeypatch):
    repository = fixture(tmp_path)
    # Rollback-journal mode needs no WAL coordination files. Live WAL readers
    # can legitimately create SHM/WAL sidecars; immutable inspection is separate.
    with closing(sqlite3.connect(repository.db_path)) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
    before = repository.db_path.read_bytes()
    files = {p.name for p in tmp_path.iterdir()}
    def forbidden(*args):
        pytest.fail("writable/full loader invoked")
    monkeypatch.setattr(repository, "_connect", forbidden)
    monkeypatch.setattr(repository, "_load_chunks_connection", forbidden)
    snapshot = read(repository)
    assert snapshot.index_signature == "sig"
    assert len(snapshot.chunks) == 1
    assert "内容" not in repr(snapshot)
    assert repository.db_path.read_bytes() == before
    assert {p.name for p in tmp_path.iterdir()} == files
    with closing(sqlite3.connect(repository.db_path)) as connection:
        assert connection.execute("SELECT key FROM meta").fetchall() == [("index_signature",)]


@pytest.mark.parametrize("kind", ("missing", "corrupt", "empty", "no_index"))
def test_unavailable_does_not_initialize(tmp_path, kind):
    repository = SemanticRepository(tmp_path / "index.sqlite3")
    if kind == "corrupt":
        repository.db_path.write_bytes(b"not a sqlite database")
    elif kind == "empty":
        repository.db_path.touch()
    elif kind == "no_index":
        repository = fixture(tmp_path)
        with closing(sqlite3.connect(repository.db_path)) as connection:
            connection.execute("DROP INDEX idx_chunks_path")
            connection.commit()
    before = repository.db_path.read_bytes() if repository.db_path.exists() else None
    with pytest.raises(SemanticReadUnavailableError, match="^semantic_unavailable$"):
        read(repository)
    assert (repository.db_path.read_bytes() if repository.db_path.exists() else None) == before


def test_live_wal_and_consistent_snapshot(tmp_path, monkeypatch):
    repository = fixture(tmp_path)
    original_connect = sqlite3.connect
    with closing(original_connect(repository.db_path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE chunks SET content='wal committed'")
        writer.commit()
        assert repository.db_path.with_name("index.sqlite3-wal").exists()
        assert read(repository).chunks[0].content == "wal committed"

        class Connection(sqlite3.Connection):
            def cursor(self, *args, **kwargs):
                return super().cursor(factory=Cursor)

        class Cursor(sqlite3.Cursor):
            def execute(self, sql, parameters=()):
                if sql.startswith("SELECT CASE WHEN typeof(id)"):
                    writer.execute("UPDATE chunks SET content='new version'")
                    writer.execute("UPDATE meta SET value='new sig'")
                    writer.commit()
                return super().execute(sql, parameters)

        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: original_connect(*a, **k, factory=Connection))
        snapshot = read(repository)
        assert snapshot.index_signature == "sig"
        assert snapshot.chunks[0].content == "wal committed"


def test_indexed_eligibility_work_independent_of_unrelated_rows(tmp_path):
    repository = fixture(tmp_path)
    baseline = ScopedBudget((SID,))
    read(repository, budget=baseline)
    with closing(sqlite3.connect(repository.db_path)) as connection:
        connection.executemany(
            "INSERT INTO chunks(path,chunk_index,heading,content,embedding,dimensions) VALUES(?,0,NULL,'x',x'00',1)",
            ((f"unrelated/{i}.md",) for i in range(2000)),
        )
        connection.commit()
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM chunks INDEXED BY idx_chunks_path WHERE path=?", ("a.md",),
        ).fetchone()[3]
        assert "SEARCH" in plan and "idx_chunks_path" in plan
    budget = ScopedBudget((SID,))
    assert len(read(repository, budget=budget).chunks) == 1
    assert budget.used[SID]["semantic_rows"] == 1
    assert budget.used[SID]["sqlite_steps"] < baseline.used[SID]["sqlite_steps"] + 100


@pytest.mark.parametrize("reverse", (False, True))
def test_canonical_order_and_chunk_ties(tmp_path, reverse):
    paths = ("z.md", "ß.md", "SS.md", "a.md", "A.md")
    repository = fixture(tmp_path, paths[::-1] if reverse else paths, (2, 0, 1, 1))
    snapshot = read(repository, tuple(reversed(paths)))
    expected = [(p, i) for p in sorted(paths, key=lambda p: (p.casefold(), p)) for i in (0, 1, 1, 2)]
    assert [(chunk.path, chunk.chunk_index) for chunk in snapshot.chunks] == expected


@pytest.mark.parametrize("limit,success", ((0, False), (1, False), (2, True), (3, True)))
def test_row_boundary_and_overflow(tmp_path, limit, success):
    repository = fixture(tmp_path, indices=(0, 1))
    budget = ScopedBudget((SID,), limits={"semantic_rows": (limit, limit)})
    if success:
        assert len(read(repository, budget=budget).chunks) == 2
    else:
        with pytest.raises(BudgetExhausted) as error:
            read(repository, budget=budget)
        assert error.value.resource == "semantic_rows"
    assert budget.used[SID]["semantic_rows"] == min(2, limit)


@pytest.mark.parametrize("delta", (-1, 0, 1))
def test_payload_utf8_exact_preflight(tmp_path, delta, monkeypatch):
    repository = fixture(tmp_path)
    required = len("sig".encode()) + len("a.mdé内容".encode()) + 4
    limit = required + delta
    statements = []
    original = sqlite3.connect
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, "connect", connect)
    if delta < 0:
        with pytest.raises(BudgetExhausted) as error:
            read(repository, limits={"semantic_bytes": (limit, limit)})
        assert error.value.resource == "semantic_bytes"
        assert not any(sql.startswith("SELECT path, chunk_index") for sql in statements)
    else:
        assert len(read(repository, limits={"semantic_bytes": (limit, limit)}).chunks) == 1


def test_metadata_payload_and_vm_work_counted_even_without_paths(tmp_path):
    repository = fixture(tmp_path)
    budget = ScopedBudget((SID,))
    assert read(repository, (), budget=budget).chunks == ()
    steps = budget.used[SID]["sqlite_steps"]
    assert steps > 0
    assert budget.used[SID]["semantic_bytes"] == 3
    assert budget.used[SID]["semantic_rows"] == 0
    for limit in (0, steps - 1):
        budget = ScopedBudget((SID,), limits={"sqlite_steps": (limit, limit)})
        with pytest.raises(BudgetExhausted) as error:
            read(repository, (), budget=budget)
        assert error.value.resource == "sqlite_steps"
        assert budget.used[SID]["sqlite_steps"] == limit
    assert read(repository, (), limits={"sqlite_steps": (steps, steps)}).chunks == ()
    with pytest.raises(BudgetExhausted):
        read(repository, (), limits={"semantic_bytes": (0, 0)})


@pytest.mark.parametrize("phase", ("sql", "row", "interrupt", "programming", "unreadable"))
def test_cancellation_errors_and_cleanup(tmp_path, monkeypatch, phase):
    repository = fixture(tmp_path, indices=(0, 1, 2))
    original = sqlite3.connect
    token = CancellationToken()
    connections, cursors = [], []
    class Connection(sqlite3.Connection):
        closed = False
        rolled_back = False
        def cursor(self, *args, **kwargs):
            cursor = super().cursor(factory=Cursor)
            cursors.append(cursor)
            return cursor
        def rollback(self):
            self.rolled_back = True
            return super().rollback()
        def close(self):
            self.closed = True
            return super().close()
        def set_progress_handler(self, callback, n):
            if callback is not None and phase == "sql":
                def cancelling():
                    token.cancel()
                    return callback()
                callback_to_use = cancelling
            else:
                callback_to_use = callback
            return super().set_progress_handler(callback_to_use, n)
    class Cursor(sqlite3.Cursor):
        closed = False
        payload = False
        def execute(self, sql, parameters=()):
            if sql.startswith("SELECT path, chunk_index"):
                self.payload = True
                if phase == "interrupt":
                    raise KeyboardInterrupt
                if phase == "programming":
                    raise sqlite3.ProgrammingError("test bug")
                if phase == "unreadable":
                    raise sqlite3.OperationalError("private storage detail")
            return super().execute(sql, parameters)
        def fetchone(self):
            result = super().fetchone()
            if phase == "row" and self.payload:
                token.cancel()
            return result
        def close(self):
            self.closed = True
            return super().close()
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs, factory=Connection)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, "connect", connect)
    expected = {
        "sql": SpaceError, "row": SpaceError, "interrupt": KeyboardInterrupt,
        "programming": sqlite3.ProgrammingError, "unreadable": SemanticReadUnavailableError,
    }[phase]
    with pytest.raises(expected) as error:
        read(repository, cancel=token)
    if phase in ("sql", "row"):
        assert str(error.value) == "operation_cancelled"
    assert connections and all(connection.closed for connection in connections)
    assert all(cursor.closed for cursor in cursors)
    if phase != "sql":
        assert all(connection.rolled_back for connection in connections)


def test_quota_cleanup_and_no_partial_payload(tmp_path, monkeypatch):
    repository = fixture(tmp_path, indices=(0, 1))
    original = sqlite3.connect
    connections, statements = [], []
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(BudgetExhausted):
        read(repository, limits={"semantic_rows": (1, 1)})
    assert not any(sql.startswith("SELECT path, chunk_index") for sql in statements)
    assert statements[-1] == "ROLLBACK"
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_empty_eligible_universe_with_zero_row_quota(tmp_path):
    repository = fixture(tmp_path)
    assert read(repository, ("absent.md",), limits={"semantic_rows": (0, 0)}).chunks == ()


def test_open_failure_is_safe_unavailability(tmp_path, monkeypatch):
    repository = fixture(tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError("private path")
    monkeypatch.setattr(sqlite3, "connect", denied)
    with pytest.raises(SemanticReadUnavailableError, match="^semantic_unavailable$"):
        read(repository)


def test_callback_interruption_preserved(tmp_path):
    repository = fixture(tmp_path)
    class InterruptedBudget(ScopedBudget):
        def charge(self, sid, resource, count=1):
            if resource == "sqlite_steps":
                raise KeyboardInterrupt
            return super().charge(sid, resource, count)
    with pytest.raises(KeyboardInterrupt):
        read(repository, budget=InterruptedBudget((SID,)))


def test_metadata_preflight_before_loading_value(tmp_path, monkeypatch):
    repository = fixture(tmp_path)
    with closing(sqlite3.connect(repository.db_path)) as connection:
        connection.execute("UPDATE meta SET value=?", ("é" * 1000,))
        connection.commit()
    original = sqlite3.connect
    statements = []
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(BudgetExhausted):
        read(repository, limits={"semantic_bytes": (10, 10)})
    assert not any(sql.startswith("SELECT value FROM meta") for sql in statements)


def test_reject_unbounded_or_oversized_path_selection(tmp_path):
    repository = fixture(tmp_path)
    with pytest.raises(ValueError, match="bounded"):
        read(repository, iter(("a.md",)))
    with pytest.raises(BudgetExhausted):
        read(repository, ("a.md",), limits={"paths": (0, 0)})


def test_pre_cancelled_never_opens_storage(tmp_path, monkeypatch):
    token = CancellationToken()
    token.cancel()
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("opened storage"))
    with pytest.raises(SpaceError, match="operation_cancelled"):
        read(SemanticRepository(tmp_path / "missing"), cancel=token)


def test_replaced_storage_discards_snapshot(tmp_path, monkeypatch):
    repository = fixture(tmp_path)
    from app.repositories import semantic_reads
    original = semantic_reads._storage_identity
    count = 0
    def identity(path, **kwargs):
        nonlocal count
        count += 1
        directories, file = original(path, **kwargs)
        return directories, file if count == 1 else (file[0], file[1] + 1)
    monkeypatch.setattr(semantic_reads, "_storage_identity", identity)
    with pytest.raises(SemanticReadUnavailableError):
        read(repository)


def test_redirected_storage_rejected(tmp_path):
    repository = fixture(tmp_path)
    link = tmp_path / "link.sqlite3"
    try:
        link.symlink_to(repository.db_path)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(SemanticReadUnavailableError):
        read(SemanticRepository(link))


@pytest.mark.parametrize("wal", (False, True))
@pytest.mark.parametrize("boundary", (
    "zero", "ancestor", "database", "sidecars", "open",
    "recheck_ancestor", "recheck_database", "recheck_sidecars", "complete",
))
def test_storage_probes_admitted_before_access_and_recheck_cleanup(tmp_path, monkeypatch, wal, boundary):
    repository = fixture(tmp_path)
    original_connect = sqlite3.connect
    with closing(original_connect(repository.db_path)) as writer:
        writer.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
        if wal:
            writer.execute("UPDATE chunks SET content='live WAL'")
            writer.commit()
        ancestors = len(repository.db_path.absolute().parents)
        initial = ancestors + 4  # Ancestors, DB and all three sidecar probes.
        complete = initial * 2 + 1  # Includes the explicit connection open.
        limit = {
            "zero": 0, "ancestor": ancestors - 1, "database": ancestors,
            "sidecars": initial - 1, "open": initial,
            "recheck_ancestor": initial + 1,
            "recheck_database": initial + 1 + ancestors,
            "recheck_sidecars": complete - 1, "complete": complete,
        }[boundary]
        budget = ScopedBudget((SID,), limits={"probes": (limit, limit)})
        events, connections, cursors = [], [], []
        original_lstat = type(repository.db_path).lstat

        def lstat(path, *args, **kwargs):
            assert budget.used[SID]["probes"] == len(events) + 1
            events.append(path)
            return original_lstat(path, *args, **kwargs)

        class Connection(sqlite3.Connection):
            rolled_back = False

            def cursor(self, *args, **kwargs):
                cursor = super().cursor(factory=Cursor)
                cursors.append(cursor)
                return cursor

            def rollback(self):
                self.rolled_back = True
                return super().rollback()

        class Cursor(sqlite3.Cursor):
            closed = False

            def close(self):
                self.closed = True
                return super().close()

        def connect(*args, **kwargs):
            assert budget.used[SID]["probes"] == len(events) + 1
            events.append("connect")
            connection = original_connect(*args, **kwargs, factory=Connection)
            connections.append(connection)
            return connection

        monkeypatch.setattr(type(repository.db_path), "lstat", lstat)
        monkeypatch.setattr(sqlite3, "connect", connect)
        if boundary == "complete":
            assert len(read(repository, budget=budget).chunks) == 1
        else:
            with pytest.raises(BudgetExhausted) as error:
                read(repository, budget=budget)
            assert error.value.resource == "probes"
        assert len(events) == budget.used[SID]["probes"] == budget.total["probes"] == limit
        assert bool(connections) == (limit > initial)
        assert all(cursor.closed for cursor in cursors)
        for connection in connections:
            assert connection.rolled_back
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")


def test_storage_probes_share_fixed_space_and_global_quota(tmp_path):
    repository = fixture(tmp_path)
    other = SpaceId("other")
    per_read = 2 * (len(repository.db_path.absolute().parents) + 4) + 1
    budget = ScopedBudget((SID, other), limits={"probes": (2 * per_read, per_read)})
    # Simulate probes already consumed by another phase of the same space.
    budget.charge(SID, "probes", 1)
    with pytest.raises(BudgetExhausted) as error:
        read(repository, budget=budget)
    assert error.value.resource == "probes"
    assert budget.used[SID]["probes"] == per_read
    assert budget.remaining(other, "probes") == per_read
    snapshot = repository.read_eligible_snapshot(
        ("a.md",), budget=budget, space_id=other, cancel=CancellationToken(),
    )
    assert len(snapshot.chunks) == 1
    assert budget.total["probes"] == 2 * per_read
    with pytest.raises(BudgetExhausted):
        repository.read_eligible_snapshot(
            ("a.md",), budget=budget, space_id=other, cancel=CancellationToken(),
        )


def test_cancellation_between_storage_probes_stops_before_next_access(tmp_path, monkeypatch):
    repository = fixture(tmp_path)
    token = CancellationToken()
    budget = ScopedBudget((SID,))
    original_lstat = type(repository.db_path).lstat
    calls = []

    def lstat(path, *args, **kwargs):
        calls.append(path)
        result = original_lstat(path, *args, **kwargs)
        token.cancel()
        return result

    monkeypatch.setattr(type(repository.db_path), "lstat", lstat)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("opened storage"))
    with pytest.raises(SpaceError, match="operation_cancelled"):
        read(repository, budget=budget, cancel=token)
    assert len(calls) == budget.used[SID]["probes"] == 1
