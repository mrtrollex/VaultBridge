"""VB-152 A: private transports over real registry-bound filesystem/index owners."""

import asyncio
import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from pydantic import SecretStr

from app.api.private_reads import create_private_read_app
from app.cli import _run_scoped_cli
from app.core.ui_session import create_ui_session_token
from app.mcp_server import create_private_read_server
from app.services.knowledge_spaces import SpaceError
from app.services.persisted_space_reads import compose_persisted_space_reads
from app.services.space_operations import SpaceOperations
from app.services.space_runtime import compose_space_runtime
from app.services.vault import VaultService
from app.space_read_transport import parse_resource_uri, scoped_resource_uri
from tests.test_space_runtime import backend as backend_fixture
from tests.test_space_runtime import binding, configuration


@pytest.fixture(name="backend")
def local_backend(monkeypatch):
    return backend_fixture.__wrapped__(monkeypatch)


@pytest.fixture
def settings(tmp_path):
    return configuration(tmp_path).model_copy(
        update={"api_key": SecretStr("slice-a-test-key"), "rate_limit_enabled": False}
    )


@pytest.fixture
def runtime(settings, backend):
    with compose_space_runtime(settings) as runtime:
        runtime.scheduler.wait(20)
        yield runtime


def rest(settings, runtime, **kwargs):
    return TestClient(create_private_read_app(settings=settings, operations=runtime.operations, **kwargs))


AUTH = {"Authorization": "Bearer slice-a-test-key"}


def tool(server, name, arguments):
    async def invoke():
        async with Client(server) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(invoke())


@pytest.mark.parametrize("endpoint,method,data", [
    ("read", "get", {"space": "space-1", "path": "same.md"}),
    ("list", "get", {"spaces": ["space-1", "default"]}),
    ("search", "post", {"spaces": ["space-1", "default"], "query": "Independent"}),
    ("related", "post", {"spaces": ["space-1", "default"], "text": "Independent", "min_score": 0}),
    ("duplicates", "post", {"space": "space-1", "title": "same"}),
    ("links", "get", {"space": "space-1", "path": "same.md"}),
    ("backlinks", "get", {"space": "space-1", "path": "same.md"}),
])
def test_private_rest_real_read_flows(settings, runtime, endpoint, method, data):
    client = rest(settings, runtime)
    response = getattr(client, method)(f"/api/v1/notes/{endpoint}", headers=AUTH,
                                       **{"params" if method == "get" else "json": data})
    assert response.status_code == 200, response.text
    result = response.json()
    if "spaces" in data:
        assert [row["space_id"] for row in result["coverage"]] == ["default", "space-1"]
        assert {tuple(row["identity"].values()) for row in result["items"]} == {
            ("default", "same.md"), ("space-1", "same.md")}
        assert result["state"] == "complete"
    else:
        assert result["space_id"] == "space-1"
    assert str(settings.vault_path.parent) not in response.text


def test_hygiene_and_default_omission(settings, runtime):
    client = rest(settings, runtime)
    response = client.post("/api/v1/knowledge/hygiene/scan", headers=AUTH,
                           json={"space": "space-1", "groups": ["frontmatter"]})
    assert response.status_code == 200, response.text
    assert response.json()["space_id"] == "space-1"
    legacy = client.get("/notes/read", headers=AUTH, params={"path": "same.md"})
    assert legacy.json() == {"path": "same.md", "content": "# default\nIndependent default notes"}
    explicit = client.get("/notes/read", headers=AUTH, params={"path": "same.md", "space": "default"})
    assert explicit.json() == {"space_id": "default", "result": legacy.json()}


@pytest.mark.parametrize("data,code", [
    ({"spaces": []}, "invalid_scope"), ({"spaces": ["default"] * 9}, "invalid_scope"),
    ({"spaces": None}, "invalid_scope"), ({"spaces": "default"}, "invalid_scope"),
    ({"spaces": ["Default"]}, "invalid_scope"), ({"space": "default"}, "invalid_scope"),
    ({"spaces": ["default"], "space": "default"}, "invalid_scope"),
    ({"spaces": ["default"], "limit": True}, "invalid_request"),
    ({"spaces": ["default"], "folder": ""}, "invalid_request"),
    ({"spaces": ["default"], "limit": 101}, "invalid_request"),
    ({"spaces": ["default"], "surprise": "PRIVATE"}, "invalid_request"),
])
def test_rest_strict_scope_and_payload_before_work(settings, runtime, monkeypatch, data, code):
    def forbidden(*args, **kwargs):
        pytest.fail("owner work on invalid scoped request")
    monkeypatch.setattr(VaultService, "scoped_session", forbidden)
    response = rest(settings, runtime).post("/notes/search", headers=AUTH, json={"query": "Independent", **data})
    assert response.status_code == 422
    assert response.json() == {"detail": code}


def test_duplicate_keys_and_foreign_get_selectors(settings, runtime):
    client = rest(settings, runtime)
    for body in ('{"spaces":["default"],"query":"a","query":"PRIVATE"}',
                 '{"spaces":["default"],"query":"a","limit":NaN}',
                 '{"spaces":["default"],"query":"PRIVATE",',
                 '{"spac\\u0065s":["default"],"query":"PRIVATE",'):
        response = client.post("/notes/search", headers=AUTH, content=body)
        assert response.json() == {"detail": "invalid_request"}
    for query in ("space=default&space=default&path=same.md", "spaces=default&path=same.md"):
        response = client.get("/notes/read?" + query, headers=AUTH)
        assert response.json() == {"detail": "invalid_scope"}


def test_unknown_denied_complete_selection_no_work(tmp_path, backend, monkeypatch):
    settings = configuration(tmp_path, denied=("space-1",)).model_copy(update={"rate_limit_enabled": False})
    runtime = compose_space_runtime(settings)
    client = rest(settings, runtime)
    def forbidden(*args, **kwargs):
        pytest.fail("work before authorization")
    monkeypatch.setattr(VaultService, "scoped_session", forbidden)
    monkeypatch.setattr(SpaceOperations, "_admit", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    try:
        settings = settings.model_copy(update={"api_key": SecretStr("slice-a-test-key")})
        client = rest(settings, runtime)
        responses = [client.post("/notes/search", headers=AUTH,
                     json={"spaces": ["default", sid], "query": "PRIVATE"}) for sid in ("space-1", "unknown")]
        assert [(r.status_code, r.json()) for r in responses] == [(403, {"detail": "scope_not_allowed"})] * 2
        assert backend.instances == []
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("name,arguments", [
    ("read_note", {"space": "space-1", "path": "same.md"}),
    ("list_notes", {"spaces": ["default", "space-1"]}),
    ("search_notes", {"spaces": ["default", "space-1"], "query": "Independent"}),
    ("related_notes", {"spaces": ["default", "space-1"], "text": "Independent", "min_score": 0}),
    ("duplicate_candidates", {"space": "space-1", "title": "same"}),
    ("note_links", {"space": "space-1", "path": "same.md"}),
    ("note_backlinks", {"space": "space-1", "path": "same.md"}),
    ("knowledge_hygiene_scan", {"space": "space-1", "groups": []}),
])
def test_private_mcp_real_scoped_reads(settings, runtime, name, arguments):
    server = create_private_read_server(settings=settings, operations=runtime.operations)
    response = tool(server, name, arguments)
    assert not response.is_error, response
    result = response.structured_content
    assert json.loads(response.content[0].text) == result
    if "items" in result:
        for row in result["items"]:
            assert row["resource_uri"] == scoped_resource_uri(**row["identity"])
    elif name == "read_note":
        assert result["result"]["resource_uri"] == "vaultbridge://space/space-1/note/same.md"


def test_qualified_resource_unicode_and_default_templates(settings, runtime):
    path = "a #percent% café.md"
    (settings.vault_path.parent / "space-1" / path).write_text("Resource synthetic body", encoding="utf-8")
    server = create_private_read_server(settings=settings, operations=runtime.operations)
    async def read():
        async with Client(server) as client:
            resource = await client.read_resource(scoped_resource_uri("space-1", path))
            default = await client.read_resource("vaultbridge://note/same.md")
            return resource, default
    resource, default = asyncio.run(read())
    assert resource.contents[0].text == "Resource synthetic body"
    assert "Independent default" in default.contents[0].text


@pytest.mark.parametrize("uri", [
    "vaultbridge://space/space-1/note/..%2Fsame.md", "vaultbridge://space/space-1/note/%252Fetc.md",
    "vaultbridge://space/space-1/note/a/same.md", "vaultbridge://space/space-1/note/same.md?x=1",
    "vaultbridge://space/space-1/note/same.md#x", "vaultbridge://space/space-1/note/%zz",
    "vaultbridge://space/space-1/note/%ff", "vaultbridge://space/Space-1/note/same.md",
    "vaultbridge://space:4/space-1/note/same.md", "vaultbridge://space/space-1/note/%73ame.md",
    "vaultbridge://space/unknown/note/same.md", "vaultbridge://space/space-1/note/absent.md",
])
def test_resource_errors_uniform(settings, runtime, uri):
    from mcp.shared.exceptions import MCPError
    server = create_private_read_server(settings=settings, operations=runtime.operations)
    async def read():
        async with Client(server) as client:
            with pytest.raises(MCPError) as failure:
                await client.read_resource(uri)
            assert failure.value.message == "note_resource_unavailable: Note resource is invalid or unavailable."
    asyncio.run(read())


def test_persisted_cli_actual_index_no_mutations(settings, runtime, backend, monkeypatch):
    runtime.shutdown()
    base = settings.vault_path.parent
    def snapshot():
        return {p.relative_to(base).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in base.rglob("*") if p.is_file()}
    before = snapshot()
    def forbidden(*args, **kwargs):
        pytest.fail("offline read attempted index initialization/sync/watcher")
    monkeypatch.setattr("app.repositories.semantic.SemanticRepository._connect", forbidden)
    monkeypatch.setattr("app.services.semantic_search.SemanticSearchService.sync", forbidden)
    monkeypatch.setattr("app.services.shared_filesystem_watcher.SharedFilesystemWatcher.start", forbidden)
    for operation, data in (
        ("literal", {"spaces": ["default", "space-1"], "query": "Independent"}),
        ("related", {"spaces": ["default", "space-1"], "text": "Independent", "min_score": 0}),
        ("query", {"spaces": ["space-1"], "semantic_text": "Independent", "literal_text": None,
                   "folder": None, "paths": (), "tags": (), "limit": 20}),
        ("hygiene", {"space": "space-1", "groups": [], "inspect_derived_index": True}),
    ):
        stream = StringIO()
        code = _run_scoped_cli(settings, operation, data, json_output=True, output=stream)
        assert code == 0, stream.getvalue()
        result = json.loads(stream.getvalue())
        assert "coverage" in result or "space_id" in result
    assert snapshot() == before
    # Runtime and each separate CLI invocation have at most one shared backend.
    assert len(backend.instances) == 3


def test_missing_persisted_state_never_prepares(settings, backend):
    stream = StringIO()
    assert _run_scoped_cli(settings, "related", {"spaces": ["space-1"], "text": "Independent"},
                           json_output=True, output=stream) == 1
    assert json.loads(stream.getvalue()) == {"category": "semantic_unavailable"}
    assert backend.instances == []
    assert not list(settings.vault_path.parent.glob("*-data"))


def test_named_health_ui_cookie_fences(settings, runtime, monkeypatch):
    client = rest(settings, runtime)
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/health/live").json() == {"ok": True}
    assert client.get("/health/ready").json() == {"ready": True}
    def forbidden(*args, **kwargs):
        pytest.fail("UI/cookie reached owner")
    monkeypatch.setattr(SpaceOperations, "_owners", forbidden)
    for path in ("/ui", "/ui/", "/ui/assets/app.js", "/ui/session", "/ui/session/"):
        for method in ("get", "post", "delete", "put"):
            response = getattr(client, method)(path)
            assert response.status_code == 404
            assert response.headers["cache-control"] == "no-store"
    client.cookies.set("vaultbridge_ui_session", create_ui_session_token(settings))
    response = client.get("/notes/read?path=same.md", headers={"X-VaultBridge-UI-Request": "1"})
    assert response.status_code == 401


def test_incompatible_private_injection_rejected(settings, runtime):
    with pytest.raises(SpaceError, match="invalid_configuration"):
        create_private_read_app(settings=settings, operations=runtime.operations, vault_service=object())
    with pytest.raises(SpaceError, match="invalid_configuration"):
        create_private_read_server(settings=settings, operations=runtime.operations, semantic_search_service=object())
    with pytest.raises(SpaceError, match="invalid_configuration"):
        create_private_read_app(settings=settings, operations=SpaceOperations(runtime.resolver))


def test_uri_parser_canonical_round_trip():
    path = "folder/café #?.md"
    uri = scoped_resource_uri("default", path)
    assert uri.endswith("folder%2Fcaf%C3%A9%20%23%3F.md")
    assert parse_resource_uri(uri).relative_path == path


def test_modern_http_scoped_reads_and_auth_share_parent_operations(settings, runtime):
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from app.mcp_http import create_private_read_http_transport
    from app.services.rate_limiter import FixedWindowRateLimiter

    limiter = FixedWindowRateLimiter(requests=100, window_seconds=60, max_clients=10)
    configured = settings.model_copy(update={"mcp_http_allowed_hosts": ("testserver",)})
    server, transport = create_private_read_http_transport(settings=configured, operations=runtime.operations,
                                                          rate_limiter=limiter)
    @asynccontextmanager
    async def lifespan(_):
        async with server.session_manager.run():
            yield
    application = Starlette(routes=[Mount("/", transport)], lifespan=lifespan)
    async def invoke():
        async with application.router.lifespan_context(application):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=application),
                                         base_url="http://testserver", headers=AUTH) as http:
                assert (await http.post("/mcp", headers={"Authorization": "Bearer invalid"})).status_code == 401
                async with Client(streamable_http_client("http://testserver/mcp", http_client=http,
                                                         terminate_on_close=False)) as client:
                    results = []
                    for name, arguments in (
                        ("list_notes", {"spaces": ["default", "space-1"]}),
                        ("read_note", {"space": "space-1", "path": "same.md"}),
                        ("search_notes", {"spaces": ["space-1"], "query": "Independent"}),
                        ("related_notes", {"spaces": ["space-1"], "text": "Independent", "min_score": 0}),
                        ("duplicate_candidates", {"space": "space-1", "title": "same"}),
                        ("note_links", {"space": "space-1", "path": "same.md"}),
                        ("note_backlinks", {"space": "space-1", "path": "same.md"}),
                        ("knowledge_hygiene_scan", {"space": "space-1", "groups": []}),
                    ):
                        result = await client.call_tool(name, arguments)
                        assert not result.is_error, result
                        results.append(result.structured_content)
                    uri = results[1]["result"]["resource_uri"]
                    resource = await client.read_resource(uri)
                    assert resource.contents[0].text == results[1]["result"]["content"]
                    denied = await client.call_tool("search_notes", {"spaces": ["unknown"], "query": "PRIVATE"})
                    assert denied.is_error and denied.content[0].text == "scope_not_allowed: Operation failed."
                    raw = ('{"jsonrpc":"2.0","id":999,"method":"tools/call","params":'
                           '{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",'
                           '"io.modelcontextprotocol/clientCapabilities":{}},'
                           '"name":"search_notes","arguments":{"spaces":["default"],'
                           '"query":"a","query":"PRIVATE"}}}')
                    rejected = await http.post("/mcp", content=raw, headers={
                        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Mcp-Method": "tools/call", "Mcp-Name": "search_notes",
                    })
                    assert rejected.status_code == 200, rejected.text
                    assert rejected.json()["result"]["content"] == [
                        {"type": "text", "text": "invalid_request: Operation failed."}]
    asyncio.run(invoke())
    assert runtime.admission.running


def test_actual_private_stdio_process(settings):
    from mcp import StdioServerParameters
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tests.policy_read_stdio_server"],
        env={**os.environ, "VB_TEST_PRIVATE_SETTINGS": json.dumps({**settings.model_dump(mode="json"),
            "knowledge_spaces_json": settings.knowledge_spaces_json})}, cwd=Path(__file__).parents[1])
    async def invoke():
        async with Client(parameters) as client:
            result = await client.call_tool("search_notes", {"spaces": ["space-1", "default"], "query": "Independent"})
            assert not result.is_error
            assert len(result.structured_content["items"]) == 2
            uri = result.structured_content["items"][0]["resource_uri"]
            assert (await client.read_resource(uri)).contents[0].mime_type == "text/markdown"
    asyncio.run(asyncio.wait_for(invoke(), 20))
    assert not list(settings.vault_path.parent.glob("*-data"))


def test_actual_stdio_rejects_duplicate_scoped_json_keys(settings):
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    messages = ['{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{'
        '"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",'
        '"io.modelcontextprotocol/clientCapabilities":{}},"name":"search_notes",'
        '"arguments":{"spaces":["default"],"query":"a","query":"PRIVATE"}}}']
    process = subprocess.Popen([sys.executable, "-m", "tests.policy_read_stdio_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "VB_TEST_PRIVATE_SETTINGS": json.dumps({**settings.model_dump(mode="json"),
            "knowledge_spaces_json": settings.knowledge_spaces_json})}, cwd=Path(__file__).parents[1])
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            process.stdin.write("\n".join(messages) + "\n")
            process.stdin.flush()
            response = pool.submit(process.stdout.readline).result(15)
            result = json.loads(response)["result"]
        finally:
            process.stdin.close()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(10)
    stderr = process.stderr.read()
    assert process.returncode == 0, stderr
    assert result["isError"] is True and result["content"] == [
        {"type": "text", "text": "invalid_request: Operation failed."}]
    assert "PRIVATE" not in response + stderr


@pytest.mark.parametrize("failure,code,status", [
    ("invalid_request", "invalid_request", 422), ("scan_unavailable", "space_unavailable", 503),
    ("note", "space_unavailable", 503), ("unexpected", "internal_error", 500),
])
def test_scoped_owner_errors_identical_across_adapters(settings, runtime, monkeypatch, caplog, failure, code, status):
    from app.services.knowledge_hygiene import KnowledgeHygieneError, KnowledgeHygieneService
    from app.services.vault import NoteUnavailableError
    error = (KnowledgeHygieneError(failure) if failure in {"invalid_request", "scan_unavailable"} else
             NoteUnavailableError("PRIVATE body path") if failure == "note" else RuntimeError("PRIVATE body path"))
    def fail(*_):
        raise error
    monkeypatch.setattr(KnowledgeHygieneService, "scan", fail)
    data = {"space": "space-1", "groups": []}
    response = rest(settings, runtime).post("/api/v1/knowledge/hygiene/scan", headers=AUTH, json=data)
    assert (response.status_code, response.json()) == (status, {"detail": code})
    response = tool(create_private_read_server(settings=settings, operations=runtime.operations),
                    "knowledge_hygiene_scan", data)
    assert response.is_error and len(response.content) == 1 and response.structured_content is None
    assert response.content[0].text == f"{code}: Operation failed."
    stream = StringIO()
    assert _run_scoped_cli(
        settings, "hygiene", data, operations=runtime.operations, json_output=True, output=stream
    ) == (1 if status == 503 else 2)
    assert json.loads(stream.getvalue()) == {"category": code}
    assert "PRIVATE" not in caplog.text


def test_available_empty_partial_and_all_unavailable(settings, runtime, monkeypatch):
    import shutil
    # A selected runtime root disappearing is operational availability, not registry rediscovery.
    shutil.rmtree(settings.vault_path.parent / "space-1")
    client = rest(settings, runtime)
    response = client.post("/notes/search", headers=AUTH,
                           json={"spaces": ["default", "space-1"], "query": "absent"})
    result = response.json()
    assert response.status_code == 200 and result["items"] == [] and result["state"] == "partial"
    assert result["coverage"][0]["state"] == "complete"
    assert result["coverage"][1]["state"] == "unavailable"
    response = client.get("/notes/list", headers=AUTH, params={"spaces": "space-1"})
    assert response.status_code == 503 and response.json() == {"detail": "space_unavailable"}


def test_readiness_denial_does_not_probe(tmp_path, backend, monkeypatch):
    settings = configuration(tmp_path, denied=("default",)).model_copy(update={"rate_limit_enabled": False})
    with compose_space_runtime(settings) as runtime:
        def fail(*args, **kwargs):
            pytest.fail("denied readiness probed owner")
        monkeypatch.setattr(SpaceOperations, "_owners", fail)
        response = rest(settings, runtime).get("/health/ready")
        assert response.status_code == 503 and response.json() == {"ready": False}


def test_read_cancellation_drains_real_owner(settings, runtime, monkeypatch):
    from app.read_execution import run_read
    entered, release = threading.Event(), threading.Event()
    original = VaultService.read_note
    def blocked(owner, path):
        entered.set()
        assert release.wait(10)
        return original(owner, path)
    monkeypatch.setattr(VaultService, "read_note", blocked)
    async def invoke():
        task = asyncio.create_task(run_read(lambda token: runtime.operations.read_note(
            parse_resource_uri("vaultbridge://space/space-1/note/same.md"), cancel=token)))
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(invoke())
    assert runtime.admission._active_thread is None


def test_persisted_model_resolution_never_enters_download_fallback(tmp_path, monkeypatch):
    from app.services.semantic_search import FastEmbedder, SemanticIndexOperationError
    cache = tmp_path / "cache"
    repository = cache / "models--example--model"
    revision = "a" * 40
    model = repository / "snapshots" / revision
    (model / "onnx").mkdir(parents=True)
    (repository / "refs").mkdir()
    (repository / "refs" / "main").write_text(revision, encoding="ascii")
    (model / "onnx/model.onnx").write_bytes(b"synthetic")
    residue = cache / "tmp"
    residue.mkdir()
    (residue / "keep").write_bytes(b"preserve")
    calls = []
    class Embedding:
        @staticmethod
        def list_supported_models():
            return [{"model": "example/model", "sources": {"hf": "example/model"}, "model_file": "onnx/model.onnx"}]
        def __init__(self, **options):
            calls.append(options)
    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=Embedding))
    owner = FastEmbedder("example/model", cache, _read_only=True)
    owner._get_model()
    assert calls[0]["local_files_only"] is True and calls[0]["specific_model_path"] == str(model)
    assert (residue / "keep").read_bytes() == b"preserve"
    owner.invalidate_embedding_compatibility_cache()
    (model / "onnx/model.onnx").unlink()
    with pytest.raises(SemanticIndexOperationError):
        owner._get_model()
    assert len(calls) == 1 and (residue / "keep").exists()


def test_native_cross_space_redirect_is_not_a_resource_grant(settings, runtime):
    import subprocess
    link = settings.vault_path.parent / "space-1" / "redirect"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(settings.vault_path)],
                                capture_output=True, check=False)
        assert result.returncode == 0
    else:
        link.symlink_to(settings.vault_path, target_is_directory=True)
    client = rest(settings, runtime)
    response = client.get("/notes/read", headers=AUTH, params={"space": "space-1", "path": "redirect/same.md"})
    assert response.status_code == 400 and response.json() == {"detail": "invalid_path"}
    result = tool(create_private_read_server(settings=settings, operations=runtime.operations), "read_note",
                  {"space": "space-1", "path": "redirect/same.md"})
    assert result.is_error and result.content[0].text == "invalid_path: Operation failed."


def test_cli_denial_precedes_profile_owners(tmp_path, backend, monkeypatch):
    settings = configuration(tmp_path, denied=("space-1",))
    def fail(*args, **kwargs):
        pytest.fail("denied CLI constructed operational owner")
    monkeypatch.setattr(VaultService, "__init__", fail)
    for sid in ("space-1", "unknown"):
        stream = StringIO()
        assert _run_scoped_cli(settings, "literal", {"spaces": ["default", sid], "query": "PRIVATE"},
                               json_output=True, output=stream) == 2
        assert json.loads(stream.getvalue()) == {"category": "scope_not_allowed"}
    assert backend.instances == []


def test_public_entry_points_and_named_maintenance_stay_guarded(settings, monkeypatch, capsys):
    from app import cli
    from app.main import create_app
    from app.mcp_http import create_mcp_http_transport
    from app.mcp_server import create_mcp_server
    from app.services.knowledge_spaces import SpaceRegistry
    def fail(*args, **kwargs):
        pytest.fail("public named composition did work before activation guard")
    monkeypatch.setattr(SpaceRegistry, "from_settings", fail)
    monkeypatch.setattr(VaultService, "__init__", fail)
    for factory in (create_app, create_mcp_server):
        with pytest.raises(SpaceError, match="invalid_configuration"):
            factory(settings=settings)
    with pytest.raises(SpaceError, match="invalid_configuration"):
        create_mcp_http_transport(settings=settings, vault_service=None, semantic_search_service=None,
            duplicate_candidate_service=None, relationship_service=None, knowledge_hygiene_service=None,
            semantic_indexer=None, rate_limiter=None)
    for arguments in (["status"], ["index"], ["index", "check"], ["index", "rebuild"], ["reindex"]):
        assert cli.main(arguments, settings=settings) == 2
        assert capsys.readouterr().err == "invalid_configuration: Operation failed.\n"
    for run in (cli.run_status, cli.run_index, cli.run_index_check, cli.run_index_rebuild, cli.run_reindex):
        with pytest.raises(SpaceError, match="invalid_configuration"):
            run(settings)


def test_saturated_admission_rejects_transport_and_cancelled_waiters_release(settings, runtime, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from app.services.scoped_budget import CancellationToken
    from tests.test_space_runtime import waiters
    entered, release = threading.Event(), threading.Event()
    original = VaultService.read_note
    calls = []
    def blocked(owner, path):
        calls.append(path)
        entered.set()
        assert release.wait(15)
        return original(owner, path)
    monkeypatch.setattr(VaultService, "read_note", blocked)
    qualified = parse_resource_uri("vaultbridge://space/space-1/note/same.md")
    token = CancellationToken()
    def read():
        try:
            return runtime.operations.read_note(qualified, cancel=token)
        except SpaceError as exc:
            return exc.category
    with ThreadPoolExecutor(max_workers=65) as pool:
        active = pool.submit(read)
        try:
            assert entered.wait(10)
            queued = [pool.submit(read) for _ in range(64)]
            waiters(runtime.admission, 64)
            response = rest(settings, runtime).get("/notes/read", headers=AUTH,
                                                  params={"space": "space-1", "path": "same.md"})
            assert response.status_code == 503 and response.json() == {"detail": "space_unavailable"}
            assert calls == ["same.md"]
            token.cancel()
            assert [future.result(10) for future in queued] == ["operation_cancelled"] * 64
        finally:
            release.set()
        assert active.result(10) == "operation_cancelled"
    assert runtime.admission._waiting == 0 and runtime.admission._active_thread is None


def test_eight_space_persisted_profile_has_one_model_no_workers(tmp_path, backend):
    from app.space_read_transport import execute_scoped
    settings = configuration(tmp_path, 8)
    with compose_space_runtime(settings) as runtime:
        runtime.scheduler.wait(20)
    previous_models = len(backend.instances)
    with compose_persisted_space_reads(settings) as profile:
        assert profile.scheduler is None and profile.watcher is None
        selected = ["default", *[f"space-{n}" for n in range(1, 8)]]
        result = execute_scoped(
            profile.operations, "related", {"spaces": selected, "text": "Independent", "min_score": 0, "limit": 100}
        )
        assert len(result["items"]) == 8 and len(result["coverage"]) == 8
        assert len(backend.instances) == previous_models + 1
    assert profile._state == "stopped" and profile.resource._closed


def test_scoped_cli_query_inheritance_json_and_human_escaping(settings, runtime):
    query = {"spaces": ["space-1", "space-1"], "literal_text": "Independent", "semantic_text": None,
             "folder": None, "paths": ("same.md",), "tags": (), "limit": 20}
    output = StringIO()
    assert _run_scoped_cli(settings, "query", query, operations=runtime.operations, output=output) == 0
    assert '["space-1"] "same.md"' in output.getvalue() and "coverage:" in output.getvalue()
    query["spaces"] = ["default", "space-1"]
    output = StringIO()
    assert (
        _run_scoped_cli(settings, "query", query, operations=runtime.operations, json_output=True, output=output) == 2
    )
    assert json.loads(output.getvalue()) == {"category": "invalid_scope"}


def test_scoped_openapi_preserves_routes_and_describes_scope(settings, runtime):
    schema = rest(settings, runtime).app.openapi()
    assert "/api/v1/knowledge/query" not in schema["paths"]
    for path in ("/notes/read", "/api/v1/notes/read"):
        operation = schema["paths"][path]["get"]
        assert any(parameter["name"] == "space" for parameter in operation["parameters"])
        assert len(operation["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]) == 2
    request = schema["paths"]["/api/v1/notes/search"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert request["anyOf"][0]["properties"]["limit"]["maximum"] == 50
    assert request["anyOf"][1]["properties"]["limit"]["maximum"] == 100


def test_public_cli_explicit_default_and_unscoped_json(settings, capsys):
    from app.cli import main
    from app.core.config import Settings
    legacy = Settings(**settings.model_dump())
    assert main(["search", "Independent", "--json"], settings=legacy) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["query"] == "Independent" and result["results"][0]["path"] == "same.md"
    assert main(["search", "Independent", "--space", "default", "--json", "--limit", "100"], settings=legacy) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["items"][0]["identity"] == {"space_id": "default", "path": "same.md"}
    assert main(["search", "Independent", "--space", "default", "--folder", "", "--json"], settings=legacy) == 2
    assert json.loads(capsys.readouterr().out) == {"category": "invalid_request"}


def test_scoped_cli_parse_errors_never_echo_input(settings, capsys):
    from app.cli import main
    with pytest.raises(SystemExit) as error:
        main(["search", "PRIVATE", "--space", "default", "--limit", "PRIVATE", "--json"], settings=settings)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == '{"category": "invalid_request"}\n' and captured.err == ""


def test_disabled_semantics_denies_before_index_or_discovery(tmp_path, backend, monkeypatch):
    settings = configuration(tmp_path, disabled=("space-1",)).model_copy(update={
        "api_key": SecretStr("slice-a-test-key"), "rate_limit_enabled": False})
    with compose_space_runtime(settings) as runtime:
        runtime.scheduler.wait(20)
        before = list(backend.calls)
        def fail(*args, **kwargs):
            pytest.fail("disabled semantic request performed operational work")
        monkeypatch.setattr(VaultService, "scoped_session", fail)
        monkeypatch.setattr("sqlite3.connect", fail)
        response = rest(settings, runtime).post("/notes/related", headers=AUTH,
                                               json={"spaces": ["space-1"], "text": "PRIVATE"})
        assert response.status_code == 409 and response.json() == {"detail": "capability_unavailable"}
        assert backend.calls == before
        assert not (tmp_path / "space-1-data").exists()


def test_named_ui_fence_handles_proxy_root_path(settings, runtime):
    client = TestClient(create_private_read_app(settings=settings, operations=runtime.operations), root_path="/proxy")
    response = client.post("/proxy/ui/session", content="PRIVATE")
    assert response.status_code == 404 and response.headers["cache-control"] == "no-store"


def test_legacy_registry_cannot_be_injected_into_private_named_adapter(settings, runtime):
    from app.core.config import Settings
    from app.services.adapter_composition import legacy_operations
    legacy = legacy_operations(Settings(**settings.model_dump()), vault=binding(runtime)._owners.vault)
    with pytest.raises(SpaceError, match="invalid_configuration"):
        create_private_read_app(settings=settings, operations=legacy)


@pytest.mark.parametrize("fault", ["signature", "namespace", "sidecar"])
def test_persisted_incompatible_state_is_immutable_and_model_free(settings, runtime, backend, fault):
    import sqlite3
    runtime.shutdown()
    database = settings.vault_path.parent / "space-1-data" / "semantic-index.sqlite3"
    if fault == "sidecar":
        Path(str(database) + "-wal").write_bytes(b"Synthetic unavailable sidecar")
    else:
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE meta SET value=? WHERE key=?",
                               ("{}", "index_signature") if fault == "signature" else
                               ("foreign", "knowledge_space_id"))
    files = [p for p in database.parent.rglob("*") if p.is_file()]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    models = len(backend.instances)
    output = StringIO()
    assert _run_scoped_cli(settings, "related", {"spaces": ["space-1"], "text": "Independent"},
                           json_output=True, output=output) == 1
    assert json.loads(output.getvalue()) == {"category": "semantic_unavailable"}
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before
    assert len(backend.instances) == models


def test_hygiene_supplementary_capability_denial_before_admission(tmp_path, backend, monkeypatch):
    from app.services.knowledge_spaces import Capability
    settings = configuration(tmp_path, capabilities=[c.value for c in Capability if c != Capability.RELATIONSHIPS])
    settings = settings.model_copy(update={"api_key": SecretStr("slice-a-test-key"), "rate_limit_enabled": False})
    runtime = compose_space_runtime(settings)
    def fail(*args, **kwargs):
        pytest.fail("supplementary capability denial admitted work")
    monkeypatch.setattr(SpaceOperations, "_admit", fail)
    try:
        response = rest(settings, runtime).post("/api/v1/knowledge/hygiene/scan", headers=AUTH,
                                               json={"space": "space-1", "groups": ["relationships"]})
        assert response.status_code == 409 and response.json() == {"detail": "capability_unavailable"}
        assert backend.instances == []
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("policy", ["disabled", "capability"])
def test_omitted_duplicate_optional_semantics_respects_named_policy(tmp_path, backend, monkeypatch, policy):
    from app.services.knowledge_spaces import Capability
    from app.services.semantic_search import SemanticSearchService
    settings = configuration(tmp_path, disabled=("default",) if policy == "disabled" else (),
        capabilities=([c.value for c in Capability if c != Capability.SEMANTIC_RETRIEVAL]
                      if policy == "capability" else None))
    settings = settings.model_copy(update={"api_key": SecretStr("slice-a-test-key"), "rate_limit_enabled": False})
    with compose_space_runtime(settings) as runtime:
        runtime.scheduler.wait(20)
        calls = list(backend.calls)
        def fail(*args, **kwargs):
            pytest.fail("optional semantic policy exclusion called semantic owner")
        monkeypatch.setattr(SemanticSearchService, "search", fail)
        monkeypatch.setattr(SemanticSearchService, "probe_search_availability", fail)
        response = rest(settings, runtime).post("/notes/duplicates", headers=AUTH, json={"title": "same"})
        assert response.status_code == 200 and response.json()["results"][0]["match_type"] == "exact_title"
        response = tool(create_private_read_server(settings=settings, operations=runtime.operations),
                        "duplicate_candidates", {"title": "same"})
        assert not response.is_error and response.structured_content["results"][0]["match_type"] == "exact_title"
        assert backend.calls == calls


def test_explicit_duplicate_absent_advice_is_unavailable_without_semantics(settings, runtime, monkeypatch):
    from app.services.semantic_search import SemanticSearchService
    def fail(*args, **kwargs):
        pytest.fail("explicit title advice requested semantic evidence")
    monkeypatch.setattr(SemanticSearchService, "search", fail)
    data = {"space": "space-1", "title": "Absent title"}
    response = rest(settings, runtime).post("/notes/duplicates", headers=AUTH, json=data)
    assert response.status_code == 503 and response.json() == {"detail": "semantic_unavailable"}
    response = tool(
        create_private_read_server(settings=settings, operations=runtime.operations), "duplicate_candidates", data
    )
    assert response.is_error and response.content[0].text == "semantic_unavailable: Operation failed."


def test_actual_mcp_cancellation_signals_shared_token(settings, runtime, monkeypatch):
    from app.services.scoped_budget import CancellationToken
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    original_read, original_cancel = VaultService.read_note, CancellationToken.cancel
    def read(owner, path):
        entered.set()
        assert release.wait(10)
        return original_read(owner, path)
    def cancel(token):
        cancelled.set()
        original_cancel(token)
    monkeypatch.setattr(VaultService, "read_note", read)
    monkeypatch.setattr(CancellationToken, "cancel", cancel)
    server = create_private_read_server(settings=settings, operations=runtime.operations)
    async def invoke():
        async with Client(server) as client:
            task = asyncio.create_task(client.call_tool("read_note", {"space": "space-1", "path": "same.md"}))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                task.cancel()
                assert await asyncio.to_thread(cancelled.wait, 5)
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.to_thread(runtime.admission.drain)
    asyncio.run(invoke())
    assert runtime.admission._active_thread is None


def test_actual_http_disconnect_drains_without_response(settings, runtime, monkeypatch):
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    original = VaultService.read_note
    from app.services.scoped_budget import CancellationToken
    original_cancel = CancellationToken.cancel
    def read(owner, path):
        entered.set()
        assert release.wait(10)
        return original(owner, path)
    def cancel(token):
        cancelled.set()
        original_cancel(token)
    monkeypatch.setattr(VaultService, "read_note", read)
    monkeypatch.setattr(CancellationToken, "cancel", cancel)
    application = create_private_read_app(settings=settings, operations=runtime.operations)
    async def invoke():
        closed, body_read = asyncio.Event(), False
        sent = []
        async def receive():
            nonlocal body_read
            if not body_read:
                body_read = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await closed.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": "/notes/read", "raw_path": b"/notes/read",
                 "query_string": b"space=space-1&path=same.md", "root_path": "",
                 "headers": [(b"authorization", b"Bearer slice-a-test-key")],
                 "client": ("testclient", 1), "server": ("testserver", 80)}
        task = asyncio.create_task(application(scope, receive, send))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            closed.set()
            assert await asyncio.to_thread(cancelled.wait, 5)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sent == []
    asyncio.run(invoke())
    assert runtime.admission._active_thread is None
