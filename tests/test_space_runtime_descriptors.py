"""Real SQLite connection and exclusive initialization descriptor ownership."""

import errno
import os
import sqlite3
from contextlib import closing

import pytest

from app.repositories.semantic import SemanticRepository
from app.repositories.semantic_binding import BINDING_KEYS, IndexBindingUnavailableError
from app.services.space_runtime import compose_space_runtime
from tests.test_semantic_resources import launch
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration
from tests.test_space_runtime_namespace_shutdown import rows


def observe_connections(repository, monkeypatch, *, deny=None):
    connect = repository._connect
    observed = []

    def tracked():
        connection = connect()
        attempt = repository._initializations.get(connection)
        if attempt is not None:
            os.fstat(attempt[1])
        observed.append((connection, attempt))
        if deny is not None:
            connection.set_authorizer(deny)
        return connection

    monkeypatch.setattr(repository, "_connect", tracked)
    return observed


def assert_released(repository, observed):
    assert not repository._initializations
    for connection, attempt in observed:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
        if attempt is not None:
            with pytest.raises(OSError) as error:
                os.fstat(attempt[1])
            assert error.value.errno == errno.EBADF


def test_fresh_default_metadata_releases_creation_descriptor_before_shutdown(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    observed = observe_connections(repository, monkeypatch)
    assert not repository.db_path.exists()
    try:
        assert repository.get_metadata("index_state") is None
        assert len(observed) == 1 and observed[0][1] is not None
        runtime.shutdown()
        live = 0
        for _, attempt in observed:
            try:
                os.fstat(attempt[1])
            except OSError as error:
                assert error.errno == errno.EBADF
            else:
                live += 1
        print(dict(runtime=runtime._state, initialization_entries=len(repository._initializations),
                   live_creation_descriptors=live))
        assert runtime._state == "stopped" and live == 0
        assert_released(repository, observed)
        assert backend.instances == [] and backend.calls == []
        # Native Windows deletion proves no SQLite/creation handle was retained.
        if repository.db_path.exists():
            repository.db_path.unlink()
        assert not repository.db_path.exists()
    finally:
        # Keep the intentional pre-fix reproduction from leaking its descriptor.
        for connection in tuple(repository._initializations):
            repository._close_connection(connection)
        runtime.shutdown()


@pytest.mark.parametrize("failure", [False, True])
def test_fresh_metadata_reads_rollback_and_never_accumulate_ownership(tmp_path, backend, monkeypatch, failure):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    owner = binding(runtime)._owners.semantic
    repository = owner.repository
    commits = []

    def authorize(action, table, column, *_):
        if action == sqlite3.SQLITE_TRANSACTION and table == "COMMIT":
            commits.append(table)
        if failure and action == sqlite3.SQLITE_READ and table == "meta" and column == "value":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        with monkeypatch.context() as patch:
            observed = observe_connections(repository, patch, deny=authorize)
            for _ in range(8):
                if failure:
                    with pytest.raises(sqlite3.DatabaseError):
                        repository.get_metadata("index_state")
                else:
                    assert repository.get_metadata("index_state") is None
                assert_released(repository, observed)
                # Closing the read must roll back the uncommitted schema, not
                # authorize initialization or namespace metadata on its behalf.
                if repository.db_path.exists():
                    with closing(sqlite3.connect(repository.db_path)) as connection:
                        assert connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
            assert not commits
        assert backend.instances == []
        runtime.start()
        runtime.scheduler.wait(20)
        assert not binding(runtime)._owners.scheduler.status().paused
        assert owner.search("notes", min_score=0)
        assert not set(BINDING_KEYS) & dict(rows(repository.db_path)[0]).keys()
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("mode", ["default_legacy", "default_bound", "named", "unbound"])
@pytest.mark.parametrize("failure", [False, True])
def test_existing_metadata_preserves_rows_and_closes_only_its_connection(tmp_path, backend, monkeypatch, mode, failure):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime, "space-1" if mode == "named" else "default")._owners.semantic.repository
    if mode == "default_bound":
        with closing(sqlite3.connect(repository.db_path)) as connection, connection:
            connection.executemany("INSERT INTO meta VALUES(?,?)",
                                   zip(BINDING_KEYS, repository._namespace_binding.values))
    if mode == "unbound":
        repository = SemanticRepository(repository.db_path)
    original = rows(repository.db_path)
    original_bytes = repository.db_path.read_bytes()
    try:
        if failure:
            # A real SQLite value with an invalid text encoding fails during
            # retrieval on both the owned and independently opened RO paths.
            with closing(sqlite3.connect(repository.db_path)) as connection, connection:
                connection.execute("INSERT INTO meta VALUES('broken',CAST(x'80' AS TEXT))")
            original_bytes = repository.db_path.read_bytes()
        observed = observe_connections(repository, monkeypatch)
        if failure:
            with pytest.raises((sqlite3.DatabaseError, IndexBindingUnavailableError)):
                repository.get_metadata("broken")
        else:
            assert repository.get_metadata("index_state") == "ready"
        assert_released(repository, observed)
        assert repository.db_path.read_bytes() == original_bytes
        if not failure:
            assert rows(repository.db_path) == original
        assert not binding(runtime)._owners.semantic.repository._initializations
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("operation", ["prepare", "reset", "transaction", "set", "finalize"])
@pytest.mark.parametrize("failure", [False, True])
def test_owned_writable_connection_paths_release_initialization(tmp_path, backend, monkeypatch, operation, failure):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository

    def authorize(action, first, *_):
        if failure and action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    observed = observe_connections(repository, monkeypatch, deny=authorize)

    def transaction():
        with repository.transaction() as session:
            assert session.load_notes() == {}

    actions = {
        "prepare": lambda: repository.prepare_index("signature"),
        "reset": lambda: repository.reset_index("signature", "uninitialized"),
        "transaction": transaction, "set": lambda: repository.set_metadata("custom", "value"),
        "finalize": lambda: repository.finalize_full_sync_success(last_successful_sync="now", ready_state="ready"),
    }
    try:
        if failure:
            with pytest.raises((sqlite3.DatabaseError, IndexBindingUnavailableError)):
                actions[operation]()
        else:
            actions[operation]()
        assert len(observed) == 1 and observed[0][1] is not None
        assert_released(repository, observed)
        with closing(sqlite3.connect(repository.db_path)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert tables == (set() if failure else {"meta", "notes", "chunks", "sqlite_sequence"})
    finally:
        runtime.shutdown()


def test_independent_read_does_not_release_active_initialization(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path))
    repository = binding(runtime)._owners.semantic.repository
    peer = binding(runtime, "space-1")._owners.semantic.repository
    connection = repository._connect()
    peer_connection = peer._connect()
    attempt = repository._initializations[connection]
    peer_attempt = peer._initializations[peer_connection]
    try:
        reader = launch(repository.read_status)
        assert not reader.result(20).storage_initialized
        assert repository._initializations == {connection: attempt}
        os.fstat(attempt[1])
        os.fstat(peer_attempt[1])
        repository._close_connection(connection)
        assert_released(repository, [(connection, attempt)])
        assert peer._initializations == {peer_connection: peer_attempt}
        os.fstat(peer_attempt[1])
        peer._commit(peer_connection)
        peer._close_connection(peer_connection)
        assert_released(peer, [(peer_connection, peer_attempt)])
        assert tuple(dict(rows(peer.db_path)[0])[key] for key in BINDING_KEYS) == peer._namespace_binding.values
    finally:
        repository._close_connection(connection)
        peer._close_connection(peer_connection)
        runtime.shutdown()


@pytest.mark.parametrize("failure", [False, True])
def test_unbound_chunk_reads_close_repository_owned_connection(tmp_path, monkeypatch, failure):
    repository = SemanticRepository(tmp_path / "legacy.sqlite3")

    def authorize(action, table, *_):
        if failure and action == sqlite3.SQLITE_READ and table == "chunks":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    observed = observe_connections(repository, monkeypatch, deny=authorize)
    if failure:
        with pytest.raises(sqlite3.DatabaseError):
            repository.load_chunks()
    else:
        assert repository.load_chunks() == []
    assert_released(repository, observed)
    assert repository.storage_initialized()
