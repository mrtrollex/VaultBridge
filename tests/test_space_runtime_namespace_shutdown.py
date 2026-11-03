"""Real SQLite namespace adoption and interrupted executor disposal regressions."""

import hashlib
import os
import shutil
import sqlite3
import threading
from contextlib import closing

import pytest

from app.repositories.semantic_binding import BINDING_KEYS, IndexBindingUnavailableError
from app.services.knowledge_spaces import ReadScope, SpaceError, SpaceId
from app.services.semantic_search import SemanticSearchService
from app.services.space_runtime import compose_space_runtime
from tests.test_semantic_resources import launch
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration


def rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return tuple(tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1"))
                     for table in ("meta", "notes", "chunks"))


def copied_foreign_runtime(tmp_path):
    source = compose_space_runtime(configuration(tmp_path / "source"))
    foreign = binding(source, "space-1")._owners
    authored = foreign.vault.vault_root / "same.md"
    authored.write_bytes(b"FOREIGN_CONTENT" + b"x" * 20)
    source.start()
    source.scheduler.wait(20)
    stamp = authored.stat().st_mtime_ns
    source.shutdown()
    original = foreign.semantic.db_path.read_bytes()
    runtime = compose_space_runtime(configuration(tmp_path / "target"))
    target = binding(runtime)._owners
    note = target.vault.vault_root / "same.md"
    note.write_bytes(b"DEFAULT_CONTENT" + b"x" * 20)
    os.utime(note, ns=(stamp, stamp))
    assert note.stat().st_mtime_ns == stamp and note.stat().st_size == authored.stat().st_size
    database = target.semantic.db_path
    database.parent.mkdir(parents=True)
    shutil.copyfile(foreign.semantic.db_path, database)
    return runtime, foreign.semantic.db_path, original


def test_default_rejects_copied_foreign_index_matching_size_and_mtime(tmp_path, backend):
    runtime, source, original = copied_foreign_runtime(tmp_path)
    target = binding(runtime)._owners
    runtime.start()
    runtime.scheduler.wait(20)
    try:
        leaked = False
        try:
            result = runtime.operations.scoped_semantic_search("CONTENT", ReadScope((SpaceId("default"),)))
        except SpaceError as error:
            assert error.category == "semantic_unavailable"
        else:
            leaked = any("FOREIGN_CONTENT" in row.snippet for row in result.items)
        status = target.scheduler.status()
        print(dict(default_paused=status.paused, progress=str(status.progress), default_leaked_foreign=leaked))
        assert status.paused and status.requires_full_sync and not leaked
        assert target.semantic.db_path.read_bytes() == original and source.read_bytes() == original
        peer = binding(runtime, "space-1")._owners
        before = rows(peer.semantic.db_path)
        assert not peer.scheduler.status().paused and peer.semantic.repository.load_chunks()
        assert target.scheduler.retry()
        runtime.scheduler.wait(20)
        assert target.scheduler.status().paused and target.scheduler.status().requires_full_sync
        assert rows(peer.semantic.db_path) == before
    finally:
        runtime.shutdown()


def test_executor_shutdown_interruption_retries_same_live_executor(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    scheduler = runtime.scheduler
    executor = scheduler._executor
    shutdown = executor.shutdown
    attempts = []

    def interrupted(*args, **kwargs):
        attempts.append(executor)
        if len(attempts) == 1:
            raise KeyboardInterrupt()
        return shutdown(*args, **kwargs)

    monkeypatch.setattr(executor, "shutdown", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.shutdown()
        first = dict(state=runtime._state, complete=scheduler._shutdown_complete,
                     stopped=scheduler._stopped.is_set(), resource_closed=runtime.resource._closed)
        runtime.shutdown()
        alive = [thread.is_alive() for thread in executor._threads]
        print(dict(first=first, final=runtime._state, executor_shutdown=executor._shutdown,
                   workers_alive=alive, attempts=len(attempts)))
        assert executor._shutdown and not any(alive)
        assert first == dict(state="stopping", complete=False, stopped=False, resource_closed=False)
        assert scheduler._executor is executor and scheduler._future.done()
        assert scheduler._shutdown_complete and scheduler._stopped.is_set() and runtime._state == "stopped"
        runtime.shutdown()
        assert len(attempts) == 2 and backend.cleaned == 1
    finally:
        shutdown(wait=True, cancel_futures=True)
        runtime.shutdown()


@pytest.mark.parametrize("mode", [
    "legacy", "default", "foreign", "foreign_bad_signature", "version_only", "id_only", "root_only",
    "missing_version", "missing_id", "missing_root", "wrong_root", "unknown_version", "null", "blob", "duplicate",
])
def test_default_namespace_rules_across_restart_and_reopens(tmp_path, backend, mode):
    settings = configuration(tmp_path)
    first = compose_space_runtime(settings).start()
    first.scheduler.wait(20)
    owner = binding(first)._owners
    database, root = owner.semantic.db_path, owner.vault.vault_root
    first.shutdown()
    expected = ["1", "default", hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()]
    values = dict(zip(BINDING_KEYS, expected)) if mode != "legacy" else {}
    if mode.startswith("foreign"):
        values[BINDING_KEYS[1]] = "space-1"
    if mode.endswith("_only"):
        key = {"version_only": BINDING_KEYS[0], "id_only": BINDING_KEYS[1], "root_only": BINDING_KEYS[2]}[mode]
        values = {key: values[key]}
    if mode.startswith("missing_"):
        del values[BINDING_KEYS[{"missing_version": 0, "missing_id": 1, "missing_root": 2}[mode]]]
    if mode == "wrong_root":
        values[BINDING_KEYS[2]] = "f" * 64
    if mode == "unknown_version":
        values[BINDING_KEYS[0]] = "2"
    if mode in {"null", "blob"}:
        values[BINDING_KEYS[1]] = None if mode == "null" else b"default"
    with closing(sqlite3.connect(database)) as connection, connection:
        if mode == "null":
            connection.execute("ALTER TABLE meta RENAME TO old_meta")
            connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            connection.execute("INSERT INTO meta SELECT * FROM old_meta")
            connection.execute("DROP TABLE old_meta")
        connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)", values.items())
        if mode == "foreign_bad_signature":
            connection.execute("UPDATE meta SET value='incompatible' WHERE key='index_signature'")
        if mode == "duplicate":
            connection.execute("ALTER TABLE meta RENAME TO old_meta")
            connection.execute("CREATE TABLE meta(key TEXT,value TEXT)")
            connection.execute("INSERT INTO meta SELECT * FROM old_meta")
            connection.execute("DROP TABLE old_meta")
            connection.execute("INSERT INTO meta VALUES(?,?)", (BINDING_KEYS[1], "space-1"))
    before = database.read_bytes()
    runtime = compose_space_runtime(settings).start()
    runtime.scheduler.wait(20)
    target = binding(runtime)._owners
    try:
        if mode in {"legacy", "default"}:
            assert not target.scheduler.status().paused
            assert runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("default"),))).items
            with closing(sqlite3.connect(database)) as connection:
                found = dict(connection.execute("SELECT key,value FROM meta"))
            assert {key: found[key] for key in BINDING_KEYS if key in found} == values
        else:
            assert target.scheduler.status().paused and target.scheduler.status().requires_full_sync
            with pytest.raises(SpaceError, match="semantic_unavailable"):
                runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("default"),)))
            repository = target.semantic.repository
            for operation in (
                repository.load_chunks,
                lambda: repository.get_metadata("index_signature"),
                lambda: repository.prepare_index("must-not-reset"),
                lambda: repository.reset_index("must-not-reset", "uninitialized"),
                lambda: repository.set_metadata("index_state", "ready"),
            ):
                with pytest.raises(IndexBindingUnavailableError):
                    operation()
            assert target.scheduler.retry()
            runtime.scheduler.wait(20)
            assert target.scheduler.status().paused and database.read_bytes() == before
        assert not binding(runtime, "space-1")._owners.scheduler.status().paused
        assert runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("space-1"),))).items
    finally:
        runtime.shutdown()


def test_namespace_validation_rechecks_in_place_metadata_changes(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    owner = binding(runtime)._owners
    peer = binding(runtime, "space-1")._owners
    peer_rows = rows(peer.semantic.db_path)
    database = owner.semantic.db_path
    values = ["1", "space-1", "0" * 64]
    try:
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)", zip(BINDING_KEYS, values))
        original = database.read_bytes()
        with pytest.raises(SpaceError, match="semantic_unavailable"):
            runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("default"),)))
        assert owner.scheduler.request_full_sync()
        runtime.scheduler.wait(20)
        assert owner.scheduler.status().paused and owner.scheduler.status().requires_full_sync
        assert database.read_bytes() == original and rows(peer.semantic.db_path) == peer_rows
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("fault", [KeyboardInterrupt, OSError])
@pytest.mark.parametrize("stage,failures", [("before", 1), ("before", 3), ("after", 1)])
def test_executor_disposal_failure_keeps_completion_unpublished(tmp_path, backend, monkeypatch, fault, stage, failures):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    scheduler, executor = runtime.scheduler, runtime.scheduler._executor
    shutdown, attempts = executor.shutdown, []
    original_threads = set(executor._threads)

    def interrupted(*args, **kwargs):
        attempts.append(executor)
        if stage == "after" or len(attempts) > failures:
            shutdown(*args, **kwargs)
        if len(attempts) <= failures:
            raise fault()

    monkeypatch.setattr(executor, "shutdown", interrupted)
    try:
        for count in range(1, failures + 1):
            with pytest.raises(fault):
                runtime.shutdown()
            assert len(attempts) == count and runtime._state == "stopping"
            assert scheduler._executor is executor and not scheduler._shutdown_complete
            assert not scheduler._stopped.is_set() and not runtime.resource._closed
            assert all(entry.cancel.is_set() for entry in scheduler._owners.values())
            assert not binding(runtime)._owners.scheduler.retry()
            assert not binding(runtime)._owners.scheduler.request_full_sync()
            with pytest.raises(SpaceError, match="operation_cancelled"):
                runtime.operations.scoped_list()
        def forbidden(*args, **kwargs):
            pytest.fail("executor retry allocated another executor or reran synchronization")
        with monkeypatch.context() as patch:
            patch.setattr("app.services.semantic_scheduler.ThreadPoolExecutor", forbidden)
            patch.setattr(SemanticSearchService, "sync_step", forbidden)
            runtime.shutdown()
        assert executor._shutdown and not any(thread.is_alive() for thread in original_threads)
        assert set(executor._threads) == original_threads and len(attempts) == failures + 1
        assert scheduler._future.done() and scheduler._stopped.is_set() and scheduler._shutdown_complete
        assert runtime._state == "stopped" and len(backend.instances) == 1 and backend.cleaned == 1
        runtime.shutdown()
        assert len(attempts) == failures + 1
    finally:
        shutdown(wait=True, cancel_futures=True)
        runtime.shutdown()


def test_executor_noop_cannot_publish_terminal_shutdown(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    executor = runtime.scheduler._executor
    shutdown = executor.shutdown
    try:
        with monkeypatch.context() as patch:
            patch.setattr(executor, "shutdown", lambda **kwargs: None)
            with pytest.raises(RuntimeError, match="executor shutdown is incomplete"):
                runtime.shutdown()
        assert runtime._state == "stopping" and not runtime.scheduler._stopped.is_set()
        runtime.shutdown()
        assert runtime._state == "stopped" and not any(thread.is_alive() for thread in executor._threads)
    finally:
        shutdown(wait=True, cancel_futures=True)
        runtime.shutdown()


@pytest.mark.parametrize("layer", ["runtime", "scheduler"])
def test_concurrent_executor_shutdown_retries_are_serialized(tmp_path, backend, monkeypatch, layer):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    executor, scheduler = runtime.scheduler._executor, runtime.scheduler
    shutdown = executor.shutdown
    entered, resume = threading.Event(), threading.Event()
    attempts, active = [], [0, 0]
    gate = threading.Barrier(5)
    runtime.admission.begin_shutdown()
    close = runtime.shutdown if layer == "runtime" else scheduler.shutdown

    def interrupted(*args, **kwargs):
        active[0] += 1
        active[1] = max(active)
        try:
            attempts.append(executor)
            if len(attempts) == 1:
                entered.set()
                assert resume.wait(20)
                raise KeyboardInterrupt()
            return shutdown(*args, **kwargs)
        finally:
            active[0] -= 1

    def caller():
        gate.wait(10)
        close()

    monkeypatch.setattr(executor, "shutdown", interrupted)
    callers = [launch(caller) for _ in range(4)]
    try:
        gate.wait(10)
        assert entered.wait(10)
        assert not scheduler._shutdown_complete and not scheduler._stopped.is_set()
        resume.set()
        interrupted_callers = 0
        for call in callers:
            try:
                call.result(20)
            except KeyboardInterrupt:
                interrupted_callers += 1
        assert interrupted_callers == 1 and len(attempts) == 2 and active[1] == 1
        runtime.shutdown()
        assert runtime._state == "stopped" and scheduler._stopped.is_set()
        assert not any(thread.is_alive() for thread in executor._threads)
        assert scheduler._executor is executor and backend.cleaned == 1
    finally:
        resume.set()
        shutdown(wait=True, cancel_futures=True)
        runtime.shutdown()


def test_interrupted_executor_retry_drains_active_embedding_boundary(tmp_path, backend, monkeypatch):
    backend.release = threading.Event()
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    assert backend.entered.wait(10)
    executor = runtime.scheduler._executor
    shutdown = executor.shutdown
    attempts = []
    retry_entered = threading.Event()

    def interrupted(*args, **kwargs):
        attempts.append(executor)
        if len(attempts) == 1:
            raise KeyboardInterrupt()
        retry_entered.set()
        return shutdown(*args, **kwargs)

    monkeypatch.setattr(executor, "shutdown", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.shutdown()
        assert runtime._state == "stopping" and not runtime.scheduler._stopped.is_set()
        assert runtime.resource._busy and runtime.resource._pins and not runtime.resource._closed
        calls = len(backend.calls)
        closing = launch(runtime.shutdown)
        assert retry_entered.wait(10)
        assert not closing.done()
        backend.release.set()
        closing.result(20)
        assert runtime._state == "stopped" and len(backend.calls) == calls
        assert len(attempts) == 2 and runtime.resource._pins == 0 and not runtime.resource._busy
        assert not any(thread.is_alive() for thread in executor._threads)
        assert all(entry.service._active_sync is None for entry in runtime.scheduler._owners.values())
    finally:
        backend.release.set()
        shutdown(wait=True, cancel_futures=True)
        runtime.shutdown()


def test_default_namespace_is_checked_in_live_snapshot_after_screening(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    database = binding(runtime)._owners.semantic.db_path
    connect = sqlite3.connect
    injected = []

    def alter_before_live_open(target, *args, **kwargs):
        if str(target) == f"{database.absolute().as_uri()}?mode=ro" and not injected:
            injected.append(True)
            with closing(connect(database)) as connection, connection:
                connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)",
                                       zip(BINDING_KEYS, ["1", "space-1", "0" * 64]))
        return connect(target, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr("app.repositories.semantic_reads.sqlite3.connect", alter_before_live_open)
            with pytest.raises(SpaceError, match="semantic_unavailable"):
                runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("default"),)))
        assert injected
        with closing(connect(database)) as connection:
            actual = connection.execute("SELECT value FROM meta WHERE key=?", (BINDING_KEYS[1],)).fetchone()
            assert actual == ("space-1",)
    finally:
        runtime.shutdown()


def test_default_namespace_is_revalidated_before_content_writes_and_commit(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    before = rows(repository.db_path)
    try:
        for mutate in (False, True):
            with pytest.raises(IndexBindingUnavailableError):
                with repository.transaction() as session:
                    session._connection.executemany("INSERT INTO meta(key,value) VALUES(?,?)",
                                                    zip(BINDING_KEYS, ["1", "space-1", "0" * 64]))
                    if mutate:
                        session.delete_note("same.md")
            assert rows(repository.db_path) == before
    finally:
        runtime.shutdown()
