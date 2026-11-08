"""Acceptance repairs at the raw MCP Query input boundary, using real transports."""

import asyncio
import http.client
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
from pathlib import Path

import pytest

from app.cli import _query_cli_input, _run_scoped_cli
from app.space_read_transport import InvalidReadRequest
from tests.test_mcp_read_input_security import audit_owners, invoke
from tests.test_policy_read_adapters import AUTH, rest
from tests.test_policy_read_adapters import local_backend as local_backend
from tests.test_policy_read_adapters import runtime as runtime
from tests.test_policy_read_adapters import settings as settings
from tests.test_policy_read_repairs import http_scope, live_http, rpc


def query_body(arguments, version="2026-07-28"):
    return rpc(arguments, version=version).replace(b'"name":"read_note"', b'"name":"knowledge_query"')


def call(transport, body, settings, runtime, monkeypatch, tmp_path):
    if transport == "stdio":
        return invoke("stdio", body, settings, runtime, monkeypatch, tmp_path)
    calls = audit_owners(monkeypatch)
    headers = http_scope(body, version=transport)["headers"]
    with live_http(settings, runtime) as (address, _, _, _):
        connection = http.client.HTTPConnection(*address, timeout=10)
        try:
            connection.request("POST", "/mcp", body=body, headers={
                key.decode(): value.decode() for key, value in headers if key != b"content-length"})
            response = connection.getresponse()
            assert response.status == 200
            payload = json.loads(response.read())
        finally:
            connection.close()
    return payload, calls, b""


def assert_invalid(payload, calls, stderr):
    result = payload["result"]
    assert result.get("isError") is True, (payload, calls)
    assert result["content"] == [{"type": "text", "text": "invalid_request: Operation failed."}]
    assert "structuredContent" not in result
    assert calls == []
    assert "Needle" not in json.dumps(payload) + stderr.decode()


@pytest.mark.parametrize("version", ["2026-07-28", "2025-11-25"])
@pytest.mark.parametrize("invalid", [b"\xff", b"\xc3", b"\xc0\xaf", b"\xed\xa0\x80"])
def test_raw_stdio_utf8_query_is_rejected_before_work(settings, monkeypatch, tmp_path, invalid, version):
    (settings.vault_path / "replacement.md").write_text("Needle \ufffd", encoding="utf-8")
    body = query_body('{"literal_text":"Needle X"}', version).replace(b"Needle X", b"Needle " + invalid)
    payload, calls, stderr = call("stdio", body, settings, None, monkeypatch, tmp_path)
    if version == "2025-11-25":
        # Stdio's SDK stateless path supports only the modern version. Keep
        # protocol negotiation ahead of tool dispatch, unlike the HTTP bridge.
        assert payload["error"]["code"] == -32022
        assert payload["error"]["message"] == "Unsupported protocol version"
        assert payload["error"]["data"]["requested"] == version
        assert calls == [] and "Needle" not in json.dumps(payload) + stderr.decode()
    else:
        assert_invalid(payload, calls, stderr)


@pytest.mark.parametrize("transport", ["stdio", "2026-07-28", "2025-11-25"])
@pytest.mark.parametrize("arguments", [None, 123, True, False, 1.25, "", "literal_text", [], ["scope"], [{}]])
def test_non_object_query_arguments_are_invalid_request(settings, runtime, monkeypatch, tmp_path,
                                                       transport, arguments):
    body = query_body(json.dumps(arguments), transport if transport != "stdio" else "2026-07-28")
    assert_invalid(*call(transport, body, settings, runtime, monkeypatch, tmp_path))


@pytest.mark.parametrize("transport", ["stdio", "2026-07-28", "2025-11-25"])
@pytest.mark.parametrize("text", ["Needle \ufffd", "Život 雪"])
def test_valid_raw_utf8_query_is_preserved(settings, runtime, monkeypatch, tmp_path, transport, text):
    (settings.vault_path / "unicode.md").write_text(text, encoding="utf-8")
    body = query_body(json.dumps({"literal_text": text}, ensure_ascii=False),
                      transport if transport != "stdio" else "2026-07-28")
    payload, calls, _ = call(transport, body, settings, runtime, monkeypatch, tmp_path)
    result = payload["result"]
    assert not result.get("isError")
    assert result["structuredContent"]["items"][0]["identity"] == {"space_id": "default", "path": "unicode.md"}
    assert result["structuredContent"]["items"][0]["resource_uri"] == "vaultbridge://space/default/note/unicode.md"
    assert [row["space_id"] for row in result["structuredContent"]["coverage"]] == ["default"]
    assert "authorize_read" in calls and "operation" in calls and "scoped_session" in calls


@pytest.mark.parametrize("arguments", [None, True, 123, 1.25, "text", [], ["scope"]])
def test_non_object_validation_matches_rest_and_structured_cli(settings, runtime, monkeypatch, arguments):
    calls = audit_owners(monkeypatch)
    raw = json.dumps(arguments).encode()
    response = rest(settings, runtime).post("/api/v1/knowledge/query", headers=AUTH, content=raw)
    assert response.status_code == 422 and response.json() == {"detail": "invalid_request"}
    output = StringIO()
    assert _run_scoped_cli(settings, "query", lambda: _query_cli_input(
        request_json=True, input_stream=BytesIO(raw)), operations=runtime.operations,
        json_output=True, output=output) == 2
    assert json.loads(output.getvalue()) == {"category": "invalid_request"}
    assert calls == []


@pytest.mark.parametrize("envelope", ["arguments", "params"])
def test_invalid_encoding_and_duplicate_envelope_cannot_erase_query(settings, monkeypatch, tmp_path, envelope):
    body = query_body('{"literal_text":"Needle X"}').replace(b"Needle X", b"Needle \xff")
    if envelope == "arguments":
        body = body[:-2] + b',"arguments":{"literal_text":"Independent"}}}'
    else:
        params = json.loads(query_body('{}'))["params"]
        params.update(name="read_note", arguments={"path": "same.md"})
        body = body[:-1] + b',"params":' + json.dumps(params).encode() + b'}'
    assert_invalid(*call("stdio", body, settings, None, monkeypatch, tmp_path))


@pytest.mark.parametrize("failure", [ValueError, TypeError, RecursionError])
def test_encoding_rejection_construction_cannot_resume_sdk_input(monkeypatch, failure):
    from app import mcp_read_input
    body = query_body('{"literal_text":"Needle X"}').replace(b"Needle X", b"Needle \xff")
    observed = []
    def fail(_):
        raise failure("synthetic private construction failure")
    monkeypatch.setattr(mcp_read_input, "_rejection_message", fail)
    async def check():
        async def source():
            yield body.decode("utf-8", errors="surrogateescape")
        async for line in mcp_read_input.StrictScopedLines(source()):
            observed.append(line)
    with pytest.raises(InvalidReadRequest, match="Invalid MCP request"):
        asyncio.run(check())
    assert observed == []


@pytest.mark.parametrize("failure", [ValueError, UnicodeError, RecursionError])
def test_encoding_taint_cannot_fall_back_when_classification_fails(monkeypatch, failure):
    from app import mcp_read_input
    body = query_body('{"literal_text":"Needle X"}').replace(b"Needle X", b"Needle \xff")
    line = body.decode("utf-8", errors="surrogateescape")
    def fail(*args, **kwargs):
        raise failure("synthetic JSON classification failure")
    monkeypatch.setattr(mcp_read_input.json, "loads", fail)
    async def check():
        async def source():
            yield line
        return [value async for value in mcp_read_input.StrictScopedLines(source())]
    observed = asyncio.run(check())
    assert len(observed) == 1 and mcp_read_input.INVALID_SCOPED_JSON in observed[0]
    assert "Needle" not in observed[0] and "\ufffd" not in observed[0]
    observed[0].encode("utf-8", errors="strict")


@pytest.mark.parametrize("fail_rejection", [False, True])
def test_stdio_framing_and_rejection_boundary(settings, tmp_path, fail_rejection):
    audit = tmp_path / "frame-audit.log"
    process = subprocess.Popen([sys.executable, "-m", "tests.policy_read_stdio_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "VB_TEST_INPUT_AUDIT": str(audit),
             **({"VB_TEST_FAIL_REJECTION": "1"} if fail_rejection else {}),
             "VB_TEST_PRIVATE_SETTINGS": json.dumps({**settings.model_dump(mode="json"),
                                                     "knowledge_spaces_json": settings.knowledge_spaces_json})},
        cwd=Path(__file__).parents[1])
    (settings.vault_path / "replacement.md").write_text("Needle \ufffd", encoding="utf-8")
    def send_and_read(pool, body):
        process.stdin.write(body + b"\r\n")
        process.stdin.flush()
        return pool.submit(process.stdout.readline).result(10)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            first = send_and_read(pool, query_body('{"literal_text":"Needle X"}').replace(b" X", b" \xff"))
            assert not audit.exists(), "malformed first frame executed request work"
            if fail_rejection:
                assert first == b""
            else:
                assert_invalid(json.loads(first), [], b"")
                valid = query_body('{"literal_text":"Needle \\ufffd"}').replace(b'"id":101', b'"id":102')
                result = json.loads(send_and_read(pool, valid))
                assert result["id"] == 102 and not result["result"].get("isError")
                assert result["result"]["structuredContent"]["items"][0]["identity"]["path"] == "replacement.md"
                legacy = rpc('{"path":"same.md"}').replace(b'"id":101', b'"id":103')
                result = json.loads(send_and_read(pool, legacy))
                assert result["id"] == 103 and result["result"]["structuredContent"]["path"] == "same.md"
                assert "space_id" not in result["result"]["structuredContent"]
        finally:
            process.stdin.close()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(10)
    stderr = process.stderr.read().decode()
    assert process.returncode != 0 if fail_rejection else process.returncode == 0, stderr
    if fail_rejection:
        assert "Invalid MCP request." in stderr and "synthetic private rejection construction" not in stderr
        assert not audit.exists()


def test_legacy_omitted_stdio_retains_prior_decoding_compatibility(settings, monkeypatch, tmp_path):
    (settings.vault_path / "replacement.md").write_text("Needle \ufffd", encoding="utf-8")
    body = rpc('{"query":"Needle X"}').replace(b'"name":"read_note"', b'"name":"search_notes"')
    body = body.replace(b"Needle X", b"Needle \xff")
    payload, calls, _ = call("stdio", body, settings, None, monkeypatch, tmp_path)
    result = payload["result"]["structuredContent"]
    assert result["query"] == "Needle \ufffd" and result["results"][0]["path"] == "replacement.md"
    assert "coverage" not in result and "authorize_read" in calls
