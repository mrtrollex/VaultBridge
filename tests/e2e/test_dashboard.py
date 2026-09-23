from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import Page, expect

from tests.e2e.conftest import E2E_API_KEY, BrowserFailureMonitor, E2EServer

KNOWN_QUERY = "quartz lighthouse phrase"
EXPECTED_TITLE = "Playwright Guide"
EXPECTED_PATH = str(Path("Knowledge") / "Playwright Guide.md")
EXPECTED_CONTENT = "The quartz lighthouse phrase proves literal retrieval"


def open_dashboard(page: Page, server: E2EServer) -> None:
    page.goto(f"{server.base_url}/ui/")
    expect(page).to_have_title("VaultBridge Dashboard")


def unlock(page: Page) -> None:
    page.get_by_role("button", name="API / Integration").click()
    page.get_by_label("API key").fill(E2E_API_KEY)
    page.get_by_role("button", name="Unlock").click()
    expect(page.get_by_text("UNLOCKED", exact=True)).to_be_visible()


def search_for_fixture(page: Page) -> None:
    page.get_by_role("button", name="Search", exact=True).click()
    page.get_by_label("Query").fill(KNOWN_QUERY)
    page.get_by_role("button", name="Search notes").click()
    expect(page.get_by_role("button", name=EXPECTED_TITLE)).to_be_visible()


def test_overview_loads_and_protected_access_unlocks(
    page: Page,
    e2e_server: E2EServer,
    browser_failures: BrowserFailureMonitor,
) -> None:
    open_dashboard(page, e2e_server)

    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    expect(page.get_by_role("status").filter(has_text="Health information updated.")).to_be_visible()
    expect(page.get_by_text("Vault notes")).to_be_visible()
    expect(page.get_by_text("2", exact=True)).to_be_visible()
    expect(page.get_by_text("LOCKED", exact=True)).to_be_visible()

    page.get_by_role("button", name="API / Integration").click()
    page.get_by_label("API key").fill("definitely-wrong-e2e-key")
    browser_failures.allow_http_failure(
        "GET",
        401,
        f"{e2e_server.base_url}/api/v1/notes/list?limit=1",
    )
    page.get_by_role("button", name="Unlock").click()
    expect(page.get_by_text("Authentication required", exact=True)).to_be_visible()
    expect(page.get_by_text("LOCKED", exact=True)).to_be_visible()

    page.get_by_label("API key").fill(E2E_API_KEY)
    page.get_by_role("button", name="Unlock").click()
    expect(page.get_by_text("UNLOCKED", exact=True)).to_be_visible()
    expect(page.get_by_text("Protected requests are available for this browser-tab session.")).to_be_visible()
    browser_failures.assert_clean()


def test_literal_search_reads_note_and_returns_to_results(
    page: Page,
    e2e_server: E2EServer,
    browser_failures: BrowserFailureMonitor,
) -> None:
    open_dashboard(page, e2e_server)
    unlock(page)
    search_for_fixture(page)

    expect(page.get_by_text(EXPECTED_PATH, exact=True)).to_be_visible()
    expect(page.get_by_text("Unrelated Archive", exact=True)).not_to_be_visible()
    page.get_by_role("button", name=EXPECTED_TITLE).click()

    reader = page.get_by_role("region", name=EXPECTED_TITLE)
    expect(reader.get_by_role("heading", name=EXPECTED_TITLE)).to_be_visible()
    expect(reader.get_by_text(EXPECTED_PATH, exact=True)).to_be_visible()
    expect(reader.get_by_text(EXPECTED_CONTENT, exact=False)).to_be_visible()
    expect(reader.get_by_role("heading", name="Relationships")).to_be_visible()
    expect(reader.get_by_text("Written target: Unrelated Archive", exact=True)).to_be_visible()
    expect(reader.get_by_text("State: Resolved", exact=True)).to_be_visible()
    expect(reader.get_by_text("Resolved path: Unrelated Archive.md", exact=True)).to_be_visible()
    expect(reader.get_by_text("Written target: Missing Plan", exact=True)).to_be_visible()
    expect(reader.get_by_text("State: Unresolved", exact=True)).to_be_visible()
    expect(reader.get_by_text("Source note: Unrelated Archive.md", exact=True)).to_be_visible()
    expect(reader.get_by_text("Display alias: Guide alias", exact=True)).to_be_visible()
    expect(reader).to_be_focused()
    page.get_by_role("button", name="Back to results").click()

    expect(page.get_by_role("button", name=EXPECTED_TITLE)).to_be_visible()
    expect(page.get_by_label("Query")).to_have_value(KNOWN_QUERY)
    browser_failures.assert_clean()


def test_same_tab_reload_restores_session(
    page: Page,
    e2e_server: E2EServer,
    browser_failures: BrowserFailureMonitor,
) -> None:
    open_dashboard(page, e2e_server)
    unlock(page)

    page.reload()

    expect(page.get_by_text("UNLOCKED", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Logout").first).to_be_visible()
    browser_failures.assert_clean()


def test_logout_clears_protected_data_and_survives_reload(
    page: Page,
    e2e_server: E2EServer,
    browser_failures: BrowserFailureMonitor,
) -> None:
    open_dashboard(page, e2e_server)
    unlock(page)
    search_for_fixture(page)

    page.get_by_role("button", name=EXPECTED_TITLE).click()
    reader = page.get_by_role("region", name=EXPECTED_TITLE)
    expect(reader.get_by_role("heading", name=EXPECTED_TITLE)).to_be_visible()
    expect(reader.get_by_text(EXPECTED_PATH, exact=True)).to_be_visible()
    expect(reader.get_by_text(EXPECTED_CONTENT, exact=False)).to_be_visible()

    page.get_by_role("button", name="Logout").first.click()

    expect(page.get_by_text("LOCKED", exact=True)).to_be_visible()
    expect(reader.get_by_role("heading", name=EXPECTED_TITLE)).not_to_be_visible()
    expect(reader.get_by_text(EXPECTED_PATH, exact=True)).not_to_be_visible()
    expect(reader.get_by_text(EXPECTED_CONTENT, exact=False)).not_to_be_visible()
    expect(reader.get_by_role("heading", name="Relationships")).not_to_be_visible()
    page.reload()
    expect(page.get_by_text("LOCKED", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=EXPECTED_TITLE)).not_to_be_visible()
    expect(page.get_by_text(EXPECTED_PATH, exact=True)).not_to_be_visible()
    expect(page.get_by_text(EXPECTED_CONTENT, exact=False)).not_to_be_visible()
    browser_failures.assert_clean()


def test_relationship_panel_handles_empty_failure_bounded_unicode_and_inert_text(
    page: Page,
    e2e_server: E2EServer,
    browser_failures: BrowserFailureMonitor,
) -> None:
    pending_routes = {}

    def hold_relationship_response(route) -> None:
        pending_routes[route.request.url.split("?", 1)[0].rsplit("/", 1)[-1]] = route

    page.route("**/api/v1/notes/links?*", hold_relationship_response)
    page.route("**/api/v1/notes/backlinks?*", hold_relationship_response)
    open_dashboard(page, e2e_server)
    unlock(page)
    search_for_fixture(page)
    page.get_by_role("button", name=EXPECTED_TITLE).click()

    reader = page.get_by_role("region", name=EXPECTED_TITLE)
    expect(reader.get_by_text("Loading outgoing links…", exact=True)).to_be_visible()
    expect(reader.get_by_text("Loading backlinks…", exact=True)).to_be_visible()
    assert set(pending_routes) == {"links", "backlinks"}

    hostile_target = "<script>window.relationshipOwned=true</script>"
    long_unicode_alias = "Žltý kôň 東京 — " + ("veľmi dlhý alias " * 18)
    links = [
        {
            "target": hostile_target,
            "heading": "Oddiel <img src=x onerror=alert(1)>",
            "alias": long_unicode_alias,
            "state": "unresolved",
            "resolved_path": None,
        },
        {
            "target": "Duplicate",
            "heading": "Same",
            "alias": "Repeated",
            "state": "resolved",
            "resolved_path": "Priečinok/Duplicate.md",
        },
        {
            "target": "Duplicate",
            "heading": "Same",
            "alias": "Repeated",
            "state": "resolved",
            "resolved_path": "Priečinok/Duplicate.md",
        },
    ]
    links.extend(
        {
            "target": f"Nested/Target {index}",
            "heading": f"Heading {index}",
            "alias": None,
            "state": "resolved",
            "resolved_path": f"Deep/Nested/Target {index}.md",
        }
        for index in range(19)
    )
    pending_routes["links"].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"links": links}),
    )
    pending_routes["backlinks"].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"backlinks": []}),
    )

    expect(reader.get_by_text("20 outgoing links shown. 2 additional outgoing links not shown.")).to_be_visible()
    expect(reader.get_by_text("No backlinks.", exact=True)).to_be_visible()
    expect(reader.get_by_text(f"Written target: {hostile_target}", exact=True)).to_be_visible()
    expect(reader.get_by_text(f"Display alias: {long_unicode_alias}", exact=True)).to_be_visible()
    expect(reader.locator("#outgoing-links-list > li")).to_have_count(20)
    expect(reader.get_by_text("Written target: Duplicate", exact=True)).to_have_count(2)
    assert reader.locator("script, img").count() == 0
    assert page.evaluate("window.relationshipOwned") is None
    expect(reader).to_be_focused()

    page.set_viewport_size({"width": 360, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    browser_failures.assert_clean()

    page.unroute("**/api/v1/notes/links?*", hold_relationship_response)
    page.unroute("**/api/v1/notes/backlinks?*", hold_relationship_response)
    page.route(
        "**/api/v1/notes/links?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"links": []}),
        ),
    )
    page.route(
        "**/api/v1/notes/backlinks?*",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"detail":"C:\\\\private\\\\vault token-secret traceback"}',
        ),
    )
    page.get_by_role("button", name="Back to results").click()
    browser_failures.allow_http_failure(
        "GET",
        500,
        f"{e2e_server.base_url}/api/v1/notes/backlinks?path={quote(EXPECTED_PATH, safe='')}",
    )
    page.get_by_role("button", name=EXPECTED_TITLE).click()
    reader = page.get_by_role("region", name=EXPECTED_TITLE)
    expect(reader.get_by_text("No outgoing links.", exact=True)).to_be_visible()
    expect(reader.get_by_text("Relationship data is temporarily unavailable.", exact=True)).to_be_visible()
    expect(reader.get_by_text("token-secret", exact=False)).not_to_be_visible()
    expect(reader.get_by_text("private", exact=False)).not_to_be_visible()
    browser_failures.assert_clean()

    page.unroute("**/api/v1/notes/links?*")
    page.unroute("**/api/v1/notes/backlinks?*")
    for endpoint in ("links", "backlinks"):
        page.route(
            f"**/api/v1/notes/{endpoint}?*",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"internal relationship service detail"}',
            ),
        )
        browser_failures.allow_http_failure(
            "GET",
            503,
            f"{e2e_server.base_url}/api/v1/notes/{endpoint}?path={quote(EXPECTED_PATH, safe='')}",
        )
    page.get_by_role("button", name="Back to results").click()
    page.get_by_role("button", name=EXPECTED_TITLE).click()
    reader = page.get_by_role("region", name=EXPECTED_TITLE)
    expect(
        reader.get_by_text("Relationship data is temporarily unavailable.", exact=True)
    ).to_have_count(2)
    expect(reader.get_by_text("internal relationship service detail", exact=False)).not_to_be_visible()
    browser_failures.assert_clean()


def test_relationship_responses_are_authenticated_encoded_and_cannot_overwrite_a_new_note(
    page: Page,
    e2e_server: E2EServer,
    browser_failures: BrowserFailureMonitor,
) -> None:
    page.add_init_script("AbortController.prototype.abort = function () {};")
    pending_first_note = {}
    observed_requests = []

    def route_relationships(route) -> None:
        parsed = urlsplit(route.request.url)
        note_path = parse_qs(parsed.query)["path"][0]
        endpoint = parsed.path.rsplit("/", 1)[-1]
        observed_requests.append(
            (parsed.path, parsed.query, note_path, route.request.headers.get("authorization"))
        )
        if note_path == EXPECTED_PATH:
            pending_first_note[endpoint] = route
            return
        payload = (
            {"links": []}
            if endpoint == "links"
            else {
                "backlinks": [
                    {
                        "source_path": "Second note current source.md",
                        "target": "Unrelated Archive",
                        "heading": None,
                        "alias": "Current note backlink",
                    }
                ]
            }
        )
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/v1/notes/links?*", route_relationships)
    page.route("**/api/v1/notes/backlinks?*", route_relationships)
    open_dashboard(page, e2e_server)
    unlock(page)
    search_for_fixture(page)
    page.get_by_role("button", name=EXPECTED_TITLE).click()
    first_reader = page.get_by_role("region", name=EXPECTED_TITLE)
    expect(first_reader.get_by_text("Loading outgoing links…", exact=True)).to_be_visible()
    expect(first_reader.get_by_text("Loading backlinks…", exact=True)).to_be_visible()
    assert set(pending_first_note) == {"links", "backlinks"}

    page.get_by_role("button", name="Back to results").click()
    page.get_by_label("Query").fill("garden planning")
    page.get_by_role("button", name="Search notes").click()
    page.get_by_role("button", name="Unrelated Archive").click()
    second_reader = page.get_by_role("region", name="Unrelated Archive")
    expect(second_reader.get_by_text("No outgoing links.", exact=True)).to_be_visible()
    expect(second_reader.get_by_text("Source note: Second note current source.md", exact=True)).to_be_visible()

    stale_links = {
        "links": [
            {
                "target": "STALE NOTE A TARGET",
                "heading": None,
                "alias": None,
                "state": "unresolved",
                "resolved_path": None,
            }
        ]
    }
    stale_backlinks = {
        "backlinks": [
            {
                "source_path": "STALE NOTE A SOURCE.md",
                "target": "Playwright Guide",
                "heading": None,
                "alias": None,
            }
        ]
    }
    pending_first_note["links"].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(stale_links),
    )
    pending_first_note["backlinks"].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(stale_backlinks),
    )
    expect(second_reader.get_by_text("Source note: Second note current source.md", exact=True)).to_be_visible()
    expect(page.get_by_text("STALE NOTE A TARGET", exact=False)).not_to_be_visible()
    expect(page.get_by_text("STALE NOTE A SOURCE", exact=False)).not_to_be_visible()

    assert {
        path for path, _, _, _ in observed_requests
    } == {"/api/v1/notes/links", "/api/v1/notes/backlinks"}
    assert all(auth == f"Bearer {E2E_API_KEY}" for _, _, _, auth in observed_requests)
    first_queries = [
        query for _, query, note_path, _ in observed_requests if note_path == EXPECTED_PATH
    ]
    assert first_queries == [
        f"path={quote(EXPECTED_PATH, safe='')}",
        f"path={quote(EXPECTED_PATH, safe='')}",
    ]
    browser_failures.assert_clean()
