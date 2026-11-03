"""Retryable ownership of interrupted SQLite initialization and descriptor close."""

import errno
import os
import sqlite3
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest

from app.repositories.semantic import RepositoryCleanupIncompleteError, SemanticRepository
from app.repositories.semantic_binding import BINDING_KEYS
from app.services.knowledge_spaces import SpaceError
from app.services.space_runtime import compose_space_runtime
from tests.test_semantic_resources import launch
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration
from tests.test_space_runtime_descriptors import observe_connections
from tests.test_space_runtime_namespace_shutdown import rows


def descriptor_open(descriptor):
    try:
        os.fstat(descriptor)
    except OSError as error:
        assert error.errno == errno.EBADF
        return False
    return True


def connection_open(connection):
    try:
        connection.in_transaction
    except sqlite3.ProgrammingError:
        return False
    return True


def test_reviewer_descriptor_release_failure_remains_owned(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    observed = observe_connections(repository, monkeypatch)
    original_close = os.close
    failures = []

    def fail_once(descriptor):
        if observed and observed[0][1] is not None and descriptor == observed[0][1][1] and not failures:
            failures.append(descriptor)
            raise OSError("injected release failure")
        return original_close(descriptor)

    monkeypatch.setattr(os, "close", fail_once)
    try:
        runtime.start()
        runtime.scheduler.wait(20)
        assert failures
        descriptor = failures[0]
        retained = bool(repository._initializations)
        monkeypatch.setattr(os, "close", original_close)
        incomplete = False
        try:
            runtime.shutdown()
        except RuntimeError:
            incomplete = True
        with pytest.raises(RuntimeError):
            runtime.shutdown()
        assert runtime._state == "stopping" and descriptor_open(descriptor)
        assert failures == [descriptor]
        # ADR 0010: restoring an opaque closer is not a no-effect receipt.
        # The fixture knows fail_once never dispatched close; release externally.
        original_close(descriptor)
        runtime.shutdown()
        live = descriptor_open(descriptor)
        print(dict(retained_after_failure=retained, first_shutdown_incomplete=incomplete,
                   final_state=runtime._state, descriptor_live=live))
        assert retained and incomplete and not live
        assert runtime._state == "stopped" and not repository._initializations
        repository.db_path.unlink(missing_ok=True)
    finally:
        monkeypatch.setattr(os, "close", original_close)
        for connection in tuple(repository._initializations):
            repository._close_connection(connection)
        for descriptor in failures:
            if descriptor_open(descriptor):
                original_close(descriptor)
        runtime.shutdown()


def test_reviewer_keyboard_interrupt_unwinds_initialization(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    validate = repository._validate_namespace
    captured = []

    def interrupted(*args, **kwargs):
        if repository._initializations:
            captured.extend(repository._initializations.items())
            raise KeyboardInterrupt()
        return validate(*args, **kwargs)

    monkeypatch.setattr(repository, "_validate_namespace", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            repository.get_metadata("index_state")
        monkeypatch.setattr(repository, "_validate_namespace", validate)
        assert len(captured) == 1
        connection, attempt = captured[0]
        runtime.shutdown()
        runtime.shutdown()
        locked = False
        with closing(sqlite3.connect(repository.db_path, timeout=0)) as probe:
            try:
                probe.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                locked = True
            else:
                probe.rollback()
        live, sqlite_live = descriptor_open(attempt[1]), connection_open(connection)
        print(dict(state=runtime._state, descriptor_live=live, connection_live=sqlite_live, writer_locked=locked))
        assert not live and not sqlite_live and not locked
        assert not repository._initializations
    finally:
        monkeypatch.setattr(repository, "_validate_namespace", validate)
        for connection in tuple(repository._initializations):
            repository._close_connection(connection)
        runtime.shutdown()


@pytest.mark.parametrize("operation", ["read", "default_commit", "named_commit"])
@pytest.mark.parametrize("failure", ["raise", "noop"])
def test_persistent_descriptor_failure_fences_work_and_shutdown(tmp_path, backend, monkeypatch, operation, failure):
    runtime = compose_space_runtime(configuration(tmp_path))
    repository = binding(runtime, "space-1" if operation == "named_commit" else "default")._owners.semantic.repository
    peer = binding(runtime, "default" if operation == "named_commit" else "space-1")._owners.semantic.repository
    observed = observe_connections(repository, monkeypatch)
    close = os.close
    calls = []
    enabled = True

    def failing(descriptor):
        if observed and descriptor == observed[0][1][1]:
            calls.append(descriptor)
            if enabled:
                if failure == "raise":
                    raise OSError("injected descriptor failure")
                return
        return close(descriptor)

    monkeypatch.setattr(os, "close", failing)
    try:
        with pytest.raises((OSError, RepositoryCleanupIncompleteError)):
            if operation == "read":
                repository.get_metadata("index_state")
            else:
                repository.prepare_index("signature")
        connection, original = observed[0]
        assert not connection_open(connection) and descriptor_open(original[1])
        assert repository._initializations[connection] is original
        assert len(calls) == 1
        # The unrelated owner can initialize, commit and release independently.
        peer.prepare_index("peer-signature")
        peer_rows = rows(peer.db_path)
        assert not peer._initializations
        for count in range(3):
            with pytest.raises(RuntimeError):
                runtime.shutdown()
            assert runtime._state == "stopping" and not runtime.scheduler._shutdown_complete
            assert not runtime.resource._closed and runtime.admission._stopped.is_set()
            assert repository._initializations[connection] is original
            assert descriptor_open(original[1])
            with pytest.raises(RepositoryCleanupIncompleteError):
                if operation == "named_commit":
                    repository.prepare_index("new")
                else:
                    repository.get_metadata("index_state")
            with pytest.raises(SpaceError):
                with runtime.admission.operation():
                    pytest.fail("shutdown reopened operation admission")
            assert len(observed) == 1 and len(calls) == 1
            assert rows(peer.db_path) == peer_rows
        enabled = False
        with pytest.raises(RuntimeError):
            runtime.shutdown()
        assert descriptor_open(original[1]) and len(calls) == 1
        # Only the fault fixture has proof that its callback did not close.
        close(original[1])
        runtime.shutdown()
        assert runtime._state == "stopped" and not repository._initializations
        assert not descriptor_open(original[1]) and len(calls) == 1
        assert rows(peer.db_path) == peer_rows
        if operation == "read":
            with closing(sqlite3.connect(repository.db_path)) as probe:
                assert not probe.execute("SELECT name FROM sqlite_master").fetchall()
        else:
            stored = dict(rows(repository.db_path)[0])
            assert stored["index_signature"] == "signature"
            if operation == "named_commit":
                assert tuple(stored[key] for key in BINDING_KEYS) == repository._namespace_binding.values
        repository.db_path.unlink()
    finally:
        enabled = False
        runtime.shutdown()


@pytest.mark.parametrize("recycle", [False, True, "same_file"])
def test_close_raising_after_release_never_closes_recycled_number(tmp_path, backend, monkeypatch, recycle):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    observed = observe_connections(repository, monkeypatch)
    unrelated_path = tmp_path / "unrelated"
    unrelated_path.write_bytes(b"must remain owned by the test")
    unrelated = os.open(unrelated_path, os.O_RDONLY)
    original_identity = os.fstat(unrelated)
    close = os.close
    calls = []
    reused = None

    def released_then_raised(descriptor):
        nonlocal reused
        if observed and descriptor == observed[0][1][1]:
            calls.append(descriptor)
            close(descriptor)
            if recycle == "same_file":
                other = os.open(repository.db_path, os.O_WRONLY)
                reused = os.dup2(other, descriptor)
                if other != descriptor:
                    close(other)
            elif recycle:
                reused = os.dup2(unrelated, descriptor)
            raise OSError("raised after releasing original handle")
        return close(descriptor)

    try:
        monkeypatch.setattr(os, "close", released_then_raised)
        with pytest.raises(OSError):
            repository.get_metadata("index_state")
        if recycle == "same_file":
            assert repository._initializations
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    runtime.shutdown()
                os.fstat(reused)
                assert runtime._state == "stopping" and len(calls) == 1
            close(reused)  # Fixture-owned replacement; never the old allocation.
            reused = None
        assert not repository._initializations or recycle == "same_file"
        runtime.shutdown()
        runtime.shutdown()
        assert len(calls) == 1 and runtime._state == "stopped"
        assert not connection_open(observed[0][0])
        if recycle and recycle != "same_file":
            assert reused == observed[0][1][1]
            info = os.fstat(reused)
            expected = observed[0][1][2] if recycle == "same_file" else original_identity
            assert (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino)
        else:
            assert not descriptor_open(observed[0][1][1])
    finally:
        monkeypatch.setattr(os, "close", close)
        if reused is not None:
            close(reused)
        close(unrelated)
        runtime.shutdown()


@pytest.mark.parametrize("api", ["runtime", "repository"])
def test_concurrent_cleanup_serializes_the_same_descriptor(tmp_path, backend, monkeypatch, api):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    observed = observe_connections(repository, monkeypatch)
    prepare = repository._prepare_descriptor
    calls = []
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()

    def failing_then_blocked(descriptor):
        if observed and descriptor == observed[0][1][1]:
            calls.append(descriptor)
            if len(calls) == 1:
                raise OSError("initial release failed")
            entered.set()
            assert release.wait(20)
        return prepare(descriptor)

    # A read-only pre-dispatch failure is positively safe to retry (ADR 0010).
    monkeypatch.setattr(repository, "_prepare_descriptor", failing_then_blocked)
    try:
        with pytest.raises(OSError):
            repository.get_metadata("index_state")
        with pytest.raises(RuntimeError):
            runtime.shutdown()
        first = launch(runtime.shutdown if api == "runtime" else repository.close_initializations)
        assert entered.wait(20)

        def second():
            second_started.set()
            return runtime.shutdown() if api == "runtime" else repository.close_initializations()

        other = launch(second)
        assert second_started.wait(20)
        release.set()
        first.result(20)
        other.result(20)
        runtime.shutdown()
        assert len(calls) == 2 and not descriptor_open(observed[0][1][1])
        assert runtime._state == "stopped" and not repository._initializations
    finally:
        release.set()
        monkeypatch.setattr(repository, "_prepare_descriptor", prepare)
        runtime.shutdown()


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("rollback_fails", [False, True])
@pytest.mark.parametrize("close_failure", ["none", "before", "after", "noop"])
def test_interrupted_initialization_preserves_original_and_retryable_connection(
    tmp_path, backend, monkeypatch, interruption, rollback_fails, close_failure,
):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    state = SimpleNamespace(target=None, rollbacks=0, closes=0, fail=True, attempt=None)
    connect, validate = sqlite3.connect, repository._validate_namespace

    class Connection(sqlite3.Connection):
        def rollback(self):
            if self is state.target:
                state.rollbacks += 1
                if rollback_fails and state.fail:
                    raise OSError("rollback failed")
            return super().rollback()

        def close(self):
            if self is state.target:
                state.closes += 1
                if close_failure == "before" and state.fail:
                    raise OSError("connection close failed")
                if close_failure == "noop" and state.fail:
                    return
            super().close()
            if self is state.target and close_failure == "after" and state.fail:
                raise OSError("connection close raised after release")

    def actual_connection(*args, **kwargs):
        return connect(*args, factory=Connection, **kwargs)

    def interrupted(*args, **kwargs):
        if repository._initializations:
            state.target, state.attempt = next(iter(repository._initializations.items()))
            raise interruption()
        return validate(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", actual_connection)
    monkeypatch.setattr(repository, "_validate_namespace", interrupted)
    try:
        # Real worker-thread initialization; main-thread shutdown must be able
        # to dispose this exact retained connection after a failed close.
        future = launch(lambda: repository.get_metadata("index_state"))
        with pytest.raises(interruption):
            future.result(20)
        monkeypatch.setattr(repository, "_validate_namespace", validate)
        assert state.rollbacks == state.closes == 1
        assert state.target is not None and state.attempt is not None
        if close_failure in {"before", "noop"}:
            assert connection_open(state.target) and repository._initializations[state.target] is state.attempt
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    runtime.shutdown()
                assert runtime._state == "stopping" and not runtime.resource._closed
            assert state.closes == 2  # No repeated cleanup in the first shutdown.
            state.fail = False
        runtime.shutdown()
        assert runtime._state == "stopped" and not repository._initializations
        assert not connection_open(state.target) and not descriptor_open(state.attempt[1])
        with closing(connect(repository.db_path, timeout=0)) as probe:
            probe.execute("BEGIN IMMEDIATE")
            assert not probe.execute("SELECT name FROM sqlite_master").fetchall()
            probe.rollback()
        repository.db_path.unlink(missing_ok=True)
    finally:
        state.fail = False
        monkeypatch.setattr(repository, "_validate_namespace", validate)
        runtime.shutdown()


def test_legacy_initialization_interrupt_still_closes_real_connection(tmp_path, monkeypatch):
    repository = SemanticRepository(tmp_path / "legacy.sqlite3")
    captured = []
    connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def executescript(self, script):
            self.execute("BEGIN IMMEDIATE")
            captured.append(self)
            raise KeyboardInterrupt()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
    with pytest.raises(KeyboardInterrupt):
        repository.get_metadata("index_state")
    assert len(captured) == 1 and not connection_open(captured[0])
    assert not repository._initializations
    with closing(connect(repository.db_path, timeout=0)) as probe:
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()


@pytest.mark.parametrize("stage", ["stat", "connect", "namespace"])
@pytest.mark.parametrize("release_fails", [False, True])
def test_interrupt_before_return_always_has_reachable_original_owner(
    tmp_path, backend, monkeypatch, stage, release_fails,
):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    stat, close, connect, validate = os.fstat, os.close, sqlite3.connect, repository._validate_namespace
    state = SimpleNamespace(attempt=None, interrupted=False, fail_release=release_fails)

    def interrupt():
        state.attempt = next(iter(repository._initializations.values()))
        state.interrupted = True
        raise KeyboardInterrupt()

    def checked_stat(descriptor):
        if stage == "stat" and not state.interrupted and repository._initializations:
            interrupt()
        return stat(descriptor)

    def checked_connect(*args, **kwargs):
        if stage == "connect" and not state.interrupted and repository._initializations:
            interrupt()
        return connect(*args, **kwargs)

    def checked_validate(*args, **kwargs):
        if stage == "namespace" and not state.interrupted and repository._initializations:
            interrupt()
        return validate(*args, **kwargs)

    def checked_close(descriptor):
        if state.attempt is not None and descriptor == state.attempt[1] and state.fail_release:
            raise OSError("descriptor cleanup failed during interrupt")
        return close(descriptor)

    monkeypatch.setattr(os, "fstat", checked_stat)
    monkeypatch.setattr(sqlite3, "connect", checked_connect)
    monkeypatch.setattr(repository, "_validate_namespace", checked_validate)
    monkeypatch.setattr(os, "close", checked_close)
    try:
        with pytest.raises(KeyboardInterrupt):
            repository.get_metadata("index_state")
        assert state.attempt is not None
        if release_fails:
            assert state.attempt in repository._initializations.values()
            assert descriptor_open(state.attempt[1])
            with pytest.raises(RuntimeError):
                runtime.shutdown()
            with pytest.raises(RuntimeError):
                runtime.shutdown()
            assert runtime._state == "stopping"
        state.fail_release = False
        if release_fails:
            with pytest.raises(RuntimeError):
                runtime.shutdown()
            assert descriptor_open(state.attempt[1])
            close(state.attempt[1])  # Explicit fixture disposal of its known held FD.
        runtime.shutdown()
        assert runtime._state == "stopped" and not repository._initializations
        assert not descriptor_open(state.attempt[1])
    finally:
        state.fail_release = False
        runtime.shutdown()


def test_admitted_cleanup_after_scheduler_completion_is_verified_before_stopped(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1)).start()
    runtime.scheduler.wait(20)
    owner = binding(runtime)._owners.semantic
    repository = owner.repository
    before = rows(repository.db_path)
    entered, owner_closed = threading.Event(), threading.Event()
    connect, prepare, close_owner = sqlite3.connect, owner.prepare_federation_candidates, owner.close_synchronization
    state = SimpleNamespace(target=None, fail=True, closes=0)

    class Connection(sqlite3.Connection):
        def close(self):
            if self is state.target:
                state.closes += 1
                if state.fail:
                    raise OSError("late admitted connection cleanup failure")
            return super().close()

    def tracked_connection(*args, **kwargs):
        connection = connect(*args, factory=Connection, **kwargs)
        if kwargs.get("uri") and "mode=rw" in str(args[0]):
            state.target = connection
        return connection

    def admitted(*args, **kwargs):
        entered.set()
        assert owner_closed.wait(20)
        repository.get_metadata("index_state")
        return prepare(*args, **kwargs)

    def closed(*args, **kwargs):
        result = close_owner(*args, **kwargs)
        owner_closed.set()
        return result

    monkeypatch.setattr(sqlite3, "connect", tracked_connection)
    monkeypatch.setattr(owner, "prepare_federation_candidates", admitted)
    monkeypatch.setattr(owner, "close_synchronization", closed)
    try:
        operation = launch(lambda: runtime.operations.scoped_semantic_search("notes"))
        assert entered.wait(20)
        with pytest.raises((OSError, RuntimeError)):
            runtime.shutdown()
        with pytest.raises((OSError, SpaceError)):
            operation.result(20)
        assert runtime.scheduler._shutdown_complete  # E4's cleanup was already complete.
        assert runtime._state == "stopping" and not runtime.resource._closed
        assert state.target in repository._initializations and connection_open(state.target)
        original = repository._initializations[state.target]
        state.fail = False
        runtime.shutdown()
        assert not connection_open(state.target) and not repository._initializations
        assert original.connection is state.target and state.closes == 3
        assert runtime._state == "stopped" and rows(repository.db_path) == before
    finally:
        state.fail = False
        owner_closed.set()
        runtime.shutdown()


def test_permission_change_is_not_mistaken_for_descriptor_release(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    observed = observe_connections(repository, monkeypatch)
    prepare = repository._prepare_descriptor
    failed = False

    def fail_once(descriptor):
        nonlocal failed
        if observed and descriptor == observed[0][1][1] and not failed:
            failed = True
            raise OSError("initial descriptor release failed")
        return prepare(descriptor)

    monkeypatch.setattr(repository, "_prepare_descriptor", fail_once)
    try:
        with pytest.raises(OSError):
            repository.get_metadata("index_state")
        descriptor = observed[0][1][1]
        assert descriptor_open(descriptor)
        repository.db_path.chmod(0o400)
        with pytest.raises(RuntimeError):
            runtime.shutdown()
        runtime.shutdown()
        assert not descriptor_open(descriptor) and not repository._initializations
        assert runtime._state == "stopped"
    finally:
        if repository.db_path.exists():
            repository.db_path.chmod(0o600)
        runtime.shutdown()


def test_interrupt_after_descriptor_conversion_closes_the_descriptor_once(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1))
    repository = binding(runtime)._owners.semantic.repository
    allocate, close = repository._allocate_descriptor, os.close
    captured, closed = [], []

    def interrupted(attempt):
        allocate(attempt)
        captured.append(attempt)
        if os.name == "nt":
            import msvcrt

            # The native-to-CRT transfer succeeded, but its bookkeeping clear
            # was interrupted. The descriptor is now the sole resource owner.
            attempt.native_handle = msvcrt.get_osfhandle(attempt.descriptor)
        raise KeyboardInterrupt()

    def tracked(descriptor):
        closed.append(descriptor)
        return close(descriptor)

    monkeypatch.setattr(repository, "_allocate_descriptor", interrupted)
    monkeypatch.setattr(os, "close", tracked)
    try:
        with pytest.raises(KeyboardInterrupt):
            repository.get_metadata("index_state")
        assert len(captured) == 1
        descriptor = captured[0].descriptor
        assert closed.count(descriptor) == 1 and not descriptor_open(descriptor)
        assert not repository._initializations
        runtime.shutdown()
        assert runtime._state == "stopped"
    finally:
        runtime.shutdown()
