"""Validation readers must not be mistaken for unavailable, fully released storage."""

import sqlite3
import threading
from contextlib import closing

import pytest

from app.repositories.semantic import RepositoryCleanupIncompleteError
from app.services.space_runtime import compose_space_runtime
from tests.test_semantic_resources import launch
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration, scope
from tests.test_space_runtime_offline_cleanup import sidecars


@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("error_type", [OSError, sqlite3.OperationalError, KeyboardInterrupt])
def test_validation_reader_close_failure_is_nonterminal_and_retryable(
    tmp_path, backend, monkeypatch, released, error_type,
):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    runtime.operations.scoped_semantic_search("Independent notes", scope(runtime))
    connect, target, attempts, allocations = sqlite3.connect, [], [], []
    failing = True

    class Connection(sqlite3.Connection):
        def close(self):
            attempts.append(self)
            if not failing or released:
                super().close()
            if failing:
                raise error_type("validation reader close fault")

    def tracked(database, *args, **kwargs):
        if not target and str(database) == repository.db_path.as_uri() + "?mode=ro":
            connection = connect(database, *args, factory=Connection, **kwargs)
            target.append(connection)
        else:
            connection = connect(database, *args, **kwargs)
        allocations.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked)
    try:
        expected = KeyboardInterrupt if error_type is KeyboardInterrupt else RepositoryCleanupIncompleteError
        with pytest.raises(expected):
            runtime.shutdown()
        assert runtime._state == "stopping" and len(target) == 1
        assert repository._reader_connections.pending is not released
        if not released:
            assert target[0].execute("SELECT 1").fetchone() == (1,)
        else:
            with pytest.raises(sqlite3.ProgrammingError):
                target[0].execute("SELECT 1")
        failing = False
        runtime.shutdown()
        assert runtime._state == "stopped" and not repository._reader_connections.pending
        assert attempts == target * (1 if released else 2)
        assert not any(p.exists() for p in sidecars(repository.db_path))
        previous = len(allocations), len(attempts)
        runtime.shutdown()
        runtime.shutdown()
        assert (len(allocations), len(attempts)) == previous
    finally:
        failing = False
        repository.close_initializations()
        runtime.shutdown()


def test_exclusive_sqlite_lock_is_observable_and_retry_preserves_committed_data(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    with closing(sqlite3.connect(repository.db_path)) as writer:
        writer.execute("PRAGMA locking_mode=EXCLUSIVE")
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO meta(key,value) VALUES('committed_probe','preserve me')")
        writer.commit()  # Keep the exclusive owner alive with committed WAL frames.
        try:
            with pytest.raises(sqlite3.OperationalError) as error:
                runtime.shutdown()
            assert error.value.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
            assert runtime._state == "stopping" and not repository._reader_connections.pending
        finally:
            writer.close()
            runtime.shutdown()
    assert runtime._state == "stopped" and not any(p.exists() for p in sidecars(repository.db_path))
    with closing(sqlite3.connect(repository.db_path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        assert db.execute("SELECT value FROM meta WHERE key='committed_probe'").fetchone() == ("preserve me",)
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_concurrent_shutdown_retries_the_retained_validation_reader(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    runtime.operations.scoped_semantic_search("Independent notes", scope(runtime))
    entered, resume = threading.Event(), threading.Event()
    connect, target, attempts = sqlite3.connect, [], []

    class Connection(sqlite3.Connection):
        def close(self):
            attempts.append(self)
            if len(attempts) == 1:
                entered.set()
                assert resume.wait(10)
                raise OSError("first validation close")
            super().close()

    def tracked(database, *args, **kwargs):
        if not target and str(database) == repository.db_path.as_uri() + "?mode=ro":
            connection = connect(database, *args, factory=Connection, **kwargs)
            target.append(connection)
            return connection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    first = launch(runtime.shutdown)
    second = None
    try:
        assert entered.wait(10)
        second = launch(runtime.shutdown)
        resume.set()
        with pytest.raises(RepositoryCleanupIncompleteError):
            first.result(10)
        second.result(10)
        assert runtime._state == "stopped" and attempts == target * 2
        assert not repository._reader_connections.pending
        assert not any(p.exists() for p in sidecars(repository.db_path))
    finally:
        resume.set()
        try:
            first.result(10)
        except RepositoryCleanupIncompleteError:
            pass
        if second is not None:
            second.result(10)
        repository.close_initializations()
        runtime.shutdown()


def test_locked_validation_is_not_suppressed_as_unavailable_storage(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    runtime.operations.scoped_semantic_search("Independent notes", scope(runtime))
    connect = sqlite3.connect
    failing = True

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if failing and sql == "BEGIN":
                error = sqlite3.OperationalError("injected SQLITE_LOCKED")
                error.sqlite_errorcode = sqlite3.SQLITE_LOCKED
                raise error
            return super().execute(sql, *args, **kwargs)

    def tracked(database, *args, **kwargs):
        if str(database) == repository.db_path.as_uri() + "?mode=ro":
            kwargs["factory"] = Connection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    try:
        with pytest.raises(sqlite3.OperationalError) as error:
            runtime.shutdown()
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_LOCKED
        assert runtime._state == "stopping" and not repository._reader_connections.pending
        failing = False
        runtime.shutdown()
        assert runtime._state == "stopped" and not any(p.exists() for p in sidecars(repository.db_path))
    finally:
        failing = False
        runtime.shutdown()


def test_cancellation_during_finalization_rollback_releases_connection(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    runtime.operations.scoped_semantic_search("Independent notes", scope(runtime))
    connect, opened, closed = sqlite3.connect, [], []

    class Connection(sqlite3.Connection):
        def rollback(self):
            raise KeyboardInterrupt("cancel finalization")

        def close(self):
            closed.append(self)
            super().close()

    def tracked(database, *args, **kwargs):
        if str(database) == repository.db_path.as_uri() + "?mode=rw":
            connection = connect(database, *args, factory=Connection, **kwargs)
            opened.append(connection)
            return connection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    try:
        with pytest.raises(KeyboardInterrupt, match="cancel finalization"):
            runtime.shutdown()
        assert runtime._state == "stopping" and len(opened) == 1 and closed == opened
        assert not repository._reader_connections.pending
        runtime.shutdown()
        runtime.shutdown()
        assert runtime._state == "stopped" and len(opened) == 1 and closed == opened
        assert not any(p.exists() for p in sidecars(repository.db_path))
    finally:
        runtime.shutdown()
