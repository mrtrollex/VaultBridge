from __future__ import annotations

from pathlib import Path

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
    page.reload()
    expect(page.get_by_text("LOCKED", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=EXPECTED_TITLE)).not_to_be_visible()
    expect(page.get_by_text(EXPECTED_PATH, exact=True)).not_to_be_visible()
    expect(page.get_by_text(EXPECTED_CONTENT, exact=False)).not_to_be_visible()
    browser_failures.assert_clean()
