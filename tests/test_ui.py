from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

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
        ("/ui/assets/search.js", "text/javascript"),
        ("/ui/assets/vaultbridge-logo.webp", "image/webp"),
    ):
        response = client.get(path)
        head = client.head(path)
        assert response.status_code == head.status_code == 200
        assert response.headers["content-type"].startswith(media_type)
        assert head.headers["content-type"].startswith(media_type)
        assert response.content
        assert head.content == b""
        assert head.headers["content-length"] == response.headers["content-length"]


def test_ui_asset_route_inventory_matches_bundled_files(tmp_path):
    asset_names = {path.name for path in (Path("app/ui/assets")).iterdir() if path.is_file()}

    assert asset_names == {
        "app.css",
        "app.js",
        "overview.js",
        "search.js",
        "vaultbridge-logo.webp",
    }
    client = client_for(tmp_path)
    assert all(client.get(f"/ui/assets/{name}").status_code == 200 for name in asset_names)


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

    for path in (
        "/ui/",
        "/ui/assets/app.css",
        "/ui/assets/app.js",
        "/ui/assets/overview.js",
        "/ui/assets/search.js",
        "/ui/assets/vaultbridge-logo.webp",
    ):
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
        for path in (
            "/ui/",
            "/ui/assets/app.css",
            "/ui/assets/app.js",
            "/ui/assets/overview.js",
            "/ui/assets/search.js",
        )
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
    assert ">Restoring protected access<" in html
    assert "Checking for a saved dashboard session." in html
    api_key = next(attrs for tag, attrs in elements if tag == "input" and attrs.get("id") == "api-key")
    assert api_key["aria-describedby"] == "api-key-help"
    assert api_key["aria-errormessage"] == "global-status"
    assert {
        attrs.get("id")
        for tag, attrs in elements
        if tag == "section"
    } >= {"overview-panel", "search-panel", "api-panel", "about-panel"}

    overview_panel = html[html.index('<section id="overview-panel"') : html.index('<section id="search-panel"')]
    search_panel = html[html.index('<section id="search-panel"') : html.index('<section id="api-panel"')]
    api_panel = html[html.index('<section id="api-panel"') : html.index('<section id="about-panel"')]
    about_panel = html[html.index('<section id="about-panel"') :]
    assert 'id="api-key"' not in overview_panel
    assert 'id="api-key"' not in search_panel
    assert 'id="api-key"' in api_panel
    assert 'id="api-key"' not in about_panel
    assert api_panel.count('id="api-key"') == 1

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
    assert "Operator console" not in html
    assert "VaultBridge Overview" not in html
    assert "Overview" in html
    for heading in ("Overall status", "Knowledge index", "Vault", "Semantic lifecycle", "Background indexing"):
        assert f">{heading}<" in html
    for label in (
        "Application health",
        "Available",
        "Vault notes",
        "State",
        "Ready",
        "Search",
        "Indexed notes",
        "Semantic chunks",
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


def test_search_replaces_placeholder_with_protected_accessible_retrieval_form(tmp_path):
    html = client_for(tmp_path).get("/ui/").text
    parser = ShellHTMLParser()
    parser.feed(html)
    elements = parser.elements

    assert "future search workspace" not in html
    assert "reserved for VB-073" not in html
    assert "Checking protected access…" in html
    assert 'data-search-state="checking"' in html
    assert "Ready to search." not in html
    assert any(
        tag == "fieldset"
        and attrs.get("id") == "search-fieldset"
        and "disabled" in attrs
        for tag, attrs in elements
    )

    inputs = {
        attrs.get("id"): attrs
        for tag, attrs in elements
        if tag == "input" and attrs.get("id", "").startswith("search-")
    }
    assert inputs["search-mode-literal"]["type"] == "radio"
    assert inputs["search-mode-literal"]["value"] == "literal"
    assert inputs["search-mode-literal"]["checked"] is None
    assert inputs["search-mode-semantic"]["type"] == "radio"
    assert inputs["search-mode-semantic"]["value"] == "semantic"
    assert inputs["search-query"]["minlength"] == "1"
    assert inputs["search-query"]["maxlength"] == "300"
    assert inputs["search-query"]["aria-describedby"] == "search-query-help"
    assert inputs["search-folder"]["maxlength"] == "500"
    assert inputs["search-folder"]["aria-describedby"] == "search-folder-help"
    assert inputs["search-limit"]["min"] == "1"
    assert inputs["search-limit"]["max"] == "50"
    assert inputs["search-limit"]["value"] == "10"
    assert inputs["search-min-score"]["min"] == "-1"
    assert inputs["search-min-score"]["max"] == "1"
    assert inputs["search-min-score"]["value"] == "0.28"
    assert inputs["search-min-score"]["aria-describedby"] == "search-min-score-help"

    label_targets = {
        attrs.get("for")
        for tag, attrs in elements
        if tag == "label" and attrs.get("for")
    }
    assert {
        "search-query",
        "search-folder",
        "search-limit",
        "search-min-score",
    } <= label_targets
    assert any(
        attrs.get("id") == "semantic-score-field" and "hidden" in attrs
        for _tag, attrs in elements
    )
    assert [
        attrs.get("aria-live")
        for _tag, attrs in elements
        if attrs.get("id") == "search-status"
    ] == ["polite"]
    assert 'id="search-access-action"' in html
    assert "Unlock in API / Integration →" in html
    assert 'id="note-reader"' in html
    assert 'id="note-reader-back"' in html
    assert 'id="note-reader-content"' in html


def test_section_headings_share_vertical_accent_title_structure(tmp_path):
    html = client_for(tmp_path).get("/ui/").text

    assert html.count('class="panel-heading__title"') == 4
    for eyebrow, heading in (
        ("Workspace", "Overview"),
        ("Knowledge retrieval", "Search"),
        ("Client connection", "API / Integration"),
        ("Project", "About VaultBridge"),
    ):
        assert f'<p class="eyebrow">{eyebrow}</p>' in html
        assert f">{heading}</h2>" in html


def test_session_badge_and_api_only_unlock_share_one_state_model(tmp_path):
    client = client_for(tmp_path)
    html = client.get("/ui/").text
    script = client.get("/ui/assets/app.js").text
    css = client.get("/ui/assets/app.css").text

    assert html.count('id="session-state"') == 1
    assert html.count('id="api-key"') == 1
    assert html.index('id="api-panel"') < html.index('id="api-key"') < html.index('id="about-panel"')
    assert '"checking-session": "CHECKING SESSION"' in script
    assert 'locked: "LOCKED"' in script
    assert 'unlocked: "UNLOCKED"' in script
    assert "Ready — unlocked" not in script
    assert "sessionCard.dataset.sessionState = state" in script
    assert "authenticatedSession.hidden = !unlocked" in script
    assert "unlockForm.hidden = checking || unlocked" in script
    assert 'const apiLogoutButton = document.querySelector("#api-logout-button")' in script
    assert 'min-width: 10.75rem' in css
    assert ".state-unlocked .state-badge" in css
    assert "color: var(--success)" in css


def test_product_polish_uses_reference_and_principle_structures(tmp_path):
    html = client_for(tmp_path).get("/ui/").text

    assert 'class="integration-reference"' in html
    assert 'class="information-grid"' not in html
    assert 'class="principles-grid"' in html
    for principle in ("Local first", "API first", "Self hosted", "Deliberately small"):
        assert f">{principle}<" in html
    assert "Markdown remains the source of truth" in html
    assert "local embeddings by default" in html
    assert "ChatGPT" not in html


def test_search_ui_excludes_mutation_detail_duplicate_and_index_controls(tmp_path):
    html = client_for(tmp_path).get("/ui/").text
    search_panel = html[html.index('<section id="search-panel"') : html.index('<section id="api-panel"')]

    for prohibited in (
        "View note",
        "Open in Obsidian",
        "Create note",
        "Edit note",
        "Append",
        "Delete",
        "Duplicate",
        "Rebuild index",
        "Sync index",
        "Folder picker",
    ):
        assert prohibited.lower() not in search_panel.lower()


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
    overview_source = client_for(tmp_path).get("/ui/assets/overview.js").text.replace("\r\n", "\n")

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
    assert "activeController?.abort()" in script
    assert "const generation = ++requestGeneration" in script
    assert "generation !== requestGeneration" in script
    assert "signal: controller.signal" in script
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
    search_script = client_for(tmp_path).get("/ui/assets/search.js").text
    all_scripts = f"{script}\n{overview_script}\n{search_script}"

    assert 'import { initializeOverview } from "./overview.js"' in script
    assert "initializeOverview(applicationUrl)" in script
    assert 'import { initializeSearch } from "./search.js"' in script
    assert "searchController = initializeSearch({" in script
    assert "searchController?.setAccessState(state)" in script
    assert "searchController?.deactivate()" in script
    assert 'const unlockHeading = document.querySelector("#unlock-heading")' in script
    assert '? "Restoring protected access"' in script
    assert ': "Unlock protected features"' in script
    assert '"checking-session": "CHECKING SESSION"' in script
    assert 'locked: "LOCKED"' in script
    assert 'unlocked: "UNLOCKED"' in script
    assert "Ready — unlocked" not in script
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
        "document.cookie",
        "serviceWorker",
        "caches.",
    ):
        assert prohibited not in all_scripts


def test_session_focus_management_distinguishes_actions_from_reload(tmp_path):
    script = client_for(tmp_path).get("/ui/assets/app.js").text

    assert "if (focusAfterSuccess)" in script
    assert "logoutButton.focus();" in script
    assert "void validateCredential(credential, false, true);" in script
    assert "void validateCredential(storedCredential.value, true, true);" in script
    assert "void validateCredential(initialCredential.value, true, false);" in script
    assert 'setSessionState("checking-session", "Revalidating the saved session.", true);' in script
    assert 'apiKeyInput.setAttribute("aria-invalid", "true")' in script
    assert 'apiKeyInput.removeAttribute("aria-invalid")' in script


def test_search_javascript_maps_exact_protected_request_contracts_without_reranking(tmp_path):
    script = client_for(tmp_path).get("/ui/assets/search.js").text

    assert 'path: "api/v1/notes/search"' in script
    assert 'body: { query: queryInput.value, folder, limit }' in script
    assert 'path: "api/v1/notes/related"' in script
    for field_mapping in (
        "text: queryInput.value",
        "folder,",
        "limit,",
        "min_score: Number(minScoreInput.value)",
    ):
        assert field_mapping in script
    assert 'method: "POST"' in script
    assert '"Content-Type": "application/json"' in script
    assert "authenticatedFetch(request.path" in script
    assert "JSON.stringify(request.body)" in script

    for field in (
        "result.title",
        "result.path",
        "result.heading",
        "result.snippet",
        "result.score",
        "result.semantic_score",
        "result.lexical_score",
    ):
        assert field in script
    assert "results.forEach((result, index)" in script
    assert ".sort(" not in script
    assert '`api/v1/notes/read?path=${encodeURIComponent(result.path)}`' in script
    assert '{ method: "GET", headers: { Accept: "application/json" }, signal: controller.signal }' in script
    assert "api/v1/notes/duplicates" not in script
    assert '["Combined score", result.score]' in script
    assert '["Semantic score", result.semantic_score]' in script
    assert '["Lexical score", result.lexical_score]' in script


def test_search_javascript_has_private_safe_lifecycle_and_error_contract(tmp_path):
    script = client_for(tmp_path).get("/ui/assets/search.js").text

    assert "document.createElement(" in script
    assert "textContent" in script
    assert "replaceChildren()" in script
    assert "activeController?.abort()" in script
    assert "generation !== requestGeneration" in script
    assert "mode !== selectedMode" in script
    assert 'setStatus("loading"' in script
    assert 'setStatus("empty"' in script
    assert 'setStatus("error"' in script
    assert 'let accessState = "checking-session"' in script
    assert 'setAccessState(value)' in script
    assert 'setText(searchAccessMessage, "Checking protected access…")' in script
    assert 'setStatus(accessState === "checking-session" ? "checking" : "locked", "", false)' in script
    assert 'searchStatus.hidden = !visible' in script
    assert "Search is locked." not in script
    assert 'setStatus("idle", "Ready to search.")' in script
    assert 'setText(searchAccessMessage, "Protected search requires unlock.")' in script
    assert 'setText(searchAccessAction, "Unlock in API / Integration →")' in script
    assert "submitButton.disabled = true" in script
    assert "Semantic search is currently unavailable. Literal search remains available." in script
    assert "Rate limit reached. Retry in" in script
    assert 'error.kind === "authentication-required"' in script
    assert "onAuthenticationRequired();" in script
    assert "No snippet available." in script
    assert 'value === null ? "Not available"' in script
    assert "scoreFormatter.format(value)" in script
    assert "percentage" not in script.lower()

    for prohibited in (
        "sessionStorage",
        "localStorage",
        "indexedDB",
        "history.",
        "location.",
        "URLSearchParams",
        "window.location",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "setInterval(",
        "setTimeout(",
        "console.",
    ):
        assert prohibited not in script


def test_note_reader_uses_existing_read_endpoint_and_text_only_lifecycle(tmp_path):
    client = client_for(tmp_path)
    script = client.get("/ui/assets/search.js").text
    app_script = client.get("/ui/assets/app.js").text
    hostile_content = "# Safe title\n\n<script>alert(1)</script>\n<img src=x onerror=alert(2)>"
    (tmp_path / "Hostile.md").write_text(hostile_content, encoding="utf-8")

    response = client.get(
        "/api/v1/notes/read",
        params={"path": "Hostile.md"},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"path": "Hostile.md", "content": hostile_content}
    assert '`api/v1/notes/read?path=${encodeURIComponent(result.path)}`' in script
    assert 'openButton.type = "button"' in script
    assert 'openButton.addEventListener("click"' in script
    assert 'setText(noteReaderContent, payload.content)' in script
    assert "noteReaderContent.hidden = false" in script
    assert 'setReaderStatus("loading", "Loading complete note…")' in script
    assert 'setReaderStatus("ready", "Complete note loaded.")' in script
    assert 'error.kind === "not-found"' in script
    assert 'error.kind === "rate-limited"' in script
    assert 'error.kind === "network"' in script
    assert 'response.status === 404' in app_script
    assert "activeNoteController?.abort()" in script
    assert "generation !== noteRequestGeneration" in script
    assert "!unlocked || noteReader.hidden" in script
    assert 'error.kind === "authentication-required"' in script
    assert "onAuthenticationRequired();" in script
    assert 'noteReaderBack.addEventListener("click"' in script
    assert "showSearchWorkspace(true)" in script
    assert "focusTarget?.isConnected" in script
    assert "focusTarget.focus()" in script
    assert "resetProtectedState();" in script
    assert "showSearchWorkspace(false)" in script

    for prohibited in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "localStorage",
        "indexedDB",
        "history.",
        "URLSearchParams",
    ):
        assert prohibited not in script


def test_search_uses_existing_auth_wrapper_and_preserves_session_on_non_auth_errors(tmp_path):
    app_script = client_for(tmp_path).get("/ui/assets/app.js").text
    search_script = client_for(tmp_path).get("/ui/assets/search.js").text

    assert "Authorization" not in search_script
    assert "authenticatedFetch" in search_script
    assert 'headers.set("Authorization", `Bearer ${credential}`)' in app_script
    assert "callerSignal?.addEventListener" in app_script
    assert "callerSignal?.removeEventListener" in app_script
    assert 'error.kind === "authentication-required"' in search_script
    assert 'error.kind === "rate-limited"' in search_script
    assert 'error.kind === "service-unavailable"' in search_script
    assert "onAuthenticationRequired();" in search_script
    assert "clearCredentialState()" not in search_script


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


def test_ui_css_has_narrow_reflow_long_content_focus_and_reduced_motion_contract(tmp_path):
    css = client_for(tmp_path).get("/ui/assets/app.css").text

    assert "@media (max-width: 46rem)" in css
    assert "flex-wrap: wrap" in css
    assert "overflow-x: visible" in css
    assert "overflow-wrap: anywhere" in css
    assert "grid-template-columns: 1fr" in css
    panel_heading = css[css.index(".panel-heading {") : css.index(".panel-heading::before")]
    assert "grid-template-columns: 0.2rem minmax(0, 1fr)" in panel_heading
    assert "align-items: center" in panel_heading
    assert "column-gap: 0.75rem" in panel_heading
    panel_heading_rule = css[css.index(".panel-heading::before") : css.index(".panel-heading__title")]
    assert "width: 0.2rem" in panel_heading_rule
    assert "height: 2.25rem" in panel_heading_rule
    assert "button:focus-visible" in css
    assert "input:focus-visible" in css
    assert "999px" not in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    reduced_motion = css[css.index("@media (prefers-reduced-motion: reduce)") :]
    assert "animation: none" in reduced_motion
    assert "transition: none" in reduced_motion


def test_ui_resources_exclude_remote_dependencies_truenas_behavior_and_mutation_controls(tmp_path):
    client = client_for(tmp_path)
    resources = {
        path: client.get(path).text
        for path in (
            "/ui/",
            "/ui/assets/app.css",
            "/ui/assets/app.js",
            "/ui/assets/overview.js",
            "/ui/assets/search.js",
        )
    }
    combined = "\n".join(resources.values())

    assert not re.search(r"(?:src|href)=[\"'](?:https?:)?//", resources["/ui/"], re.IGNORECASE)
    assert not re.search(r"@import\s|url\(\s*[\"']?(?:https?:)?//", resources["/ui/assets/app.css"], re.IGNORECASE)
    for prohibited in (
        "Create note",
        "Append note",
        "Edit note",
        "Delete note",
        "Rebuild index",
        "Sync index",
        "TrueNAS",
        "NAS statistics",
        "Watcher control",
    ):
        assert prohibited.casefold() not in combined.casefold()


def test_ui_preserves_exact_api_operation_id_contract(tmp_path):
    schema = client_for(tmp_path).app.openapi()
    actual = {
        (method.upper(), path, operation["operationId"])
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
    }
    assert actual == {
        ("GET", "/health", "healthCheck"),
        ("GET", "/health/live", "livenessCheck"),
        ("GET", "/health/ready", "readinessCheck"),
        ("POST", "/notes", "createNote"),
        ("POST", "/notes/append", "appendNote"),
        ("GET", "/notes/read", "readNote"),
        ("POST", "/notes/search", "searchNotes"),
        ("POST", "/notes/related", "findRelatedNotes"),
        ("POST", "/notes/duplicates", "findDuplicateCandidates"),
        ("GET", "/notes/list", "listNotes"),
        ("POST", "/api/v1/notes", "createNoteV1"),
        ("POST", "/api/v1/notes/append", "appendNoteV1"),
        ("GET", "/api/v1/notes/read", "readNoteV1"),
        ("POST", "/api/v1/notes/search", "searchNotesV1"),
        ("POST", "/api/v1/notes/related", "findRelatedNotesV1"),
        ("POST", "/api/v1/notes/duplicates", "findDuplicateCandidatesV1"),
        ("GET", "/api/v1/notes/list", "listNotesV1"),
    }


def test_dashboard_palette_meets_text_component_and_focus_contrast_baseline(tmp_path):
    css = client_for(tmp_path).get("/ui/assets/app.css").text

    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def contrast(foreground: str, background: str) -> float:
        lighter, darker = sorted(
            (luminance(foreground), luminance(background)),
            reverse=True,
        )
        return (lighter + 0.05) / (darker + 0.05)

    expected_variables = {
        "#07101b",
        "#0b1725",
        "#0f1d2c",
        "#142436",
        "#26394e",
        "#415a74",
        "#77a5d4",
        "#94b8dc",
        "#5b83ae",
        "#f1f4f7",
        "#d9e0e8",
        "#98a8ba",
        "#a7caf0",
        "#789f91",
        "#d9bb72",
        "#e38d9a",
        "#84b3dd",
    }
    assert expected_variables <= set(re.findall(r"#[0-9a-fA-F]{6}", css))

    for foreground, background in (
        ("#d9e0e8", "#07101b"),
        ("#98a8ba", "#07101b"),
        ("#f1f4f7", "#0b1725"),
        ("#77a5d4", "#0b1725"),
        ("#789f91", "#0b1725"),
        ("#d9bb72", "#0b1725"),
        ("#e38d9a", "#0b1725"),
        ("#84b3dd", "#0b1725"),
        ("#07101b", "#77a5d4"),
    ):
        assert contrast(foreground, background) >= 4.5

    for focus, surface in (("#a7caf0", "#07101b"), ("#a7caf0", "#0b1725")):
        assert contrast(focus, surface) >= 3
