"""Slice C: real filesystem owners, private adapters and actual transport frames."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from mcp import Client
from pydantic import SecretStr

from app.api.private_reads import create_private_read_app
from app.cli import _run_write_cli, main, run_capture, run_promote_apply, run_promote_review
from app.core.config import Settings
from app.main import create_app
from app.mcp_read_input import normalize_scoped_message
from app.mcp_server import create_private_read_server
from app.services.frontmatter import FrontmatterParser
from app.services.knowledge_spaces import (
    Capability,
    SpaceError,
    SpaceId,
    SpacePolicyResolver,
)
from app.services.scoped_budget import CancellationToken
from app.services.space_admission import ScopedOperationAdmission
from app.services.space_runtime import RuntimeSpaceIndexLifecycle, compose_space_runtime
from app.services.vault import AtomicCaptureError, VaultService
from app.space_write_transport import execute_note_write
from tests.test_policy_read_adapters import AUTH, rest, tool
from tests.test_policy_read_repairs import request as http_request
from tests.test_policy_read_repairs import transport_for
from tests.test_promotion import CAPTURE_ID, PROMOTION_ID, SOURCE
from tests.test_scoped_promotion import request as capture_request
from tests.test_scoped_promotion import runtime_settings
from tests.test_space_runtime import backend as backend_fixture


@pytest.fixture
def backend(monkeypatch):
    return backend_fixture.__wrapped__(monkeypatch)


@pytest.fixture
def settings(tmp_path):
    return runtime_settings(tmp_path).model_copy(update={
        "api_key": SecretStr("slice-a-test-key"), "mcp_write_enabled": True,
        "rate_limit_enabled": False, "semantic_watch_enabled": False,
    })


@pytest.fixture
def runtime(settings):
    with compose_space_runtime(settings) as runtime:
        yield runtime


def body(operation, **changes):
    return ({"title": "Target", "folder": "Notes", "content": "Body", "tags": ["#tag"]}
            if operation == "create" else {
                "path": "Notes/Target.md", "content": "Addition", "dedupe_key": "stable"}) | changes


def url(operation):
    return "/api/v1/notes" + ("/append" if operation == "append" else "")


def root(runtime, sid="research"):
    return runtime.registry._owners[SpaceId(sid)].vault.vault_root


def freeze_clock(monkeypatch):
    class Clock:
        @staticmethod
        def now(_):
            return datetime(2026, 11, 8, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.vault.datetime", Clock)


@pytest.mark.parametrize("sid", ["default", "research"])
@pytest.mark.parametrize("operation", ["create", "append"])
def test_scoped_real_domain_rest_mcp_and_dedup(settings, runtime, sid, operation):
    data = body(operation, space=sid)
    if operation == "append":
        (root(runtime, sid) / "Notes/Target.md").write_bytes(b"Original")
    expected = execute_note_write(runtime.operations, operation, data)
    response = rest(settings, runtime).post(url(operation), headers=AUTH, json=data)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    expected["result"]["status"] = "unchanged" if operation == "create" else "already_applied"
    assert response.json() == expected
    result = tool(create_private_read_server(settings=settings, operations=runtime.operations),
                  operation + "_note", data)
    assert not result.is_error and result.structured_content == expected
    assert json.loads(result.content[0].text) == expected
    other = "research" if sid == "default" else "default"
    assert not (root(runtime, other) / "Notes/Target.md").exists()


@pytest.mark.parametrize("operation", ["create", "append"])
def test_omitted_and_explicit_legacy_bytes_and_response(settings, runtime, monkeypatch, operation):
    freeze_clock(monkeypatch)
    if operation == "append":
        for sid in ("default", "research"):
            (root(runtime, sid) / "Notes/Target.md").write_bytes(b"Original")
    client = rest(settings, runtime)
    legacy = client.post(url(operation), headers=AUTH, json=body(operation))
    explicit = client.post(url(operation), headers=AUTH, json=body(operation, space="research"))
    assert legacy.status_code == explicit.status_code == 200
    assert set(legacy.json()) == {"success", "path", "status"}
    normalized = {**legacy.json(), "path": legacy.json()["path"].replace("\\", "/")}
    assert explicit.json() == {"space_id": "research", "result": normalized}
    assert (root(runtime) / "Notes/Target.md").read_bytes() == (
        root(runtime, "default") / "Notes/Target.md").read_bytes()


def change_policy(settings, sid="research", **changes):
    config = json.loads(settings.knowledge_spaces_json)
    for record in config["spaces"]:
        if record["space_id"] == sid:
            record.update(changes)
    return Settings(**{**settings.model_dump(), "knowledge_spaces_json": json.dumps(config)})


@pytest.mark.parametrize("operation", ["create", "append"])
@pytest.mark.parametrize("denial", ["unknown", "write", "read", "capability"])
@pytest.mark.parametrize("transport", ["domain", "rest", "mcp"])
def test_denial_before_admission_root_or_owner(settings, monkeypatch, operation, denial, transport):
    configured = settings
    sid = "research"
    if denial == "unknown":
        sid = "private-unknown"
    elif denial == "write":
        configured = change_policy(settings, write="deny")
    elif denial == "read":
        configured = change_policy(settings, read="deny", write="deny", indexing="disabled")
    else:
        configured = change_policy(settings, capabilities=[
            c.value for c in Capability if c.value != operation + "_note"])
    with compose_space_runtime(configured) as rt:
        def fail(*args, **kwargs):
            pytest.fail("denied request reached admission/root/owner")
        monkeypatch.setattr(ScopedOperationAdmission, "operation", fail)
        monkeypatch.setattr(VaultService, "root_binding_identity", fail)
        monkeypatch.setattr(VaultService, operation + "_note", fail)
        data = body(operation, space=sid)
        code = "capability_unavailable" if denial == "capability" else "scope_not_allowed"
        if transport == "domain":
            with pytest.raises(SpaceError):
                execute_note_write(rt.operations, operation, data)
        elif transport == "rest":
            response = rest(configured, rt).post(url(operation), headers=AUTH, json=data)
            assert response.status_code == (409 if denial == "capability" else 403)
            assert response.json() == {"detail": code}
            assert response.headers["cache-control"] == "no-store"
        else:
            result = tool(create_private_read_server(settings=configured, operations=rt.operations),
                          operation + "_note", data)
            assert result.is_error and result.structured_content is None
            assert result.content[0].text == f"{code}: Operation failed."


@pytest.mark.parametrize("operation", ["create", "append"])
@pytest.mark.parametrize("invalid", [
    {"space": None}, {"space": []}, {"space": "Default"}, {"spaces": ["research"]},
    {"space": "research", "spaces": ["research"]}, {"scope": "research"},
    {"space": "research", "content": None}, {"space": "research", "content": True},
    {"space": "research", "unexpected": "PRIVATE"}, {"space": "research", "content": "\ud800"},
])
def test_strict_rest_mcp_validation_zero_policy(settings, runtime, monkeypatch, operation, invalid):
    def fail(*args, **kwargs):
        pytest.fail("invalid input reached policy")
    monkeypatch.setattr(SpacePolicyResolver, "authorize_write", fail)
    data = body(operation, **invalid)
    response = rest(settings, runtime).post(url(operation), headers=AUTH,
                                          content=json.dumps(data).encode())
    assert response.status_code == 422, response.text
    assert response.json()["detail"] in {"invalid_scope", "invalid_request"}
    result = tool(create_private_read_server(settings=settings, operations=runtime.operations),
                  operation + "_note", data)
    assert result.is_error and "PRIVATE" not in result.content[0].text


@pytest.mark.parametrize("operation", ["create", "append"])
@pytest.mark.parametrize("sid", ["denied", "private-unknown"])
def test_omitted_named_policy_and_unknown_privacy(settings, runtime, operation, sid):
    denied = rest(settings, runtime).post(url(operation), headers=AUTH, json=body(operation, space=sid))
    assert denied.status_code == 403 and denied.json() == {"detail": "scope_not_allowed"}
    configured = change_policy(settings, sid="default", write="deny")
    with compose_space_runtime(configured) as rt:
        response = rest(configured, rt).post(url(operation), headers=AUTH, json=body(operation))
        assert response.status_code == 403 and response.json() == denied.json()


@pytest.mark.parametrize("operation", ["create", "append"])
@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/device", "//server/share"])
def test_containment_failure_safe(settings, runtime, operation, path):
    data = body(operation, space="research", **({"folder": path} if operation == "create" else {"path": path + ".md"}))
    response = rest(settings, runtime).post(url(operation), headers=AUTH, json=data)
    assert response.status_code == 400 and response.json() == {"detail": "invalid_path"}


@pytest.mark.parametrize("operation", ["create", "append"])
def test_root_replacement_before_write(settings, runtime, operation):
    original = root(runtime)
    moved = original.with_name("moved-research")
    original.rename(moved)
    original.mkdir()
    try:
        response = rest(settings, runtime).post(url(operation), headers=AUTH, json=body(operation, space="research"))
        assert response.status_code == 503 and response.json() == {"detail": "space_unavailable"}
        assert not list(original.iterdir())
    finally:
        original.rmdir()
        moved.rename(original)


@pytest.mark.parametrize("enabled", [False, True])
def test_write_tool_gate_only_two_tools(settings, runtime, enabled):
    configured = settings.model_copy(update={"mcp_write_enabled": enabled})
    server = create_private_read_server(settings=configured, operations=runtime.operations)
    async def listing():
        async with Client(server) as client:
            return await client.list_tools()
    tools = asyncio.run(listing()).tools
    names = {tool.name for tool in tools}
    assert ({"create_note", "append_note"} <= names) is enabled
    assert not names & {"capture", "promote", "delete_note", "move_note"}
    if enabled:
        for entry in tools:
            if entry.name in {"create_note", "append_note"}:
                assert "space" in entry.input_schema["properties"]
                assert not entry.annotations.read_only_hint
    else:
        result = tool(server, "create_note", body("create", space="research"))
        assert result.is_error and not (root(runtime) / "Notes/Target.md").exists()


def rpc(operation, arguments, version="2026-07-28"):
    return json.dumps({"jsonrpc": "2.0", "id": 101, "method": "tools/call", "params": {
        "name": operation + "_note", "arguments": arguments, "_meta": {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientCapabilities": {},
        }}}).encode()


def stdio(settings, frame):
    process = subprocess.Popen([sys.executable, "-m", "tests.authorized_write_stdio_server"],
        cwd=Path(__file__).parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "VB_TEST_PRIVATE_SETTINGS": json.dumps({
            **settings.model_dump(mode="json"), "knowledge_spaces_json": settings.knowledge_spaces_json})})
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            process.stdin.write(frame + b"\n")
            process.stdin.flush()
            response = executor.submit(process.stdout.readline).result(15)
        finally:
            process.stdin.close()
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(15)
    stderr = process.stderr.read()
    assert process.returncode == 0, stderr.decode(errors="replace")
    return json.loads(response)


def http(settings, runtime, frame, version="2026-07-28"):
    server, app = transport_for(settings, runtime)
    async def call():
        from tests.test_policy_read_repairs import http_scope

        scope = http_scope(frame.decode("utf-8", errors="replace").encode(), version=version)
        sent, first, closed = [], True, asyncio.Event()
        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": frame, "more_body": False}
            await closed.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
        async with server.session_manager.run():
            await app(scope, receive, send)
        return json.loads(b"".join(message.get("body", b"") for message in sent))
    return asyncio.run(call())


@pytest.mark.parametrize("transport", ["stdio", "2026-07-28", "2025-11-25"])
@pytest.mark.parametrize("operation", ["create", "append"])
@pytest.mark.parametrize("explicit", [False, True])
def test_real_transport_success(settings, runtime, operation, transport, explicit):
    sid = "research" if explicit else "default"
    if operation == "append":
        (root(runtime, sid) / "Notes/Target.md").write_bytes(b"Original")
    frame = rpc(operation, body(operation, **({"space": sid} if explicit else {})),
                transport if transport != "stdio" else "2026-07-28")
    response = stdio(settings, frame) if transport == "stdio" else http(settings, runtime, frame, transport)
    result = response["result"]
    assert not result.get("isError"), result
    content = result["structuredContent"]
    if explicit:
        assert content["space_id"] == sid
        content = content["result"]
    else:
        assert "space_id" not in content
    assert content["status"] == ("created" if operation == "create" else "appended")
    assert content["path"].replace("\\", "/") == "Notes/Target.md"


@pytest.mark.parametrize("transport", ["stdio", "2026-07-28", "2025-11-25"])
@pytest.mark.parametrize("operation", ["create", "append"])
@pytest.mark.parametrize("attack", ["duplicate", "erasure", "nonfinite", "utf8", "surrogate"])
def test_real_transport_strict_rejection(settings, runtime, operation, transport, attack):
    frame = rpc(operation, body(operation, space="research"),
                transport if transport != "stdio" else "2026-07-28")
    if attack == "duplicate":
        frame = frame.replace(b'"space": "research"', b'"space":"denied","space":"research"')
    elif attack == "erasure":
        frame = frame.replace(b'"arguments": {', b'"arguments":{"space":"denied"},"arguments":{')
    elif attack == "nonfinite":
        frame = frame[:-1] + b',"PRIVATE":1e400}'
    elif attack == "utf8":
        frame = frame.replace(b'"content": "', b'"content": "\xff')
    else:
        frame = frame.replace(b'"content": "', b'"content": "\\ud800')
    response = stdio(settings, frame) if transport == "stdio" else http(settings, runtime, frame, transport)
    assert response.get("result", {}).get("isError") or "error" in response, response
    assert "PRIVATE" not in json.dumps(response)
    assert not (root(runtime) / "Notes/Target.md").exists()


@pytest.mark.parametrize("operation", ["create", "append"])
def test_failed_rejection_cannot_resume_write(monkeypatch, operation):
    frame = rpc(operation, body(operation, space="research"))[:-1] + b',"duplicate":1,"duplicate":2}'
    def failure(_):
        raise TypeError("PRIVATE")
    monkeypatch.setattr("app.mcp_read_input._rejection_message", failure)
    with pytest.raises(ValueError, match="Invalid MCP request"):
        normalize_scoped_message(frame)


@pytest.mark.parametrize("sid", ["default", "research"])
@pytest.mark.parametrize("action", ["create", "append"])
def test_cli_scoped_capture_review_apply_roundtrip(settings, runtime, sid, action):
    output = StringIO()
    assert _run_write_cli(settings, "capture", space=sid, operations=runtime.operations,
        input_stream=BytesIO(json.dumps(capture_request()).encode()), output=output) == 0
    captured = json.loads(output.getvalue())
    assert captured["space_id"] == sid and captured["result"]["category"] == "created"
    source = root(runtime, sid) / SOURCE
    metadata = FrontmatterParser.parse(source.read_text(encoding="utf-8")).metadata
    assert metadata["capture_space_id"] == sid and metadata["capture_provenance_version"] == 2
    target = root(runtime, sid) / "Notes/Target.md"
    if action == "append":
        target.write_bytes(b"Original")
    output = StringIO()
    assert _run_write_cli(settings, "review", space=sid, operations=runtime.operations,
        source_path=SOURCE, capture_id=CAPTURE_ID, destination="Notes/Target.md", output=output) == 0
    review = json.loads(output.getvalue())["result"]
    assert review["provenance_version"] == 2
    assert ("destination" in review) is (action == "append")
    approved = dict(source_path=SOURCE, capture_id=CAPTURE_ID,
        expected_source_sha256=review["source_sha256"], approved_content="Exact approved body",
        action=action, destination="Notes/Target.md", promotion_id=PROMOTION_ID,
        approved_at="2026-11-08T12:00:00Z", transfer_fields=["title"])
    approved.update({"expected_destination": "absent"} if action == "create" else
                    {"expected_destination_sha256": review["destination"]["sha256"]})
    for category in ("created" if action == "create" else "appended", "already_applied"):
        output = StringIO()
        assert _run_write_cli(settings, "apply", space=sid, operations=runtime.operations,
            input_stream=BytesIO(json.dumps(approved).encode()), output=output) == 0
        evidence = json.loads(output.getvalue())
        assert evidence["space_id"] == sid and evidence["result"]["category"] == category
    assert b"Exact approved body" in target.read_bytes()
    assert b"Original body" not in target.read_bytes()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == review["source_sha256"]


def test_cli_actual_profile_and_restart_reconstruction(settings):
    output = StringIO()
    assert _run_write_cli(settings, "capture", space="research", input_stream=BytesIO(
        json.dumps(capture_request()).encode()), output=output) == 0
    output = StringIO()
    assert _run_write_cli(settings, "review", space="research", source_path=SOURCE, capture_id=CAPTURE_ID,
                          destination="Notes/New.md", output=output) == 0
    evidence = json.loads(output.getvalue())["result"]
    assert evidence["provenance_version"] == 2 and "destination" not in evidence
    decision = dict(source_path=SOURCE, capture_id=CAPTURE_ID, expected_source_sha256=evidence["source_sha256"],
        approved_content="Approved", action="create", destination="Notes/New.md", promotion_id=PROMOTION_ID,
        approved_at="2026-11-08T12:00:00Z", transfer_fields=[], expected_destination="absent")
    output = StringIO()
    assert _run_write_cli(settings, "apply", space="research", input_stream=BytesIO(
        json.dumps(decision).encode()), output=output) == 0
    assert json.loads(output.getvalue())["result"]["committed"]
    assert not list(settings.vault_path.parent.glob("*-data"))


@pytest.mark.parametrize("command", [["capture"], ["promote", "review", "--source-path", SOURCE,
    "--capture-id", CAPTURE_ID], ["promote", "apply"]])
def test_cli_singular_selection_and_public_guard(settings, capsys, command):
    assert main([*command, "--space", "research"], settings=settings) == 2
    assert "invalid_configuration" in capsys.readouterr().out
    assert main([*command, "--space", "research", "--space", "default"], settings=Settings()) == 2
    # Parsing retains all repetitions; dispatch rejects rather than last-wins.
    assert json.loads(capsys.readouterr().out) == {"category": "invalid_scope"}


@pytest.mark.parametrize("runner,arguments", [
    (run_capture, {}), (run_promote_review, {"source_path": SOURCE, "capture_id": CAPTURE_ID}),
    (run_promote_apply, {}),
])
def test_exported_named_cli_guard(settings, runner, arguments):
    with pytest.raises(SpaceError, match="invalid_configuration"):
        runner(settings, space="research", **arguments)


@pytest.mark.parametrize("operation", ["create", "append"])
def test_cancel_before_owner_and_after_commit(runtime, monkeypatch, operation):
    token = CancellationToken()
    token.cancel()
    with pytest.raises(SpaceError, match="operation_cancelled"):
        execute_note_write(runtime.operations, operation, body(operation, space="research"), cancel=token)
    if operation == "append":
        (root(runtime) / "Notes/Target.md").write_bytes(b"Original")
    token = CancellationToken()
    original = getattr(VaultService, operation + "_note")
    def committed(owner, **arguments):
        result = original(owner, **arguments)
        token.cancel()
        return result
    monkeypatch.setattr(VaultService, operation + "_note", committed)
    result = execute_note_write(runtime.operations, operation, body(operation, space="research"), cancel=token)
    assert result["result"]["status"] in {"created", "appended"}


def test_ambiguous_capture_and_late_cancel_evidence(runtime, settings, monkeypatch):
    original = VaultService.create_capture_if_absent
    def uncertain(owner, **arguments):
        original(owner, **arguments)
        runtime.admission.begin_shutdown()
        raise AtomicCaptureError("commit_unknown")
    monkeypatch.setattr(VaultService, "create_capture_if_absent", uncertain)
    output = StringIO()
    assert _run_write_cli(settings, "capture", space="research", operations=runtime.operations,
        input_stream=BytesIO(json.dumps(capture_request()).encode()), output=output) == 1
    assert json.loads(output.getvalue()) == {"space_id": "research", "result": {
        "category": "commit_unknown", "committed": False}}
    assert (root(runtime) / SOURCE).exists()


@pytest.mark.parametrize("operation", ["create", "append"])
def test_shutdown_drains_confirmed_write(runtime, monkeypatch, operation):
    entered, release = threading.Event(), threading.Event()
    if operation == "append":
        (root(runtime) / "Notes/Target.md").write_bytes(b"Original")
    original = getattr(VaultService, operation + "_note")
    def blocked(owner, **arguments):
        result = original(owner, **arguments)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(VaultService, operation + "_note", blocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        work = executor.submit(execute_note_write, runtime.operations, operation, body(operation, space="research"))
        assert entered.wait(5)
        stopping = executor.submit(runtime.shutdown)
        try:
            assert not stopping.done()
        finally:
            release.set()
        assert work.result(10)["result"]["status"] in {"created", "appended"}
        stopping.result(10)
    assert runtime._state == "stopped"


def test_qualified_enqueue_new_commit_only_and_failure(settings, backend, monkeypatch):
    configured = change_policy(settings, indexing="enabled")
    calls = []
    original = RuntimeSpaceIndexLifecycle.enqueue
    with compose_space_runtime(configured) as rt:
        rt.scheduler.wait(20)
        def enqueue(owner, path):
            assert path.space_id == SpaceId("research")
            calls.append(path)
            return original(owner, path)
        monkeypatch.setattr(RuntimeSpaceIndexLifecycle, "enqueue", enqueue)
        for operation in ("create", "create", "append", "append"):
            execute_note_write(rt.operations, operation, body(operation, space="research"))
        assert len(calls) == 2 and all(p.relative_path == "Notes/Target.md" for p in calls)
        rt.scheduler.wait(20)
        assert rt.registry._owners[SpaceId("research")].semantic.repository.load_chunks()
        assert not (settings.vault_path.parent / "default-data/semantic-index.sqlite3").exists()
        def failed(*args):
            raise RuntimeError("PRIVATE enqueue failure")
        monkeypatch.setattr(RuntimeSpaceIndexLifecycle, "enqueue", failed)
        result = execute_note_write(rt.operations, "create", body("create", title="Committed", space="research"))
        assert result["result"]["status"] == "created"
        assert (root(rt) / "Notes/Committed.md").exists()


def test_disabled_indexing_zero_enqueue(runtime, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("disabled index work")
    monkeypatch.setattr(RuntimeSpaceIndexLifecycle, "enqueue", fail)
    for operation in ("create", "append"):
        execute_note_write(runtime.operations, operation, body(operation, space="research"))
    assert runtime.scheduler is runtime.resource is runtime.watcher is None


def test_frozen_operation_ids_and_schema_alternatives(settings, runtime):
    schema = create_private_read_app(settings=settings, operations=runtime.operations).openapi()
    for path, operation_id in (("/api/v1/notes", "createNoteV1"), ("/api/v1/notes/append", "appendNoteV1"),
                                ("/notes", "createNote"), ("/notes/append", "appendNote")):
        endpoint = schema["paths"][path]["post"]
        assert endpoint["operationId"] == operation_id
        assert len(endpoint["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]) == 2
        assert "space" in endpoint["requestBody"]["content"]["application/json"]["schema"]["properties"]
    with pytest.raises(SpaceError, match="invalid_configuration"):
        create_app(settings=settings)


@pytest.mark.parametrize("operation", ["create", "append"])
@pytest.mark.parametrize("version", ["2026-07-28", "2025-11-25"])
def test_http_explicit_write_disconnect_drains_commit(settings, runtime, monkeypatch, operation, version):
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    if operation == "append":
        (root(runtime) / "Notes/Target.md").write_bytes(b"Original")
    original = getattr(VaultService, operation + "_note")
    cancel = CancellationToken.cancel
    def blocked(owner, **arguments):
        result = original(owner, **arguments)
        entered.set()
        assert release.wait(10)
        return result
    def signal(token):
        cancelled.set()
        cancel(token)
    monkeypatch.setattr(VaultService, operation + "_note", blocked)
    monkeypatch.setattr(CancellationToken, "cancel", signal)
    server, app = transport_for(settings, runtime)
    async def invoke():
        async with server.session_manager.run():
            closed, sent = asyncio.Event(), []
            task = asyncio.create_task(http_request(app,
                rpc(operation, body(operation, space="research"), version),
                version=version, disconnected=closed, sent=sent))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                closed.set()
                assert await asyncio.to_thread(cancelled.wait, 5)
                assert not task.done() and runtime.admission._active_thread is not None
                assert sent == []
            finally:
                release.set()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            assert sent == [] and runtime.admission._active_thread is None
    asyncio.run(invoke())
    assert (root(runtime) / "Notes/Target.md").exists()


@pytest.mark.parametrize("operation", ["create", "append"])
def test_write_symlink_or_junction_escape(settings, runtime, tmp_path, operation):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Target.md").write_bytes(b"Private outside content")
    link = root(runtime) / "redirect"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    data = body(operation, space="research", **(
        {"folder": "redirect"} if operation == "create" else {"path": "redirect/Target.md"}))
    response = rest(settings, runtime).post(url(operation), headers=AUTH, json=data)
    assert response.status_code == 400 and response.json() == {"detail": "invalid_path"}
    assert (outside / "Target.md").read_bytes() == b"Private outside content"


@pytest.mark.parametrize("space", [None, "default", "research"])
def test_cli_capture_idempotency_and_same_id_conflict(settings, runtime, space):
    for data, category, exit_code in ((capture_request(), "created", 0),
            (capture_request(), "already_applied", 0), ({**capture_request(), "content": "Different"}, "conflict", 1)):
        output = StringIO()
        assert _run_write_cli(settings, "capture", space=space, operations=runtime.operations,
            input_stream=BytesIO(json.dumps(data).encode()), output=output) == exit_code
        evidence = json.loads(output.getvalue())
        if space is not None:
            assert evidence["space_id"] == space
            evidence = evidence["result"]
        assert evidence["category"] == category
    sid = space or "default"
    metadata = FrontmatterParser.parse((root(runtime, sid) / SOURCE).read_text(encoding="utf-8")).metadata
    assert ("capture_provenance_version" in metadata) is (space is not None)


@pytest.mark.parametrize("space", [None, "default", "research"])
def test_capture_cli_v1_v2_bytes_parity(settings, runtime, tmp_path, space):
    from app.services.capture import CaptureService

    other = tmp_path / "golden"
    other.mkdir()
    vault = VaultService(vault_root=other, max_note_bytes=settings.max_note_bytes)
    expected = CaptureService(vault).capture(capture_request(),
                                            _space_id=None if space is None else SpaceId(space))
    assert expected.committed
    output = StringIO()
    assert _run_write_cli(settings, "capture", space=space, operations=runtime.operations,
        input_stream=BytesIO(json.dumps(capture_request()).encode()), output=output) == 0
    assert (root(runtime, space or "default") / SOURCE).read_bytes() == (other / SOURCE).read_bytes()


def approved_request(runtime, *, action="create", space="research"):
    from app.services.knowledge_spaces import QualifiedPath, WriteScope

    runtime.operations.scoped_capture(WriteScope(SpaceId(space)), capture_request())
    target = root(runtime, space) / "Notes/Target.md"
    if action == "append":
        target.write_bytes(b"Original")
    snapshot = runtime.operations.scoped_promotion_review(QualifiedPath(SpaceId(space), SOURCE), CAPTURE_ID,
        destination=QualifiedPath(SpaceId(space), "Notes/Target.md"))
    return dict(source_path=SOURCE, capture_id=CAPTURE_ID, expected_source_sha256=snapshot.review.source_sha256,
        approved_content="Approved", action=action, destination="Notes/Target.md", promotion_id=PROMOTION_ID,
        approved_at="2026-11-08T12:00:00Z", transfer_fields=[], **(
            {"expected_destination": "absent"} if action == "create" else
            {"expected_destination_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}))


@pytest.mark.parametrize("action", ["create", "append"])
@pytest.mark.parametrize("changes", [
    {"source_space": "default"}, {"destination_space": "default"}, {"approved_content": ""},
    {"approved_content": "\ud800"}, {"approved_content": "x" * 65_537}, {"action": True},
    {"expected_source_sha256": "bad"}, {"promotion_id": "bad"}, {"capture_id": "bad"},
    {"approved_at": "bad"}, {"transfer_fields": ["PRIVATE"]}, {"destination": "Inbox/Captures/other.md"},
], ids=["source-space", "destination-space", "empty", "surrogate", "oversize", "action",
        "source-hash", "promotion-id", "capture-id", "timestamp", "transfer", "capture-destination"])
def test_cli_invalid_decision_zero_owner(settings, runtime, monkeypatch, action, changes):
    approved = approved_request(runtime, action=action)
    def fail(*args, **kwargs):
        pytest.fail("invalid decision reached policy or owner")
    monkeypatch.setattr(SpacePolicyResolver, "authorize_read", fail)
    monkeypatch.setattr(SpacePolicyResolver, "authorize_write", fail)
    output = StringIO()
    assert _run_write_cli(settings, "apply", space="research", operations=runtime.operations,
        input_stream=BytesIO(json.dumps({**approved, **changes}).encode()), output=output) == 1
    evidence = json.loads(output.getvalue())
    assert evidence["category"] in {"invalid_request", "size_limit", "unsafe_destination"}
    assert "PRIVATE" not in output.getvalue()


@pytest.mark.parametrize("change", ["source", "destination", "version", "copied"])
def test_cli_stale_and_copied_artifact_rejection(settings, runtime, change):
    approved = approved_request(runtime, action="append")
    source, target = root(runtime) / SOURCE, root(runtime) / "Notes/Target.md"
    if change == "source":
        source.write_bytes(source.read_bytes() + b"changed")
    elif change == "destination":
        target.write_bytes(b"changed")
    elif change == "version":
        source.write_bytes(source.read_bytes().replace(b'"capture_provenance_version": 2, ', b"")
                           .replace(b', "capture_space_id": "research"', b""))
    else:
        source.write_bytes(source.read_bytes().replace(b'"capture_space_id": "research"',
                                                       b'"capture_space_id": "default"'))
    before = target.read_bytes()
    output = StringIO()
    assert _run_write_cli(settings, "apply", space="research", operations=runtime.operations,
        input_stream=BytesIO(json.dumps(approved).encode()), output=output) == 1
    evidence = json.loads(output.getvalue())
    evidence = evidence.get("result", evidence)
    assert evidence["category"] in {"source_changed", "destination_changed", "unsafe_source"}
    assert target.read_bytes() == before


@pytest.mark.parametrize("field,value", [("capture_space_id", "default"), ("capture_provenance_version", 2)])
@pytest.mark.parametrize("metadata", [False, True])
def test_capture_client_cannot_select_provenance(settings, runtime, field, value, metadata):
    data = capture_request()
    data.update({"metadata": {field: value}} if metadata else {field: value})
    output = StringIO()
    assert _run_write_cli(settings, "capture", space="research", operations=runtime.operations,
        input_stream=BytesIO(json.dumps(data).encode()), output=output) == 1
    assert json.loads(output.getvalue())["result"] == {"category": "invalid_request", "committed": False}
    assert not (root(runtime) / SOURCE).exists()


@pytest.mark.parametrize("operation", ["capture", "apply"])
@pytest.mark.parametrize("payload", [b'{"content":"PRIVATE","content":"other"}', b'{"PRIVATE":NaN}',
                                     b'{"content":"\xff"}', b'{}{}', b"x" * 1_048_577],
                         ids=["duplicate", "nonfinite", "encoding", "trailing", "oversize"])
def test_cli_bounded_json_rejected_before_composition(settings, monkeypatch, operation, payload):
    def fail(*args, **kwargs):
        pytest.fail("invalid bounded input reached composition")
    monkeypatch.setattr("app.services.write_composition.compose_space_runtime", fail)
    output = StringIO()
    assert _run_write_cli(settings, operation, space="research", input_stream=BytesIO(payload), output=output) in {1, 2}
    assert json.loads(output.getvalue())["category"] in {"invalid_request", "size_limit"}
    assert "PRIVATE" not in output.getvalue()


def test_cli_review_requires_explicit_destination_before_owner(settings, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("missing destination reached composition")
    monkeypatch.setattr("app.services.write_composition.compose_space_registry", fail)
    output = StringIO()
    assert _run_write_cli(settings, "review", space="research", source_path=SOURCE,
                          capture_id=CAPTURE_ID, output=output) == 2
    assert json.loads(output.getvalue()) == {"category": "invalid_request"}


def test_cli_write_profile_preflight_zero_owner(settings, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("denied profile constructed owners")
    monkeypatch.setattr("app.services.write_composition.compose_space_runtime", fail)
    output = StringIO()
    assert _run_write_cli(settings, "capture", space="denied", input_stream=BytesIO(
        json.dumps(capture_request()).encode()), output=output) == 2
    assert json.loads(output.getvalue()) == {"category": "scope_not_allowed"}


def test_cli_actual_runtime_reconciles_unselected_enabled_spaces(settings, backend):
    configured = change_policy(change_policy(settings, indexing="enabled"), sid="default", indexing="enabled")
    (settings.vault_path / "Unselected.md").write_text("Unselected synthetic note", encoding="utf-8")
    output = StringIO()
    assert _run_write_cli(configured, "capture", space="research", input_stream=BytesIO(
        json.dumps(capture_request()).encode()), output=output) == 0
    assert json.loads(output.getvalue())["result"]["committed"]
    assert (settings.vault_path.parent / "default-data/semantic-index.sqlite3").is_file()
    assert (settings.vault_path.parent / "research-data/semantic-index.sqlite3").is_file()
    assert not (settings.vault_path.parent / "denied-data").exists()
    assert backend.instances and backend.calls


@pytest.mark.parametrize("flags", [["--spa", "default", "--candidate-limit", "PRIVATE"],
    ["--spaces", "PRIVATE"], ["--source-space", "PRIVATE"], ["--destination-space", "PRIVATE"]])
def test_scoped_cli_parse_errors_do_not_echo_values(capsys, flags):
    with pytest.raises(SystemExit) as error:
        main(["promote", "review", "--source-path", SOURCE, "--capture-id", CAPTURE_ID, *flags],
             settings=Settings())
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    assert json.loads(captured.out)["category"] in {"invalid_request", "invalid_scope"}
