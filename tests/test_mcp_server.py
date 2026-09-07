from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError

from app.api.dependencies import require_auth
from app.core.config import Settings
from app.core.logging import JsonLogFormatter
from app.mcp_server import NOTE_RESOURCE_TEMPLATE, create_mcp_server, note_resource_uri
from app.repositories.semantic import (
    ImmutableIndexInspectionUnavailableError,
    SemanticRepository,
)
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.rate_limiter import FixedWindowRateLimiter
from app.services.semantic_search import IndexState, SemanticResult, SemanticSearchService
from app.services.vault import VaultService


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSemanticSearchService:
    def __init__(self, *, available: bool = True, state: IndexState = IndexState.READY) -> None:
        self.available = available
        self.state = state
        self.calls: list[dict] = []
        self.read_only_checks = 0
        self.results = [
            SemanticResult(
                path="Stale.md",
                title="Stale",
                score=0.99,
                semantic_score=0.99,
                lexical_score=0.0,
                snippet="stale private content",
                heading=None,
            ),
            SemanticResult(
                path="Folder/Second.md",
                title="Wrong service title",
                score=0.83,
                semantic_score=0.8,
                lexical_score=0.4,
                snippet="Second note snippet",
                heading="Runbook",
            ),
        ]

    def use_persisted_index_for_read_only_search(self) -> bool:
        self.read_only_checks += 1
        return self.available

    def inspect_persisted_index(self):
        return SimpleNamespace(state=self.state)

    def search(self, text: str, *, folder: str, limit: int, min_score: float):
        self.calls.append(
            {"text": text, "folder": folder, "limit": limit, "min_score": min_score}
        )
        return self.results[:limit]

    def sync(self, *_args, **_kwargs):
        raise AssertionError("MCP must not synchronize the semantic index")

    def sync_paths(self, *_args, **_kwargs):
        raise AssertionError("MCP must not refresh the semantic index")


class ConstantEmbedder:
    def embed(self, texts):
        import numpy as np

        return [np.asarray([1.0, 0.0], dtype=np.float32) for _text in texts]


def settings_for(vault: Path, **overrides) -> Settings:
    values = {
        "API_KEY": "",
        "VAULT_PATH": str(vault),
        "SEMANTIC_DATA_PATH": str(vault.parent / "semantic-data"),
        "SEMANTIC_MODEL": "tests/fake-model",
        "RATE_LIMIT_ENABLED": True,
        "RATE_LIMIT_REQUESTS": 100,
        "RATE_LIMIT_WINDOW_SECONDS": 60,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def server_for(tmp_path: Path, *, semantic=None, limiter=None, settings=None):
    vault_root = tmp_path / "vault"
    (vault_root / "Folder").mkdir(parents=True)
    (vault_root / "First note.md").write_text("# First\n\nLiteral needle.", encoding="utf-8")
    (vault_root / "Folder" / "Second.md").write_text(
        "# Second\n\nSemantic candidate.", encoding="utf-8"
    )
    app_settings = settings or settings_for(vault_root)
    vault = VaultService(vault_root=vault_root, max_note_bytes=app_settings.max_note_bytes)
    semantic_service = semantic or FakeSemanticSearchService()
    duplicate_service = DuplicateCandidateService(
        vault_service=vault,
        semantic_search_service=semantic_service,
    )
    return (
        create_mcp_server(
            settings=app_settings,
            vault_service=vault,
            semantic_search_service=semantic_service,
            duplicate_candidate_service=duplicate_service,
            rate_limiter=limiter,
        ),
        vault,
        semantic_service,
    )


def run_client(server, action):
    async def run():
        async with Client(server) as client:
            return await action(client)

    return asyncio.run(run())


def run_client_error(server, action) -> MCPError:
    async def run():
        async with Client(server) as client:
            try:
                await action(client)
            except MCPError as exc:
                return exc
        raise AssertionError("MCP operation did not fail")

    return asyncio.run(run())


@contextmanager
def capture_mcp_logs():
    import logging

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    app_logger = logging.getLogger("vaultbridge")
    previous_level = app_logger.level
    app_logger.setLevel(logging.INFO)
    app_logger.addHandler(handler)
    try:
        yield stream
    finally:
        app_logger.removeHandler(handler)
        app_logger.setLevel(previous_level)


def test_sdk_v2_server_advertises_exact_read_only_surface_and_no_prompts(tmp_path):
    from importlib.metadata import version

    server, _vault, _semantic = server_for(tmp_path)

    async def inspect_surface(client):
        return (
            await client.list_tools(),
            await client.list_resources(),
            await client.list_resource_templates(),
            await client.list_prompts(),
        )

    tools, resources, templates, prompts = run_client(server, inspect_surface)
    assert version("mcp").split(".", 1)[0] == "2"
    assert [tool.name for tool in tools.tools] == [
        "list_notes",
        "read_note",
        "search_notes",
        "related_notes",
        "duplicate_candidates",
    ]
    assert not {"create_note", "append_note", "semantic_search"} & {
        tool.name for tool in tools.tools
    }
    assert all(tool.annotations.read_only_hint is True for tool in tools.tools)
    assert resources.resources == []
    assert [(template.uri_template, template.mime_type) for template in templates.resource_templates] == [
        (NOTE_RESOURCE_TEMPLATE, "text/markdown")
    ]
    assert prompts.prompts == []


def test_tool_schemas_keep_adr_defaults_and_bounds(tmp_path):
    server, _vault, _semantic = server_for(tmp_path)
    tools = run_client(server, lambda client: client.list_tools()).tools
    by_name = {tool.name: tool.input_schema for tool in tools}

    assert by_name["list_notes"]["properties"]["limit"] | {} == {
        "default": 50,
        "maximum": 200,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }
    assert by_name["search_notes"]["properties"]["query"]["maxLength"] == 300
    assert by_name["related_notes"]["properties"]["text"]["minLength"] == 2
    assert by_name["related_notes"]["properties"]["min_score"]["default"] == 0.28
    assert by_name["duplicate_candidates"]["properties"]["title"]["maxLength"] == 180


def test_list_read_and_literal_search_delegate_and_return_resource_links(tmp_path):
    server, _vault, _semantic = server_for(tmp_path)

    async def call(client):
        return (
            await client.call_tool("list_notes", {"folder": "Folder", "limit": 2}),
            await client.call_tool("read_note", {"path": "First note.md"}),
            await client.call_tool("search_notes", {"query": "needle", "limit": 3}),
        )

    listed, read, searched = run_client(server, call)
    assert listed.is_error is not True
    assert listed.structured_content["folder"] == "Folder"
    assert listed.structured_content["notes"][0]["path"] == "Folder/Second.md"
    assert listed.structured_content["notes"][0]["resource_uri"] == (
        "vaultbridge://note/Folder%2FSecond.md"
    )
    assert read.structured_content == {
        "path": "First note.md",
        "content": "# First\n\nLiteral needle.",
        "mime_type": "text/markdown",
        "resource_uri": "vaultbridge://note/First%20note.md",
    }
    assert json.loads(read.content[0].text) == read.structured_content
    assert searched.structured_content["query"] == "needle"
    assert [item["path"] for item in searched.structured_content["results"]] == [
        "First note.md"
    ]


def test_related_notes_preserves_verified_order_scores_and_never_syncs(tmp_path):
    semantic = FakeSemanticSearchService()
    server, _vault, semantic = server_for(tmp_path, semantic=semantic)

    result = run_client(
        server,
        lambda client: client.call_tool(
            "related_notes",
            {"text": "semantic concept", "folder": "Folder", "limit": 2, "min_score": 0.4},
        ),
    )

    assert result.is_error is not True
    assert semantic.read_only_checks == 1
    assert semantic.calls == [
        {"text": "semantic concept", "folder": "Folder", "limit": 6, "min_score": 0.4}
    ]
    assert result.structured_content["results"] == [
        {
            "path": "Folder/Second.md",
            "title": "Second",
            "score": 0.83,
            "semantic_score": 0.8,
            "lexical_score": 0.4,
            "snippet": "Second note snippet",
            "heading": "Runbook",
            "resource_uri": "vaultbridge://note/Folder%2FSecond.md",
        }
    ]


def test_real_semantic_request_uses_read_only_chunk_connection_and_leaves_storage_unchanged(
    tmp_path, monkeypatch
):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "Indexed.md").write_text("semantic indexed body", encoding="utf-8")
    data_path = tmp_path / "semantic-data"
    repository = SemanticRepository(data_path / "semantic-index.sqlite3")
    semantic = SemanticSearchService(
        vault_root=vault_root,
        repository=repository,
        model_name="tests/fake-model",
        embedder=ConstantEmbedder(),
    )
    semantic.sync()
    before = {path.name: path.read_bytes() for path in data_path.iterdir() if path.is_file()}

    def reject_write_capable_loader():
        raise AssertionError("read-only MCP search used the write-capable chunk loader")

    monkeypatch.setattr(repository, "load_chunks", reject_write_capable_loader)
    settings = settings_for(vault_root)
    vault = VaultService(vault_root=vault_root, max_note_bytes=settings.max_note_bytes)
    server = create_mcp_server(
        settings=settings,
        vault_service=vault,
        semantic_search_service=semantic,
        duplicate_candidate_service=DuplicateCandidateService(
            vault_service=vault,
            semantic_search_service=semantic,
        ),
    )

    result = run_client(
        server,
        lambda client: client.call_tool("related_notes", {"text": "semantic indexed"}),
    )
    after = {path.name: path.read_bytes() for path in data_path.iterdir() if path.is_file()}

    assert result.is_error is not True
    assert result.structured_content["results"][0]["path"] == "Indexed.md"
    assert after == before


def test_immutable_chunk_read_race_returns_safe_retryable_semantic_error(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "Indexed.md").write_text("semantic indexed body", encoding="utf-8")
    repository = SemanticRepository(tmp_path / "semantic-data" / "semantic-index.sqlite3")
    semantic = SemanticSearchService(
        vault_root=vault_root,
        repository=repository,
        model_name="tests/fake-model",
        embedder=ConstantEmbedder(),
    )
    semantic.sync()
    settings = settings_for(vault_root)
    vault = VaultService(vault_root=vault_root, max_note_bytes=settings.max_note_bytes)
    server = create_mcp_server(
        settings=settings,
        vault_service=vault,
        semantic_search_service=semantic,
        duplicate_candidate_service=DuplicateCandidateService(
            vault_service=vault,
            semantic_search_service=semantic,
        ),
    )

    def unavailable_chunks():
        raise ImmutableIndexInspectionUnavailableError

    monkeypatch.setattr(repository, "load_chunks_read_only", unavailable_chunks)
    result = run_client(
        server,
        lambda client: client.call_tool("related_notes", {"text": "private semantic query"}),
    )

    assert result.is_error is True
    assert result.content[0].text.startswith("semantic_index_unavailable:")
    assert "private semantic query" not in result.content[0].text
    assert str(tmp_path) not in result.content[0].text


def test_duplicate_candidates_delegate_with_advisory_semantics(tmp_path):
    semantic = FakeSemanticSearchService()
    semantic.results = [
        SemanticResult(
            path="Folder/Second.md",
            title="Second",
            score=0.75,
            semantic_score=0.7,
            lexical_score=0.3,
            snippet="candidate",
            heading=None,
        )
    ]
    server, _vault, semantic = server_for(tmp_path, semantic=semantic)

    result = run_client(
        server,
        lambda client: client.call_tool(
            "duplicate_candidates",
            {"title": "Second", "text": "body", "folder": "Folder", "limit": 4},
        ),
    )

    assert result.is_error is not True
    assert semantic.read_only_checks == 1
    assert semantic.calls[0]["text"] == "Second\n\nbody"
    assert [item["match_type"] for item in result.structured_content["results"]] == [
        "exact_title"
    ]
    assert result.structured_content["results"][0]["semantic_score"] == 0.7


def test_note_resource_returns_markdown_and_requires_canonical_encoding(tmp_path):
    server, _vault, _semantic = server_for(tmp_path)
    canonical_uri = note_resource_uri("First note.md")

    resource = run_client(server, lambda client: client.read_resource(canonical_uri))
    assert len(resource.contents) == 1
    assert resource.contents[0].text == "# First\n\nLiteral needle."
    assert resource.contents[0].mime_type == "text/markdown"

    error = run_client_error(
        server,
        lambda client: client.read_resource("vaultbridge://note/First note.md"),
    )
    assert "invalid or unavailable" in str(error)


@pytest.mark.parametrize(
    "uri",
    [
        "vaultbridge://note/..%2Foutside.md",
        "vaultbridge://note/%2E%2E%2Foutside.md",
        "vaultbridge://note/C%3A%5Cprivate%5Cnote.md",
        "vaultbridge://note/%2Fprivate%2Fnote.md",
        "vaultbridge://note/note.txt",
    ],
)
def test_note_resource_rejects_traversal_absolute_and_non_markdown_without_path_leak(
    tmp_path, uri
):
    server, _vault, _semantic = server_for(tmp_path)

    error = run_client_error(server, lambda client: client.read_resource(uri))
    message = str(error)
    assert "invalid or unavailable" in message
    assert str(tmp_path) not in message
    assert "outside.md" not in message
    assert "private" not in message


def test_note_resource_rejects_external_symlink_escape_when_supported(tmp_path):
    server, vault, _semantic = server_for(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("private outside content", encoding="utf-8")
    link = vault.vault_root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available to this test process")

    error = run_client_error(
        server,
        lambda client: client.read_resource(note_resource_uri("linked.md")),
    )
    assert "invalid or unavailable" in str(error)
    assert "private outside content" not in str(error)
    assert str(outside) not in str(error)


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (IndexState.UNINITIALIZED, "semantic_index_unavailable"),
        (IndexState.ERROR, "semantic_index_unavailable"),
        (IndexState.INDEXING, "semantic_index_rebuilding"),
    ],
)
def test_semantic_unavailable_errors_are_bounded_safe_and_do_not_call_search(tmp_path, state, code):
    semantic = FakeSemanticSearchService(available=False, state=state)
    server, _vault, semantic = server_for(tmp_path, semantic=semantic)

    result = run_client(
        server,
        lambda client: client.call_tool("related_notes", {"text": "private query"}),
    )

    assert result.is_error is True
    assert result.content[0].text.startswith(f"{code}:")
    assert "private query" not in result.content[0].text
    assert str(tmp_path) not in result.content[0].text
    assert semantic.calls == []


def test_process_wide_limiter_uses_deterministic_monotonic_clock_for_tools_and_resources(tmp_path):
    clock = MutableClock()
    limiter = FixedWindowRateLimiter(
        requests=2,
        window_seconds=10,
        max_clients=1,
        clock=clock,
    )
    server, _vault, _semantic = server_for(tmp_path, limiter=limiter)

    async def exhaust(client):
        first = await client.call_tool("list_notes", {})
        second = await client.read_resource(note_resource_uri("First note.md"))
        limited = await client.call_tool("search_notes", {"query": "needle"})
        return first, second, limited

    first, resource, limited = run_client(server, exhaust)
    assert first.is_error is not True
    assert resource.contents
    assert limited.is_error is True
    assert limited.content[0].text == (
        "rate_limited: Operation limit exceeded; retry in 10 seconds."
    )

    clock.advance(10)
    recovered = run_client(server, lambda client: client.call_tool("list_notes", {}))
    assert recovered.is_error is not True


def test_api_key_is_not_required_for_stdio_but_rest_auth_boundary_is_unchanged(tmp_path):
    server, _vault, _semantic = server_for(tmp_path)
    result = run_client(server, lambda client: client.call_tool("list_notes", {}))
    assert result.is_error is not True

    with pytest.raises(HTTPException) as captured:
        require_auth(authorization=None, settings=settings_for(tmp_path / "vault"))
    assert captured.value.status_code == 500
    assert captured.value.detail == "Server API_KEY is not configured"


def test_validation_and_unexpected_failures_do_not_echo_arguments_paths_or_exception_text(tmp_path):
    server, vault, _semantic = server_for(tmp_path)
    private_query = "very-private-query-" * 30

    validation = run_client(
        server,
        lambda client: client.call_tool("search_notes", {"query": private_query}),
    )
    assert validation.is_error is True
    assert validation.content[0].text.startswith("validation_error:")
    assert private_query not in validation.content[0].text

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"private failure at {tmp_path}")

    vault.list_notes = fail
    unexpected = run_client(server, lambda client: client.call_tool("list_notes", {}))
    assert unexpected.is_error is True
    assert unexpected.content[0].text == "internal_error: The operation could not be completed."
    assert str(tmp_path) not in unexpected.content[0].text


def test_mcp_logs_are_allowlisted_and_stdout_stays_empty(tmp_path, capsys):
    server, vault, _semantic = server_for(tmp_path)
    vault.list_notes = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError(f"Authorization: Bearer secret at {tmp_path}")
    )

    with capture_mcp_logs() as stream:
        result = run_client(server, lambda client: client.call_tool("list_notes", {}))

    captured = capsys.readouterr()
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert result.is_error is True
    assert captured.out == ""
    assert records[0]["event"] == "mcp_operation_failed"
    assert records[0]["transport"] == "stdio"
    assert records[0]["operation"] == "list_notes"
    assert records[0]["failure_class"] == "internal_error"
    assert re.fullmatch(r"[0-9a-f]{32}", records[0]["request_id"])
    assert "secret" not in stream.getvalue()
    assert str(tmp_path) not in stream.getvalue()


def test_entry_point_invalid_configuration_is_bounded_nonzero_and_stdout_clean(tmp_path):
    missing = tmp_path / "missing-vault"
    env = os.environ.copy()
    env.update({"VAULT_PATH": str(missing), "API_KEY": ""})

    completed = subprocess.run(
        [sys.executable, "-m", "app.mcp_server"],
        cwd=Path(__file__).parents[1],
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "mcp_startup_failed" in completed.stderr
    assert str(missing) not in completed.stderr


def test_official_client_stdio_round_trip_and_clean_eof_shutdown(tmp_path):
    vault = tmp_path / "smoke-vault"
    vault.mkdir()
    (vault / "Smoke.md").write_text("# Smoke\n\nprotocol round trip", encoding="utf-8")
    semantic_data = tmp_path / "smoke-semantic"
    env = os.environ.copy()
    env.update(
        {
            "VAULT_PATH": str(vault),
            "SEMANTIC_DATA_PATH": str(semantic_data),
            "API_KEY": "",
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env=env,
        cwd=Path(__file__).parents[1],
    )

    async def smoke():
        async with Client(parameters) as client:
            tools = await client.list_tools()
            prompts = await client.list_prompts()
            templates = await client.list_resource_templates()
            listed = await client.call_tool("list_notes", {})
            read = await client.call_tool("read_note", {"path": "Smoke.md"})
            searched = await client.call_tool("search_notes", {"query": "round trip"})
            resource = await client.read_resource(note_resource_uri("Smoke.md"))
            return tools, prompts, templates, listed, read, searched, resource

    tools, prompts, templates, listed, read, searched, resource = asyncio.run(
        asyncio.wait_for(smoke(), timeout=20)
    )
    assert [tool.name for tool in tools.tools] == [
        "list_notes",
        "read_note",
        "search_notes",
        "related_notes",
        "duplicate_candidates",
    ]
    assert prompts.prompts == []
    assert [template.uri_template for template in templates.resource_templates] == [
        NOTE_RESOURCE_TEMPLATE
    ]
    assert listed.structured_content["notes"][0]["path"] == "Smoke.md"
    assert read.structured_content["content"].endswith("protocol round trip")
    assert searched.structured_content["results"][0]["path"] == "Smoke.md"
    assert resource.contents[0].text.endswith("protocol round trip")
    assert semantic_data.exists() is False
