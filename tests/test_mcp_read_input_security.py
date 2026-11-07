"""Raw MCP rejection must never resume dispatch of a rejected request."""

import http.client
import json
from functools import wraps

import pytest

from app.services.knowledge_spaces import SpacePolicyResolver
from app.services.space_admission import ScopedOperationAdmission
from app.services.vault import VaultService
from tests.test_policy_read_adapters import local_backend as local_backend
from tests.test_policy_read_adapters import runtime as runtime
from tests.test_policy_read_adapters import settings as settings
from tests.test_policy_read_repairs import duplicate_envelopes, http_scope, live_http, read_body, rpc, stdio_request


def exploit(number):
    body = rpc('{"space":"unknown","path":"same.md"},"arguments":{"path":"same.md"}')
    return body[:-1] + b',"untrusted":' + number.encode() + b"}"


def resource_envelopes():
    base = read_body("qualified_resource").decode()
    qualified = '"vaultbridge://space/default/note/same.md"'
    legacy = '"vaultbridge://note/same.md"'
    packet = json.loads(base)
    omitted = {**packet["params"], "uri": json.loads(legacy)}
    cases = {
        "uri_erasure": base.replace('"uri": ' + qualified, '"uri": ' + qualified + ',"uri":' + legacy).encode(),
        "params_erasure": (base[:-1] + ',"params":' + json.dumps(omitted) + '}').encode(),
    }
    cases.update({
        "named_uri_erasure": cases["uri_erasure"].replace(b"space/default/", b"space/space-1/"),
        "unknown_params_erasure": cases["params_erasure"].replace(b"space/default/", b"space/unknown/"),
        "escaped_uri": cases["uri_erasure"].replace(b'"uri":', b'"\\u0075ri":'),
        "qualified_last": base.replace('"uri": ' + qualified, '"uri":' + legacy + ',"uri":' + qualified).encode(),
        "legacy_duplicate": cases["uri_erasure"].replace(qualified.encode(), legacy.encode()),
        "jsonrpc": base.replace('"jsonrpc": "2.0"', '"jsonrpc":"2.0","jsonrpc":"2.0"').encode(),
        "id": base.replace('"id": 101', '"id":100,"id":101').encode(),
        "method": base.replace('"method": "resources/read"',
                               '"method":"resources/read","method":"resources/read"').encode(),
        "nested": base.replace('"_meta": {', '"_meta": {"extra":{"k":1,"k":2},').encode(),
    })
    for number in ("1e400", "NaN", "Infinity"):
        cases["nonfinite_" + number] = base[:-1].encode() + b',"extra":' + number.encode() + b'}'
        cases["duplicate_nonfinite_" + number] = cases["params_erasure"][:-1] + b',"extra":' + number.encode() + b'}'
    return cases


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("case", resource_envelopes())
def test_resource_selection_cannot_be_erased(settings, runtime, monkeypatch, tmp_path, transport, case):
    payload, calls, stderr = invoke(transport, resource_envelopes()[case], settings, runtime, monkeypatch, tmp_path)
    assert "error" in payload, (payload, calls)
    assert payload["error"]["message"] == "note_resource_unavailable: Note resource is invalid or unavailable."
    assert calls == []
    assert "Independent" not in json.dumps(payload) + stderr.decode()


def invalid_resource_envelopes():
    packet = json.loads(read_body("qualified_resource"))
    cases = {}
    for name, uri in {
        "missing": None, "numeric": 123, "object": {"SECRET": "default"},
        "escaped_unreserved": "vaultbridge://space/default/note/%73ame.md",
        "raw_slash": "vaultbridge://space/default/note/folder/same.md",
        "double_encoded": "vaultbridge://space/default/note/%252Fsame.md",
        "invalid_utf8": "vaultbridge://space/default/note/%ff",
        "fragment": "vaultbridge://space/default/note/same.md#SECRET",
        "query": "vaultbridge://space/default/note/same.md?SECRET",
        "invalid_id": "vaultbridge://space/Default/note/same.md",
        "port": "vaultbridge://space:4/default/note/same.md",
        "long_path": "vaultbridge://space/default/note/" + "x" * 1025,
    }.items():
        params = {**packet["params"], "uri": uri}
        if uri is None:
            params.pop("uri")
        cases[name] = json.dumps({**packet, "params": params}).encode()
    return cases


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("case", invalid_resource_envelopes())
def test_invalid_resource_rejected_before_work(settings, runtime, monkeypatch, tmp_path, transport, case):
    from app.mcp_read_input import INVALID_SCOPED_JSON, normalize_scoped_message

    body = invalid_resource_envelopes()[case]
    normalized = json.loads(normalize_scoped_message(body))
    assert normalized["params"][INVALID_SCOPED_JSON] is True
    assert normalized["method"] == "resources/read"
    original_uri = json.loads(body)["params"].get("uri")
    assert normalized["params"]["uri"] == (original_uri if isinstance(original_uri, str) else "vaultbridge://invalid")
    payload, calls, stderr = invoke(transport, body, settings, runtime, monkeypatch, tmp_path)
    assert payload["id"] == 101 and payload["jsonrpc"] == "2.0"
    if transport == "http" and not isinstance(original_uri, str):
        assert payload["error"]["message"] == "mcp-name header does not match the request body's 'uri' parameter"
    else:
        assert payload["error"]["code"] == -32602
        assert payload["error"]["message"] == "note_resource_unavailable: Note resource is invalid or unavailable."
    assert calls == []
    assert "SECRET" not in json.dumps(payload) + stderr.decode()


@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_resource_method_cannot_be_overwritten(settings, runtime, monkeypatch, tmp_path, transport):
    packet = json.loads(read_body("omitted"))
    body = (read_body("qualified_resource")[:-1] + b',"method":"tools/call","params":'
            + json.dumps(packet["params"]).encode() + b'}')
    payload, calls, _ = invoke(transport, body, settings, runtime, monkeypatch, tmp_path)
    assert payload["result"]["isError"] is True
    assert calls == []


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("space", [None, "default", "space-1"])
@pytest.mark.parametrize("path", ["same.md", "café #%.md"])
def test_resource_identity_preserved(settings, runtime, monkeypatch, tmp_path, transport, space, path):
    from urllib.parse import quote

    from app.mcp_read_input import normalize_scoped_message

    root = settings.vault_path.parent / (space or "default")
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    text = f"# {space or 'default'}\nIndependent {space or 'default'} notes"
    target.write_text(text, encoding="utf-8")
    packet = json.loads(read_body("legacy_resource"))
    component = quote(path, safe="")
    uri = f"vaultbridge://space/{space}/note/{component}" if space else f"vaultbridge://note/{component}"
    packet["params"]["uri"] = uri
    body = json.dumps(packet).encode()
    assert normalize_scoped_message(body) is body
    payload, calls, _ = invoke(transport, body, settings, runtime, monkeypatch, tmp_path)
    assert "result" in payload, payload
    assert payload["result"]["contents"] == [{
        "uri": uri, "mimeType": "text/markdown",
        "text": text,
    }]
    assert "authorize_read" in calls and "operation" in calls and "read_note" in calls


def test_resource_rejection_stops_before_sdk_handler():
    import asyncio
    from types import SimpleNamespace

    from mcp.shared.exceptions import MCPError

    from app.mcp_read_input import normalize_scoped_message
    from app.mcp_server import _scoped_read_middleware

    packet = json.loads(normalize_scoped_message(resource_envelopes()["uri_erasure"]))

    async def sdk_handler(_):
        pytest.fail("rejected resource dispatched to SDK handler")

    with pytest.raises(MCPError, match="note_resource_unavailable"):
        asyncio.run(_scoped_read_middleware(None)(SimpleNamespace(**packet), sdk_handler))


def test_canonical_subfolder_uri_is_preserved_before_sdk():
    from app.mcp_read_input import normalize_scoped_message
    from app.space_read_transport import parse_resource_uri, scoped_resource_uri

    path = "folder/café #%.md"
    uri = scoped_resource_uri("space-1", path)
    packet = json.loads(read_body("qualified_resource"))
    packet["params"]["uri"] = uri
    body = json.dumps(packet).encode()
    assert normalize_scoped_message(body) is body
    assert parse_resource_uri(uri).relative_path == path


def audit_owners(monkeypatch):
    calls = []
    for owner, name in (
        (SpacePolicyResolver, "authorize_read"),
        (ScopedOperationAdmission, "operation"),
        (VaultService, "read_note"),
        (VaultService, "scoped_session"),
    ):
        original = getattr(owner, name)

        def wrap(original, name):
            @wraps(original)
            def recorded(*args, **kwargs):
                calls.append(name)
                return original(*args, **kwargs)

            return recorded

        monkeypatch.setattr(owner, name, wrap(original, name))
    return calls


def invoke(transport, body, settings, runtime, monkeypatch, tmp_path):
    if transport == "stdio":
        audit = tmp_path / "input-audit.log"
        monkeypatch.setenv("VB_TEST_INPUT_AUDIT", str(audit))
        payload, stderr = stdio_request(settings, body)
        calls = audit.read_text().splitlines() if audit.exists() else []
    else:
        calls = audit_owners(monkeypatch)
        # Malformed resource parameter types have no string routing header.
        header_body = json.loads(body)
        if not isinstance(header_body["params"].get("uri", ""), str):
            header_body["params"].pop("uri")
        headers = http_scope(json.dumps(header_body).encode())["headers"]
        with live_http(settings, runtime) as (address, _, _, _):
            connection = http.client.HTTPConnection(*address, timeout=10)
            try:
                connection.request(
                    "POST",
                    "/mcp",
                    body=body,
                    headers={key.decode(): value.decode() for key, value in headers if key != b"content-length"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
            finally:
                connection.close()
        stderr = b""
    return payload, calls, stderr


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("number", ["1e400", "NaN", "Infinity"])
def test_nonfinite_cannot_reopen_duplicate_rejection(settings, runtime, monkeypatch, tmp_path, transport, number):
    payload, calls, stderr = invoke(transport, exploit(number), settings, runtime, monkeypatch, tmp_path)
    assert payload.get("result", {}).get("isError") is True, (payload, calls)
    assert payload["result"]["content"] == [{"type": "text", "text": "invalid_request: Operation failed."}]
    assert calls == []
    assert "Independent" not in json.dumps(payload) + stderr.decode()
    assert "unknown" not in json.dumps(payload) + stderr.decode()


def hostile_envelopes():
    base = exploit("1e400")
    cases = {key: body[:-1] + b',"untrusted":1e400}' for key, body in duplicate_envelopes().items()}
    cases.update(
        {
            "negative_overflow": exploit("-1e400"),
            "negative_infinity": exploit("-Infinity"),
            "nested_nonfinite": base.replace(b'"untrusted":1e400', b'"untrusted":{"SECRET":[NaN]}'),
            "params_nonfinite": base.replace(b'"name":"read_note"', b'"extra":NaN,"name":"read_note"'),
            "meta_nonfinite": base.replace(b'"_meta":{', b'"_meta":{"SECRET":Infinity,'),
            "invalid_id": base.replace(b'"id":101', b'"id":{"SECRET":NaN}'),
            "standalone_overflow": rpc('{"space":"default","path":"same.md"}')[:-1] + b',"extra":1e400}',
            "standalone_nan": rpc('{"space":"default","path":"same.md"}')[:-1] + b',"extra":NaN}',
            "standalone_infinity": rpc('{"space":"default","path":"same.md"}')[:-1] + b',"extra":Infinity}',
        }
    )
    return cases


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("case", hostile_envelopes())
def test_hostile_envelope_rejects_before_any_work(settings, runtime, monkeypatch, tmp_path, transport, case):
    payload, calls, stderr = invoke(transport, hostile_envelopes()[case], settings, runtime, monkeypatch, tmp_path)
    assert payload.get("result", {}).get("isError") is True, (payload, calls)
    assert payload["result"]["content"] == [{"type": "text", "text": "invalid_request: Operation failed."}]
    assert payload["id"] == (0 if case == "invalid_id" else 101)
    assert calls == []
    for secret in ("unknown", "Independent", "resource_uri", "SECRET"):
        assert secret not in json.dumps(payload) + stderr.decode()


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("mode", ["explicit", "omitted", "qualified_resource", "legacy_resource", "tools"])
def test_valid_requests_retain_framing_correlation_and_legacy_shapes(
    settings, runtime, monkeypatch, tmp_path, transport, mode
):
    body = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        ).encode()
        if mode == "tools"
        else read_body(mode)
    )
    payload, calls, _ = invoke(transport, body, settings, runtime, monkeypatch, tmp_path)
    assert payload["id"] == 101 and payload["jsonrpc"] == "2.0"
    assert "error" not in payload and payload["result"].get("isError") is not True
    if mode == "tools":
        assert "read_note" in {tool["name"] for tool in payload["result"]["tools"]}
        assert calls == []
    else:
        assert "read_note" in calls and "operation" in calls and "authorize_read" in calls
        if mode in {"explicit", "omitted"}:
            result = payload["result"]["structuredContent"]
            if mode == "explicit":
                assert result["space_id"] == "default"
                assert result["result"]["resource_uri"] == "vaultbridge://space/default/note/same.md"
            else:
                assert "space_id" not in result
                assert result["resource_uri"] == "vaultbridge://note/same.md"


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("mode", ["explicit", "omitted", "tools", "qualified_resource", "legacy_resource"])
def test_valid_input_is_preserved_byte_for_byte(binary, mode):
    from app.mcp_read_input import normalize_scoped_message

    body = read_body(mode) if mode != "tools" else b'{"jsonrpc":"2.0","id":"correlation","method":"tools/list"}'
    raw = body if binary else body.decode()
    assert normalize_scoped_message(raw) is raw


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("identifier", [101, "correlation"])
def test_rejection_is_allowlisted_and_correlated(binary, identifier):
    from app.mcp_read_input import INVALID_SCOPED_JSON, normalize_scoped_message

    raw = exploit("1e400").replace(b'"id":101', b'"id":' + json.dumps(identifier).encode())
    rejected = json.loads(normalize_scoped_message(raw if binary else raw.decode()))
    assert rejected == {
        "jsonrpc": "2.0",
        "id": identifier,
        "method": "tools/call",
        "params": {
            "name": "read_note",
            "arguments": {"scope": True, INVALID_SCOPED_JSON: True},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


@pytest.mark.parametrize("failure", [ValueError, TypeError, RecursionError])
@pytest.mark.parametrize("boundary", ["http", "stdio"])
@pytest.mark.parametrize("kind", ["tool", "resource"])
def test_rejection_construction_failure_never_dispatches_original(monkeypatch, failure, boundary, kind):
    import asyncio

    from app import mcp_read_input
    from app.space_read_transport import InvalidReadRequest

    raw = exploit("1e400") if kind == "tool" else resource_envelopes()["duplicate_nonfinite_1e400"]
    observed = []

    def fail(_):
        raise failure("SECRET construction value")

    monkeypatch.setattr(mcp_read_input, "_rejection_message", fail)

    async def check():
        if boundary == "stdio":

            async def source():
                yield raw.decode()

            async for message in mcp_read_input.StrictScopedLines(source()):
                observed.append(message)
        else:

            async def sdk(scope, receive, send):
                observed.append(await receive())

            async def receive():
                return {"type": "http.request", "body": raw, "more_body": False}

            async def send(_):
                pytest.fail("raw input boundary sent a successful response")

            await mcp_read_input.StrictScopedHTTPInput(sdk)(http_scope(raw), receive, send)

    with pytest.raises(InvalidReadRequest, match=r"^Invalid MCP request\.$") as error:
        asyncio.run(check())
    assert error.value.__suppress_context__
    assert observed == []


@pytest.mark.parametrize("kind", ["tool", "resource"])
def test_serialization_failure_after_rejection_cannot_fall_back(monkeypatch, kind):
    from app import mcp_read_input
    from app.space_read_transport import InvalidReadRequest

    raw = exploit("1e400") if kind == "tool" else resource_envelopes()["duplicate_nonfinite_1e400"]

    def fail(*args, **kwargs):
        raise ValueError("SECRET serialization value")

    monkeypatch.setattr(mcp_read_input.json, "dumps", fail)
    with pytest.raises(InvalidReadRequest, match=r"^Invalid MCP request\.$"):
        mcp_read_input.normalize_scoped_message(raw)


@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_rejected_envelope_cannot_be_redirected_to_resource(settings, runtime, monkeypatch, tmp_path, transport):
    packet = json.loads(read_body("legacy_resource"))
    body = (
        exploit("1e400")[:-1] + b',"method":"resources/read","params":' + json.dumps(packet["params"]).encode() + b"}"
    )
    payload, calls, stderr = invoke(transport, body, settings, runtime, monkeypatch, tmp_path)
    assert payload.get("result", {}).get("isError") is True or "error" in payload
    assert "contents" not in payload.get("result", {})
    assert calls == []
    for secret in ("unknown", "Independent", "resource_uri", "same.md"):
        assert secret not in json.dumps(payload) + stderr.decode()


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("kind", ["tool", "resource"])
def test_real_transport_aborts_when_rejection_construction_fails(
    settings, runtime, monkeypatch, tmp_path, transport, caplog, kind
):
    from app import mcp_read_input

    raw = exploit("1e400") if kind == "tool" else resource_envelopes()["duplicate_nonfinite_1e400"]
    if transport == "stdio":
        import os
        import subprocess
        import sys
        from concurrent.futures import ThreadPoolExecutor

        audit = tmp_path / "aborted-input-audit.log"
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.policy_read_stdio_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "VB_TEST_INPUT_AUDIT": str(audit),
                "VB_TEST_FAIL_REJECTION": "1",
                "VB_TEST_PRIVATE_SETTINGS": json.dumps(
                    {**settings.model_dump(mode="json"), "knowledge_spaces_json": settings.knowledge_spaces_json}
                ),
            },
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                process.stdin.write(raw + b"\n")
                process.stdin.flush()
                response = pool.submit(process.stdout.readline).result(10)
            finally:
                process.stdin.close()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(10)
        stderr = process.stderr.read().decode()
        assert process.returncode != 0 and response == b""
        assert "Invalid MCP request." in stderr
        assert "synthetic private rejection construction" not in stderr
        assert not audit.exists()
    else:
        calls = audit_owners(monkeypatch)

        def fail(_):
            raise ValueError("SECRET construction value")

        monkeypatch.setattr(mcp_read_input, "_rejection_message", fail)
        with live_http(settings, runtime) as (address, _, _, _):
            connection = http.client.HTTPConnection(*address, timeout=10)
            try:
                connection.request(
                    "POST",
                    "/mcp",
                    body=raw,
                    headers={key.decode(): value.decode() for key, value in http_scope(raw)["headers"]},
                )
                response = connection.getresponse()
                assert response.status >= 400
                stderr = response.read().decode()
            finally:
                connection.close()
        assert calls == []
    for secret in ("unknown", "Independent", "resource_uri", "SECRET"):
        assert secret not in stderr + caplog.text


def test_parse_failure_after_duplicate_detection_never_restores_raw():
    from app.mcp_read_input import normalize_scoped_message

    raw = exploit("1e400") + b" trailing SECRET"
    rejected = normalize_scoped_message(raw)
    assert rejected != raw
    assert "SECRET" not in rejected.decode()
    assert json.loads(rejected)["params"]["arguments"]["__vaultbridge_invalid_scoped_json"] is True
