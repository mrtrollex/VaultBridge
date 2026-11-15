"""A closed read-only WAL connection can leave SQLite coordination files."""

import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from queue import Empty, Queue
from types import SimpleNamespace

import pytest

from app.repositories.semantic_binding import IndexBindingUnavailableError
from app.services.space_runtime import compose_space_runtime
from tests.test_public_space_composition import environment, frame
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration, scope


def sidecars(path):
    return [Path(str(path) + suffix) for suffix in ("-wal", "-shm")]


def test_stopped_runtime_is_immutable_readable_after_semantic_retrieval(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path))
    repository = binding(runtime)._owners.semantic.repository
    try:
        runtime.start()
        runtime.scheduler.wait(20)
        result = runtime.operations.scoped_semantic_search("Independent notes", scope(runtime))
        assert result.state == "complete" and len(result.items) == 2
        # Actual readers have closed; leftover files alone are not an ownership leak.
        assert not repository._reader_connections._leases
        assert all(path.exists() for path in sidecars(repository.db_path))
        before = repository.load_chunks_read_only()
        runtime.shutdown()
        assert runtime._state == "stopped"
        assert not any(path.exists() for path in sidecars(repository.db_path))
        assert repository.read_immutable_status().index_state == "ready"
        with closing(sqlite3.connect(repository.db_path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            rows = db.execute(
                "SELECT path, chunk_index, heading, content, embedding, dimensions FROM chunks"
            ).fetchall()
            assert rows == [
                (c.path, c.chunk_index, c.heading, c.content, c.embedding, c.dimensions) for c in before
            ]
    finally:
        runtime.shutdown()


@contextmanager
def stdio_session(command, *, env=None, response_timeout=20, exit_timeout=20):
    """Reap the child before joining any pipe reader, including timed-out I/O."""
    process = subprocess.Popen(command, env=env,
        cwd=Path(__file__).parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    condition, events, stderr, workers = threading.Condition(), [], [], []
    logs_closed = threading.Event()

    def logs():
        try:
            for line in process.stderr:
                stderr.append(line)
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                with condition:
                    events.append(event)
                    condition.notify_all()
        finally:
            with condition:
                logs_closed.set()
                condition.notify_all()

    reader = threading.Thread(target=logs, daemon=True)

    def wait_for_events(predicate, timeout=20):
        with condition:
            if not condition.wait_for(lambda: predicate(events) or logs_closed.is_set(), timeout):
                raise TimeoutError("stdio startup events missing")
            if not predicate(events):
                raise EOFError("stdio exited before startup: " + b"".join(stderr).decode(errors="replace"))

    def request(payload):
        results, done = Queue(), threading.Event()

        def exchange():
            try:
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                process.stdin.write(data + b"\n")
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise EOFError("stdio response missing")
                result = True, json.loads(line)
            except BaseException as error:
                result = False, error
            done.set()  # No pipe I/O remains once the result is published.
            results.put(result)

        worker = threading.Thread(target=exchange, daemon=True)
        workers.append((worker, done))
        worker.start()
        try:
            success, value = results.get(timeout=response_timeout)
        except Empty:
            raise TimeoutError("stdio response deadline exceeded") from None
        if not success:
            raise value
        return value

    session = SimpleNamespace(process=process, request=request, wait_for_events=wait_for_events,
                              stderr=stderr, reader=reader, workers=workers)
    try:
        reader.start()
        yield session
    finally:
        try:
            if any(not done.is_set() for _, done in workers):
                if process.poll() is None:
                    process.kill()  # A blocked writer must not delay stdin.close().
            else:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass  # The child has already closed its read end.
        finally:
            try:
                process.wait(exit_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
            for worker, _ in workers:
                worker.join(5)
                assert not worker.is_alive(), "stdio I/O worker did not stop after child exit"
            if reader.ident is not None:
                reader.join(5)
                assert not reader.is_alive(), "stdio stderr reader did not stop after child exit"
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()


@pytest.mark.parametrize("protocol", ["legacy", "2026-07-28"])
def test_named_stdio_eof_leaves_default_ready_for_immutable_reads(tmp_path, protocol):
    settings = configuration(tmp_path)
    # Real module/stdio/lifespan; only the CPU embedding backend is synthetic.
    bootstrap = (
        "import pytest, runpy; from tests.test_space_runtime import backend; "
        "backend.__wrapped__(pytest.MonkeyPatch()); "
        "runpy.run_module('app.mcp_server', run_name='__main__', alter_sys=True)"
    )
    with stdio_session([sys.executable, "-c", bootstrap], env=environment(settings)) as session:
        session.wait_for_events(lambda events: sum(e.get("event") == "semantic_sync_completed" for e in events) == 2)
        arguments = {"spaces": ["default", "space-1"], "text": "Independent notes", "min_score": 0}
        payload = frame("related_notes", arguments)
        if protocol == "legacy":
            initialized = session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "EOF regression", "version": "1"},
            }})
            assert initialized["result"]["protocolVersion"] == "2025-03-26"
            notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            request = {"jsonrpc": "2.0", "id": 101, "method": "tools/call",
                       "params": {"name": "related_notes", "arguments": arguments}}
            payload = (json.dumps(notification) + "\n" + json.dumps(request)).encode()
        response = session.request(payload)
        result = response["result"]["structuredContent"]
        assert result["state"] == "complete"
        assert {r["identity"]["space_id"] for r in result["items"]} == {"default", "space-1"}
        assert all(c["semantic_index_basis"] == "compatible_ready" for c in result["coverage"])
    assert session.process.returncode == 0, b"".join(session.stderr).decode(errors="replace")
    for sid in ("default", "space-1"):
        database = tmp_path / (sid + "-data") / "semantic-index.sqlite3"
        assert not any(p.exists() for p in sidecars(database))
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            assert db.execute("SELECT value FROM meta WHERE key='index_state'").fetchone() == ("ready",)
            assert db.execute("PRAGMA quick_check").fetchall() == [("ok",)]


@pytest.mark.parametrize("mode,error_type", [
    ("missing", TimeoutError), ("delayed", TimeoutError), ("incomplete", TimeoutError),
    ("malformed", json.JSONDecodeError), ("eof", EOFError),
])
def test_stdio_response_failure_reaps_child_and_pipe_readers(mode, error_type):
    child = """
import json, sys, threading
print(json.dumps({'event': 'ready'}), file=sys.stderr, flush=True)
sys.stdin.buffer.readline()
print(json.dumps({'event': 'request_received'}), file=sys.stderr, flush=True)
mode = sys.argv[1]
if mode == 'missing':
    threading.Event().wait()
elif mode == 'delayed':
    sys.stdin.buffer.readline()  # Deterministic release barrier, never sent before the deadline.
    print(json.dumps({'result': 'late'}), flush=True)
elif mode == 'incomplete':
    sys.stdout.write('{"result":'); sys.stdout.flush()
    threading.Event().wait()
elif mode == 'malformed':
    print('{not-json}', flush=True)
    sys.stdin.buffer.read()
else:
    sys.exit(0)  # Close every inherited stdout handle, including Windows standard handles.
"""
    with pytest.raises(error_type):
        with stdio_session([sys.executable, "-u", "-c", child, mode],
                           response_timeout=.5, exit_timeout=.5) as session:
            session.wait_for_events(lambda events: any(e.get("event") == "ready" for e in events))
            session.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
    assert session.process.poll() is not None
    assert not session.reader.is_alive() and all(not t.is_alive() for t, _ in session.workers)
    assert all(p.closed for p in (session.process.stdin, session.process.stdout, session.process.stderr))
    assert any(json.loads(line).get("event") == "request_received" for line in session.stderr)


def test_stdio_missing_startup_event_still_reaps_child():
    child = "import threading; threading.Event().wait()"
    with pytest.raises(TimeoutError, match="startup"):
        with stdio_session([sys.executable, "-c", child], exit_timeout=.5) as session:
            session.wait_for_events(lambda events: bool(events), timeout=.5)
    assert session.process.poll() is not None and not session.reader.is_alive()


def test_stdio_blocked_stdin_write_is_bounded_before_pipe_cleanup():
    child = (
        "import sys, threading; print('{\"event\":\"ready\"}', file=sys.stderr, flush=True); "
        "threading.Event().wait()"
    )
    with pytest.raises(TimeoutError, match="response deadline"):
        with stdio_session([sys.executable, "-u", "-c", child], response_timeout=.5, exit_timeout=.5) as session:
            session.wait_for_events(lambda events: bool(events))
            session.request(b"x" * (2 * 1024 * 1024))  # Fill the pipe while the controlled child never reads stdin.
    assert session.process.poll() is not None
    assert not session.reader.is_alive() and all(not t.is_alive() for t, _ in session.workers)


@pytest.mark.parametrize("released", [False, True])
def test_offline_cleanup_close_failure_retains_exact_connection_for_retry(tmp_path, backend, monkeypatch, released):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    runtime.operations.scoped_semantic_search("Independent notes", scope(runtime))
    connect = sqlite3.connect
    allocated, attempts = [], []
    failing = True

    class Connection(sqlite3.Connection):
        def close(self):
            attempts.append(self)
            if not failing or released:
                super().close()
            if failing:
                raise OSError("injected final SQLite close failure")

    def tracked(database, *args, **kwargs):
        if str(database).endswith("?mode=rw"):
            connection = connect(database, *args, factory=Connection, **kwargs)
            allocated.append(connection)
            return connection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    try:
        with pytest.raises(OSError, match="injected final SQLite close"):
            runtime.shutdown()
        assert runtime._state == "stopping" and len(allocated) == 1
        assert repository._reader_connections.pending is not released
        failing = False
        runtime.shutdown()
        assert runtime._state == "stopped" and not repository._reader_connections.pending
        assert len(allocated) == 1  # Retry/disposal uses the original object, not a replacement connection.
        assert attempts == allocated * (1 if released else 2)
        assert not any(p.exists() for p in sidecars(repository.db_path))
    finally:
        failing = False
        runtime.shutdown()


def test_offline_cleanup_does_not_initialize_missing_storage(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path))
    repository = binding(runtime)._owners.semantic.repository
    runtime.shutdown()
    assert not repository.db_path.exists() and not backend.instances


def test_offline_cleanup_does_not_open_rejected_storage_read_write(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    runtime.operations.scoped_semantic_search("Independent notes", scope(runtime))
    original = {p: p.read_bytes() for p in [repository.db_path, *sidecars(repository.db_path)]}
    validated = repository.validate_expected_binding

    def reject(**_kwargs):
        raise IndexBindingUnavailableError

    def forbidden(*_args, **_kwargs):
        pytest.fail("rejected storage reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "validate_expected_binding", reject)
        patch.setattr(repository._reader_connections, "connect", forbidden)
        repository.finish_stopped_storage()
    assert {p: p.read_bytes() for p in original} == original
    repository.validate_expected_binding = validated
    runtime.shutdown()


def test_live_peer_blocks_offline_completion_until_its_actual_release(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    repository = binding(runtime)._owners.semantic.repository
    runtime.operations.scoped_semantic_search("Independent notes", scope(runtime))
    peer = sqlite3.connect(repository.db_path.as_uri() + "?mode=ro", uri=True)
    try:
        peer.execute("BEGIN")
        peer.execute("SELECT value FROM meta").fetchall()
        with pytest.raises(IndexBindingUnavailableError):
            runtime.shutdown()
        assert runtime._state == "stopping"
        assert any(p.exists() for p in sidecars(repository.db_path))
        peer.close()
        runtime.shutdown()
        assert runtime._state == "stopped"
        assert not any(p.exists() for p in sidecars(repository.db_path))
    finally:
        peer.close()
        runtime.shutdown()
