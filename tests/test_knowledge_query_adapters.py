"""Slice B: shared strict requests through real policy/domain owners and transports."""

import asyncio
import json
import threading
from io import BytesIO, StringIO

import pytest
from mcp import Client

from app.api.private_reads import create_private_read_app
from app.cli import _query_cli_input, _run_scoped_cli, main, run_knowledge_query
from app.knowledge_query_transport import knowledge_query_schema, parse_knowledge_query
from app.mcp_server import create_private_read_server
from app.services.knowledge_spaces import Capability, SpaceError
from app.services.persisted_space_reads import compose_persisted_space_reads
from app.services.scoped_budget import CancellationToken
from app.services.space_operations import SpaceOperations
from app.services.space_runtime import compose_space_runtime
from app.services.vault import VaultService
from app.space_read_transport import execute_scoped, public_error, scoped_resource_uri
from tests.test_mcp_read_input_security import invoke as raw_invoke
from tests.test_policy_read_adapters import AUTH, rest, tool
from tests.test_policy_read_adapters import local_backend as local_backend
from tests.test_policy_read_adapters import runtime as runtime
from tests.test_policy_read_adapters import settings as settings
from tests.test_policy_read_repairs import http_scope, request, rpc, transport_for
from tests.test_space_runtime import configuration

URL = "/api/v1/knowledge/query"


def assert_parity(settings, runtime, data):
    expected = execute_scoped(runtime.operations, "query", data)
    response = rest(settings, runtime).post(URL, headers=AUTH, json=data)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == expected
    response = tool(create_private_read_server(settings=settings, operations=runtime.operations),
                    "knowledge_query", data)
    assert not response.is_error, response
    projected = response.structured_content
    assert json.loads(response.content[0].text) == projected
    for row in projected["items"]:
        assert row.pop("resource_uri") == scoped_resource_uri(**row["identity"])
    assert projected == expected
    output = StringIO()
    assert _run_scoped_cli(settings, "query", data, operations=runtime.operations,
                           json_output=True, output=output) == 0
    assert json.loads(output.getvalue()) == expected
    return expected


@pytest.mark.parametrize("texts", [
    {"literal_text": "Independent"}, {"semantic_text": "Independent"},
    {"semantic_text": "Independent", "literal_text": "Independent"},
])
@pytest.mark.parametrize("spaces", [None, ["default"], ["space-1"], ["space-1", "default", "space-1"]])
def test_real_query_modes_and_selection_parity(settings, runtime, texts, spaces):
    data = {**texts, **({"spaces": spaces} if spaces is not None else {})}
    result = assert_parity(settings, runtime, data)
    selected = sorted(set(spaces or ["default"]))
    assert [row["identity"] for row in result["items"]] == [
        {"space_id": sid, "path": "same.md"} for sid in selected]
    assert [row["space_id"] for row in result["coverage"]] == selected
    assert all(row["semantic_index_basis"] == ("compatible_ready" if "semantic_text" in texts else "none")
               for row in result["coverage"])
    assert result["ordering"] == ("semantic" if "semantic_text" in texts else "canonical_path")
    if "semantic_text" in texts:
        assert all({"score", "semantic_score", "lexical_score", "heading", "snippet"} <= row.keys()
                   for row in result["items"])
    else:
        assert all(row["snippet"] is None and row["modified"] is None for row in result["items"])


@pytest.mark.parametrize("direction", ["incoming", "outgoing"])
@pytest.mark.parametrize("origin", [None, "obsidian_wikilink", "markdown_link"])
@pytest.mark.parametrize("semantic", [False, True])
def test_typed_live_filters_and_local_relationships(settings, runtime, direction, origin, semantic):
    root = settings.vault_path
    content = "---\ntags: [ exact, other ]\nkind: 2\nseq: [2, true]\nnullable: null\n---\nNeedle\n"
    (root / "same.md").write_text(content + "[[Target]] [target](Target.md)", encoding="utf-8")
    (root / "Target.md").write_text("[[same]] [source](same.md)", encoding="utf-8")
    predicate = {"direction": direction, "other": {"space_id": "default", "path": "Target.md"}}
    if origin is not None:
        predicate["origin"] = origin
    data = {"spaces": ["default", "space-1"], "literal_text": "Needle", "tags": [" exact ", "other"],
            "metadata": [{"key": "kind", "operator": "equals", "value": 2},
                         {"key": "seq", "operator": "sequence_contains", "value": True},
                         {"key": "nullable", "operator": "equals", "value": None},
                         {"key": "kind", "operator": "not_equals", "value": 3},
                         {"key": "kind", "operator": "exists"}], "relationships": [predicate]}
    if semantic:
        data["semantic_text"] = "Independent"
    result = assert_parity(settings, runtime, data)
    assert [row["identity"] for row in result["items"]] == [{"space_id": "default", "path": "same.md"}]
    # Typed integer != bool; live filters exclude stale indexed content before ranking.
    data["metadata"][0]["value"] = True
    assert assert_parity(settings, runtime, data)["items"] == []


def invalid_requests():
    yield {}, "invalid_request"
    for field in ("semantic_text", "literal_text", "folder", "paths", "tags", "metadata", "relationships", "limit"):
        yield {"literal_text": "Independent", field: None}, "invalid_request"
    for spaces in (None, [], ["default"] * 9, "default", [True], ["Default"], ["*"], ["default,research"]):
        yield {"literal_text": "Independent", "spaces": spaces}, "invalid_scope"
    for field in ("space", "space_id", "scope"):
        yield {"literal_text": "Independent", field: "default"}, "invalid_scope"
    for limit in (0, 101, True, 1.0, "20"):
        yield {"literal_text": "Independent", "limit": limit}, "invalid_request"
    for field, value in (("semantic_text", "x" * 4097), ("literal_text", "é" * 4097),
                         ("folder", "x" * 1025), ("semantic_text", ""), ("folder", ""),
                         ("tags", ["x"] * 17), ("tags", [""]), ("tags", ["  "]),
                         ("tags", [True]), ("tags", ["x" * 1025]),
                         ("paths", [{"space_id": "default", "path": "same.md"}] * 65),
                         ("paths", ["same.md"]), ("paths", [{"space_id": "default", "path": "x" * 1025}]),
                         ("metadata", [{"key": "kind", "operator": "exists"}] * 17),
                         ("metadata", [{"key": "x" * 257, "operator": "exists"}]),
                         ("metadata", [{"key": "x", "operator": "equals", "value": 2**63}]),
                         ("metadata", [{"key": "x", "operator": "equals", "value": []}]),
                         ("metadata", [{"key": "x", "operator": "equals", "value": "x" * 1025}]),
                         ("metadata", [{"key": "x", "operator": "equals"}]),
                         ("metadata", [{"key": "x", "operator": "exists", "value": None}]),
                         ("metadata", [{"key": "x", "operator": "bad"}]),
                         ("metadata", [{"key": "x", "operator": "exists", "extra": 1}]),
                         ("relationships", [{"direction": "outgoing", "other": {"space_id": "default", "path": "a.md"},
                                             "origin": None}]),
                         ("relationships", [{"direction": "both", "other": {"space_id": "default", "path": "a.md"}}]),
                         ("relationships", [{"direction": "incoming", "other": {"space_id": "default", "path": "a.md"}}]
                          * 17), ("extra", "PRIVATE")):
        yield {"literal_text": "Independent", field: value}, "invalid_request"


@pytest.mark.parametrize("data,code", list(invalid_requests()))
def test_shared_strict_validation_before_work(settings, runtime, monkeypatch, data, code):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid request reached admission/owner")
    monkeypatch.setattr(SpaceOperations, "_admit", forbidden)
    monkeypatch.setattr(VaultService, "scoped_session", forbidden)
    response = rest(settings, runtime).post(URL, headers=AUTH, json=data)
    assert response.json() == {"detail": code}, response.text
    assert response.status_code == 422
    response = tool(create_private_read_server(settings=settings, operations=runtime.operations),
                    "knowledge_query", data)
    assert response.is_error and response.content[0].text == f"{code}: Operation failed."
    output = StringIO()
    assert _run_scoped_cli(settings, "query", data, operations=runtime.operations,
                           json_output=True, output=output) == 2
    assert json.loads(output.getvalue()) == {"category": code}


@pytest.mark.parametrize("body", [
    b'{"literal_text":"a","literal_text":"PRIVATE"}', b'{"literal_text":"a","limit":NaN}',
    b'{"literal_text":"a","limit":1e400}', b'{"literal_text":"a"}{}',
    b'{"literal_text":"\\ud800"}', b'{"literal_text":"\xff"}',
    b'{"literal_text":"a","metadata":[{"key":"x","operator":"equals","value":Infinity}]}',
])
def test_rest_and_stdin_raw_json_rejections(settings, runtime, body):
    response = rest(settings, runtime).post(URL, headers=AUTH, content=body)
    assert response.status_code == 422 and response.json() == {"detail": "invalid_request"}
    output = StringIO()
    assert _run_scoped_cli(settings, "query", lambda: _query_cli_input(request_json=True, input_stream=BytesIO(body)),
                           operations=runtime.operations, json_output=True, output=output) == 2
    assert json.loads(output.getvalue()) == {"category": "invalid_request"}


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("arguments", [
    '"arguments":{"literal_text":"a","literal_text":"PRIVATE"}',
    '"arguments":{"literal_text":"a","limit":1e400}',
    '"arguments":{"literal_text":"a","limit":NaN}',
    '"arguments":{"spaces":["unknown"],"literal_text":"a"},"arguments":{"literal_text":"Independent"}',
    '"arguments":{"literal_text":"a","metadata":[{"key":"x","operator":"equals","value":1,"value":2}]}',
])
def test_raw_mcp_query_always_strict_even_without_spaces(settings, runtime, monkeypatch, tmp_path,
                                                       transport, arguments):
    body = rpc("{}").replace(b'"name":"read_note","arguments":{}',
                             b'"name":"knowledge_query",' + arguments.encode())
    payload, calls, stderr = raw_invoke(transport, body, settings, runtime, monkeypatch, tmp_path)
    assert payload["result"]["isError"] is True, payload
    assert payload["result"]["content"] == [{"type": "text", "text": "invalid_request: Operation failed."}]
    assert calls == []
    assert "PRIVATE" not in json.dumps(payload) + stderr.decode()


def test_openapi_and_mcp_exact_query_schema(settings, runtime):
    schema = rest(settings, runtime).app.openapi()
    endpoint = schema["paths"][URL]["post"]
    assert endpoint["operationId"] == "queryKnowledgeV1"
    assert "/knowledge/query" not in schema["paths"]
    assert endpoint["requestBody"]["content"]["application/json"]["schema"] == knowledge_query_schema()
    assert len(endpoint["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]) == 2
    assert "HTTPValidationError" not in str(endpoint["responses"]["422"])
    async def inspect():
        async with Client(create_private_read_server(settings=settings, operations=runtime.operations)) as client:
            tools = (await client.list_tools()).tools
            query, = [row for row in tools if row.name == "knowledge_query"]
            assert query.input_schema == knowledge_query_schema()
            assert query.annotations.read_only_hint is True
    asyncio.run(inspect())


@pytest.mark.parametrize("version", ["2026-07-28", "2025-11-25"])
def test_real_http_query_protocol_paths(settings, runtime, version):
    server, app = transport_for(settings, runtime)
    data = {"spaces": ["space-1", "default"], "semantic_text": "Independent", "literal_text": "Independent"}
    body = rpc(json.dumps(data), version=version).replace(b'"name":"read_note"', b'"name":"knowledge_query"')
    async def invoke():
        async with server.session_manager.run():
            sent = await request(app, body, version=version)
        assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 200
        payload = json.loads(b"".join(m.get("body", b"") for m in sent))
        result = payload["result"]["structuredContent"]
        assert result == execute_scoped(runtime.operations, "query", data, resource_uris=True)
    asyncio.run(invoke())


@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_real_transport_omitted_query_and_canonical_uri(settings, runtime, monkeypatch, tmp_path, transport):
    root = settings.vault_path
    (root / "folder").mkdir()
    (root / "folder" / "note.md").write_text("Needle", encoding="utf-8")
    body = rpc('{"literal_text":"Needle"}').replace(b'"name":"read_note"', b'"name":"knowledge_query"')
    payload, calls, _ = raw_invoke(transport, body, settings, runtime, monkeypatch, tmp_path)
    result = payload["result"]["structuredContent"]
    assert result["items"][0]["identity"] == {"space_id": "default", "path": "folder/note.md"}
    assert result["items"][0]["resource_uri"] == "vaultbridge://space/default/note/folder%2Fnote.md"
    assert "authorize_read" in calls and "operation" in calls


@pytest.mark.parametrize("failure", ["denied", "capability", "dialect", "disabled", "selector"])
def test_profile_preflight_denial_before_owner_construction(tmp_path, backend, monkeypatch, failure):
    settings = configuration(tmp_path, denied=("space-1",) if failure == "denied" else (),
        disabled=("space-1",) if failure == "disabled" else (),
        capabilities=[c.value for c in Capability if c != Capability.RELATIONSHIPS]
                     if failure == "capability" else None)
    if failure == "dialect":
        config = json.loads(settings.knowledge_spaces_json)
        config["spaces"][1]["dialects"] = ["obsidian_wikilink"]
        settings = settings.model_copy(update={"knowledge_spaces_json": json.dumps(config)})
        # model_copy deliberately does not reparse settings; reconstruct the typed configuration.
        settings = type(settings).model_validate({**settings.model_dump(), "knowledge_spaces_json": json.dumps(config)})
    data = {"spaces": ["default", "space-1"], "literal_text": "Independent", "relationships": [
        {"direction": "outgoing", "other": {"space_id": "unknown" if failure == "selector" else "default",
                                             "path": "same.md"}}]}
    if failure == "disabled":
        data["semantic_text"] = "Independent"
    def forbidden(*args, **kwargs):
        pytest.fail("preflight failure constructed an owner")
    monkeypatch.setattr(VaultService, "__init__", forbidden)
    request_value = parse_knowledge_query(data)
    with pytest.raises(SpaceError) as caught:
        compose_persisted_space_reads(settings, operation="query", scope=request_value.scope, request=request_value)
    assert public_error(caught.value)[0] == {
        "denied": "scope_not_allowed", "selector": "invalid_scope",
    }.get(failure, "capability_unavailable")
    assert backend.instances == []


def test_coverage_empty_partial_unavailable_and_global_limit(settings, runtime):
    empty = assert_parity(settings, runtime, {"literal_text": "absent"})
    assert empty["items"] == [] and empty["state"] == "complete" and not empty["result_limited"]
    limited = assert_parity(settings, runtime, {"spaces": ["default", "space-1"],
                                               "literal_text": "Independent", "limit": 1})
    assert limited["result_limited"] and limited["items"][0]["identity"]["space_id"] == "default"
    other = settings.vault_path.parent / "space-1"
    (other / "same.md").unlink()
    other.rmdir()
    partial = assert_parity(settings, runtime, {"spaces": ["default", "space-1"], "literal_text": "Independent"})
    assert partial["state"] == "partial" and partial["coverage"][1]["reasons"] == ["root_unavailable"]
    response = rest(settings, runtime).post(URL, headers=AUTH,
                                          json={"spaces": ["space-1"], "literal_text": "Independent"})
    assert response.status_code == 503 and response.json() == {"detail": "space_unavailable"}


@pytest.mark.parametrize("state,basis", [("indexing", "compatible_previous_refresh"),
                                       ("error", "compatible_previous_error")])
def test_query_semantic_previous_basis_and_index_unavailability(settings, runtime, state, basis):
    from tests.test_space_runtime import binding
    owner = binding(runtime)._owners.semantic
    owner.repository.set_metadata("index_state", state)
    data = {"spaces": ["default", "space-1"], "semantic_text": "Independent"}
    result = assert_parity(settings, runtime, data)
    assert result["coverage"][0]["semantic_index_basis"] == basis
    binding(runtime, "space-1")._owners.semantic.repository.db_path.write_bytes(b"synthetic corrupt index")
    result = assert_parity(settings, runtime, data)
    assert result["state"] == "partial" and result["coverage"][1]["reasons"] == ["index_unavailable"]
    owner.repository.db_path.write_bytes(b"synthetic corrupt index")
    response = rest(settings, runtime).post(URL, headers=AUTH, json=data)
    assert response.status_code == 503 and response.json() == {"detail": "semantic_unavailable"}


@pytest.mark.parametrize("field,maximum", [("semantic_text", 4096), ("literal_text", 8192), ("folder", 1024)])
def test_exact_utf8_bounds(field, maximum):
    data = {field: "é" * (maximum // 2)}
    assert parse_knowledge_query(data).query is not None
    with pytest.raises(Exception) as caught:
        parse_knowledge_query({field: data[field] + "é"})
    assert public_error(caught.value)[0] == "invalid_request"


def test_exact_raw_collection_bounds_before_deduplication():
    path = {"space_id": "default", "path": "same.md"}
    data = {"spaces": ["default"] * 8, "literal_text": "x", "tags": ["exact"] * 16,
            "paths": [path] * 64, "metadata": [{"key": "x" * 256, "operator": "exists"}] * 16,
            "relationships": [{"direction": "incoming", "other": path}] * 16}
    value = parse_knowledge_query(data)
    assert len(value.scope.space_ids) == 1 and len(value.paths) == 64
    assert len(value.query.tags) == len(value.query.metadata) == len(value.relationships) == 16


def test_cli_request_input_flags_limits_and_legacy(settings, capsys):
    legacy = type(settings).model_validate({**settings.model_dump(), "knowledge_spaces_json": None})
    assert run_knowledge_query(legacy, request_json=True, input_stream=BytesIO(b'{"literal_text":"Independent"}'),
                               json_output=True) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["identity"]["space_id"] == "default"
    assert main(["query", "--literal-text", "Independent", "--qualified-path",
                 '{"space_id":"default","path":"same.md"}', "--json"], settings=legacy) == 0
    assert "coverage" in json.loads(capsys.readouterr().out)
    assert main(["query", "--literal-text", "Independent", "--json"], settings=legacy) == 0
    assert "matches" in json.loads(capsys.readouterr().out)
    for flag in ("--limit=20", "--lim=20"):
        assert main(["query", "--request-json", flag, "--json"], settings=legacy) == 2
        assert json.loads(capsys.readouterr().out) == {"category": "invalid_request"}
    for selector in ("--request-json", "--request-j", "--qualified-path", "--qualified-p"):
        argv = ["query", selector]
        if selector.startswith("--qualified"):
            argv.append('{"space_id":"default","path":"same.md"}')
        for invalid in ("PRIVATE", "PRIVATE--space"):
            with pytest.raises(SystemExit) as caught:
                main([*argv, "--limit", invalid, "--json"], settings=legacy)
            assert caught.value.code == 2
            output = capsys.readouterr()
            assert json.loads(output.out) == {"category": "invalid_request"}
            assert "PRIVATE" not in output.err
    valid = b'{"literal_text":"Independent"}'
    for raw in (valid + b" " * (1_048_576 - len(valid)),):
        assert _query_cli_input(request_json=True, input_stream=BytesIO(raw)) == {"literal_text": "Independent"}
    for kwargs, category in (
        ({"input_stream": BytesIO(valid + b" " * 1_048_576)}, "invalid_request"),
        ({"content_flags": True, "input_stream": BytesIO(valid)}, "invalid_request"),
        ({"spaces": ["default"], "input_stream": BytesIO(b'{"spaces":["default"],"literal_text":"a"}')},
         "invalid_scope"),
    ):
        with pytest.raises(Exception) as caught:
            _query_cli_input(request_json=True, **kwargs)
        assert public_error(caught.value)[0] == category
    assert _query_cli_input(request_json=True, spaces=["default"], input_stream=BytesIO(valid))["spaces"] == ["default"]
    for kwargs in ({"paths": ["same.md"], "qualified_paths": ['{"space_id":"default","path":"same.md"}']},
                   {"spaces": ["default", "space-1"], "paths": ["same.md"]}):
        with pytest.raises(SpaceError, match="invalid_scope"):
            _query_cli_input(**kwargs)


@pytest.mark.parametrize("denied", ["unknown", "space-1"])
def test_mixed_selection_denied_before_all_work(tmp_path, backend, monkeypatch, denied):
    from pydantic import SecretStr
    settings = configuration(tmp_path, denied=("space-1",)).model_copy(update={
        "api_key": SecretStr("slice-a-test-key"), "rate_limit_enabled": False})
    runtime = compose_space_runtime(settings)
    def forbidden(*args, **kwargs):
        pytest.fail("denial reached owner/admission")
    monkeypatch.setattr(SpaceOperations, "_admit", forbidden)
    monkeypatch.setattr(VaultService, "scoped_session", forbidden)
    try:
        data = {"spaces": ["default", denied], "literal_text": "PRIVATE"}
        response = rest(settings, runtime).post(URL, headers=AUTH, json=data)
        assert response.status_code == 403 and response.json() == {"detail": "scope_not_allowed"}
        response = tool(create_private_read_server(settings=settings, operations=runtime.operations),
                        "knowledge_query", data)
        assert response.content[0].text == "scope_not_allowed: Operation failed."
        output = StringIO()
        assert _run_scoped_cli(settings, "query", data, operations=runtime.operations,
                               json_output=True, output=output) == 2
        assert json.loads(output.getvalue()) == {"category": "scope_not_allowed"}
        assert backend.instances == []
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("data,code,status", [
    ({"literal_text": "Independent", "paths": [{"space_id": "space-1", "path": "same.md"}]},
     "invalid_scope", 422),
    ({"literal_text": "Independent", "paths": [{"space_id": "default", "path": "../same.md"}]},
     "invalid_path", 400),
    ({"literal_text": "Independent", "paths": [{"space_id": "default", "path": "/same.md"}]},
     "invalid_path", 400),
])
def test_qualified_selector_scope_and_containment(settings, runtime, data, code, status):
    response = rest(settings, runtime).post(URL, headers=AUTH, json=data)
    assert response.status_code == status and response.json() == {"detail": code}


def test_cross_space_relationships_never_form_edges(settings, runtime):
    for root in (settings.vault_path, settings.vault_path.parent / "space-1"):
        (root / "same.md").write_text("Independent [[same]]", encoding="utf-8")
    data = {"spaces": ["default", "space-1"], "literal_text": "Independent", "relationships": [
        {"direction": "outgoing", "other": {"space_id": sid, "path": "same.md"}}
        for sid in ("default", "space-1")]}
    assert assert_parity(settings, runtime, data)["items"] == []


@pytest.mark.parametrize("kind,code,status,exit_code", [
    ("cancel", "operation_cancelled", 503, 1), ("semantic", "semantic_unavailable", 503, 1),
    ("unexpected", "internal_error", 500, 2),
])
def test_safe_query_failure_projection(settings, runtime, monkeypatch, caplog, kind, code, status, exit_code):
    from app.services.semantic_search import SemanticSearchUnavailableError
    error = {"cancel": SpaceError("operation_cancelled"), "semantic": SemanticSearchUnavailableError(),
             "unexpected": RuntimeError("PRIVATE query/path/owner")}[kind]
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(SpaceOperations, "scoped_query", fail)
    data = {"literal_text": "Independent"}
    response = rest(settings, runtime).post(URL, headers=AUTH, json=data)
    assert (response.status_code, response.json()) == (status, {"detail": code})
    response = tool(create_private_read_server(settings=settings, operations=runtime.operations),
                    "knowledge_query", data)
    assert response.is_error and response.structured_content is None
    assert [row.text for row in response.content] == [f"{code}: Operation failed."]
    output = StringIO()
    assert _run_scoped_cli(settings, "query", data, operations=runtime.operations,
                           json_output=True, output=output) == exit_code
    assert json.loads(output.getvalue()) == {"category": code}
    assert "PRIVATE" not in caplog.text


def test_query_admission_saturation_and_recovery(settings, runtime):
    from concurrent.futures import ThreadPoolExecutor

    from tests.test_semantic_resources import launch
    from tests.test_space_runtime import waiters
    entered, release = threading.Event(), threading.Event()
    def active():
        with runtime.admission.operation():
            entered.set()
            assert release.wait(20)
    tokens = [CancellationToken() for _ in range(64)]
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(active)
        assert entered.wait(5)
        waiting = [launch(lambda token=token: runtime.operations.scoped_query(
            parse_knowledge_query({"literal_text": "Independent"}), cancel=token)) for token in tokens]
        try:
            waiters(runtime.admission, 64)
            response = rest(settings, runtime).post(URL, headers=AUTH, json={"literal_text": "Independent"})
            assert response.status_code == 503 and response.json() == {"detail": "space_unavailable"}
            for token in tokens:
                token.cancel()
            for future in waiting:
                with pytest.raises(SpaceError, match="operation_cancelled"):
                    future.result(5)
            assert runtime.admission._active_thread is not None and runtime.admission._waiting == 0
        finally:
            release.set()
            running.result(5)
    assert assert_parity(settings, runtime, {"literal_text": "Independent"})["state"] == "complete"


@pytest.mark.parametrize("transport", ["rest", "mcp-http-modern", "mcp-http-compat"])
def test_query_disconnect_signals_same_token_and_drains(settings, runtime, monkeypatch, transport):
    from app.services._scoped_vault import ScopedVaultSession
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    read, cancel = ScopedVaultSession.read, CancellationToken.cancel
    tokens, cancelled_tokens = [], []
    def blocked(owner, *args, **kwargs):
        tokens.append(owner.cancel)
        entered.set()
        assert release.wait(10)
        return read(owner, *args, **kwargs)
    def signal(token):
        cancelled_tokens.append(token)
        cancelled.set()
        cancel(token)
    monkeypatch.setattr(ScopedVaultSession, "read", blocked)
    monkeypatch.setattr(CancellationToken, "cancel", signal)
    async def invoke():
        closed, sent = asyncio.Event(), []
        version = "2025-11-25" if transport.endswith("compat") else "2026-07-28"
        body = rpc('{"literal_text":"Independent"}', version=version).replace(
            b'"name":"read_note"', b'"name":"knowledge_query"')
        if transport == "rest":
            app = create_private_read_app(settings=settings, operations=runtime.operations)
            scope = http_scope(body)
            scope.update(path=URL, raw_path=URL.encode())
            body = b'{"literal_text":"Independent"}'
            first = True
            async def receive():
                nonlocal first
                if first:
                    first = False
                    return {"type": "http.request", "body": body, "more_body": False}
                await closed.wait()
                return {"type": "http.disconnect"}
            async def send(message):
                sent.append(message)
            task = asyncio.create_task(app(scope, receive, send))
            server = None
        else:
            server, app = transport_for(settings, runtime)
            lifetime = server.session_manager.run()
            await lifetime.__aenter__()
            task = asyncio.create_task(request(app, body, version=version, disconnected=closed, sent=sent))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            closed.set()
            assert await asyncio.to_thread(cancelled.wait, 5)
            assert any(tokens[0]._caller is token for token in cancelled_tokens)
            with pytest.raises(SpaceError, match="operation_cancelled"):
                tokens[0].check()
            assert runtime.admission._active_thread is not None
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if server is not None:
                await lifetime.__aexit__(None, None, None)
        assert sent == []
    asyncio.run(invoke())
    assert runtime.admission._active_thread is None
