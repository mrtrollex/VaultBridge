from __future__ import annotations

import json
from copy import deepcopy

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import BrowserFailureMonitor, E2EServer
from tests.e2e.test_dashboard import open_dashboard, unlock
from tests.test_api_knowledge_hygiene import result_fixture, wire_json

ENDPOINT = "/api/v1/knowledge/hygiene/scan"


def clean_payload():
    return {
        "findings": [], "findings_truncated": False,
        "scan": {"state": "complete", "reasons": [], "eligible_paths": 0,
                 "inspected_notes": 0, "unavailable_notes": 0},
        "candidates": {"state": "not_requested", "source_notes": 0, "reasons": []},
        "derived_index": {"status": "not_requested"},
    }


def respond(route, payload):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def open_hygiene(page, e2e_server, browser_failures):
    open_dashboard(page, e2e_server, browser_failures)
    unlock(page)
    page.get_by_role("button", name="Hygiene", exact=True).click()
    expect(page.get_by_role("heading", name="Knowledge Hygiene")).to_be_visible()
    expect(page.locator("#hygiene-status")).to_contain_text("Ready")


def test_hygiene_real_scan_is_explicit_cookie_authenticated_and_read_only(
    page: Page, e2e_server: E2EServer, browser_failures: BrowserFailureMonitor,
):
    requests = []
    page.on("request", lambda request: requests.append(request) if ENDPOINT in request.url else None)
    before = {p.relative_to(e2e_server.vault_path): p.read_bytes()
              for p in e2e_server.vault_path.rglob("*") if p.is_file()}
    open_dashboard(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Hygiene", exact=True).click()
    expect(page.locator("#hygiene-status")).to_contain_text("Authentication required")
    expect(page.get_by_role("button", name="Run scan", exact=True)).to_be_disabled()
    page.locator("#hygiene-access").click()
    expect(page.get_by_label("API key")).to_be_focused()
    unlock(page)
    page.get_by_role("button", name="Hygiene", exact=True).click()
    expect(page.locator("#nav-hygiene")).to_have_attribute("aria-current", "page")
    assert requests == []
    with page.expect_response(lambda response: ENDPOINT in response.url) as response_info:
        page.get_by_role("button", name="Run scan", exact=True).click()
    response = response_info.value
    assert response.status == 200
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "findings")
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and request.url == e2e_server.base_url + ENDPOINT
    assert request.headers["x-vaultbridge-ui-request"] == "1"
    assert "authorization" not in request.headers
    assert request.post_data_json == {
        "groups": ["relationships", "isolation", "frontmatter", "aliases"],
        "finding_limit": 500, "duplicate_source_limit": 0,
        "semantic_candidates": False, "inspect_derived_index": False,
    }
    payload = response.json()
    expect(page.locator(".hygiene-findings > li")).to_have_count(len(payload["findings"]))
    for finding in payload["findings"]:
        assert finding["primary_path"] in page.locator("#hygiene-results").inner_text()
    assert before == {p.relative_to(e2e_server.vault_path): p.read_bytes()
                      for p in e2e_server.vault_path.rglob("*") if p.is_file()}
    assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
    page.get_by_role("button", name="Overview", exact=True).click()
    page.get_by_role("button", name="Hygiene", exact=True).click()
    expect(page.locator(".hygiene-findings > li")).to_have_count(len(payload["findings"]))
    assert len(requests) == 1
    browser_failures.assert_clean()


def test_hygiene_loading_clean_refresh_and_keyboard(page, e2e_server, browser_failures):
    pending = []
    page.route("**" + ENDPOINT, lambda route: pending.append(route))
    open_hygiene(page, e2e_server, browser_failures)
    page.locator("#nav-hygiene").focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    run = page.get_by_role("button", name="Run scan", exact=True)
    expect(run).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#hygiene-status")).to_contain_text("Scanning live Markdown")
    expect(page.locator("#hygiene-results")).to_have_attribute("aria-busy", "true")
    expect(run).to_be_disabled()
    run.press("Enter")
    assert len(pending) == 1
    respond(pending.pop(), clean_payload())
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "clean")
    expect(page.locator("#hygiene-status")).to_contain_text("No findings in the scanned diagnostics")
    expect(page.locator("#hygiene-status")).to_have_attribute("role", "status")
    expect(page.locator("#hygiene-results")).to_have_attribute("aria-busy", "false")
    expect(page.get_by_role("button", name="Refresh scan")).to_be_focused()
    page.get_by_role("button", name="Refresh scan").click()
    expect(page.locator("#hygiene-status")).to_contain_text("Scanning")
    respond(pending.pop(), clean_payload())
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "clean")
    browser_failures.assert_clean()


def test_hygiene_all_evidence_grouping_bounds_inert_text_and_reflow(page, e2e_server, browser_failures):
    payload = wire_json(result_fixture("storage_unavailable"))
    hostile = "Notes/<img src=x onerror=window.hygieneOwned=true>" + "long" * 100 + ".md"
    payload["findings"][0]["primary_path"] = hostile
    payload["findings"][0]["category"] = "<script>window.hygieneOwned=true</script>"
    payload["findings"][0]["related_paths"] = [f"Related/{i}.md" for i in range(10)]
    # Interleaved kinds preserve first-seen group order and server order within groups.
    payload["findings"].append({**deepcopy(payload["findings"][0]), "primary_path": "second.md"})
    payload["findings"].extend(deepcopy(payload["findings"][3]) for _ in range(500 - len(payload["findings"])))
    page.route("**" + ENDPOINT, lambda route: respond(route, payload))
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "partial")
    expect(page.locator("#hygiene-status")).to_contain_text("500 returned findings")
    expect(page.locator("#hygiene-status")).to_contain_text("Findings truncated")
    expect(page.locator(".hygiene-counts > li").first).to_have_text("missing relationship target: 2")
    expect(page.locator(".hygiene-findings").first.locator(".hygiene-path")).to_have_text([hostile, "second.md"])
    expect(page.locator(".hygiene-findings > li")).to_have_count(500)
    expect(page.locator("#hygiene-results img, #hygiene-results script")).to_have_count(0)
    assert page.evaluate("window.hygieneOwned") is None
    for expected in ["Source order (zero-based)", "Line", "Column", "Field", "Source index (zero-based)",
                     "Source indices (zero-based)", "Peer count", "Related paths truncated", "Vault-level diagnostic",
                     "storage unavailable", "semantic unavailable", "relationship unavailable", "Related/9.md"]:
        assert expected in page.locator("#hygiene-results").inner_text()
    for width in [1280, 375, 320]:
        page.set_viewport_size({"width": width, "height": 800})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    browser_failures.assert_clean()


@pytest.mark.parametrize("coverage", ["not_requested", "complete", "partial", "unavailable"])
def test_hygiene_partial_empty_and_independent_coverage(page, e2e_server, browser_failures, coverage):
    payload = clean_payload()
    payload["scan"].update(state="partial", reasons=["note_unavailable"], unavailable_notes=1, eligible_paths=1)
    payload["candidates"]["state"] = coverage
    payload["derived_index"]["status"] = "inspection_unavailable"
    page.route("**" + ENDPOINT, lambda route: respond(route, payload))
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "partial")
    expect(page.locator("#hygiene-status")).not_to_contain_text("No findings in the scanned diagnostics")
    expect(page.locator("#hygiene-results")).to_contain_text(coverage.replace("_", " "))
    expect(page.locator("#hygiene-results")).to_contain_text("inspection unavailable")
    browser_failures.assert_clean()


@pytest.mark.parametrize("status,state,message", [
    (401, "locked", "Authentication required"), (429, "rate-limited", "Retry in 7 seconds"),
    (503, "service-unavailable", "temporarily unavailable"), (500, "server-error", "server error"),
    (422, "request-rejected", "rejected the request"),
])
def test_hygiene_errors_clear_prior_data_and_only_401_locks(page, e2e_server, browser_failures, status, state, message):
    payload = wire_json(result_fixture())
    page.route("**" + ENDPOINT, lambda route: respond(route, payload))
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator(".hygiene-findings > li")).to_have_count(len(payload["findings"]))
    page.unroute("**" + ENDPOINT)
    browser_failures.allow_http_failure("POST", status, e2e_server.base_url + ENDPOINT)
    page.route("**" + ENDPOINT, lambda route: route.fulfill(
        status=status, headers={"Retry-After": "7"}, content_type="application/json", body='{"detail":"private"}',
    ))
    page.get_by_role("button", name="Refresh scan").click()
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", state)
    expect(page.locator("#hygiene-results")).to_be_empty()
    if status == 401:
        expect(page.get_by_label("API key")).to_be_focused()
        expect(page.get_by_text("LOCKED", exact=True)).to_be_visible()
        page.get_by_role("button", name="Hygiene", exact=True).click()
    else:
        expect(page.get_by_text("UNLOCKED", exact=True)).to_be_visible()
    expect(page.locator("#hygiene-status")).to_contain_text(message)
    assert "private" not in page.locator("#hygiene-panel").inner_text()
    browser_failures.assert_clean()


@pytest.mark.parametrize("invalid", [
    "empty", "json", "null", "missing", "evidence", "extra_evidence", "oversized", "related_paths", "coverage", "count",
])
def test_hygiene_rejects_empty_malformed_and_out_of_bounds_response(page, e2e_server, browser_failures, invalid):
    payload = wire_json(result_fixture())
    if invalid == "null":
        payload = None
    elif invalid == "missing":
        del payload["scan"]
    elif invalid == "evidence":
        payload["findings"][0]["evidence"]["source_order"] = True
    elif invalid == "extra_evidence":
        payload["findings"][0]["evidence"]["secret"] = "PRIVATE"
    elif invalid == "oversized":
        payload["findings"] *= 40
    elif invalid == "related_paths":
        payload["findings"][0]["related_paths"] = ["z.md"] * 11
    elif invalid == "coverage":
        payload["candidates"]["state"] = "unknown"
    elif invalid == "count":
        payload["scan"]["eligible_paths"] = 10001
    body = "" if invalid == "empty" else "{" if invalid == "json" else json.dumps(payload)
    page.route("**" + ENDPOINT, lambda route: route.fulfill(status=200, content_type="application/json", body=body))
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "malformed")
    expect(page.locator("#hygiene-results")).to_be_empty()
    expect(page.get_by_text("UNLOCKED", exact=True)).to_be_visible()
    browser_failures.assert_clean()


@pytest.mark.parametrize("late_status", [200, 500, 401])
def test_hygiene_stale_response_after_navigation_cannot_replace_new_scan(
    page, e2e_server, browser_failures, late_status,
):
    # Defeat cancellation to prove generation checks, including stale failures.
    page.add_init_script("""
        const realFetch = window.fetch;
        window.fetch = (url, options) => realFetch(url, { ...options, signal: undefined });
    """)
    pending = []
    page.route("**" + ENDPOINT, lambda route: pending.append(route))
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_contain_text("Scanning")
    first = pending.pop()
    page.get_by_role("button", name="Overview", exact=True).click()
    page.get_by_role("button", name="Hygiene", exact=True).click()
    expect(page.locator("#hygiene-status")).to_contain_text("interrupted")
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_contain_text("Scanning")
    respond(pending.pop(), clean_payload())
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "clean")
    if late_status != 200:
        browser_failures.allow_http_failure("POST", late_status, e2e_server.base_url + ENDPOINT)
    first.fulfill(status=late_status, content_type="application/json", body=json.dumps(wire_json(result_fixture())))
    # Drain browser promises before asserting the late response had no effect.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "clean")
    expect(page.locator(".hygiene-findings > li")).to_have_count(0)
    expect(page.get_by_text("UNLOCKED", exact=True)).to_be_visible()
    browser_failures.assert_clean()


def test_hygiene_logout_clears_results_immediately_and_invalidates_late_scan(page, e2e_server, browser_failures):
    page.add_init_script("""
        const realFetch = window.fetch;
        window.fetch = (url, options) => realFetch(url, { ...options, signal: undefined });
    """)
    payload = wire_json(result_fixture())
    page.route("**" + ENDPOINT, lambda route: respond(route, payload))
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator(".hygiene-findings > li")).to_have_count(len(payload["findings"]))
    page.get_by_role("button", name="Logout").first.click()
    expect(page.locator("#hygiene-results")).to_be_empty()
    expect(page.get_by_text("LOCKED", exact=True)).to_be_visible()
    unlock(page)
    page.get_by_role("button", name="Hygiene", exact=True).click()
    pending = []
    page.unroute("**" + ENDPOINT)
    page.route("**" + ENDPOINT, lambda route: pending.append(route))
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_contain_text("Scanning")
    page.get_by_role("button", name="Logout").first.click()
    expect(page.get_by_text("LOCKED", exact=True)).to_be_visible()
    respond(pending.pop(), payload)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#hygiene-results")).to_be_empty()
    browser_failures.assert_clean()


def test_hygiene_network_failure_keeps_session_and_retry_available(page, e2e_server, browser_failures):
    page.add_init_script("""
        const realFetch = window.fetch;
        window.fetch = (url, options) => String(url).endsWith('/knowledge/hygiene/scan')
            ? Promise.reject(new TypeError('synthetic offline')) : realFetch(url, options);
    """)
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "network")
    expect(page.locator("#hygiene-status")).to_contain_text("Unable to connect")
    expect(page.locator("#hygiene-run")).to_be_enabled()
    expect(page.locator("#hygiene-results")).to_be_empty()
    expect(page.get_by_text("UNLOCKED", exact=True)).to_be_visible()
    browser_failures.assert_clean()


def test_hygiene_complete_truncated_is_distinct_from_clean(page, e2e_server, browser_failures):
    payload = clean_payload()
    payload["findings_truncated"] = True
    page.route("**" + ENDPOINT, lambda route: respond(route, payload))
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "truncated")
    expect(page.locator("#hygiene-status")).not_to_contain_text("No findings in the scanned diagnostics")
    browser_failures.assert_clean()


def test_hygiene_complete_scan_does_not_hide_unavailable_subevidence(page, e2e_server, browser_failures):
    payload = clean_payload()
    payload["candidates"].update(state="unavailable", reasons=["candidate_unavailable"])
    payload["derived_index"]["status"] = "storage_unavailable"
    page.route("**" + ENDPOINT, lambda route: respond(route, payload))
    open_hygiene(page, e2e_server, browser_failures)
    page.get_by_role("button", name="Run scan", exact=True).click()
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "clean")
    expect(page.locator("#hygiene-results")).to_contain_text("candidate unavailable")
    expect(page.locator("#hygiene-results")).to_contain_text("storage unavailable")
    browser_failures.assert_clean()


def test_hygiene_checking_and_unavailable_session_do_not_scan(page, e2e_server, browser_failures):
    session = []
    scans = []
    page.route("**/ui/session", lambda route: session.append(route))
    page.on("request", lambda request: scans.append(request) if ENDPOINT in request.url else None)
    page.goto(e2e_server.base_url + "/ui/")
    page.get_by_role("button", name="Hygiene", exact=True).click()
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "checking-session")
    expect(page.locator("#hygiene-run")).to_be_disabled()
    browser_failures.allow_http_failure("GET", 503, e2e_server.base_url + "/ui/session")
    session.pop().fulfill(status=503, body="{}", content_type="application/json")
    expect(page.locator("#hygiene-status")).to_have_attribute("data-hygiene-state", "unavailable")
    expect(page.locator("#hygiene-run")).to_be_disabled()
    assert scans == []
    browser_failures.assert_clean()
