from __future__ import annotations

import asyncio
import io
import json
import logging
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx2
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

import app.main as main
from app.core.config import Settings
from app.mcp_server import NOTE_RESOURCE_TEMPLATE, create_mcp_server
from app.repositories.semantic import SemanticRepository
from app.services.rate_limiter import FixedWindowRateLimiter
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService
from tests.test_api import FakeEmbedder


@contextmanager
def capture_logs(logger_name: str):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    from app.core.logging import JsonLogFormatter

    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)


def application_for(
    tmp_path: Path,
    *,
    enabled: bool = True,
    api_key: str = "test-current-secret",
    previous_api_key: str = "test-previous-secret",
    rate_limit_requests: int = 120,
    rate_limiter: FixedWindowRateLimiter | None = None,
) -> FastAPI:
    settings = Settings(
        api_key=api_key,
        previous_api_key=previous_api_key,
        vault_path=tmp_path,
        semantic_data_path=tmp_path / ".test-semantic",
        mcp_http_enabled=enabled,
        mcp_http_allowed_hosts=("testserver", "allowed.example.test"),
        mcp_http_allowed_origins=("https://allowed-origin.example.test",),
        rate_limit_requests=rate_limit_requests,
    )
    vault_service = VaultService(
        vault_root=settings.vault_path,
        max_note_bytes=settings.max_note_bytes,
    )
    semantic_service = SemanticSearchService(
        vault_root=settings.vault_path,
        repository=SemanticRepository(settings.semantic_data_path / "semantic-index.sqlite3"),
        max_note_bytes=settings.max_note_bytes,
        chunk_chars=300,
        chunk_overlap=50,
        embedder=FakeEmbedder(),
    )
    return main.create_app(
        settings=settings,
        vault_service=vault_service,
        semantic_search_service=semantic_service,
        rate_limiter=rate_limiter,
    )


def auth(token: str = "test-current-secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_disabled_transport_is_absent_and_does_not_change_existing_surfaces(tmp_path):
    application = application_for(tmp_path, enabled=False)
    client = TestClient(application)

    assert client.post("/mcp").status_code == 404
    assert client.get("/health/live").status_code == 200
    assert client.get("/ui/").status_code == 200
    assert application.state.mcp_server is None
    assert "/mcp" not in application.openapi()["paths"]


def test_enabled_transport_is_exactly_mcp_and_schema_hidden(tmp_path):
    application = application_for(tmp_path)
    client = TestClient(application)

    assert client.post("/mcp").status_code == 401
    assert client.post("/mcp/mcp", headers=auth()).status_code == 404
    assert client.post("/mcp/", headers=auth()).status_code == 404
    assert application.state.mcp_server is not None
    assert "/mcp" not in application.openapi()["paths"]


def test_http_auth_accepts_current_and_previous_then_rejects_removed_previous(tmp_path):
    with capture_logs("vaultbridge") as log_stream:
        with TestClient(application_for(tmp_path)) as client:
            assert client.post("/mcp").status_code == 401
            invalid = client.post("/mcp", headers=auth("invalid-secret"))
            assert invalid.status_code == 401
            assert "invalid-secret" not in invalid.text
            assert client.post("/mcp", headers=auth()).status_code != 401
            assert client.post("/mcp", headers=auth("test-previous-secret")).status_code != 401
    assert "invalid-secret" not in log_stream.getvalue()
    assert "test-current-secret" not in log_stream.getvalue()
    assert "test-previous-secret" not in log_stream.getvalue()

    with TestClient(
        application_for(tmp_path / "removed", previous_api_key="")
    ) as without_previous:
        assert without_previous.post(
            "/mcp",
            headers=auth("test-previous-secret"),
        ).status_code == 401


def test_transport_security_uses_configured_host_and_origin_allowlists(tmp_path):
    headers = {**auth(), "Content-Type": "application/json"}
    with TestClient(application_for(tmp_path)) as client:
        invalid_host = client.post(
            "/mcp",
            headers={**headers, "Host": "invalid.example.test"},
            content="{}",
        )
        invalid_origin = client.post(
            "/mcp",
            headers={**headers, "Origin": "https://invalid-origin.example.test"},
            content="{}",
        )
        allowed_origin = client.post(
            "/mcp",
            headers={
                **headers,
                "Host": "allowed.example.test",
                "Origin": "https://allowed-origin.example.test",
            },
            content="{}",
        )
        absent_origin = client.post(
            "/mcp",
            headers={**headers, "Host": "allowed.example.test"},
            content="{}",
        )

    assert invalid_host.status_code == 421
    assert invalid_origin.status_code == 403
    assert allowed_origin.status_code not in {403, 421}
    assert absent_origin.status_code not in {403, 421}
    assert logging.getLogger("mcp.server.transport_security").level == logging.CRITICAL


def test_http_rate_limit_uses_shared_peer_budget_once_per_request(tmp_path):
    limiter = FixedWindowRateLimiter(requests=2, window_seconds=60, max_clients=10)
    application = application_for(
        tmp_path,
        rate_limit_requests=2,
        rate_limiter=limiter,
    )
    with TestClient(application, client=("198.51.100.10", 50000)) as client:
        assert client.post("/mcp").status_code == 401
        assert client.post(
            "/mcp",
            headers={**auth(), "Content-Type": "application/json"},
            content="{}",
        ).status_code != 429
        exhausted = client.post("/mcp", headers=auth())

    assert exhausted.status_code == 429
    assert exhausted.json() == {"detail": "Rate limit exceeded"}
    assert exhausted.headers["retry-after"] == "60"


def test_stdio_operation_budget_does_not_consume_http_peer_budget(tmp_path):
    application = application_for(tmp_path, rate_limit_requests=1)
    stdio_server = create_mcp_server(
        settings=application.state.settings,
        vault_service=application.state.vault_service,
        semantic_search_service=application.state.semantic_search_service,
        duplicate_candidate_service=application.state.duplicate_candidate_service,
    )

    async def call_stdio() -> None:
        async with Client(stdio_server) as client:
            await client.call_tool("list_notes", {})

    asyncio.run(call_stdio())
    with TestClient(application, client=("198.51.100.20", 50000)) as client:
        assert client.post(
            "/mcp",
            headers={**auth(), "Content-Type": "application/json"},
            content="{}",
        ).status_code != 429
        assert client.post("/mcp", headers=auth()).status_code == 429


def test_official_client_modern_http_lists_and_calls_read_only_surface(tmp_path):
    (tmp_path / "Smoke.md").write_text("# Smoke\n\nprotocol round trip", encoding="utf-8")
    application = application_for(tmp_path)

    async def smoke():
        async with application.router.lifespan_context(application):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(
                    app=application,
                    client=("198.51.100.30", 50000),
                ),
                base_url="http://testserver",
                headers=auth(),
            ) as http_client:
                transport = streamable_http_client(
                    "http://testserver/mcp",
                    http_client=http_client,
                    terminate_on_close=False,
                )
                async with Client(transport, mode="2026-07-28") as client:
                    tools = await client.list_tools()
                    templates = await client.list_resource_templates()
                    listed = await client.call_tool("list_notes", {})
                    read = await client.call_tool("read_note", {"path": "Smoke.md"})
                    links = await client.call_tool("note_links", {"path": "Smoke.md"})
                    resource = await client.read_resource("vaultbridge://note/Smoke.md")
                    return tools, templates, listed, read, links, resource

    with capture_logs("vaultbridge.mcp") as log_stream:
        tools, templates, listed, read, links, resource = asyncio.run(smoke())

    assert [tool.name for tool in tools.tools] == [
        "list_notes",
        "read_note",
        "search_notes",
        "related_notes",
        "duplicate_candidates",
        "note_links",
        "note_backlinks",
    ]
    assert [template.uri_template for template in templates.resource_templates] == [
        NOTE_RESOURCE_TEMPLATE
    ]
    assert listed.structured_content["notes"][0]["path"] == "Smoke.md"
    assert read.structured_content["content"].endswith("protocol round trip")
    assert links.structured_content == {"links": []}
    assert resource.contents[0].text.endswith("protocol round trip")
    operation_logs = [
        json.loads(line)
        for line in log_stream.getvalue().splitlines()
        if json.loads(line)["event"].startswith("mcp_operation_")
    ]
    assert operation_logs
    assert {record["transport"] for record in operation_logs} == {"streamable-http"}
    assert "test-current-secret" not in log_stream.getvalue()
    assert "protocol round trip" not in log_stream.getvalue()


class RecordingIndexer:
    requires_full_sync = False

    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("indexer-start")

    def shutdown(self) -> None:
        self.events.append("indexer-stop")


def test_parent_lifespan_owns_enabled_mcp_session_manager():
    events: list[str] = []
    indexer = RecordingIndexer()

    class SessionManager:
        @asynccontextmanager
        async def run(self):
            events.append("mcp-start")
            try:
                yield
            finally:
                events.append("mcp-stop")

    application = SimpleNamespace(
        state=SimpleNamespace(
            semantic_indexer=indexer,
            semantic_watcher=None,
            mcp_server=SimpleNamespace(session_manager=SessionManager()),
        )
    )

    async def exercise() -> None:
        async with main.lifespan(application):
            events.append("running")

    asyncio.run(exercise())

    assert indexer.events == ["indexer-start", "indexer-stop"]
    assert events == ["mcp-start", "running", "mcp-stop"]
