"""Focused acceptance repairs: HTTP lifetime, raw scope preservation and root loss."""

import asyncio
import json
import socket
import threading
from contextlib import asynccontextmanager, contextmanager

import pytest
from fastapi.testclient import TestClient

from app.api.private_reads import create_private_read_app
from app.mcp_http import create_private_read_http_transport
from app.services.rate_limiter import FixedWindowRateLimiter
from app.services.scoped_budget import CancellationToken
from app.services.space_admission import ScopedOperationAdmission
from app.services.vault import VaultService
from tests.test_policy_read_adapters import AUTH
from tests.test_policy_read_adapters import local_backend as local_backend
from tests.test_policy_read_adapters import runtime as runtime
from tests.test_policy_read_adapters import settings as settings


def rpc(arguments, *, version="2026-07-28", method="tools/call"):
    meta = {"io.modelcontextprotocol/protocolVersion": version, "io.modelcontextprotocol/clientCapabilities": {}}
    return (
        '{"jsonrpc":"2.0","id":101,"method":' + json.dumps(method) + ',"params":{'
        '"_meta":' + json.dumps(meta) + ',"name":"read_note","arguments":' + arguments + "}}"
    ).encode()


def transport_for(settings, runtime):
    configured = settings.model_copy(update={"mcp_http_allowed_hosts": ("testserver", "127.0.0.1:*")})
    return create_private_read_http_transport(
        settings=configured,
        operations=runtime.operations,
        rate_limiter=FixedWindowRateLimiter(requests=100, window_seconds=60, max_clients=10),
    )


def http_scope(body, *, version="2026-07-28"):
    packet = json.loads(body)
    method, params = packet["method"], packet["params"]
    name = params.get("name", params.get("uri"))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
            (b"authorization", b"Bearer slice-a-test-key"),
            (b"mcp-protocol-version", version.encode()),
            (b"mcp-method", method.encode()),
            *([(b"mcp-name", name.encode())] if name is not None else []),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("testclient", 1),
        "server": ("testserver", 80),
    }


async def request(app, body, *, disconnected=None, sent=None, version="2026-07-28", disconnect_received=None):
    messages = [] if sent is None else sent
    closed = asyncio.Event() if disconnected is None else disconnected
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": body, "more_body": False}
        await closed.wait()
        if disconnect_received is not None:
            disconnect_received.set()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(http_scope(body, version=version), receive, send)
    return messages


READ_MODES = ("explicit", "omitted", "qualified_resource", "legacy_resource")


def read_body(mode, version="2026-07-28"):
    if mode in {"explicit", "omitted"}:
        args = {"path": "same.md", **({"space": "default"} if mode == "explicit" else {})}
        return rpc(json.dumps(args), version=version)
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "resources/read",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": version,
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
                "uri": "vaultbridge://space/default/note/same.md"
                if mode == "qualified_resource"
                else "vaultbridge://note/same.md",
            },
        }
    ).encode()


@pytest.mark.parametrize("version", ["2026-07-28", "2025-11-25"])
@pytest.mark.parametrize("mode", READ_MODES)
def test_mcp_http_disconnect_during_real_owner_cancels_before_release(settings, runtime, monkeypatch, mode, version):
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    original_read, original_cancel = VaultService.read_note, CancellationToken.cancel

    def read(owner, path):
        result = original_read(owner, path)
        entered.set()
        assert release.wait(10)
        return result

    def cancel(token):
        cancelled.set()
        original_cancel(token)

    monkeypatch.setattr(VaultService, "read_note", read)
    monkeypatch.setattr(CancellationToken, "cancel", cancel)
    server, app = transport_for(settings, runtime)

    async def invoke():
        async with server.session_manager.run():
            closed, sent = asyncio.Event(), []
            task = asyncio.create_task(
                request(app, read_body(mode, version), version=version, disconnected=closed, sent=sent)
            )
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                closed.set()
                signalled = await asyncio.to_thread(cancelled.wait, 1)
                occupied = runtime.admission._active_thread is not None
            finally:
                release.set()
            try:
                await asyncio.wait_for(task, 5)
            except asyncio.CancelledError:
                pass
            statuses = [m["status"] for m in sent if m["type"] == "http.response.start"]
            assert signalled, f"disconnect token unset while owner blocked; occupied={occupied}; statuses={statuses}"
            assert occupied, "owner must drain before its admission can be released"
            assert sent == []

    asyncio.run(invoke())
    assert runtime.admission._active_thread is None


@pytest.mark.parametrize("mode", READ_MODES)
def test_mcp_http_disconnect_releases_waiter_before_active_owner(settings, runtime, monkeypatch, mode):
    from concurrent.futures import ThreadPoolExecutor

    from app.services.knowledge_spaces import QualifiedPath, SpaceId
    from tests.test_space_runtime import waiters

    entered, release = threading.Event(), threading.Event()
    original = VaultService.read_note
    calls = []

    def read(owner, path):
        result = original(owner, path)
        calls.append(path)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(VaultService, "read_note", read)
    server, app = transport_for(settings, runtime)
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(runtime.operations.read_note, QualifiedPath(SpaceId("default"), "same.md"))

        async def invoke():
            async with server.session_manager.run():
                closed, sent = asyncio.Event(), []
                task = asyncio.create_task(request(app, read_body(mode), disconnected=closed, sent=sent))
                await asyncio.to_thread(waiters, runtime.admission, 1)
                closed.set()
                # Real admission condition, not a sleep or a mocked cancellation.
                await asyncio.to_thread(waiters, runtime.admission, 0)
                assert runtime.admission._active_thread is not None and calls == ["same.md"]
                try:
                    await asyncio.wait_for(task, 5)
                except asyncio.CancelledError:
                    pass
                assert sent == []
                release.set()
                await asyncio.wrap_future(active)
                sent = await request(app, read_body(mode))
                assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 200

        try:
            assert entered.wait(5)
            asyncio.run(invoke())
        finally:
            release.set()
        assert active.result(5).note.path == "same.md"


def test_duplicate_arguments_cannot_erase_unknown_explicit_scope(settings, runtime, monkeypatch):
    calls = []
    original = VaultService.read_note

    def read(owner, path):
        calls.append(path)
        return original(owner, path)

    monkeypatch.setattr(VaultService, "read_note", read)
    body = rpc('{"space":"unknown","path":"same.md"},"arguments":{"path":"same.md"}')
    server, app = transport_for(settings, runtime)

    async def invoke():
        async with server.session_manager.run():
            sent = await request(app, body)
        payload = json.loads(b"".join(m.get("body", b"") for m in sent))
        assert payload["result"].get("isError") is True, payload
        assert payload["result"]["content"] == [{"type": "text", "text": "invalid_request: Operation failed."}]
        assert calls == []

    asyncio.run(invoke())


def duplicate_envelopes():
    base = rpc('{"space":"unknown","path":"same.md"}').decode()
    packet = json.loads(base)
    omitted = {**packet["params"], "arguments": {"path": "same.md"}}
    return {
        "arguments_erasure": rpc('{"space":"unknown","path":"same.md"},"arguments":{"path":"same.md"}'),
        "params_erasure": (base[:-1] + ',"params":' + json.dumps(omitted) + "}").encode(),
        "jsonrpc": base.replace('"jsonrpc":"2.0"', '"jsonrpc":"2.0","jsonrpc":"2.0"').encode(),
        "id": base.replace('"id":101', '"id":100,"id":101').encode(),
        "method": base.replace('"method":"tools/call"', '"method":"tools/call","method":"tools/call"').encode(),
        "param_name": base.replace('"name":"read_note"', '"name":"read_note","name":"read_note"').encode(),
        "space": rpc('{"space":"unknown","space":"default","path":"same.md"}'),
        "escaped_space": rpc('{"space":"unknown","\\u0073pace":"default","path":"same.md"}'),
        "nested": rpc('{"space":"unknown","path":"same.md","extra":{"k":1,"k":2}}'),
    }


@pytest.mark.parametrize("case", duplicate_envelopes())
def test_duplicate_objects_reject_before_any_policy_or_owner(settings, runtime, monkeypatch, case):
    from app.services.knowledge_spaces import SpacePolicyResolver

    def fail(*args, **kwargs):
        pytest.fail("duplicate input reached policy, admission or owner")

    monkeypatch.setattr(SpacePolicyResolver, "authorize_read", fail)
    monkeypatch.setattr(ScopedOperationAdmission, "operation", fail)
    monkeypatch.setattr(VaultService, "read_note", fail)
    server, app = transport_for(settings, runtime)

    async def invoke():
        async with server.session_manager.run():
            sent = await request(app, duplicate_envelopes()[case])
        payload = json.loads(b"".join(m.get("body", b"") for m in sent))
        assert payload["result"]["isError"] is True
        assert payload["result"]["content"] == [{"type": "text", "text": "invalid_request: Operation failed."}]
        assert "unknown" not in json.dumps(payload)

    asyncio.run(invoke())


def stdio_request(settings, body):
    import os
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    process = subprocess.Popen(
        [sys.executable, "-m", "tests.policy_read_stdio_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "VB_TEST_PRIVATE_SETTINGS": json.dumps(
                {**settings.model_dump(mode="json"), "knowledge_spaces_json": settings.knowledge_spaces_json}
            ),
        },
        cwd=Path(__file__).parents[1],
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            process.stdin.write(body + b"\n")
            process.stdin.flush()
            response = pool.submit(process.stdout.readline).result(10)
        finally:
            process.stdin.close()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(10)
    stderr = process.stderr.read()
    assert process.returncode == 0, stderr
    return json.loads(response), stderr


@pytest.mark.parametrize("case", duplicate_envelopes())
def test_real_stdio_rejects_duplicate_envelopes(settings, case):
    payload, stderr = stdio_request(settings, duplicate_envelopes()[case])
    assert payload["result"]["isError"] is True
    assert payload["result"]["content"] == [{"type": "text", "text": "invalid_request: Operation failed."}]
    assert "unknown" not in json.dumps(payload) + stderr.decode()


@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_valid_omission_and_explicit_default_remain_distinct(settings, runtime, transport):
    if transport == "stdio":
        legacy = stdio_request(settings, read_body("omitted"))[0]["result"]["structuredContent"]
        explicit = stdio_request(settings, read_body("explicit"))[0]["result"]["structuredContent"]
    else:
        server, app = transport_for(settings, runtime)

        async def invoke():
            async with server.session_manager.run():
                results = []
                for mode in ("omitted", "explicit"):
                    messages = await request(app, read_body(mode))
                    results.append(
                        json.loads(b"".join(m.get("body", b"") for m in messages))["result"]["structuredContent"]
                    )
                return results

        legacy, explicit = asyncio.run(invoke())
    assert "space_id" not in legacy and legacy["resource_uri"] == "vaultbridge://note/same.md"
    assert explicit["space_id"] == "default"
    assert explicit["result"]["content"] == legacy["content"]
    assert explicit["result"]["resource_uri"] == "vaultbridge://space/default/note/same.md"


@pytest.mark.parametrize("replacement", [False, True])
def test_named_omitted_root_loss_has_operational_classification(settings, runtime, replacement):
    root = settings.vault_path
    removed = root.with_name("removed-default")
    assert root.resolve().parent == removed.resolve().parent
    root.rename(removed)
    if replacement:
        root.write_text("unavailable directory", encoding="utf-8")
    try:
        app = create_private_read_app(settings=settings, operations=runtime.operations)
        client = TestClient(app, raise_server_exceptions=False)
        explicit = client.get("/notes/read?space=default&path=same.md", headers=AUTH)
        omitted = client.get("/notes/read?path=same.md", headers=AUTH)
        assert (explicit.status_code, omitted.status_code) == (503, 503)
        assert explicit.json() == omitted.json() == {"detail": "space_unavailable"}
    finally:
        if replacement:
            root.unlink()
        removed.rename(root)


@pytest.mark.parametrize("mode", READ_MODES)
def test_mcp_root_loss_has_safe_tool_or_resource_projection(settings, runtime, mode):
    root = settings.vault_path
    removed = root.with_name("removed-default")
    server, app = transport_for(settings, runtime)
    root.rename(removed)
    try:

        async def invoke():
            async with server.session_manager.run():
                messages = await request(app, read_body(mode))
            return json.loads(b"".join(m.get("body", b"") for m in messages))

        payload = asyncio.run(invoke())
        if mode in {"explicit", "omitted"}:
            assert payload["result"]["isError"] is True
            assert payload["result"]["content"] == [{"type": "text", "text": "space_unavailable: Operation failed."}]
        else:
            assert payload["error"]["message"] == "note_resource_unavailable: Note resource is invalid or unavailable."
        assert str(root) not in json.dumps(payload) and "Independent" not in json.dumps(payload)
    finally:
        removed.rename(root)


def test_named_root_loss_cli_projection_and_public_fence(settings, runtime, monkeypatch):
    from io import StringIO

    from app.cli import _run_scoped_cli, main
    from app.services.knowledge_spaces import SpaceError
    from app.space_read_transport import public_error

    root = settings.vault_path
    removed = root.with_name("removed-default")
    root.rename(removed)
    try:
        with pytest.raises(SpaceError) as error:
            runtime.operations.compatibility_read("literal", query="Independent", folder="", limit=10, cli=True)
        assert public_error(error.value) == ("space_unavailable", 503, 1)
        output = StringIO()
        assert (
            _run_scoped_cli(
                settings,
                "hygiene",
                {"space": "default", "groups": ["frontmatter"]},
                operations=runtime.operations,
                json_output=True,
                output=output,
            )
            == 1
        )
        assert json.loads(output.getvalue()) == {"category": "space_unavailable"}

        def fail(*args, **kwargs):
            pytest.fail("fenced public CLI reached a filesystem owner")

        monkeypatch.setattr(VaultService, "root_binding_identity", fail)
        assert main(["search", "Independent"], settings=settings) == 2
    finally:
        removed.rename(root)


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_legacy_owner_errors_and_named_programming_errors_are_preserved(settings, runtime, monkeypatch, error_type):
    from app.core.config import Settings
    from app.services.adapter_composition import legacy_operations
    from tests.test_space_runtime import binding

    owner = binding(runtime, "default")._owners.vault
    legacy = legacy_operations(Settings(**settings.model_dump()), vault=owner)
    failure = error_type("synthetic private failure")

    def fail(*args, **kwargs):
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(VaultService, "read_note", fail)
        with pytest.raises(error_type) as error:
            legacy.compatibility_read("read", path="same.md")
        assert error.value is failure
    if error_type is RuntimeError:
        monkeypatch.setattr(VaultService, "root_binding_identity", fail)
        with pytest.raises(RuntimeError) as error:
            runtime.operations.compatibility_read("read", path="same.md")
        assert error.value is failure


def test_legacy_root_loss_retains_note_and_cli_behavior(settings):
    from io import StringIO

    from app.cli import run_search
    from app.core.config import Settings
    from app.services.adapter_composition import legacy_operations
    from app.services.vault import NoteNotFoundError

    legacy_settings = Settings(**settings.model_dump())
    owner = VaultService(vault_root=settings.vault_path, max_note_bytes=settings.max_note_bytes)
    settings.vault_path.rename(settings.vault_path.with_name("removed-default"))
    with pytest.raises(NoteNotFoundError):
        legacy_operations(legacy_settings, vault=owner).compatibility_read("read", path="same.md")
    output, errors = StringIO(), StringIO()
    assert run_search(legacy_settings, "Independent", vault_service=owner, output=output, error_output=errors) == 1
    assert output.getvalue() == ""
    assert errors.getvalue() == "Search not started: configured vault is unavailable.\n"


@pytest.mark.parametrize("uncertain", [False, True])
def test_http_disconnect_does_not_cancel_or_reinterpret_legacy_write(tmp_path, monkeypatch, uncertain):
    from app.mcp_http import _protected_http_app
    from tests.test_mcp_server import RecordingIndexer, server_for, settings_for

    configured = settings_for(
        tmp_path / "vault",
        API_KEY="slice-a-test-key",
        MCP_WRITE_ENABLED=True,
        MCP_HTTP_ALLOWED_HOSTS=["testserver"],
        RATE_LIMIT_ENABLED=False,
    )
    indexer = RecordingIndexer()
    server, vault, _ = server_for(tmp_path, settings=configured, indexer=indexer)
    app = _protected_http_app(
        server, configured, FixedWindowRateLimiter(requests=100, window_seconds=60, max_clients=10)
    )
    committed, release = threading.Event(), threading.Event()
    original = VaultService.create_note

    def create(owner, **arguments):
        result = original(owner, **arguments)
        committed.set()
        assert release.wait(10)
        if uncertain:
            raise OSError("synthetic uncertain owner outcome")
        return result

    def unexpected_cancel(_):
        pytest.fail("HTTP read lifetime cancelled a write")

    monkeypatch.setattr(VaultService, "create_note", create)
    monkeypatch.setattr(CancellationToken, "cancel", unexpected_cancel)
    body = rpc('{"title":"Committed","content":"synthetic body"}').replace(
        b'"name":"read_note"', b'"name":"create_note"'
    )

    async def invoke():
        async with server.session_manager.run():
            closed, received, sent = asyncio.Event(), asyncio.Event(), []
            task = asyncio.create_task(request(app, body, disconnected=closed, sent=sent, disconnect_received=received))
            try:
                assert await asyncio.to_thread(committed.wait, 5)
                closed.set()
                await asyncio.wait_for(received.wait(), 5)
                assert not task.done()
            finally:
                release.set()
            await asyncio.wait_for(task, 5)
            assert sent == []

    asyncio.run(invoke())
    assert (vault.vault_root / "Inbox" / "Committed.md").read_text(encoding="utf-8").endswith("synthetic body\n")
    assert indexer.paths == ([] if uncertain else ["Inbox/Committed.md"])


@contextmanager
def live_http(settings, runtime):
    """Real loopback sockets and uvicorn receive events, over the same SDK app."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    sdk, transport = transport_for(settings, runtime)
    ready, finished, disconnected = threading.Event(), threading.Event(), threading.Event()
    sent, failures = [], []

    @asynccontextmanager
    async def lifespan(_):
        async with sdk.session_manager.run():
            yield

    async def observed(scope, receive, send):
        async def rx():
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected.set()
            return message

        async def tx(message):
            sent.append(message)
            await send(message)

        try:
            await transport(scope, rx, tx)
        finally:
            finished.set()

    application = Starlette(routes=[Mount("/", observed)], lifespan=lifespan)

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    address = listener.getsockname()
    server = Server(uvicorn.Config(application, lifespan="on", access_log=False, log_level="critical"))

    def serve():
        try:
            server.run(sockets=[listener])
        except BaseException as error:
            failures.append(error)
            ready.set()

    thread = threading.Thread(target=serve, name="vb152-repair-http")
    thread.start()
    try:
        assert ready.wait(10) and not failures
        yield address, sent, finished, disconnected
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive() and not failures


def send_socket(address, body):
    headers = http_scope(body)["headers"]
    raw = b"POST /mcp HTTP/1.1\r\n" + b"\r\n".join(k + b": " + v for k, v in headers) + b"\r\n\r\n" + body
    client = socket.create_connection(address, timeout=10)
    client.sendall(raw)
    return client


@pytest.mark.parametrize("mode", READ_MODES)
@pytest.mark.parametrize("queued", [False, True])
def test_real_socket_disconnect_cancels_same_token_and_recovers_capacity(settings, runtime, monkeypatch, mode, queued):
    from concurrent.futures import ThreadPoolExecutor

    from app.services.knowledge_spaces import QualifiedPath, SpaceError, SpaceId
    from tests.test_space_runtime import waiters

    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    original_read, original_admit, original_cancel = (
        VaultService.read_note,
        ScopedOperationAdmission.operation,
        CancellationToken.cancel,
    )
    tokens, cancelled_tokens = [], []

    def read(owner, path):
        result = original_read(owner, path)
        entered.set()
        assert release.wait(15)
        return result

    @contextmanager
    def admission(gate, cancel=None):
        if cancel is not None:
            tokens.append(cancel)
        with original_admit(gate, cancel) as token:
            yield token

    def cancel(token):
        cancelled_tokens.append(token)
        original_cancel(token)
        cancelled.set()

    monkeypatch.setattr(VaultService, "read_note", read)
    monkeypatch.setattr(ScopedOperationAdmission, "operation", admission)
    monkeypatch.setattr(CancellationToken, "cancel", cancel)
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = (
            pool.submit(runtime.operations.read_note, QualifiedPath(SpaceId("default"), "same.md")) if queued else None
        )
        if queued:
            assert entered.wait(5)
        with live_http(settings, runtime) as (address, sent, finished, disconnected):
            client = send_socket(address, read_body(mode))
            try:
                if queued:
                    waiters(runtime.admission, 1)
                else:
                    assert entered.wait(5)
                client.shutdown(socket.SHUT_RDWR)
                client.close()
                assert disconnected.wait(5), "server must receive a real socket disconnect"
                assert cancelled.wait(5), "read cancellation must precede blocked-owner completion"
                assert any(token is tokens[-1] for token in cancelled_tokens)
                with pytest.raises(SpaceError, match="operation_cancelled"):
                    tokens[-1].check()
                if queued:
                    waiters(runtime.admission, 0)
                    assert finished.wait(5)
                assert runtime.admission._active_thread is not None
                assert sent == []
            finally:
                release.set()
                client.close()
            if active is not None:
                assert active.result(5).note.path == "same.md"
            assert finished.wait(5)
            assert runtime.admission._active_thread is None
            # A disconnected reader cannot poison the SDK, shared gate or peer.
            recovered = send_socket(address, read_body("omitted"))
            try:
                assert recovered.recv(65536).startswith(b"HTTP/1.1 200")
            finally:
                recovered.close()
        if active is not None:
            assert active.result(5).note.path == "same.md"
