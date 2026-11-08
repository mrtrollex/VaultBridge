"""VB-152 D: production constructors, lifespans, process entry points and fences."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import cli
from app.core.config import Settings
from app.core.ui_session import create_ui_session_token
from app.main import create_app
from app.mcp_http import create_mcp_http_transport
from app.mcp_server import create_mcp_server
from app.services.knowledge_hygiene import KnowledgeHygieneRequest
from app.services.knowledge_spaces import Capability, Dialect, SpaceError, SpaceId, SpaceRegistry
from app.services.scoped_budget import CancellationToken
from app.services.semantic_scheduler import SharedSemanticScheduler
from app.services.shared_filesystem_watcher import SharedFilesystemWatcher
from app.services.space_runtime import RuntimeSpaceIndexLifecycle
from app.services.vault import AtomicCaptureError, VaultService
from tests.test_policy_read_adapters import AUTH
from tests.test_policy_read_repairs import http_scope
from tests.test_policy_read_repairs import request as http_request
from tests.test_promotion import CAPTURE_ID, PROMOTION_ID, SOURCE
from tests.test_scoped_promotion import request as capture_request
from tests.test_space_runtime import backend as backend_fixture


@pytest.fixture
def backend(monkeypatch):
    return backend_fixture.__wrapped__(monkeypatch)


@pytest.fixture
def settings(tmp_path):
    records = []
    for sid in ("default", "research"):
        root = tmp_path / sid
        (root / "Notes").mkdir(parents=True)
        (root / "same.md").write_text(f"# {sid}\nIndependent {sid} notes", encoding="utf-8")
        (root / "Notes/nested.md").write_text("Independent nested note", encoding="utf-8")
        records.append(dict(space_id=sid, root=str(root), semantic_data_path=str(tmp_path / (sid + "-data")),
            read="allow", write="allow", indexing="disabled", dialects=[d.value for d in Dialect],
            capabilities=[c.value for c in Capability]))
    return Settings(vault_path=records[0]["root"], semantic_data_path=records[0]["semantic_data_path"],
        knowledge_spaces_json=json.dumps({"version": 1, "spaces": records}), api_key=SecretStr("slice-a-test-key"),
        rate_limit_enabled=False, mcp_http_allowed_hosts=("testserver",),
        mcp_http_allowed_origins=("http://testserver",), semantic_model="e6-test")


def policy(settings, sid="research", **changes):
    data = json.loads(settings.knowledge_spaces_json)
    next(row for row in data["spaces"] if row["space_id"] == sid).update(changes)
    return Settings(**{**settings.model_dump(), "knowledge_spaces_json": json.dumps(data)})


def environment(settings):
    result = {key: value for key, value in os.environ.items() if key not in Settings.ENVIRONMENT_VARIABLES}
    for field, info in Settings.model_fields.items():
        value = getattr(settings, field)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is not None:
            result[info.alias] = (",".join(value) if isinstance(value, tuple) else str(value))
    return result


def frame(name="read_note", arguments=None, *, version="2026-07-28", uri=None):
    return json.dumps({"jsonrpc": "2.0", "id": 101, "method": "resources/read" if uri else "tools/call",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": version,
                              "io.modelcontextprotocol/clientCapabilities": {}},
                   **({"uri": uri} if uri else {"name": name, "arguments": arguments or {}})}}).encode()


def http(client, body, *, version="2026-07-28", **headers):
    request_headers = dict(http_scope(body, version=version)["headers"])
    request_headers = {key.decode(): value.decode() for key, value in request_headers.items()}
    request_headers.pop("content-length", None)
    return client.post("/mcp", content=body,
                       headers={**request_headers, **{key.lower(): value for key, value in headers.items()}})


def stdio(settings, bodies):
    process = subprocess.Popen([sys.executable, "-m", "app.mcp_server"], env=environment(settings),
        cwd=Path(__file__).parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    responses = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            for body in bodies:
                process.stdin.write(body + b"\n")
                process.stdin.flush()
                line = executor.submit(process.stdout.readline).result(20)
                assert line, process.stderr.read().decode(errors="replace")
                responses.append(json.loads(line))
        finally:
            process.stdin.close()
            try:
                process.wait(20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(10)
    stderr = process.stderr.read().decode(errors="replace")
    assert process.returncode == 0, stderr
    assert "Traceback" not in stderr
    return responses


def snapshot(directory):
    return {p.relative_to(directory).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in directory.rglob("*") if p.is_file()}


def test_factory_exact_single_generation_and_no_legacy_components(settings, monkeypatch):
    original, registries = SpaceRegistry.from_settings, []
    def registry(value):
        result = original(value)
        registries.append(result)
        return result
    def forbidden(*args, **kwargs):
        pytest.fail("named composition allocated a legacy owner/worker")
    monkeypatch.setattr(SpaceRegistry, "from_settings", registry)
    monkeypatch.setattr("app.main.VaultService", forbidden)
    monkeypatch.setattr("app.main.BackgroundSemanticIndexer", forbidden)
    monkeypatch.setattr("app.main.SemanticFilesystemWatcher", forbidden)
    app = create_app(settings=settings)
    runtime = app.state.space_runtime
    assert registries == [runtime.registry]
    assert app.state.space_operations is runtime.operations
    assert runtime.resolver is runtime.operations._resolver
    assert runtime.admission is runtime.operations._admission
    assert runtime._state == "composed" and not runtime.admission.running
    assert runtime.scheduler is runtime.watcher is runtime.resource is None
    assert app.state.mcp_server is None
    assert not hasattr(app.state, "vault_service")
    with TestClient(app) as client:
        assert runtime._state == "running"
        assert client.get("/health").json() == {"ok": True}
    assert runtime._state == "stopped"
    assert snapshot(settings.vault_path.parent).keys() == {
        "default/same.md", "default/Notes/nested.md", "research/same.md", "research/Notes/nested.md"}
    with pytest.raises(RuntimeError, match="restarted"):
        with TestClient(app):
            pass
    assert len(registries) == 1


@pytest.mark.parametrize("owner", ["vault_service", "semantic_search_service", "duplicate_candidate_service",
    "relationship_service", "knowledge_hygiene_service", "semantic_indexer", "semantic_watcher"])
def test_conflicting_raw_injection_before_registry(settings, monkeypatch, owner):
    def forbidden(*args, **kwargs):
        pytest.fail("incompatible injection reached registry or allocation")
    monkeypatch.setattr(SpaceRegistry, "from_settings", forbidden)
    factories = [create_app] + ([] if owner == "semantic_watcher" else [create_mcp_server, create_mcp_http_transport])
    for factory in factories:
        with pytest.raises(SpaceError) as error:
            factory(settings=settings, **{owner: object()}, **({"rate_limiter": None}
                if factory is create_mcp_http_transport else {}))
        assert error.value.category == "invalid_configuration"
        assert error.value.reason == "named_serving_unsupported"


@pytest.mark.parametrize("named", [False, True])
def test_actual_module_level_application(settings, named):
    env = environment(settings)
    if not named:
        env.pop("KNOWLEDGE_SPACES_JSON")
    code = ("from app.main import app; from fastapi.testclient import TestClient; "
            "c=TestClient(app); "
            "print(hasattr(app.state,'space_runtime')); print(c.get('/health/live').json())")
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.decode().splitlines() == [str(named), "{'ok': True}"]


@pytest.mark.parametrize("prefix", ["/notes", "/api/v1/notes"])
def test_public_rest_reads_query_writes_default_and_explicit(settings, prefix):
    with TestClient(create_app(settings=settings)) as client:
        omitted = client.get(prefix + "/read", params={"path": "same.md"}, headers=AUTH)
        assert omitted.json() == {"path": "same.md", "content": "# default\nIndependent default notes"}
        scoped = client.get(prefix + "/read", params={"space": "default", "path": "same.md"}, headers=AUTH)
        assert scoped.json() == {"space_id": "default", "result": omitted.json()}
        assert scoped.headers["cache-control"] == "no-store"
        results = client.post(prefix + "/search", json={"spaces": ["research", "default"],
            "query": "Independent"}, headers=AUTH).json()
        assert {(row["identity"]["space_id"], row["identity"]["path"]) for row in results["items"]} == {
            (sid, path) for sid in ("default", "research") for path in ("same.md", "Notes/nested.md")}
        query = client.post("/api/v1/knowledge/query", headers=AUTH,
            json={"spaces": ["research", "default"], "literal_text": "Independent"})
        assert query.status_code == 200 and query.headers["cache-control"] == "no-store"
        assert [row["identity"] for row in query.json()["items"]] == [row["identity"] for row in results["items"]]
        created = client.post(prefix, headers=AUTH, json={"space": "research", "title": "Target", "content": "Body"})
        assert created.status_code == 200 and created.json()["space_id"] == "research"
        target = created.json()["result"]["path"]
        appended = client.post(prefix + "/append", headers=AUTH,
            json={"space": "research", "path": target, "content": "Addition"})
        assert appended.status_code == 200 and appended.json()["result"]["success"]
        assert not (settings.vault_path / target).exists()


@pytest.mark.parametrize("selection", [["unknown"], ["research"], ["default", "research"]])
def test_denied_unknown_and_mixed_no_operational_work(settings, monkeypatch, selection):
    settings = policy(settings, read="deny", write="deny")
    with TestClient(create_app(settings=settings)) as client:
        def forbidden(*args, **kwargs):
            pytest.fail("unauthorized operational owner access")
        monkeypatch.setattr(VaultService, "scoped_session", forbidden)
        response = client.post("/api/v1/knowledge/query", headers=AUTH,
            json={"spaces": selection, "literal_text": "PRIVATE"})
        assert response.status_code == 403 and response.json() == {"detail": "scope_not_allowed"}


@pytest.mark.parametrize("prefix", ["", "/proxy"])
@pytest.mark.parametrize("path", ["/ui", "/ui/", "/ui/session", "/ui/assets/dashboard.js", "/ui/unknown"])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "PUT", "OPTIONS", "HEAD"])
def test_public_named_ui_static_fence(settings, prefix, path, method, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("named UI performed session/body/asset/owner work")
    monkeypatch.setattr("app.ui.router.create_ui_session_token", forbidden)
    with TestClient(create_app(settings=settings), root_path=prefix) as client:
        response = client.request(method, prefix + path, content=b"PRIVATE", headers=AUTH)
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        if method != "HEAD":
            assert response.json() == {"detail": "Not Found"}


def test_named_health_readiness_cookie_and_privacy(settings, monkeypatch):
    cookie = create_ui_session_token(settings)
    with TestClient(create_app(settings=settings)) as client:
        def forbidden(*args, **kwargs):
            pytest.fail("disabled default readiness probed owner")
        monkeypatch.setattr(VaultService, "vault_available", forbidden)
        assert client.get("/health/live").json() == {"ok": True}
        assert client.get("/health").json() == {"ok": True}
        ready = client.get("/health/ready")
        assert ready.status_code == 503 and ready.json() == {"ready": False}
        assert client.get("/privacy").status_code == 200
        response = client.get("/notes/read?path=same.md", headers={"X-VaultBridge-UI-Request": "1",
            "Cookie": "vaultbridge_ui_session=" + cookie})
        assert response.status_code == 401


@pytest.mark.parametrize("version", ["2026-07-28", "2025-11-25"])
def test_mounted_http_uses_exact_parent_operations_and_limiter(settings, version):
    settings = settings.model_copy(update={"mcp_http_enabled": True, "mcp_write_enabled": True})
    app = create_app(settings=settings)
    assert app.state.mcp_server._vaultbridge_operations is app.state.space_runtime.operations
    assert app.state.mcp_server._vaultbridge_runtime is None
    mounted = app.routes[-1].app
    assert mounted.rate_limiter is app.state.rate_limiter
    with TestClient(app) as client:
        read = http(client, frame(arguments={"space": "research", "path": "same.md"}, version=version), version=version)
        assert read.status_code == 200, read.text
        assert read.json()["result"]["structuredContent"]["space_id"] == "research"
        written = http(client, frame("create_note", {"space": "research", "title": "HTTP", "content": "Body"},
                                    version=version), version=version)
        assert written.json()["result"]["structuredContent"]["result"]["success"]
        resource = http(client, frame(uri="vaultbridge://space/research/note/Notes%2Fnested.md", version=version),
                        version=version)
        assert resource.json()["result"]["contents"][0]["text"] == "Independent nested note"
        for headers, status in [({"Authorization": "Bearer wrong"}, 401),
                                ({"Host": "foreign.invalid"}, 421), ({"Origin": "http://foreign.invalid"}, 403)]:
            assert http(client, frame(arguments={"path": "same.md"}, version=version),
                        version=version, **headers).status_code == status
    assert app.state.space_runtime._state == "stopped"


@pytest.mark.parametrize("write", [False, True])
def test_actual_production_stdio_runtime_and_write_gate(settings, write):
    settings = settings.model_copy(update={"mcp_write_enabled": write})
    bodies = [frame(arguments={"space": "research", "path": "same.md"}),
        frame(uri="vaultbridge://space/research/note/Notes%2Fnested.md"),
        frame("knowledge_query", {"spaces": ["default", "research"], "literal_text": "Independent"}),
        frame("create_note", {"space": "research", "title": "Stdio", "content": "Body"})]
    responses = stdio(settings, bodies)
    assert responses[0]["result"]["structuredContent"]["space_id"] == "research"
    assert responses[1]["result"]["contents"][0]["text"] == "Independent nested note"
    assert {row["identity"]["space_id"] for row in responses[2]["result"]["structuredContent"]["items"]} == {
        "default", "research"}
    assert responses[3]["result"].get("isError", False) is not write
    assert not list(settings.vault_path.parent.glob("*-data"))


@pytest.mark.parametrize("command", [["search", "Independent", "--space", "research", "--json"],
    ["search", "Independent", "--json"], ["query", "--literal-text", "Independent", "--space", "research", "--json"],
    ["query", "--literal-text", "Independent", "--json"],
    ["hygiene", "scan", "--group", "frontmatter", "--space", "research", "--json"]])
def test_actual_cli_process_readonly(settings, command):
    before = snapshot(settings.vault_path.parent)
    result = subprocess.run([sys.executable, "-m", "app.cli", *command], env=environment(settings),
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace") + result.stdout.decode(errors="replace")
    assert isinstance(json.loads(result.stdout), dict)
    assert snapshot(settings.vault_path.parent) == before


@pytest.mark.parametrize("explicit", [False, True])
def test_exported_named_capture_review_apply_lifecycle(settings, explicit):
    selected = {"space": "research"} if explicit else {}
    stream = StringIO()
    assert cli.run_capture(settings, input_stream=BytesIO(json.dumps(capture_request()).encode()),
                           output=stream, **selected) == 0
    captured = json.loads(stream.getvalue())
    assert (captured["result"] if explicit else captured)["committed"]
    stream = StringIO()
    assert cli.run_promote_review(settings, source_path=SOURCE, capture_id=CAPTURE_ID,
        **({"destination": "Notes/Target.md"} if explicit else {}), output=stream, **selected) == 0
    review = json.loads(stream.getvalue())
    review = review["result"] if explicit else review
    assert ("provenance_version" in review) is explicit
    approved = dict(source_path=SOURCE, capture_id=CAPTURE_ID, expected_source_sha256=review["source_sha256"],
        approved_content="Approved body", action="create", destination="Notes/Target.md", promotion_id=PROMOTION_ID,
        approved_at="2026-10-03T13:00:00Z", transfer_fields=["title"], expected_destination="absent")
    stream = StringIO()
    assert cli.run_promote_apply(settings, input_stream=BytesIO(json.dumps(approved).encode()),
                                 output=stream, **selected) == 0
    result = json.loads(stream.getvalue())
    assert (result["result"] if explicit else result)["committed"]
    root = settings.vault_path.parent / ("research" if explicit else "default")
    assert (root / "Notes/Target.md").exists()
    assert not list(settings.vault_path.parent.glob("*-data"))


@pytest.mark.parametrize("runner,arguments", [(cli.run_search, {"query": "Independent"}),
    (cli.run_related, {"text": "Independent"}), (cli.run_knowledge_query, {"literal_text": "Independent"}),
    (cli.run_hygiene_scan, {"request": KnowledgeHygieneRequest(groups=frozenset({"frontmatter"}))})])
def test_exported_named_read_rejects_raw_owner_before_composition(settings, monkeypatch, runner, arguments):
    def forbidden(*args, **kwargs):
        pytest.fail("raw injection reached owner composition")
    monkeypatch.setattr(SpaceRegistry, "from_settings", forbidden)
    stream = StringIO()
    injection = {"vault_service": object()} if runner is cli.run_search else {"service": object()}
    assert runner(settings, output=stream, json_output=True, **arguments, **injection) == 2
    assert json.loads(stream.getvalue()) == {"category": "invalid_configuration"}


@pytest.mark.parametrize("fault", ["watcher", "scheduler", "mcp"])
def test_public_startup_failure_rolls_back_same_generation(settings, backend, monkeypatch, fault):
    settings = policy(policy(settings, indexing="enabled"), sid="default", indexing="enabled").model_copy(
        update={"semantic_watch_enabled": True, "mcp_http_enabled": True})
    app = create_app(settings=settings)
    runtime = app.state.space_runtime
    def failed(*args, **kwargs):
        assert not runtime.admission.running
        raise RuntimeError("Synthetic startup failure")
    if fault == "watcher":
        monkeypatch.setattr(SharedFilesystemWatcher, "start", failed)
    elif fault == "scheduler":
        monkeypatch.setattr(SharedSemanticScheduler, "start", failed)
    else:
        @asynccontextmanager
        async def fail_mcp():
            raise RuntimeError("Synthetic MCP startup failure")
            yield
        monkeypatch.setattr(app.state.mcp_server.session_manager, "run", fail_mcp)
    with pytest.raises(RuntimeError, match="Synthetic"):
        with TestClient(app):
            pass
    assert app.state.space_runtime is runtime
    assert not runtime.admission.running and runtime._state == "stopped"
    assert runtime.resource._closed


def test_public_background_readiness_reconcile_and_qualified_write(settings, backend):
    settings = policy(policy(settings, indexing="enabled"), sid="default", indexing="enabled").model_copy(
        update={"semantic_watch_enabled": True, "semantic_watch_debounce_seconds": 0.01})
    app = create_app(settings=settings)
    with TestClient(app) as client:
        rt = app.state.space_runtime
        assert rt.watcher is not None and rt.scheduler is not None
        rt.scheduler.wait(20)
        assert client.get("/health/ready").json() == {"ready": True}
        assert len(backend.instances) == 1
        for sid in ("default", "research"):
            assert rt.registry._owners[SpaceId(sid)].semantic.repository.db_path.exists()
        response = client.post("/api/v1/notes", headers=AUTH,
            json={"space": "research", "title": "Committed", "content": "Qualified"})
        assert response.status_code == 200
        rt.scheduler.wait(20)
        owners = rt.registry._owners
        assert any(chunk.path == "Inbox/Committed.md" for chunk in
                   owners[SpaceId("research")].semantic.repository.load_chunks())
        assert all(chunk.path != "Inbox/Committed.md" for chunk in
                   owners[SpaceId("default")].semantic.repository.load_chunks())
    assert rt._state == "stopped" and rt.resource._closed


def test_public_native_watcher_external_edit_is_space_bound(settings, backend, monkeypatch):
    from app.services.semantic_search import SemanticSearchService

    settings = policy(policy(settings, indexing="enabled"), sid="default", indexing="enabled").model_copy(
        update={"semantic_watch_enabled": True, "semantic_watch_debounce_seconds": 0.01})
    indexed = threading.Event()
    original = SemanticSearchService.sync_step
    def observed(owner, *args, **kwargs):
        result = original(owner, *args, **kwargs)
        if result.indexed and owner.vault_root.name == "research" and any(
            chunk.path == "same.md" and "native revision" in chunk.content for chunk in owner.repository.load_chunks()
        ):
            indexed.set()
        return result
    monkeypatch.setattr(SemanticSearchService, "sync_step", observed)
    with TestClient(create_app(settings=settings)) as client:
        rt = client.app.state.space_runtime
        rt.scheduler.wait(20)
        with rt.scheduler._condition:
            assert rt.scheduler._condition.wait_for(lambda: all(
                entry.coverage_restored == entry.coverage_revision for entry in rt.scheduler._owners.values()), 20)
        default = rt.registry._owners[SpaceId("default")].semantic.repository
        before = default.load_chunks()
        (settings.vault_path.parent / "research/same.md").write_bytes(b"# native revision\nIndependent edited note")
        assert indexed.wait(20), "production native watcher did not index external edit"
        rt.scheduler.wait(20)
        assert default.load_chunks() == before
        response = client.post("/api/v1/knowledge/query", headers=AUTH,
            json={"spaces": ["research", "default"], "literal_text": "native revision"})
        assert [row["identity"] for row in response.json()["items"]] == [{"space_id": "research", "path": "same.md"}]


def test_shutdown_fences_before_mcp_ingress_close_and_retains_failed_cleanup(settings, backend, monkeypatch):
    settings = policy(settings, indexing="enabled").model_copy(update={"mcp_http_enabled": True})
    app = create_app(settings=settings)
    rt = app.state.space_runtime
    @asynccontextmanager
    async def sessions():
        yield
        assert not rt.admission.running
        assert rt.scheduler._closed
    monkeypatch.setattr(app.state.mcp_server.session_manager, "run", sessions)
    original = type(rt.resource).close
    def failed(resource):
        raise RuntimeError("Retained cleanup")
    with pytest.raises(RuntimeError, match="Retained cleanup"):
        with TestClient(app):
            rt.scheduler.wait(20)
            monkeypatch.setattr(type(rt.resource), "close", failed)
    assert rt._state == "stopping" and not rt.admission.running
    assert app.state.space_runtime is rt
    with pytest.raises(RuntimeError, match="restarted"):
        rt.start()
    monkeypatch.setattr(type(rt.resource), "close", original)
    rt.shutdown()
    assert rt._state == "stopped"


def test_mounted_http_disconnect_drains_actual_parent_read(settings, monkeypatch):
    settings = settings.model_copy(update={"mcp_http_enabled": True})
    app = create_app(settings=settings)
    entered, release = threading.Event(), threading.Event()
    original = VaultService.read_note
    def blocked(owner, path):
        result = original(owner, path)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(VaultService, "read_note", blocked)
    async def invoke():
        async with app.router.lifespan_context(app):
            closed, sent = asyncio.Event(), []
            body = frame(arguments={"space": "research", "path": "same.md"})
            request = asyncio.create_task(http_request(app, body, disconnected=closed, sent=sent))
            assert await asyncio.to_thread(entered.wait, 10)
            closed.set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await request
            payload = b"".join(message.get("body", b"") for message in sent)
            assert b"Independent research" not in payload
            assert app.state.space_runtime.admission._active_thread is None
    asyncio.run(invoke())
    assert app.state.space_runtime._state == "stopped"


def test_unsupported_http_factory_has_no_standalone_runtime(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("standalone HTTP allocated a runtime")
    monkeypatch.setattr(SpaceRegistry, "from_settings", forbidden)
    with pytest.raises(SpaceError) as error:
        create_mcp_http_transport(settings=settings, rate_limiter=None)
    assert error.value.reason == "named_serving_unsupported"


@pytest.mark.parametrize("operation", ["related", "query"])
@pytest.mark.parametrize("explicit", [False, True])
def test_named_cli_persisted_semantics_preserves_storage(settings, backend, monkeypatch, operation, explicit):
    settings = policy(policy(settings, indexing="enabled"), sid="default", indexing="enabled")
    with TestClient(create_app(settings=settings)) as client:
        client.app.state.space_runtime.scheduler.wait(20)
    before = snapshot(settings.vault_path.parent)
    def forbidden(*args, **kwargs):
        pytest.fail("persisted CLI started a worker or synchronization")
    monkeypatch.setattr(SharedSemanticScheduler, "start", forbidden)
    monkeypatch.setattr(SharedFilesystemWatcher, "start", forbidden)
    selected = {"spaces": ["default", "research"]} if explicit else {}
    stream = StringIO()
    if operation == "related":
        code = cli.run_related(settings, "Independent", min_score=0, json_output=True, output=stream, **selected)
    else:
        code = cli.run_knowledge_query(settings, semantic_text="Independent", json_output=True,
                                       output=stream, **selected)
    assert code == 0, stream.getvalue()
    result = json.loads(stream.getvalue())
    assert result["items" if explicit else "results" if operation == "related" else "matches"]
    assert snapshot(settings.vault_path.parent) == before


@pytest.mark.parametrize("replacement", [False, True])
def test_public_native_containment_and_root_replacement(settings, replacement):
    app = create_app(settings=settings)
    with TestClient(app) as client:
        root = settings.vault_path.parent / "research"
        if replacement:
            root.rename(root.with_name("retained-root"))
            root.mkdir()
            (root / "same.md").write_text("PRIVATE replacement", encoding="utf-8")
            path, code = "same.md", "space_unavailable"
        else:
            link = root / "foreign"
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(settings.vault_path)],
                               capture_output=True, check=True)
            else:
                link.symlink_to(settings.vault_path, target_is_directory=True)
            path, code = "foreign/same.md", "invalid_path"
        response = client.get("/api/v1/notes/read", headers=AUTH, params={"space": "research", "path": path})
        assert response.json() == {"detail": code}
        assert "PRIVATE" not in response.text
        if not replacement:
            # Retain the target root; unlink only this synthetic test redirect.
            link.rmdir() if os.name == "nt" else link.unlink()


@pytest.mark.parametrize("fault", ["root", "overlap", "default-binding"])
def test_invalid_startup_before_allocating_workers(settings, monkeypatch, fault):
    from app.services import space_runtime
    data = json.loads(settings.knowledge_spaces_json)
    if fault == "root":
        data["spaces"][1]["root"] += "/missing"
    elif fault == "overlap":
        data["spaces"][1]["semantic_data_path"] = data["spaces"][0]["semantic_data_path"]
    else:
        data["spaces"][0]["root"] = data["spaces"][1]["root"]
    configured = Settings(**{**settings.model_dump(), "knowledge_spaces_json": json.dumps(data)})
    def forbidden(*args, **kwargs):
        pytest.fail("invalid startup reached owner construction")
    monkeypatch.setattr(space_runtime, "_runtime_owners", forbidden)
    with pytest.raises(SpaceError, match="invalid_configuration"):
        create_app(settings=configured)


def test_disabled_stale_state_inert_on_app_and_stdio(settings, backend, monkeypatch):
    directory = settings.vault_path.parent / "research-data"
    directory.mkdir()
    (directory / "semantic-index.sqlite3").write_bytes(b"Synthetic stale index")
    before = snapshot(settings.vault_path.parent)
    def forbidden(*args, **kwargs):
        pytest.fail("disabled indexing accessed old rows or started background work")
    monkeypatch.setattr(SharedSemanticScheduler, "start", forbidden)
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health").json() == {"ok": True}
        related = client.post("/api/v1/notes/related", headers=AUTH,
            json={"spaces": ["research"], "text": "Independent"})
        assert related.json() == {"detail": "capability_unavailable"}
    result = stdio(settings, [frame("related_notes", {"spaces": ["research"], "text": "Independent"})])[0]
    assert result["result"]["content"] == [{"type": "text", "text": "capability_unavailable: Operation failed."}]
    assert backend.instances == [] and snapshot(settings.vault_path.parent) == before


def test_public_failed_qualified_enqueue_keeps_commit(settings, backend, monkeypatch):
    settings = policy(settings, indexing="enabled")
    with TestClient(create_app(settings=settings)) as client:
        rt = client.app.state.space_runtime
        rt.scheduler.wait(20)
        def failed(lifecycle, path):
            assert path.space_id.value == "research"
            assert lifecycle is rt.registry._owners[path.space_id].scheduler
            raise RuntimeError("PRIVATE callback failure")
        monkeypatch.setattr(RuntimeSpaceIndexLifecycle, "enqueue", failed)
        response = client.post("/api/v1/notes", headers=AUTH,
            json={"space": "research", "title": "Committed", "content": "Exact content"})
        assert response.status_code == 200 and response.json()["result"]["success"]
        path = response.json()["result"]["path"]
        assert "Exact content" in (settings.vault_path.parent / "research" / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("operation", ["read", "write", "uncertain"])
def test_public_shutdown_drains_active_read_and_write_outcomes(settings, monkeypatch, operation):
    from app.services.knowledge_spaces import WriteScope

    app = create_app(settings=settings)
    rt = app.state.space_runtime
    entered, release = threading.Event(), threading.Event()
    method = {"read": "read_note", "write": "create_note", "uncertain": "create_capture_if_absent"}[operation]
    original = getattr(VaultService, method)
    def blocked(owner, *args, **kwargs):
        result = original(owner, *args, **kwargs)
        entered.set()
        assert release.wait(10)
        if operation == "uncertain":
            raise AtomicCaptureError("commit_unknown")
        return result
    monkeypatch.setattr(VaultService, method, blocked)
    async def call():
        if operation == "uncertain":
            return await asyncio.to_thread(rt.operations.scoped_capture,
                WriteScope(SpaceId("research")), capture_request())
        scope = http_scope(frame())
        scope.update(path="/api/v1/notes/read" if operation == "read" else "/api/v1/notes",
                     method="GET" if operation == "read" else "POST",
                     query_string=b"space=research&path=same.md" if operation == "read" else b"")
        scope["raw_path"] = scope["path"].encode()
        body = json.dumps({"space": "research", "title": "Target", "content": "Committed"}).encode()
        first, messages = True, []
        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()
        async def send(message):
            messages.append(message)
        await app(scope, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        payload = json.loads(b"".join(m.get("body", b"") for m in messages))
        return status, payload
    async def invoke():
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        work = asyncio.create_task(call())
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            stopping = asyncio.create_task(lifespan.__aexit__(None, None, None))
            assert await asyncio.to_thread(rt.admission._stopped.wait, 10)
            assert not stopping.done() and rt._state != "stopped"
        finally:
            release.set()
        result = await work
        await stopping
        return result
    result = asyncio.run(invoke())
    assert rt._state == "stopped" and rt.admission._active_thread is None
    if operation == "read":
        assert result == (503, {"detail": "operation_cancelled"})
    elif operation == "write":
        assert result[0] == 200 and result[1]["result"]["success"]
    else:
        assert result.result.category == "commit_unknown" and not result.result.committed
        assert (settings.vault_path.parent / "research" / SOURCE).exists()


def test_public_saturation_and_waiter_cancellation(settings):
    from app.services.knowledge_spaces import QualifiedPath
    from tests.test_space_runtime import waiters

    with TestClient(create_app(settings=settings)) as client:
        rt = client.app.state.space_runtime
        tokens = [CancellationToken() for _ in range(64)]
        with ThreadPoolExecutor(max_workers=64) as executor:
            with rt.admission.operation():
                works = [executor.submit(rt.operations.read_note, QualifiedPath(SpaceId("research"), "same.md"),
                                         cancel=token) for token in tokens]
                waiters(rt.admission, 64)
                response = client.get("/notes/read", headers=AUTH, params={"space": "research", "path": "same.md"})
                assert response.status_code == 503 and response.json() == {"detail": "space_unavailable"}
                tokens[0].cancel()
                with pytest.raises(SpaceError, match="operation_cancelled"):
                    works[0].result(10)
                waiters(rt.admission, 63)
                for token in tokens[1:]:
                    token.cancel()
                for work in works[1:]:
                    with pytest.raises(SpaceError, match="operation_cancelled"):
                        work.result(10)
                waiters(rt.admission, 0)
        assert client.get("/notes/read", headers=AUTH,
            params={"space": "research", "path": "same.md"}).status_code == 200


@pytest.mark.parametrize("operation", ["related", "query"])
def test_named_omitted_semantic_cli_missing_storage_is_operational(settings, monkeypatch, operation):
    settings = policy(settings, sid="default", indexing="enabled")
    def forbidden(*args, **kwargs):
        pytest.fail("missing persisted index initialized a model or database")
    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", forbidden)
    before = snapshot(settings.vault_path.parent)
    output, errors = StringIO(), StringIO()
    if operation == "related":
        result = cli.run_related(settings, "Independent", output=output, error_output=errors)
    else:
        result = cli.run_knowledge_query(settings, semantic_text="Independent", output=output, error_output=errors)
    assert result == 1
    assert snapshot(settings.vault_path.parent) == before


def test_production_stdio_strict_input_and_unknown_space(settings):
    bodies = [frame("knowledge_query", {"literal_text": "PRIVATE"}).replace(b"PRIVATE", b"\xff"),
        frame(arguments={"space": "unknown", "path": "same.md"}),
        frame(arguments={"space": "research", "path": "same.md"})]
    results = stdio(settings, bodies)
    for result, code in zip(results, ("invalid_request", "scope_not_allowed"), strict=False):
        assert result["result"]["content"] == [{"type": "text", "text": code + ": Operation failed."}]
    assert results[2]["result"]["structuredContent"]["space_id"] == "research"


def test_parent_shared_http_rest_limiter_previous_key_and_write_gate(settings):
    settings = settings.model_copy(update={"mcp_http_enabled": True, "previous_api_key": SecretStr("previous-key"),
        "rate_limit_enabled": True, "rate_limit_requests": 2})
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/notes/read?path=same.md", headers={"Authorization": "Bearer previous-key"})
        assert response.status_code == 200
        response = http(client, frame(arguments={"path": "same.md"}), authorization="Bearer previous-key")
        assert response.status_code == 200
        response = http(client, frame(arguments={"path": "same.md"}))
        assert response.status_code == 429 and "retry-after" in response.headers
        assert app.state.mcp_server._tool_manager.get_tool("create_note") is None
        assert app.state.mcp_server._tool_manager.get_tool("append_note") is None


def test_cross_transport_scoped_query_identity_parity(settings):
    settings = settings.model_copy(update={"mcp_http_enabled": True})
    data = {"spaces": ["research", "default"], "literal_text": "Independent"}
    with TestClient(create_app(settings=settings)) as client:
        rest = client.post("/api/v1/knowledge/query", headers=AUTH, json=data).json()
        mcp = http(client, frame("knowledge_query", data)).json()["result"]["structuredContent"]
    pipe = stdio(settings, [frame("knowledge_query", data)])[0]["result"]["structuredContent"]
    output = StringIO()
    assert cli.run_knowledge_query(settings, request_json=True, input_stream=BytesIO(json.dumps(data).encode()),
                                   output=output, json_output=True) == 0
    assert json.loads(output.getvalue()) == rest
    for result in (mcp, pipe):
        for row in result["items"]:
            assert row.pop("resource_uri").startswith("vaultbridge://space/")
        assert result == rest


@pytest.mark.parametrize("command", [["status"], ["index"], ["index", "check"], ["index", "rebuild"], ["reindex"]])
def test_public_named_maintenance_fence_precedes_settings_root_resolution(monkeypatch, capsys, command):
    monkeypatch.setenv("KNOWLEDGE_SPACES_JSON", "invalid-private-configuration")
    def forbidden(*args, **kwargs):
        pytest.fail("maintenance loaded settings or resolved roots")
    monkeypatch.setattr(Settings, "from_env", forbidden)
    assert cli.main(command) == 2
    assert capsys.readouterr().err == "invalid_configuration: Operation failed.\n"


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_production_stdio_interrupt_success_requires_verified_stopped(settings, monkeypatch, cleanup_failure):
    from app import mcp_server

    server = create_mcp_server(settings=settings)
    runtime = server._vaultbridge_runtime
    original = VaultService.close_scoped_resources
    def interrupted(owner):
        if cleanup_failure:
            raise KeyboardInterrupt()
        return original(owner)
    def run():
        runtime.start()
        runtime.shutdown()
        raise KeyboardInterrupt()
    monkeypatch.setattr(VaultService, "close_scoped_resources", interrupted)
    monkeypatch.setattr(server, "run", run)
    monkeypatch.setattr(mcp_server, "create_mcp_server", lambda **kwargs: server)
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    assert mcp_server.main() == (1 if cleanup_failure else 0)
    assert not runtime.admission.running
    assert runtime._state == ("stopping" if cleanup_failure else "stopped")
    assert server._vaultbridge_runtime is runtime
    monkeypatch.setattr(VaultService, "close_scoped_resources", original)
    runtime.shutdown()
    assert runtime._state == "stopped"


@pytest.mark.parametrize("command", [
    ["search", "Independent", "--scope", "PRIVATE-SCOPE-VALUE"],
    ["related", "PRIVATE query", "--limit", "PRIVATE limit"],
    ["hygiene", "scan", "--group", "PRIVATE group"],
    ["search", "PRIVATE query", "--limit", "51"],
])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("from_environment", [False, True])
def test_named_cli_parser_redacts_before_settings_without_expanding_legacy_limits(
    settings, monkeypatch, capsys, command, json_output, from_environment,
):
    monkeypatch.setenv("KNOWLEDGE_SPACES_JSON", "private-invalid-configuration")
    def forbidden(*args, **kwargs):
        pytest.fail("parser failure reached settings/root resolution or owners")
    monkeypatch.setattr(Settings, "from_env", forbidden)
    arguments = command + (["--json"] if json_output else [])
    with pytest.raises(SystemExit) as failure:
        cli.main(arguments, **({} if from_environment else {"settings": settings}))
    assert failure.value.code == 2
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err
    if json_output:
        assert json.loads(output.out) == {"category": "invalid_request"}
        assert output.err == ""
    else:
        assert output.out == ""
        assert output.err == "invalid_request: Operation failed.\n"


@pytest.mark.parametrize("mode", ["omitted", "explicit-default", "denied-semantic"])
def test_public_review_keeps_v1_semantic_advice_and_scoped_local_profile(settings, backend, monkeypatch, mode):
    from app.services import semantic_resources

    settings = policy(settings, sid="default", indexing="enabled")
    selected = {"space": "default"} if mode == "explicit-default" else {}
    output = StringIO()
    assert cli.run_capture(settings, input_stream=BytesIO(json.dumps(capture_request()).encode()),
                           output=output, **selected) == 0
    if mode == "denied-semantic":
        data = json.loads(settings.knowledge_spaces_json)
        default = next(row for row in data["spaces"] if row["space_id"] == "default")
        settings = policy(settings, sid="default", capabilities=[
            value for value in default["capabilities"] if value != "semantic_retrieval"])
    expected = None
    if mode == "omitted":
        # Compare the real legacy command over exactly the same persisted default data.
        monkeypatch.setattr("app.services.semantic_search.FastEmbedder", semantic_resources.FastEmbedder)
        output = StringIO()
        assert cli.run_promote_review(Settings(**settings.model_dump()), source_path=SOURCE,
            capture_id=CAPTURE_ID, candidate_limit=5, output=output) == 0
        expected = json.loads(output.getvalue())
        assert expected["evidence_state"] == "available"
        assert any(row["match_type"] == "semantic" for row in expected["evidence"])
    def forbidden(*args, **kwargs):
        pytest.fail("promotion review started indexing or a watcher")
    monkeypatch.setattr(SharedSemanticScheduler, "start", forbidden)
    monkeypatch.setattr(SharedFilesystemWatcher, "start", forbidden)
    before, models = snapshot(settings.vault_path.parent), len(backend.instances)
    output = StringIO()
    assert cli.run_promote_review(settings, source_path=SOURCE, capture_id=CAPTURE_ID, candidate_limit=5,
        **({"destination": "Notes/Target.md"} if selected else {}), output=output, **selected) == 0
    result = json.loads(output.getvalue())
    if expected is not None:
        assert result == expected
    else:
        assert len(backend.instances) == models
        evidence = result["result"] if selected else result
        assert not any(row["match_type"] == "semantic" for row in evidence["evidence"])
    assert snapshot(settings.vault_path.parent) == before


def test_omitted_review_missing_index_does_not_download_or_initialize(settings, monkeypatch):
    output = StringIO()
    assert cli.run_capture(settings, input_stream=BytesIO(json.dumps(capture_request()).encode()), output=output) == 0
    settings = policy(settings, sid="default", indexing="enabled")
    def forbidden(*args, **kwargs):
        pytest.fail("review of missing index constructed model or synchronization")
    monkeypatch.setattr("app.services.semantic_resources.FastEmbedder", forbidden)
    monkeypatch.setattr(SharedSemanticScheduler, "start", forbidden)
    before = snapshot(settings.vault_path.parent)
    output = StringIO()
    assert cli.run_promote_review(settings, source_path=SOURCE, capture_id=CAPTURE_ID,
                                  candidate_limit=5, output=output) == 0
    assert json.loads(output.getvalue())["evidence_state"] == "unavailable"
    assert snapshot(settings.vault_path.parent) == before
