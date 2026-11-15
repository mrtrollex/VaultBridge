"""Discovery must serialize in both SDK 2.2/2.3 protocol eras."""

import asyncio

import pytest
from mcp import Client

from app.mcp_server import create_mcp_server
from tests.test_mcp_server import RecordingIndexer, server_for
from tests.test_public_space_composition import settings as settings


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("writes", [False, True])
def test_output_object_unions_survive_actual_sdk_discovery(tmp_path, settings, mode, named, writes):
    if named:
        server = create_mcp_server(settings=settings.model_copy(update={"mcp_write_enabled": writes}))
    else:
        from tests.test_mcp_server import settings_for

        configured = settings_for(tmp_path / "vault", MCP_WRITE_ENABLED=writes)
        server, _, _ = server_for(tmp_path, settings=configured, indexer=RecordingIndexer() if writes else None)

    async def inspect():
        async with Client(server, mode=mode) as client:
            tools = (await client.list_tools()).tools
            result = await client.call_tool("knowledge_query", {
                "spaces": ["default"], "literal_text": "Independent",
            })
            assert not result.is_error
            assert result.structured_content["state"] == "complete"
            return tools

    tools = asyncio.run(inspect())
    assert len(tools) == (11 if writes else 9)
    assert {"create_note", "append_note"}.issubset({tool.name for tool in tools}) is writes
    for tool in tools:
        assert tool.output_schema["type"] == "object", tool.name
        assert len(tool.output_schema["anyOf"]) == 2, tool.name
        assert all(branch["type"] == "object" for branch in tool.output_schema["anyOf"])
