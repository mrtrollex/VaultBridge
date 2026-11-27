"""VB-156C content views against the named TLS app and existing scoped REST."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from app.services.knowledge_spaces import Capability, SpaceId
from tests.e2e import test_named_dashboard as dashboard
from tests.e2e.named_diagnostics import NativeEvidence
from tests.e2e.named_server import INTENT, JOURNAL, KEY, named_server
from tests.e2e.test_named_dashboard import journal, open_page, session_cookie
from tests.e2e.test_named_qualification import leave
from tests.test_space_runtime import backend as offline_backend  # noqa: F401

named_context = dashboard.named_context
NOTE = "Notes/Žurnál # + %.md"
INERT = '<img src="https://outside.invalid/leak" onerror="window.executed=1"><script>window.executed=2</script>'
TEXT = f"# Shared title\nsearch term\n{INERT}\n"
SEARCH = "**/api/v1/notes/search"
READ = "**/api/v1/notes/read?*"


@pytest.fixture
def content_profile(request):
    return getattr(request, "param", "literal")


@pytest.fixture(params=["", "/proxy"], ids=["root-https", "prefix-https"])
def named(tmp_path, request, content_profile):
    semantic = content_profile == "semantic"
    if semantic:
        request.getfixturevalue("offline_backend")

    def prepare(records):
        for record in records:
            sid = record["space_id"]
            root = Path(record["root"])
            (root / "Notes").mkdir()
            (root / NOTE).write_text(TEXT + sid, encoding="utf-8")
            if semantic:
                record["indexing"] = "enabled"
        if content_profile == "limited-capability":
            records[1]["capabilities"].remove(Capability.READ_NOTE.value)
            records[1]["capabilities"].remove(Capability.SEMANTIC_RETRIEVAL.value)
        if content_profile == "literal":
            for n in range(12):
                (tmp_path / "default" / "Notes" / f"Item {n:02}.md").write_text("# Extra note", encoding="utf-8")

    denied = ("research",) if content_profile == "denied" else ()
    with named_server(tmp_path, request.param, denied=denied, prepare=prepare) as server:
        server["proxy_server"].label = request.node.nodeid
        if semantic:
            server["app"].state.space_runtime.scheduler.wait(20)
        server["root"] = tmp_path
        yield server


def login(page):
    page.get_by_label("Existing API key").fill(KEY)
    page.get_by_role("button", name="Unlock", exact=True).click()
    expect(page.locator("#active-selection")).to_have_text("Active selection: default")
    expect(page.locator("#index-status")).to_contain_text("default:")


def show(page, view):
    page.locator("#named-views").get_by_role("button", name=view, exact=True).click()


def select_both(page):
    page.get_by_role("button", name="Choose multiple spaces").click()
    page.locator('#space-options input[value="research"]').check()
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#index-status")).to_contain_text("research:")


def search(page, query="search term"):
    page.locator("#named-search-query").fill(query)
    page.get_by_role("button", name="Run search").click()


def rows(page, region="#named-search"):
    return page.locator(f"{region} ol > li")


def identities(page, region="#named-search"):
    return rows(page, region).evaluate_all("rows => rows.map(row => JSON.parse(row.dataset.identity))")


def wire_identities(body):
    return [[hit["identity"]["space_id"], hit["identity"]["path"]] for hit in body["items"]]


def test_browse_limits_empty_keyboard_and_return_context(named_context, named):
    page = open_page(named_context, named)
    login(page)
    select_both(page)
    page.set_viewport_size({"width": 320, "height": 640})
    show(page, "Browse")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("#browse-folder").fill("Missing")
    page.locator("#browse-folder").press("Enter")
    expect(page.locator("#browse [role=status]")).to_have_text("No matching notes.")
    expect(page.locator("#browse .named-coverage")).to_contain_text("Complete coverage")
    page.locator("#browse-folder").fill("Notes")
    page.locator("#browse-limit").select_option("10")
    with page.expect_response("**/api/v1/notes/list?*") as listed:
        page.get_by_role("button", name="List notes").press("Enter")
    expect(rows(page, "#browse")).to_have_count(10)
    expect(page.locator("#browse [role=status]")).to_contain_text("Result limit reached")
    assert parse_qs(urlsplit(listed.value.url).query) == {"spaces": ["default"], "folder": ["Notes"], "limit": ["10"]}
    assert listed.value.headers["cache-control"] == "no-store"
    ordered = wire_identities(listed.value.json())
    assert identities(page, "#browse") == ordered

    opener = rows(page, "#browse").last.get_by_role("button")
    opener.scroll_into_view_if_needed()
    opener.focus()
    previous_scroll = page.evaluate("window.scrollY")
    assert previous_scroll > 0
    opener.press("Enter")
    expect(page.locator("#named-reader-heading")).to_be_focused()
    expect(page.locator("#named-reader pre")).to_be_visible()
    page.get_by_role("button", name="Back to results").press("Enter")
    expect(opener).to_be_focused()
    assert abs(page.evaluate("window.scrollY") - previous_scroll) <= 1
    expect(page.locator("#browse-folder")).to_have_value("Notes")
    expect(page.locator("#browse-limit")).to_have_value("10")
    assert identities(page, "#browse") == ordered


def test_federated_literal_qualified_unicode_fresh_reader_and_inventory(named_context, named):
    requested = []
    page = open_page(named_context, named)
    page.on("request", lambda req: requested.append((req.method, req.url)))
    login(page)
    show(page, "Search")
    with page.expect_response(SEARCH) as single:
        search(page)
    expect(rows(page)).to_have_count(1)
    assert single.value.request.post_data_json == {"spaces": ["default"], "limit": 10, "query": "search term"}

    show(page, "Spaces")
    select_both(page)
    show(page, "Search")
    page.locator("#named-search-folder").fill("Notes")
    with page.expect_response(SEARCH) as multi:
        search(page)
    expect(rows(page)).to_have_count(2)
    assert multi.value.request.post_data_json == {
        "spaces": ["default", "research"], "limit": 10, "folder": "Notes", "query": "search term",
    }
    assert multi.value.headers["cache-control"] == "no-store"
    order = identities(page)
    assert order == wire_identities(multi.value.json()) == [["default", NOTE], ["research", NOTE]]
    expect(rows(page).first.locator("h3")).to_have_text(rows(page).last.locator("h3").text_content())
    expect(page.locator("#named-search .named-coverage")).to_contain_text("Complete coverage")
    for i, sid in enumerate(("default", "research")):
        opener = rows(page).nth(i).get_by_role("button")
        with page.expect_response(READ) as read:
            opener.click()
        expect(page.locator("#named-reader .named-scope")).to_contain_text(f"{sid} · {NOTE}")
        expect(page.locator("#named-reader pre")).to_have_text(TEXT + sid, use_inner_text=False)
        assert read.value.headers["cache-control"] == "no-store"
        assert parse_qs(urlsplit(read.value.url).query) == {"space": [sid], "path": [NOTE]}
        assert "%23" in read.value.url and "%C5%BD" in read.value.url and "%2F" in read.value.url
        assert page.locator("#named-reader pre img, #named-reader pre script, #named-reader pre a").count() == 0
        page.get_by_role("button", name="Back to results").click()
        expect(opener).to_be_focused()
        expect(page.locator("#named-search-query")).to_have_value("search term")
        expect(page.locator("#named-search-folder")).to_have_value("Notes")
        assert identities(page) == order

    (named["root"] / "research" / NOTE).write_text("Fresh research revision", encoding="utf-8")
    rows(page).last.get_by_role("button").click()
    expect(page.locator("#named-reader pre")).to_have_text("Fresh research revision")
    assert not page.evaluate("window.executed")
    assert page.evaluate("Object.keys(sessionStorage).length === 0 && history.state === null")
    assert NOTE not in page.url and "Fresh research revision" not in page.title()
    stored = page.evaluate("Object.fromEntries(Object.entries(localStorage))")
    assert {JOURNAL} <= set(stored) <= {JOURNAL, INTENT}
    assert all(value not in json.dumps(stored) for value in (NOTE, "search term", "research", "default"))
    assert session_cookie(named_context)[0]["httpOnly"]
    assert all(url.startswith(named["url"] + "/") for _, url in requested)
    content_methods = {("POST", "/api/v1/notes/search"), ("GET", "/api/v1/notes/read")}
    calls = [row for row in named["observations"] if "/api/v1/notes/" in row["path"]]
    prefix = urlsplit(named["url"]).path
    assert [(row["method"], row["path"].removeprefix(prefix)) for row in calls] == [
        ("POST", "/api/v1/notes/search"), ("POST", "/api/v1/notes/search"),
        ("GET", "/api/v1/notes/read"), ("GET", "/api/v1/notes/read"), ("GET", "/api/v1/notes/read"),
    ]
    assert all(row["headers"].get(b"x-vaultbridge-ui-request") == b"1" and
               b"vaultbridge_ui_session=" in row["headers"].get(b"cookie", b"") for row in calls)
    assert all(method == "GET" or path.endswith("/ui/session") or (method, path.removeprefix(prefix)) in content_methods
               for method, path in named["inventory"])


@pytest.mark.parametrize("content_profile", ["semantic"], indirect=True)
def test_semantic_single_federated_order_and_previous_index(named_context, named):
    page = open_page(named_context, named)
    login(page)
    show(page, "Search")
    page.locator("#named-search-mode").select_option("semantic")
    with page.expect_response("**/api/v1/notes/related") as single:
        search(page)
    expect(rows(page)).to_have_count(1)
    assert single.value.request.post_data_json == {"spaces": ["default"], "limit": 5, "text": "search term"}

    runtime = named["app"].state.space_runtime
    for sid, state in [("default", "indexing"), ("research", "error")]:
        owner = runtime.registry._binding(SpaceId(sid))._owners.semantic
        owner.repository.set_metadata("index_state", state)

    show(page, "Spaces")
    select_both(page)
    show(page, "Search")
    page.locator("#named-search-mode").select_option("semantic")
    page.locator("#named-search-folder").fill("Notes")
    with page.expect_response("**/api/v1/notes/related") as multi:
        search(page)
    expect(rows(page)).to_have_count(2)
    assert multi.value.request.post_data_json == {
        "spaces": ["default", "research"], "limit": 5, "folder": "Notes", "text": "search term",
    }
    body = multi.value.json()
    assert identities(page) == wire_identities(body)
    coverage = page.locator("#named-search .named-coverage")
    expect(coverage).to_contain_text("Order: Semantic rank")
    expect(coverage).to_contain_text("Previous compatible index — refresh in progress or required")
    expect(coverage).to_contain_text("Previous compatible index — refresh failed")
    expect(rows(page).first).to_contain_text(f"Score {body['items'][0]['score']:g}")
    assert sum(path.endswith("/notes/related") for _, path in named["inventory"]) == 2
    assert all(not path.endswith("/notes/search") for _, path in named["inventory"])
    for sid in ("default", "research"):
        root = named["root"]
        (root / sid).rename(root / f"offline-{sid}")
    search(page)
    expect(page.locator("#named-search [role=status]")).to_have_text("Results unavailable. Retry explicitly.")
    expect(page.locator("#named-search-mode")).to_have_value("semantic")
    assert all(not path.endswith("/notes/search") for _, path in named["inventory"])


@pytest.mark.parametrize("content_profile", ["literal", "limited-capability"], indirect=True)
def test_capability_and_index_policy_block_semantic_without_fallback(named_context, named, content_profile):
    page = open_page(named_context, named)
    login(page)
    select_both(page)
    show(page, "Search")
    page.locator("#named-search-mode").select_option("semantic")
    expect(page.get_by_role("button", name="Run search")).to_be_disabled()
    expect(page.locator("#search-scope")).to_contain_text(
        "indexing is disabled" if content_profile == "literal" else "operation is unavailable"
    )
    page.locator("#named-search-query").fill("search term")
    page.locator("#named-search-query").press("Enter")
    assert all(not path.endswith(("/notes/search", "/notes/related")) for _, path in named["inventory"])
    page.locator("#named-search-mode").select_option("literal")
    search(page)
    expect(rows(page)).to_have_count(2)
    if content_profile == "limited-capability":
        expect(rows(page).last.get_by_role("button", name="Open note unavailable", exact=False)).to_be_disabled()


def test_real_partial_and_all_unavailable(named_context, named):
    page = open_page(named_context, named)
    login(page)
    select_both(page)
    show(page, "Search")
    root = named["root"]
    (root / "research").rename(root / "offline-research")
    search(page)
    expect(rows(page)).to_have_count(1)
    expect(page.locator("#named-search .named-coverage")).to_contain_text("Partial coverage")
    expect(page.locator("#named-search .named-coverage")).to_contain_text("research: Unavailable")
    assert identities(page) == [["default", NOTE]]
    (root / "default").rename(root / "offline-default")
    search(page)
    expect(page.locator("#named-search [role=status]")).to_have_text("Results unavailable. Retry explicitly.")
    expect(rows(page)).to_have_count(0)
    expect(page.locator("#named-search .named-coverage")).to_be_empty()
    show(page, "Browse")
    page.get_by_role("button", name="List notes").click()
    expect(page.locator("#browse [role=status]")).to_have_text("Results unavailable. Retry explicitly.")


@pytest.mark.parametrize("content_profile", ["denied"], indirect=True)
@pytest.mark.parametrize("forbidden", ["research", "unknown"])
def test_real_mixed_denial_clears_all_protected_selection(named_context, named, forbidden):
    page = open_page(named_context, named)
    login(page)
    expect(page.locator("#single-space option")).to_have_count(2)
    show(page, "Search")
    search(page)
    expect(rows(page)).to_have_count(1)
    page.route(SEARCH, lambda route: route.continue_(
        post_data=json.dumps({**route.request.post_data_json, "spaces": ["default", forbidden]})))
    with page.expect_response(SEARCH) as rejected:
        search(page)
    assert rejected.value.status == 403
    assert rejected.value.headers["cache-control"] == "no-store"
    assert "set-cookie" not in rejected.value.headers
    expect(page.locator("#spaces-message")).to_have_text("Selection unavailable. Reload spaces explicitly.")
    expect(page.locator("#index-status")).to_be_empty()
    expect(page.locator("#named-search-query")).to_have_value("")
    expect(rows(page)).to_have_count(0)
    assert session_cookie(named_context)
    page.get_by_role("button", name="Reload spaces").click()
    expect(page.locator("#single-space option")).to_have_count(2)
    expect(page.locator("#single-space")).to_have_value("")


@pytest.mark.parametrize("fault", ["non-json", "foreign", "bad-path", "duplicate", "coverage", "private-error"])
def test_invalid_or_private_result_replaces_previous_output(named_context, named, fault):
    page = open_page(named_context, named)
    login(page)
    show(page, "Search")
    with page.expect_response(SEARCH) as valid:
        search(page)
    expect(rows(page)).to_have_count(1)
    body = valid.value.json()
    if fault == "foreign":
        body["items"][0]["identity"]["space_id"] = "research"
    elif fault == "bad-path":
        body["items"][0]["identity"]["path"] = "../outside.md"
    elif fault == "duplicate":
        body["items"] *= 2
    elif fault == "coverage":
        body["coverage"][0]["space_id"] = "research"
    raw = json.dumps(body)
    status = 200
    if fault == "non-json":
        raw = "invalid PRIVATE_ERROR"
    elif fault == "private-error":
        raw = json.dumps({"detail": "PRIVATE_ERROR"})
        status = 500
    page.route(SEARCH, lambda route: route.fulfill(status=status, content_type="application/json", body=raw))
    search(page)
    expect(page.locator("#named-search [role=status]")).to_have_text("Results unavailable. Retry explicitly.")
    expect(rows(page)).to_have_count(0)
    expect(page.locator("#named-search .named-coverage")).to_be_empty()
    expect(page.locator("body")).not_to_contain_text("PRIVATE_ERROR")
    assert session_cookie(named_context)


@pytest.mark.parametrize(
    "view,region,endpoint,submit_label",
    [
        ("Browse", "#browse", "**/api/v1/notes/list?*", "List notes"),
        ("Search", "#named-search", SEARCH, "Run search"),
    ],
    ids=["browse", "search"],
)
def test_rejects_complete_coverage_with_limit_reason(named_context, named, view, region, endpoint, submit_label):
    page = open_page(named_context, named)
    login(page)
    show(page, view)
    if view == "Search":
        page.locator("#named-search-query").fill("search term")
    submit = page.get_by_role("button", name=submit_label)
    with page.expect_response(endpoint) as valid:
        submit.click()
    body = valid.value.json()
    assert body["items"]
    expect(rows(page, region)).to_have_count(len(body["items"]))
    coverage = page.locator(f"{region} .named-coverage")
    expect(coverage).to_contain_text("Complete coverage")
    assert body["coverage"][0]["reasons"] == []

    body["coverage"][0]["reasons"] = ["discovery_limit"]
    page.route(endpoint, lambda route: route.fulfill(json=body))
    submit.click()
    expect(page.locator(f"{region} [role=status]")).to_have_text("Results unavailable. Retry explicitly.")
    expect(coverage).to_be_empty()
    expect(rows(page, region)).to_have_count(0)


def test_result_title_and_snippet_are_inert_text(named_context, named):
    page = open_page(named_context, named)
    requested = []
    page.on("request", lambda req: requested.append(req.url))
    login(page)
    show(page, "Search")
    with page.expect_response(SEARCH) as valid:
        search(page)
    expect(rows(page)).to_have_count(1)
    body = valid.value.json()
    body["items"][0].update(title=INERT, snippet=INERT)
    page.route(SEARCH, lambda route: route.fulfill(json=body))
    search(page)
    expect(rows(page).first.locator("h3")).to_have_text(INERT)
    expect(rows(page).first.locator("p").last).to_have_text(INERT)
    assert page.locator("#named-search ol img, #named-search ol script").count() == 0
    assert not page.evaluate("window.executed")
    assert all(url.startswith(named["url"] + "/") for url in requested)


@pytest.mark.parametrize("fault", ["deleted", "space", "path", "malformed"])
def test_failed_reader_keeps_only_authorized_return_context(named_context, named, fault):
    page = open_page(named_context, named)
    login(page)
    show(page, "Search")
    search(page)
    opener = page.get_by_role("button", name="Open note in default", exact=False)
    opener.click()
    expect(page.locator("#named-reader pre")).to_be_visible()
    page.get_by_role("button", name="Back to results").click()
    if fault == "deleted":
        (named["root"] / "default" / NOTE).unlink()
    else:
        data = {"space_id": "default", "result": {"path": NOTE, "content": "PRIVATE_WRONG_NOTE"}}
        if fault == "space":
            data["space_id"] = "research"
        elif fault == "path":
            data["result"]["path"] = "Notes/Different.md"
        else:
            data["result"]["content"] = ["PRIVATE_WRONG_NOTE"]
        page.route(READ, lambda route: route.fulfill(json=data))
    opener.click()
    expect(page.locator("#named-reader [role=status]")).to_have_text(
        "Note no longer available." if fault == "deleted" else "Note unavailable. Retry explicitly."
    )
    expect(page.locator("#named-reader pre")).to_be_empty()
    expect(page.locator("body")).not_to_contain_text("PRIVATE_WRONG_NOTE")
    page.get_by_role("button", name="Back to results").click()
    expect(opener).to_be_focused()
    assert identities(page) == [["default", NOTE]]


def hold_content(page, endpoint, phase, outcome):
    page.evaluate(
        """({endpoint, phase, outcome}) => {
          AbortController.prototype.abort = function() {};
          const nativeFetch = window.fetch;
          let armed = true;
          const held = new Promise(resolve => window.releaseContent = resolve);
          const settle = async value => {
            document.documentElement.dataset.testContentHeld = "true";
            await held;
            if (outcome === 'error') throw Error('PRIVATE_LATE_ERROR');
            return value;
          };
          window.fetch = async (url, options) => {
            const capture = armed && new URL(url).pathname.endsWith(endpoint);
            if (capture) armed = false;
            const response = await nativeFetch(url, options);
            if (!capture) return response;
            if (phase === 'fetch') return settle(response);
            const json = response.json.bind(response);
            response.json = async () => settle(await json());
            return response;
          };
        }""",
        {"endpoint": endpoint, "phase": phase, "outcome": outcome},
    )


def release_content(page):
    # Advance one task after releasing the already received response so all
    # promise continuations run before checking for a stale DOM update.
    page.evaluate("async () => {window.releaseContent(); await new Promise(resolve => setTimeout(resolve, 0));}")


@pytest.mark.parametrize("phase", ["fetch", "json"])
@pytest.mark.parametrize("transition,outcome", [
    ("selection", "success"), ("logout", "success"), ("navigation", "error"), ("overlap", "error"),
])
def test_stale_search_completions_cannot_update_any_view(named_context, named, phase, transition, outcome):
    page = open_page(named_context, named)
    login(page)
    show(page, "Search")
    hold_content(page, "/notes/search", phase, outcome)
    search(page)
    expect(page.locator("html")).to_have_attribute("data-test-content-held", "true")
    expect(page.locator("#named-search")).to_have_attribute("aria-busy", "true")
    if transition == "selection":
        # Isolate selection invalidation from the preceding panel change.
        page.locator("#single-space").evaluate(
            "field => {field.value = 'research'; field.dispatchEvent(new Event('change'));}"
        )
        expect(page.locator("#search-scope")).to_have_text("research")
        expect(page.locator("#named-search-query")).to_have_value("")
    elif transition == "logout":
        page.get_by_role("button", name="Log out", exact=True).click()
        expect(page.locator("#session-message")).to_contain_text("removal confirmed")
        expect(page.locator("#named-views")).to_be_hidden()
    elif transition == "navigation":
        show(page, "Browse")
        expect(page.locator("#browse")).to_be_visible()
    else:
        search(page, "no such text")
        expect(page.locator("#named-search [role=status]")).to_have_text("No matching notes.")
    state = page.locator("#named-search").text_content()
    release_content(page)
    assert page.locator("#named-search").text_content() == state
    expect(rows(page)).to_have_count(0)
    expect(page.locator("#named-search")).to_have_attribute("aria-busy", "false")


@pytest.mark.parametrize("phase", ["fetch", "json"])
def test_old_reader_cannot_replace_new_space_after_back(named_context, named, phase):
    page = open_page(named_context, named)
    login(page)
    select_both(page)
    show(page, "Search")
    search(page)
    expect(rows(page)).to_have_count(2)
    hold_content(page, "/notes/read", phase, "success")
    rows(page).first.get_by_role("button").click()
    expect(page.locator("html")).to_have_attribute("data-test-content-held", "true")
    page.get_by_role("button", name="Back to results").click()
    rows(page).last.get_by_role("button").click()
    expect(page.locator("#named-reader pre")).to_have_text(TEXT + "research")
    release_content(page)
    expect(page.locator("#named-reader pre")).to_have_text(TEXT + "research")
    expect(page.locator("#named-reader .named-scope")).to_contain_text("research")


def test_navigation_aborts_pending_native_list(named_context, named):
    page = open_page(named_context, named)
    login(page)
    show(page, "Browse")
    page.evaluate(
        """() => {
          const nativeFetch = window.fetch;
          window.fetch = (url, options) => {
            if (new URL(url).pathname.endsWith('/notes/list')) window.listSignal = options.signal;
            return nativeFetch(url, options);
          };
        }"""
    )
    barrier = named["barrier"]
    barrier.arm("GET", path="/notes/list")
    page.get_by_role("button", name="List notes").click()
    assert barrier.entered.wait(10)
    show(page, "Search")
    assert page.evaluate("window.listSignal.aborted")
    barrier.release.set()
    assert barrier.finished.wait(10)
    expect(rows(page, "#browse")).to_have_count(0)
    expect(page.locator("#browse [role=status]")).to_be_empty()


def test_departing_spaces_cannot_receive_late_status(named_context, named):
    page = open_page(named_context, named)
    login(page)
    hold_content(page, "/knowledge/spaces/status", "json", "success")
    page.get_by_role("button", name="Refresh status").click()
    expect(page.locator("html")).to_have_attribute("data-test-content-held", "true")
    show(page, "Search")
    expect(page.locator("#spaces")).to_be_hidden()
    release_content(page)
    expect(page.locator("#named-search")).to_be_visible()
    expect(page.locator("#search-scope")).to_have_text("default")
    expect(page.locator("#index-status")).to_be_empty()
    show(page, "Spaces")
    expect(page.locator("#spaces-message")).to_have_text("Refresh selected-space status explicitly.")
    page.get_by_role("button", name="Refresh status").click()
    expect(page.locator("#index-status")).to_contain_text("default: disabled")


def assert_content_erased(page):
    for region in ("#named-views", "#browse", "#named-search", "#named-reader"):
        expect(page.locator(region)).to_be_hidden()
    for content in ("#browse ol", "#named-search ol", "#browse .named-coverage", "#named-search .named-coverage",
                    "#named-reader-heading", "#named-reader pre", "#named-reader .named-scope"):
        expect(page.locator(content)).to_be_empty()
    expect(page.locator("#named-search-query")).to_have_value("")
    expect(page.locator("#named-search-folder")).to_have_value("")


@pytest.mark.parametrize("transition", ["logout", "401", "pagehide"])
def test_reader_and_hidden_origin_erased_synchronously(named_context, named, transition):
    page = open_page(named_context, named)
    login(page)
    show(page, "Search")
    page.locator("#named-search-folder").fill("Notes")
    search(page)
    expect(rows(page)).to_have_count(1)
    if transition == "401":
        barrier = named["barrier"]
        barrier.arm("DELETE")
        page.route(READ, lambda route: route.fulfill(status=401, body="private malformed body"))
        rows(page).first.get_by_role("button").click()
        assert_content_erased(page)
        assert barrier.entered.wait(10)
        assert session_cookie(named_context)
        barrier.release.set()
        expect(page.locator("#session-message")).to_contain_text("removal confirmed")
        assert not session_cookie(named_context)
    else:
        rows(page).first.get_by_role("button").click()
        expect(page.locator("#named-reader pre")).to_be_visible()
        if transition == "logout":
            page.get_by_role("button", name="Log out", exact=True).click()
            expect(page.locator("#session-message")).to_contain_text("removal confirmed")
        else:
            # Deterministic controller-branch probe. Native history and BFCache
            # qualification have separate tests and retained limitations.
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
        assert_content_erased(page)
    # An old Back control cannot revive the now-discarded result context.
    page.locator("#named-reader button").evaluate("button => button.click()")
    assert_content_erased(page)


def test_stale_content_401_cannot_clear_new_login(named_context, named):
    page = open_page(named_context, named)
    login(page)
    show(page, "Search")
    hold_content(page, "/notes/search", "fetch", "success")
    barrier = named["barrier"]
    barrier.arm("POST", 401, path="/notes/search")
    barrier.release.set()
    search(page)
    expect(page.locator("html")).to_have_attribute("data-test-content-held", "true")
    page.get_by_role("button", name="Log out", exact=True).click()
    expect(page.locator("#session-message")).to_contain_text("removal confirmed")
    login(page)
    clean, cookie = journal(page), session_cookie(named_context)
    start = len(named["inventory"])
    release_content(page)
    expect(page.locator("#spaces")).to_be_visible()
    assert not any(method == "DELETE" for method, _ in named["inventory"][start:])
    assert session_cookie(named_context) == cookie and journal(page) == clean


def test_native_history_erases_reader_and_does_not_replay(playwright, browser_name, named, request):
    options = {"channel": "chromium", "ignore_default_args": ["--disable-back-forward-cache"]} \
        if browser_name == "chromium" else {}
    browser = getattr(playwright, browser_name).launch(**options)
    context = browser.new_context(ignore_https_errors=True, proxy=named["proxy"])
    evidence = NativeEvidence(context, request.node.nodeid, trace=True)
    barrier = named["barrier"]
    try:
        page = open_page(context, named)
        login(page)
        show(page, "Search")
        search(page)
        rows(page).first.get_by_role("button").click()
        expect(page.locator("#named-reader pre")).to_be_visible()
        clean, cookie = journal(page), session_cookie(context)
        page.evaluate("""() => addEventListener('pagehide', event => {
          const erased = ['#named-search ol', '#named-search .named-coverage', '#named-reader pre',
            '#named-reader .named-scope'].every(selector => !document.querySelector(selector).textContent);
          sessionStorage.setItem('content-departure', JSON.stringify({
            persisted: event.persisted, erased, queryEmpty: !document.querySelector('#named-search-query').value
          }));
        }, {once:true})""")
        leave(page, named["url"] + "/health/live")
        departure = page.evaluate("""() => {
          const facts = JSON.parse(sessionStorage.getItem('content-departure'));
          sessionStorage.removeItem('content-departure');
          return facts;
        }""")
        evidence.record("native-content-pagehide-after-erasure", data=departure)
        assert departure and departure["erased"] and departure["queryEmpty"]
        barrier.arm("GET", repeat=True)
        start = len(named["inventory"])
        page.go_back(wait_until="commit")
        assert barrier.entered.wait(10)
        assert_content_erased(page)
        assert all(b"set-cookie" not in headers for headers in barrier.captured_headers)
        barrier.release.set()
        expect(page.locator("#index-status")).to_contain_text("default:")
        assert session_cookie(context) == cookie and journal(page) == clean
        show(page, "Search")
        expect(page.locator("#named-search-query")).to_have_value("")
        expect(rows(page)).to_have_count(0)
        expect(page.locator("#named-reader")).to_be_hidden()
        assert not any("/api/v1/notes/" in path for _, path in named["inventory"][start:])
        page.wait_for_function("qualificationEvents.some(event => event.type === 'pageshow')")
        evidence.snapshot(page, "native-content-history-return")
    finally:
        barrier.release.set()
        evidence.finish_trace()
        context.close()
        evidence.save()
        browser.close()
