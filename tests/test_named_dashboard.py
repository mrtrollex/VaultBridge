"""ADR 0011 foundation security, using disposable production compositions."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.ui_session import UI_SESSION_COOKIE_NAME, create_ui_session_token
from app.main import create_app
from app.repositories.semantic import SemanticRepository
from app.repositories.semantic_binding import IndexBindingUnavailableError
from app.services.knowledge_spaces import Capability, Dialect, ReadScope, SpaceError, SpaceId
from app.services.scoped_budget import CancellationToken
from app.services.semantic_scheduler import SharedSemanticScheduler
from app.services.space_operations import SpaceOperations
from app.services.vault import VaultService
from app.ui.named_router import NAMED_UI_PATHS
from tests.test_public_space_composition import policy
from tests.test_public_space_composition import settings as settings_fixture
from tests.test_space_runtime import backend as backend_fixture

MARKER = {"X-VaultBridge-UI-Request": "1"}
KEY = "slice-a-test-key"


@pytest.fixture
def settings(tmp_path):
    return settings_fixture.__wrapped__(tmp_path)


@pytest.fixture
def backend(monkeypatch):
    return backend_fixture.__wrapped__(monkeypatch)


def session_client(settings):
    return TestClient(create_app(settings=settings), base_url="https://testserver")


def test_named_nonrenewing_and_rotation(settings):
    settings = settings.model_copy(update={"previous_api_key": SecretStr("previous-synthetic-key")})
    with session_client(settings) as client:
        response = client.post("/ui/session", headers=MARKER, json={"api_key": "previous-synthetic-key"})
        assert response.status_code == 200
        token = client.cookies.get(UI_SESSION_COOKIE_NAME)
        for _ in range(2):
            response = client.get("/ui/session", headers=MARKER)
            assert response.json() == {"authenticated": True}
            assert "set-cookie" not in response.headers
            assert client.cookies.get(UI_SESSION_COOKIE_NAME) == token
        client.app.state.settings = settings.model_copy(update={"api_key": SecretStr("rotated-synthetic-key")})
        response = client.get("/ui/session", headers=MARKER)
        assert response.status_code == 401 and "set-cookie" not in response.headers
    # A new production composition has no authentication database or memory of
    # the issuer. The unchanged signing key validates the exact original token.
    with session_client(settings) as restarted:
        restarted.cookies.set(UI_SESSION_COOKIE_NAME, token)
        response = restarted.get("/ui/session", headers=MARKER)
        assert response.json() == {"authenticated": True}
        assert "set-cookie" not in response.headers
        assert restarted.cookies.get(UI_SESSION_COOKIE_NAME) == token


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {**MARKER, "Origin": "null"},
        {**MARKER, "Origin": "https://testserver:443/"},
        {**MARKER, "Origin": "https://testserver:"},
        {**MARKER, "Origin": "https://testserver?"},
        {**MARKER, "Origin": "https://testserver:444"},
        {**MARKER, "Origin": "https://sibling.testserver"},
        {**MARKER, "Sec-Fetch-Site": "same-site"},
        {**MARKER, "Sec-Fetch-Site": "none"},
        [("X-VaultBridge-UI-Request", "1"), ("X-VaultBridge-UI-Request", "1")],
        [*MARKER.items(), ("Origin", "https://testserver"), ("Origin", "https://testserver")],
    ],
)
def test_named_session_guard_before_issuance(settings, method, headers):
    with session_client(settings) as client:
        response = client.request(method, "/ui/session", headers=headers, json={"api_key": KEY})
        assert response.status_code == 403
        assert response.json() == {"detail": "invalid_ui_request"}
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize("token", ["forged", "", "expired"])
def test_named_invalid_get_never_mutates(settings, token):
    if token == "expired":
        token = create_ui_session_token(settings, now=int(time.time()) - 8 * 86400)
    with session_client(settings) as client:
        client.cookies.set(UI_SESSION_COOKIE_NAME, token)
        response = client.get("/ui/session", headers=MARKER)
        assert response.status_code == 401 and "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "content_type,body,status",
    [
        ("text/plain", b"{}", 415),
        ("application/json; boundary=unexpected", b"{}", 415),
        ("application/x-www-form-urlencoded", b"api_key=secret", 415),
        ("application/json", b'{"api_key":"PRIVATE","extra":true}', 422),
        ("application/json", b"{broken PRIVATE", 422),
    ],
)
def test_named_unlock_safe_errors(settings, content_type, body, status):
    with session_client(settings) as client:
        response = client.post("/ui/session", headers={**MARKER, "Content-Type": content_type}, content=body)
        assert response.status_code == status and b"PRIVATE" not in response.content
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize("content_type", ["application/json; charset=utf-8", 'application/json; charset="UTF-8"'])
def test_named_json_optional_charset(settings, content_type):
    with session_client(settings) as client:
        response = client.post(
            "/ui/session", content='{"api_key":"' + KEY + '"}', headers={**MARKER, "Content-Type": content_type}
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    "path,method,expected",
    [
        ("/api/v1/notes/list", "GET", 200),
        ("/notes/list", "GET", 401),
        ("/api/v1/notes", "POST", 401),
        ("/api/v1/notes/append", "POST", 401),
        ("/api/v1/notes/duplicates", "POST", 401),
        ("/api/v1/knowledge/hygiene/scan", "POST", 401),
    ],
)
def test_named_cookie_exact_boundary(settings, path, method, expected):
    with TestClient(create_app(settings=settings)) as client:
        client.cookies.set(UI_SESSION_COOKIE_NAME, create_ui_session_token(settings))
        response = client.request(method, path, headers=MARKER, json={} if method == "POST" else None)
        assert response.status_code == expected
        if expected == 200:
            assert client.get(path, headers={**MARKER, "Authorization": "Bearer invalid"}).status_code == 401


@pytest.mark.parametrize("prefix", ["", "/proxy"])
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "DELETE", "PUT", "OPTIONS"])
def test_named_route_matrix(settings, prefix, method):
    with TestClient(create_app(settings=settings), root_path=prefix, base_url="https://testserver") as client:
        for path in NAMED_UI_PATHS:
            response = client.request(method, prefix + path, follow_redirects=False)
            if path == "/ui/session":
                expected = 403 if method in {"POST", "GET", "DELETE"} else 405
            else:
                expected = (307 if path == "/ui" else 200) if method in {"GET", "HEAD"} else 405
            assert response.status_code == expected, (method, path, response.text)
            assert response.headers["cache-control"] == "no-store"
            assert "set-cookie" not in response.headers
        if method == "GET":
            assert client.get(prefix + "/ui", follow_redirects=False).headers["location"] == prefix + "/ui/"
            shell = client.get(prefix + "/ui/").text
            assert "overview.js" not in shell and "search.js" not in shell and "hygiene.js" not in shell


@pytest.mark.parametrize("denied", [(), ("default",), ("default", "research")])
def test_discovery_policy_only(settings, monkeypatch, denied, caplog):
    for sid in denied:
        settings = policy(settings, sid, read="deny", write="deny")
    with session_client(settings) as client:

        def forbidden(*args, **kwargs):
            pytest.fail("policy projection performed operational work")

        monkeypatch.setattr(SpaceOperations, "_owners", forbidden)
        monkeypatch.setattr(VaultService, "root_binding_identity", forbidden)
        result = client.get("/api/v1/knowledge/spaces", headers={"Authorization": "Bearer " + KEY})
        assert result.status_code == 200
        rows = result.json()["spaces"]
        assert [row["space_id"] for row in rows] == [sid for sid in ("default", "research") if sid not in denied]
        for row in rows:
            assert row == dict(
                space_id=row["space_id"],
                read_capabilities=sorted(
                    [
                        "read_note",
                        "list_notes",
                        "literal_search",
                        "semantic_retrieval",
                        "knowledge_query",
                        "relationships",
                    ]
                ),
                relationship_dialects=sorted(d.value for d in Dialect),
                indexing="disabled",
            )
        assert str(settings.vault_path) not in result.text
        assert str(settings.vault_path) not in caplog.text


def test_discovery_status_strict_cache_auth_and_zero_probes(settings, monkeypatch):
    with session_client(settings) as client:

        def forbidden(*args, **kwargs):
            pytest.fail("disabled status touched runtime owner")

        monkeypatch.setattr(SpaceOperations, "_owners", forbidden)
        for route in ("/api/v1/knowledge/spaces", "/api/v1/knowledge/spaces/status"):
            client.cookies.clear()
            assert client.get(route, headers=MARKER).status_code == 401
            client.cookies.set(UI_SESSION_COOKIE_NAME, create_ui_session_token(settings))
            assert client.get(route).status_code == 401
            response = client.get(route, headers=MARKER)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert "set-cookie" not in response.headers
            assert client.get(route, headers={**MARKER, "Origin": "https://testserver:444"}).status_code == 403
            assert client.get(route + "?unknown=PRIVATE", headers=MARKER).json() == {"detail": "invalid_request"}
            assert client.request("GET", route, content=b"PRIVATE", headers=MARKER).status_code == 422
            assert client.post(route, headers=MARKER).status_code == 405
        result = client.get("/api/v1/knowledge/spaces/status?spaces=research&spaces=default", headers=MARKER)
        assert result.json() == {
            "spaces": [
                dict(space_id=sid, index_state="disabled", search_available=False, refresh_required=False)
                for sid in ("default", "research")
            ]
        }
        for query in ("spaces=", "space=default", "spaces=*", "&".join(["spaces=default"] * 9)):
            response = client.get("/api/v1/knowledge/spaces/status?" + query, headers=MARKER)
            assert response.status_code == 422 and response.json() == {"detail": "invalid_scope"}


def test_status_whole_selection_denied_before_owner(settings, monkeypatch):
    settings = policy(settings, "research", read="deny", write="deny")
    with session_client(settings) as client:

        def forbidden(*args, **kwargs):
            pytest.fail("denied selection reached admission/owner")

        monkeypatch.setattr(SpaceOperations, "_admit", forbidden)
        for values in ("research", "unknown", "default&spaces=research"):
            response = client.get(
                "/api/v1/knowledge/spaces/status?spaces=" + values, headers={"Authorization": "Bearer " + KEY}
            )
            assert response.status_code == 403 and response.json() == {"detail": "scope_not_allowed"}


def test_session_limiter_once_guard_before_body(settings, monkeypatch):
    settings = settings.model_copy(update={"rate_limit_enabled": True, "rate_limit_requests": 3})
    with session_client(settings) as client:
        for method in ("POST", "GET", "DELETE"):
            response = client.request(method, "/ui/session", content=b"PRIVATE")
            assert response.status_code == 403
        response = client.get("/api/v1/knowledge/spaces", headers={"Authorization": "Bearer " + KEY})
        assert response.status_code == 429 and response.headers["retry-after"]
        assert response.headers["cache-control"] == "no-store" and "set-cookie" not in response.headers


def test_status_cancelled_and_stopped(settings):
    app = create_app(settings=settings)
    token = CancellationToken()
    token.cancel()
    with TestClient(app):
        with pytest.raises(SpaceError, match="operation_cancelled"):
            app.state.space_operations.selected_space_status(cancel=token)
    with pytest.raises(SpaceError, match="operation_cancelled"):
        app.state.space_operations.selected_space_status()


def test_no_semantic_capability_zero_probes(settings, monkeypatch):
    settings = policy(settings, "research", indexing="enabled", capabilities=[Capability.READ_NOTE.value])
    app = create_app(settings=settings)
    # No runtime start: policy row needs no root/storage/scheduler observation.
    app.state.space_runtime.admission.open()

    def forbidden(*args, **kwargs):
        pytest.fail("disallowed semantic metadata touched owner")

    monkeypatch.setattr(SpaceOperations, "_owners", forbidden)
    assert app.state.space_operations.selected_space_status(ReadScope((SpaceId("research"),))) == (
        dict(space_id="research", index_state="not_permitted", search_available=False, refresh_required=False),
    )
    app.state.space_runtime.shutdown()


def test_native_status_readonly_mapping_and_lock_order(settings, backend, monkeypatch):
    settings = policy(settings, "research", indexing="enabled")
    app = create_app(settings=settings)
    runtime = app.state.space_runtime
    with TestClient(app) as client:
        runtime.scheduler.wait(20)
        binding = runtime.registry._binding(SpaceId("research"))
        service = binding._owners.semantic
        assert service.repository.db_path.exists()
        original = SharedSemanticScheduler.observe_status

        def observe(scheduler, selected, cancel=None):
            assert runtime.admission._active_thread is None, "scheduler condition acquired inside admission"
            return original(scheduler, selected, cancel)

        monkeypatch.setattr(SharedSemanticScheduler, "observe_status", observe)

        def forbidden(*args, **kwargs):
            pytest.fail("status invoked rich counts, initialization, model or index work")

        monkeypatch.setattr(SemanticRepository, "read_status", forbidden)
        monkeypatch.setattr(service, "_initialize_state", forbidden)
        monkeypatch.setattr(service, "_resolve_index_signature", forbidden)
        monkeypatch.setattr(service, "_embed", forbidden)
        entry = runtime.scheduler._owners[SpaceId("research")]

        def request():
            before = {
                p: (p.read_bytes(), p.stat().st_mtime_ns) for p in settings.vault_path.parent.rglob("*") if p.is_file()
            }
            response = client.get(
                "/api/v1/knowledge/spaces/status?spaces=research", headers={"Authorization": "Bearer " + KEY}
            )
            assert response.status_code == 200
            after = {
                p: (p.read_bytes(), p.stat().st_mtime_ns) for p in settings.vault_path.parent.rglob("*") if p.is_file()
            }
            assert after == before
            return response.json()["spaces"][0]

        assert request() == dict(
            space_id="research", index_state="ready", search_available=True, refresh_required=False
        )
        with runtime.scheduler._condition:
            entry.pending.add("not-inspected.md")
        assert request() == dict(
            space_id="research", index_state="indexing", search_available=True, refresh_required=True
        )
        with runtime.scheduler._condition:
            entry.paused = True
            entry.error_type = "SyntheticFailure"
        assert request() == dict(
            space_id="research", index_state="unavailable", search_available=True, refresh_required=True
        )
        with runtime.scheduler._condition:
            entry.pending.clear()
            entry.paused = False
            entry.error_type = None
            entry.coverage_revision += 1
        assert request() == dict(
            space_id="research", index_state="unavailable", search_available=True, refresh_required=True
        )
        # A compatible previous index remains searchable, without claiming ready.
        service.repository.set_metadata("index_state", "error")
        assert request() == dict(
            space_id="research", index_state="unavailable", search_available=True, refresh_required=True
        )
        # Binding failure is safe unavailable, never an alternate-space probe.
        service.repository.db_path.unlink()
        assert request() == dict(
            space_id="research", index_state="unavailable", search_available=False, refresh_required=True
        )
        assert runtime.admission._active_thread != threading.get_ident()


def test_wal_observation_never_opens_or_creates_coordination_files(settings, backend, monkeypatch):
    settings = policy(settings, "default", indexing="enabled")
    app = create_app(settings=settings)
    with TestClient(app):
        runtime = app.state.space_runtime
        runtime.scheduler.wait(20)
        repository = runtime.registry._owners[SpaceId("default")].semantic.repository

        def forbidden(*args, **kwargs):
            pytest.fail("WAL observation opened SQLite after a racy existence precheck")

        monkeypatch.setattr(SemanticRepository, "_bound_read_connection", forbidden)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repository.db_path.parent.iterdir() if p.is_file()}
        observed = repository.observe_dashboard_status()
        assert observed.storage_error and not observed.storage_initialized
        assert {
            p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repository.db_path.parent.iterdir() if p.is_file()
        } == before


def test_additive_openapi_and_legacy_no_discovery(settings, tmp_path):
    named = create_app(settings=settings).openapi()
    legacy_settings = settings.model_copy(update={"knowledge_spaces_json": None})
    # Pydantic's cached named definitions are intentionally not hand-edited.
    from app.core.config import Settings

    legacy_settings = Settings(**{**legacy_settings.model_dump(), "knowledge_spaces_json": None})
    legacy = create_app(settings=legacy_settings)
    old = legacy.openapi()
    for path, methods in old["paths"].items():
        for method, operation in methods.items():
            if "operationId" in operation:
                assert named["paths"][path][method]["operationId"] == operation["operationId"]
    assert named["paths"]["/api/v1/knowledge/spaces"]["get"]["operationId"] == "listKnowledgeSpacesV1"
    assert named["paths"]["/api/v1/knowledge/spaces/status"]["get"]["operationId"] == "getKnowledgeSpaceStatusV1"
    assert "/api/v1/knowledge/spaces" not in old["paths"]
    with TestClient(legacy) as client:
        assert client.get("/api/v1/knowledge/spaces").status_code == 404


@pytest.mark.parametrize(
    "path,method,payload",
    [
        ("/api/v1/notes/list?spaces=default", "GET", None),
        ("/api/v1/notes/read?space=default&path=same.md", "GET", None),
        ("/api/v1/notes/links?space=default&path=same.md", "GET", None),
        ("/api/v1/notes/backlinks?space=default&path=same.md", "GET", None),
        ("/api/v1/notes/search", "POST", {"spaces": ["default"], "query": "Independent"}),
        ("/api/v1/notes/related", "POST", {"spaces": ["default"], "text": "Independent"}),
        ("/api/v1/knowledge/query", "POST", {"spaces": ["default"], "literal_text": "Independent"}),
    ],
)
def test_every_allowed_cookie_read_never_mutates(settings, path, method, payload):
    with session_client(settings) as client:
        client.cookies.set(UI_SESSION_COOKIE_NAME, create_ui_session_token(settings))
        response = client.request(method, path, headers=MARKER, **({"json": payload} if payload else {}))
        assert response.status_code in {200, 409}
        assert "set-cookie" not in response.headers and response.headers["cache-control"] == "no-store"
        denied = client.request(
            method,
            path,
            headers={**MARKER, "Authorization": "Bearer invalid"},
            **({"json": payload} if payload else {}),
        )
        assert denied.status_code == 401 and "set-cookie" not in denied.headers


def test_cookie_cannot_authenticate_mcp(settings):
    settings = settings.model_copy(update={"mcp_http_enabled": True})
    with session_client(settings) as client:
        client.cookies.set(UI_SESSION_COOKIE_NAME, create_ui_session_token(settings))
        response = client.post(
            "/mcp",
            headers={**MARKER, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 401
        for path in ("/api/v1/knowledge/spaces", "/api/v1/knowledge/spaces/status"):
            response = client.post(path, headers=MARKER, json={})
            assert response.status_code == 405 and response.headers["cache-control"] == "no-store"


def test_metadata_previous_bearer_no_cookie_fallback_and_safe_projection(settings, monkeypatch, caplog):
    settings = settings.model_copy(update={"previous_api_key": SecretStr("previous-synthetic")})
    with session_client(settings) as client:
        for path in ("/api/v1/knowledge/spaces", "/api/v1/knowledge/spaces/status"):
            assert client.get(path, headers={"Authorization": "Bearer previous-synthetic"}).status_code == 200

        def failure(*args, **kwargs):
            raise RuntimeError("PRIVATE LABEL AND ROOT")

        monkeypatch.setattr(SpaceOperations, "readable_spaces", failure)
        result = client.get("/api/v1/knowledge/spaces", headers={"Authorization": "Bearer " + KEY})
        assert result.status_code == 500 and result.json() == {"detail": "internal_error"}
        assert result.headers["cache-control"] == "no-store"
        assert "PRIVATE LABEL AND ROOT" not in caplog.text


def test_scheduler_observation_wait_is_cancellable_before_admission(settings, backend, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    settings = policy(settings, "research", indexing="enabled")
    app = create_app(settings=settings)
    with TestClient(app):
        runtime = app.state.space_runtime
        runtime.scheduler.wait(20)
        token, entered = CancellationToken(), threading.Event()
        original = SharedSemanticScheduler.observe_status

        def observe(scheduler, binding, cancel=None):
            entered.set()
            return original(scheduler, binding, cancel)

        monkeypatch.setattr(SharedSemanticScheduler, "observe_status", observe)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with runtime.scheduler._condition:
                work = pool.submit(
                    runtime.operations.selected_space_status, ReadScope((SpaceId("research"),)), cancel=token
                )
                assert entered.wait(5)
                token.cancel()
                with pytest.raises(SpaceError, match="operation_cancelled"):
                    work.result(5)
                assert runtime.admission._active_thread is None


def test_status_binding_loss_between_root_guard_and_observation(settings, backend, monkeypatch):
    settings = policy(settings, "research", indexing="enabled")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        runtime = app.state.space_runtime
        runtime.scheduler.wait(20)
        service = runtime.registry._owners[SpaceId("research")].semantic

        def lost(_root):
            raise IndexBindingUnavailableError

        monkeypatch.setattr(service, "_validate_source_root", lost)
        response = client.get(
            "/api/v1/knowledge/spaces/status?spaces=research", headers={"Authorization": "Bearer " + KEY}
        )
        assert response.status_code == 200
        assert response.json() == {
            "spaces": [
                dict(space_id="research", index_state="unavailable", search_available=False, refresh_required=True)
            ]
        }
