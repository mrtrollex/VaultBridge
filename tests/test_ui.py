from __future__ import annotations

import re
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from app.ui.router import UI_CONTENT_SECURITY_POLICY
from tests.test_api import client_for


class ShellHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.script_depth = 0
        self.inline_script_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))
        if tag == "script":
            self.script_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.script_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.script_depth and data.strip():
            self.inline_script_text.append(data)


def test_ui_routes_are_explicit_canonical_and_support_head(tmp_path):
    client = client_for(tmp_path)

    redirect = client.get("/ui", follow_redirects=False)
    head_redirect = client.head("/ui", follow_redirects=False)
    document = client.get("/ui/")
    head_document = client.head("/ui/")

    assert redirect.status_code == head_redirect.status_code == 307
    assert redirect.headers["location"] == head_redirect.headers["location"] == "/ui/"
    assert head_redirect.content == b""
    assert document.status_code == head_document.status_code == 200
    assert document.headers["content-type"].startswith("text/html")
    assert head_document.headers["content-type"].startswith("text/html")
    assert head_document.content == b""
    assert head_document.headers["content-length"] == document.headers["content-length"]


def test_ui_redirect_preserves_reverse_proxy_root_path(tmp_path):
    application = client_for(tmp_path).app
    client = TestClient(application, root_path="/vaultbridge")

    response = client.get("/ui", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/vaultbridge/ui/"


def test_known_ui_assets_have_correct_media_types_and_head_support(tmp_path):
    client = client_for(tmp_path)

    for path, media_type in (
        ("/ui/assets/app.css", "text/css"),
        ("/ui/assets/app.js", "text/javascript"),
        ("/ui/assets/overview.js", "text/javascript"),
    ):
        response = client.get(path)
        head = client.head(path)
        assert response.status_code == head.status_code == 200
        assert response.headers["content-type"].startswith(media_type)
        assert head.headers["content-type"].startswith(media_type)
        assert response.content
        assert head.content == b""
        assert head.headers["content-length"] == response.headers["content-length"]


def test_unknown_ui_paths_assets_and_methods_do_not_fall_back_to_html(tmp_path):
    client = client_for(tmp_path)

    for path in (
        "/ui/search",
        "/ui/unknown",
        "/ui/assets/missing.js",
        "/ui/assets/main.py",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")

    assert client.post("/ui/").status_code == 405


def test_ui_does_not_shadow_api_health_or_privacy_routes(tmp_path):
    client = client_for(tmp_path)

    assert client.get("/api/v1/notes/list").status_code == 401
    assert client.get("/notes/list").status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/privacy").status_code == 200


def test_ui_routes_are_excluded_from_openapi_contract(tmp_path):
    schema = client_for(tmp_path).app.openapi()

    assert not any(path.startswith("/ui") for path in schema["paths"])
    assert set(schema["paths"]) == {
        "/health",
        "/health/live",
        "/health/ready",
        "/notes",
        "/notes/append",
        "/notes/read",
        "/notes/search",
        "/notes/related",
        "/notes/duplicates",
        "/notes/list",
        "/api/v1/notes",
        "/api/v1/notes/append",
        "/api/v1/notes/read",
        "/api/v1/notes/search",
        "/api/v1/notes/related",
        "/api/v1/notes/duplicates",
        "/api/v1/notes/list",
    }


def test_ui_resources_apply_strict_security_headers(tmp_path):
    client = client_for(tmp_path)

    for path in ("/ui/", "/ui/assets/app.css", "/ui/assets/app.js", "/ui/assets/overview.js"):
        response = client.get(path)
        assert response.headers["content-security-policy"] == UI_CONTENT_SECURITY_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"

    policy = client.get("/ui/").headers["content-security-policy"]
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ):
        assert directive in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy
    assert "*" not in policy


def test_server_secrets_are_never_rendered_in_ui_resources(tmp_path):
    current_secret = "private-current-dashboard-secret"
    previous_secret = "private-previous-dashboard-secret"
    client = client_for(
        tmp_path,
        api_key=current_secret,
        previous_api_key=previous_secret,
    )

    resources = "\n".join(
        client.get(path).text
        for path in ("/ui/", "/ui/assets/app.css", "/ui/assets/app.js", "/ui/assets/overview.js")
    )

    assert current_secret not in resources
    assert previous_secret not in resources
    assert "API_KEY_PREVIOUS" not in resources


def test_html_shell_is_semantic_accessible_and_uses_only_local_assets(tmp_path):
    html = client_for(tmp_path).get("/ui/").text
    parser = ShellHTMLParser()
    parser.feed(html)
    elements = parser.elements

    assert html.startswith("<!doctype html>")
    assert len([attrs for tag, attrs in elements if tag == "h1"]) == 1
    assert {tag for tag, _attrs in elements} >= {"header", "nav", "main", "section", "form", "label", "button"}
    assert [attrs.get("lang") for tag, attrs in elements if tag == "html"] == ["en"]
    assert [attrs.get("type") for tag, attrs in elements if tag == "input" and attrs.get("id") == "api-key"] == [
        "password"
    ]
    assert [attrs.get("aria-live") for tag, attrs in elements if attrs.get("id") == "global-status"] == ["polite"]
    assert {
        attrs.get("id")
        for tag, attrs in elements
        if tag == "section"
    } >= {"overview-panel", "search-panel", "api-panel", "about-panel"}

    scripts = [attrs for tag, attrs in elements if tag == "script"]
    stylesheets = [attrs for tag, attrs in elements if tag == "link" and attrs.get("rel") == "stylesheet"]
    assert scripts == [{"type": "module", "src": "assets/app.js"}]
    assert stylesheets == [{"rel": "stylesheet", "href": "assets/app.css"}]
    assert parser.inline_script_text == []
    assert not re.search(r"\son[a-z]+\s*=", html, flags=re.IGNORECASE)
    assert "https://" not in html
    assert "http://" not in html


def test_overview_replaces_placeholder_with_accessible_health_cards(tmp_path):
    html = client_for(tmp_path).get("/ui/").text
    parser = ShellHTMLParser()
    parser.feed(html)
    elements = parser.elements

    assert "Operational overview will appear here." not in html
    assert "Health details are intentionally reserved for VB-072." not in html
    assert "VaultBridge Overview" in html
    for heading in ("Overall status", "Vault", "Semantic index", "Background indexing"):
        assert f">{heading}<" in html
    for label in (
        "Application health",
        "Available",
        "Notes",
        "State",
        "Ready",
        "Search",
        "Indexed notes",
        "Chunks",
        "Last successful sync",
        "Background indexer",
        "Full sync required",
    ):
        assert f">{label}<" in html

    refresh_buttons = [
        attrs
        for tag, attrs in elements
        if tag == "button" and attrs.get("id") == "refresh-overview-button"
    ]
    assert refresh_buttons == [
        {
            "id": "refresh-overview-button",
            "class": "secondary-button",
            "type": "button",
            "disabled": None,
        }
    ]
    assert [
        attrs.get("aria-live")
        for _tag, attrs in elements
        if attrs.get("id") == "overview-update-status"
    ] == ["polite"]
    assert [
        attrs.get("aria-busy")
        for _tag, attrs in elements
        if attrs.get("id") == "overview-content"
    ] == ["true"]
    assert any(
        tag == "time" and attrs.get("id") == "last-successful-sync"
        for tag, attrs in elements
    )


def test_overview_javascript_uses_public_health_contract_without_auth_or_polling(tmp_path):
    overview_source = client_for(tmp_path).get("/ui/assets/overview.js").text

    assert 'fetch(applicationUrl("health")' in overview_source
    assert 'method: "GET"' in overview_source
    assert "Authorization" not in overview_source
    assert "authenticatedFetch" not in overview_source
    assert "sessionStorage" not in overview_source
    assert "setInterval(" not in overview_source
    assert "setTimeout(" not in overview_source
    assert 'refreshOverviewButton.addEventListener("click"' in overview_source
    assert "void loadOverview(applicationUrl);" in overview_source

    for field in (
        "ok",
        "vault_exists",
        "semantic_index_ready",
        "semantic_index_state",
        "semantic_search_available",
        "semantic_indexer_running",
        "full_sync_required",
        "indexed_notes",
        "semantic_chunks",
        "vault_notes",
        "last_successful_sync",
    ):
        assert field in overview_source


def test_overview_javascript_has_explicit_display_state_and_safe_formatting_contract(tmp_path):
    overview_source = client_for(tmp_path).get("/ui/assets/overview.js").text

    assert 'if (!health.vault_exists) {\n    return "unavailable";' in overview_source
    assert 'health.semantic_index_state === "indexing"' in overview_source
    assert "health.ok" in overview_source
    assert "health.semantic_search_available" in overview_source
    assert 'health.semantic_index_state === "ready"' in overview_source
    assert 'return "degraded";' in overview_source
    assert 'ready: "Ready"' in overview_source
    assert 'indexing: "Indexing"' in overview_source
    assert 'degraded: "Degraded"' in overview_source
    assert 'unavailable: "Unavailable"' in overview_source

    assert "new Intl.NumberFormat()" in overview_source
    assert "countFormatter.format(health.vault_notes)" in overview_source
    assert "countFormatter.format(health.indexed_notes)" in overview_source
    assert "countFormatter.format(health.semantic_chunks)" in overview_source
    assert "percentage" not in overview_source.lower()
    assert "progress" not in overview_source.lower()

    assert 'setText(lastSuccessfulSync, "Never")' in overview_source
    assert 'setText(lastSuccessfulSync, "Not available")' in overview_source
    assert 'lastSuccessfulSync.setAttribute("datetime", value)' in overview_source
    assert (
        'setText(semanticIndexState, displayIndexState(health.semantic_index_state))'
        in overview_source
    )
    assert "health.semantic_search_available" in overview_source


def test_overview_javascript_has_loading_malformed_and_unavailable_states(tmp_path):
    script = client_for(tmp_path).get("/ui/assets/overview.js").text

    assert 'setText(overviewUpdateStatus, "Loading health information.")' in script
    assert 'setOverviewError("malformed")' in script
    assert 'setOverviewError("unavailable")' in script
    assert "VaultBridge returned unexpected health information. Try refreshing." in script
    assert "Health information is unavailable. Check the connection and try again." in script
    assert 'overviewContent.setAttribute("aria-busy", "true")' in script
    assert 'overviewContent.setAttribute("aria-busy", "false")' in script
    assert "refreshOverviewButton.disabled = true" in script
    assert "refreshOverviewButton.disabled = false" in script
    assert 'overallStatus.dataset.overviewState = "unavailable"' in script
    assert 'setText(overallStatus, "Unavailable")' in script
    assert "resetHealthValues();" in script
    assert ".focus()" not in script[
        script.index("async function loadOverview") : script.index("export function initializeOverview")
    ]


def test_overview_is_read_only_and_does_not_invent_unsupported_operator_facts(tmp_path):
    resources = "\n".join(
        client_for(tmp_path).get(path).text
        for path in ("/ui/", "/ui/assets/app.js", "/ui/assets/overview.js")
    )

    for prohibited in (
        "Force sync",
        "Rebuild index",
        "Delete index",
        "Repair index",
        "Watcher running",
        "Watcher enabled",
        "Queue size",
        "TrueNAS state",
        "NAS health",
        "Progress bar",
    ):
        assert prohibited.lower() not in resources.lower()


def test_public_health_for_overview_requires_no_authorization_and_keeps_contract(tmp_path):
    client = client_for(tmp_path, api_key="dashboard-secret")

    response = client.get("/health")
    invalid_key_response = client.get(
        "/health",
        headers={"Authorization": "Bearer definitely-invalid"},
    )

    assert response.status_code == 200
    assert invalid_key_response.status_code == 200
    assert set(response.json()) == {
        "ok",
        "vault_exists",
        "semantic_index_ready",
        "semantic_index_state",
        "semantic_search_available",
        "semantic_indexer_running",
        "full_sync_required",
        "indexed_notes",
        "semantic_chunks",
        "vault_notes",
        "last_successful_sync",
    }
    assert invalid_key_response.json() == response.json()


def test_javascript_implements_session_auth_status_and_safe_rendering_contract(tmp_path):
    script = client_for(tmp_path).get("/ui/assets/app.js").text
    overview_script = client_for(tmp_path).get("/ui/assets/overview.js").text
    all_scripts = f"{script}\n{overview_script}"

    assert 'import { initializeOverview } from "./overview.js"' in script
    assert "initializeOverview(applicationUrl)" in script
    assert 'const SESSION_STORAGE_KEY = "vaultbridge.ui.apiKey"' in script
    assert "sessionStorage.getItem(SESSION_STORAGE_KEY)" in script
    assert "sessionStorage.setItem(SESSION_STORAGE_KEY, credential)" in script
    assert "sessionStorage.removeItem(SESSION_STORAGE_KEY)" in script
    assert 'authenticatedFetch("api/v1/notes/list?limit=1", { credential })' in script
    assert 'headers.set("Authorization", `Bearer ${credential}`)' in script
    assert "document.baseURI" in script
    assert "new URL(`../${relativePath}`, document.baseURI)" in script
    assert "response.status === 401" in script
    assert "response.status === 429" in script
    assert "response.status === 503" in script
    assert 'response.headers.get("Retry-After")' in script
    assert "/^[1-9]\\d*$/" in script
    assert "controller.abort()" in script
    assert "event.preventDefault()" in script
    assert "response.body.cancel()" in script
    assert "textContent" in all_scripts

    for prohibited in (
        "localStorage",
        "indexedDB",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "Function(",
        "setTimeout(",
        "setInterval(",
        "console.",
    ):
        assert prohibited not in all_scripts


def test_unlock_probe_reuses_existing_authentication_without_a_ui_auth_endpoint(tmp_path):
    client = client_for(tmp_path, previous_api_key="test-previous-secret")

    assert client.get("/api/v1/notes/list", params={"limit": 1}).status_code == 401
    assert client.get(
        "/api/v1/notes/list",
        params={"limit": 1},
        headers={"Authorization": "Bearer invalid"},
    ).status_code == 401
    assert client.get(
        "/api/v1/notes/list",
        params={"limit": 1},
        headers={"Authorization": "Bearer test-secret"},
    ).status_code == 200
    assert client.get(
        "/api/v1/notes/list",
        params={"limit": 1},
        headers={"Authorization": "Bearer test-previous-secret"},
    ).status_code == 200
    assert client.post("/ui/auth").status_code == 404
