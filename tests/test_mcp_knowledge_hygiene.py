from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace
from typing import get_args
from unittest.mock import Mock

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.knowledge_hygiene_transport import KnowledgeHygieneScanRequest, KnowledgeHygieneScanResponse
from app.mcp_server import _privacy_error_middleware, create_mcp_server
from app.services import knowledge_hygiene as d
from tests.test_api import auth
from tests.test_api_knowledge_hygiene import PRIVATE, ROUTE, injected_client, result_fixture, wire_json
from tests.test_mcp_http import application_for
from tests.test_mcp_http import auth as http_auth
from tests.test_mcp_server import RecordingIndexer, capture_mcp_logs, run_client, settings_for

TOOL = "knowledge_hygiene_scan"


def server_for(tmp_path, result=None, **kwargs):
    service = Mock(spec=d.KnowledgeHygieneService)
    service.scan.return_value = result if result is not None else result_fixture()
    server = create_mcp_server(
        settings=settings_for(tmp_path, RATE_LIMIT_ENABLED=False),
        knowledge_hygiene_service=service, **kwargs,
    )
    return server, service


def invoke(server, arguments):
    return run_client(server, lambda client: client.call_tool(TOOL, arguments))


@pytest.mark.parametrize("status", get_args(d.DerivedStatus))
def test_result_evidence_order_partial_and_defaults(tmp_path, status):
    fixture = result_fixture(status)
    server, service = server_for(tmp_path, fixture)
    result = invoke(server, {})
    assert result.is_error is False
    assert result.structured_content == wire_json(fixture)
    assert json.loads(result.content[0].text) == wire_json(fixture)
    service.scan.assert_called_once_with(d.KnowledgeHygieneRequest())


@pytest.mark.parametrize("arguments", [
    {"groups": []}, {"groups": ["aliases", "aliases"]},
    *[{"groups": [group]} for group in get_args(d.Group)],
    {"finding_limit": 1}, {"finding_limit": 500},
    {"duplicate_source_limit": 0}, {"duplicate_source_limit": 20},
    {"inspect_derived_index": True}, {"semantic_candidates": False},
    {"semantic_candidates": True, "duplicate_source_limit": 1},
])
def test_valid_requests_map_directly(tmp_path, arguments):
    server, service = server_for(tmp_path)
    assert invoke(server, arguments).is_error is False
    service.scan.assert_called_once_with(KnowledgeHygieneScanRequest.model_validate(arguments).to_domain())


@pytest.mark.parametrize("arguments", [
    {"groups": [PRIVATE]}, {"groups": PRIVATE}, {"groups": [1]}, {"groups": {}},
    {"finding_limit": 0}, {"finding_limit": 501}, {"finding_limit": True},
    {"finding_limit": "1"}, {"finding_limit": 1.0}, {"finding_limit": []},
    {"duplicate_source_limit": -1}, {"duplicate_source_limit": 21},
    {"duplicate_source_limit": False}, {"duplicate_source_limit": "1"},
    {"duplicate_source_limit": 1.0}, {"semantic_candidates": True},
    {"semantic_candidates": True, "duplicate_source_limit": 0},
    {"semantic_candidates": 1}, {"semantic_candidates": "true"},
    {"inspect_derived_index": 0}, {"inspect_derived_index": "false"},
    {PRIVATE: PRIVATE},
    *[{field: None} for field in asdict(d.KnowledgeHygieneRequest())],
])
def test_invalid_raw_mcp_arguments_never_scan_or_leak(tmp_path, arguments, caplog):
    server, service = server_for(tmp_path)
    result = invoke(server, arguments)
    assert result.is_error is True
    assert result.content[0].text == "validation_error: Tool arguments are invalid."
    assert result.structured_content is None
    service.scan.assert_not_called()
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("failure,code", [
    (d.KnowledgeHygieneError("invalid_request"), "validation_error"),
    (d.KnowledgeHygieneError("scan_unavailable"), "hygiene_unavailable"),
    (RuntimeError(PRIVATE), "internal_error"),
    (ValueError(PRIVATE), "internal_error"),
    (TypeError(PRIVATE), "internal_error"),
])
def test_failures_are_private_and_programming_errors_are_internal(tmp_path, failure, code, caplog):
    server, service = server_for(tmp_path)
    service.scan.side_effect = failure
    with capture_mcp_logs() as logs:
        result = invoke(server, {})
    assert result.is_error is True
    assert result.content[0].text.startswith(code + ":")
    assert result.structured_content is None
    assert PRIVATE not in logs.getvalue() + caplog.text + str(result)
    service.scan.assert_called_once()


def test_serialization_failure_is_internal(tmp_path, caplog):
    server, service = server_for(tmp_path)
    service.scan.return_value = {"private": PRIVATE}
    result = invoke(server, {})
    assert result.is_error is True
    assert result.content[0].text.startswith("internal_error:")
    assert PRIVATE not in str(result) + caplog.text


@pytest.mark.parametrize("writes", [False, True])
def test_schema_registration_and_read_only_metadata(tmp_path, writes):
    server = create_mcp_server(
        settings=settings_for(tmp_path, MCP_WRITE_ENABLED=writes),
        semantic_indexer=RecordingIndexer() if writes else None,
    )
    tools = run_client(server, lambda client: client.list_tools()).tools
    assert sum(tool.name == TOOL for tool in tools) == 1
    tool = next(tool for tool in tools if tool.name == TOOL)
    assert tool.input_schema == KnowledgeHygieneScanRequest.model_json_schema()
    assert tool.input_schema["additionalProperties"] is False
    assert tool.output_schema == KnowledgeHygieneScanResponse.model_json_schema()
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False
    for text in ("bounded", "read-only", "partial", "Does not repair", "refresh indexes"):
        assert text in tool.description


def test_rest_mcp_parity_preserves_all_domain_semantics(tmp_path):
    fixture = result_fixture("inspection_unavailable")
    rest, rest_service = injected_client(tmp_path, fixture)
    mcp, mcp_service = server_for(tmp_path, fixture)
    arguments = {"groups": ["aliases", "frontmatter"], "finding_limit": 500,
                 "duplicate_source_limit": 20, "semantic_candidates": True,
                 "inspect_derived_index": True}
    response = rest.post(ROUTE, json=arguments, headers=auth())
    result = invoke(mcp, arguments)
    assert response.status_code == 200
    assert result.is_error is False
    assert response.json() == result.structured_content == wire_json(fixture)
    assert rest_service.scan.call_args == mcp_service.scan.call_args
    rest_service.scan.assert_called_once()
    mcp_service.scan.assert_called_once()


def test_http_uses_same_injected_hygiene_instance(tmp_path):
    application = application_for(tmp_path)
    service = application.state.knowledge_hygiene_service
    service.scan = Mock(return_value=result_fixture())
    result = invoke(application.state.mcp_server, {})
    assert result.is_error is False
    service.scan.assert_called_once_with(d.KnowledgeHygieneRequest())


def test_real_scan_is_read_only_and_semantic_unavailable(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("---\naliases: [private-alias, private-alias]\n---\nprivate-body")
    application = application_for(tmp_path)
    state = application.state
    forbidden = Mock(side_effect=AssertionError("mutation or ordinary semantic search invoked"))
    for name in ("create_note", "append_note"):
        monkeypatch.setattr(state.vault_service, name, forbidden)
    for name in ("sync", "sync_paths", "search", "inspect_index", "use_persisted_index_for_read_only_search"):
        monkeypatch.setattr(state.semantic_search_service, name, forbidden)
    monkeypatch.setattr(state.semantic_indexer, "enqueue", forbidden)
    # Repository initialization/write entry points are outside hygiene's immutable inspection.
    repository = state.semantic_search_service.repository
    for name in ("_connect", "prepare_index", "reset_index", "transaction", "set_metadata"):
        monkeypatch.setattr(repository, name, forbidden)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = invoke(state.mcp_server, {"semantic_candidates": True, "duplicate_source_limit": 1,
                                       "inspect_derived_index": True})
    assert result.is_error is False
    assert result.structured_content["candidates"]["reasons"] == ["semantic_unavailable"]
    assert result.structured_content["derived_index"]["status"] == "missing"
    assert "private-alias" not in str(result) and "private-body" not in str(result)
    assert str(tmp_path) not in str(result)
    forbidden.assert_not_called()
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("arguments", [None, [], "private-host-path", 1])
def test_malformed_argument_objects_stop_before_sdk_and_service(arguments):
    context = SimpleNamespace(method="tools/call", params={"name": TOOL, "arguments": arguments})
    service = Mock(spec=d.KnowledgeHygieneService)

    async def next_handler(_context):
        service.scan(d.KnowledgeHygieneRequest())
        raise AssertionError("malformed arguments reached SDK handler")

    result = asyncio.run(_privacy_error_middleware(context, next_handler))
    assert result.is_error is True
    assert result.content[0].text == "validation_error: Tool arguments are invalid."
    service.scan.assert_not_called()


def test_real_rest_mcp_parity(tmp_path):
    (tmp_path / "a.md").write_text("---\naliases: [private, private]\n---\n[[missing]]")
    rest, _service = injected_client(tmp_path)
    rest.app.dependency_overrides.clear()
    state = rest.app.state
    mcp = create_mcp_server(
        settings=state.settings, vault_service=state.vault_service,
        semantic_search_service=state.semantic_search_service,
        duplicate_candidate_service=state.duplicate_candidate_service,
        relationship_service=state.relationship_service,
        knowledge_hygiene_service=state.knowledge_hygiene_service,
    )
    arguments = {"duplicate_source_limit": 1, "semantic_candidates": True, "inspect_derived_index": True}
    response = rest.post(ROUTE, json=arguments, headers=auth())
    result = invoke(mcp, arguments)
    assert response.status_code == 200 and result.is_error is False
    assert response.json() == result.structured_content
    assert result.structured_content["findings"]
    assert result.structured_content["candidates"]["reasons"] == ["semantic_unavailable"]


@pytest.mark.parametrize("writes", [False, True])
def test_official_http_hygiene_round_trip_and_invalid_input(tmp_path, writes):
    application = application_for(tmp_path, write_enabled=writes)
    service = application.state.knowledge_hygiene_service
    fixture = result_fixture("inspection_unavailable")
    service.scan = Mock(return_value=fixture)

    async def exercise():
        async with application.router.lifespan_context(application):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=application), base_url="http://testserver",
                headers=http_auth(),
            ) as http:
                transport = streamable_http_client("http://testserver/mcp", http_client=http,
                                                   terminate_on_close=False)
                async with Client(transport, mode="2026-07-28") as client:
                    tools = (await client.list_tools()).tools
                    assert sum(tool.name == TOOL for tool in tools) == 1
                    assert next(t for t in tools if t.name == TOOL).input_schema["additionalProperties"] is False
                    invalid = await client.call_tool(TOOL, {"finding_limit": True})
                    assert invalid.is_error is True
                    assert invalid.content[0].text == "validation_error: Tool arguments are invalid."
                    service.scan.assert_not_called()
                    valid = await client.call_tool(TOOL, {})
                    assert valid.is_error is False
                    assert valid.structured_content == wire_json(fixture)
                    service.scan.assert_called_once_with(d.KnowledgeHygieneRequest())

    asyncio.run(exercise())
