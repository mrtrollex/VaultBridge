"""Native named security paths. Run with each Playwright browser engine."""

import json
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from tests.e2e.named_diagnostics import NativeEvidence
from tests.e2e.named_server import COOKIE, INTENT, JOURNAL, KEY, named_server, serve

LOCK = "vaultbridge.named-ui-session.v1"

# Fault injection drops notifications, not cookies, storage or server work.
DROP_NOTIFICATIONS = """
Object.defineProperty(window, 'BroadcastChannel', {value: undefined});
for (const type of ['storage', 'focus', 'pageshow'])
  window.addEventListener(type, event => event.stopImmediatePropagation(), true);
document.addEventListener('visibilitychange', event => event.stopImmediatePropagation(), true);
AbortController.prototype.abort = function() {};
"""

OBSERVE_JSON = """
window.sessionJSON = [];
const nativeFetch = window.fetch;
window.fetch = (...args) => nativeFetch(...args).then(response => {
  if (response.url.endsWith('/ui/session')) {
    const parse = response.json.bind(response);
    response.json = async () => {
      const value = await parse();
      window.sessionJSON.push(response.status);
      return value;
    };
  }
  return response;
});
"""


@pytest.fixture(params=["", "/proxy"], ids=["root-https", "prefix-https"])
def named(tmp_path, request):
    with named_server(tmp_path, request.param) as server:
        server["proxy_server"].label = request.node.nodeid
        yield server


@pytest.fixture
def named_context(new_context, named, request):
    # Use the project's pytest-playwright factory so --tracing actually records
    # these native custom-proxy contexts, including module/bootstrap failures.
    context = new_context(ignore_https_errors=True, proxy=named["proxy"])
    evidence = NativeEvidence(context, request.node.nodeid)
    context.on(
        "requestfailed", lambda request: print("Native request failed:", urlsplit(request.url).path, request.failure)
    )
    try:
        yield context
    finally:
        named["barrier"].release.set()
        if context in context.browser.contexts:
            context.close()
        evidence.save()


def open_page(context, named):
    page = context.new_page()
    page.goto(named["url"] + "/ui/")
    return page


def login(page):
    try:
        page.locator("#api-key").fill(KEY)
    except Exception:
        print("Unlock readiness:", page.locator("#session-message").text_content())
        raise
    page.get_by_role("button", name="Unlock", exact=True).click()
    expect(page.locator("#spaces")).to_be_visible()
    expect(page.locator("#index-status")).to_contain_text("default: disabled")


def journal(page):
    return page.evaluate("key => JSON.parse(localStorage.getItem(key))", JOURNAL)


def session_cookie(context):
    return [cookie for cookie in context.cookies() if cookie["name"] == COOKIE]


def test_native_samesite_delivery(named_context, named):
    page = open_page(named_context, named)
    login(page)
    # The synthetic foreign site has no session issuer. A top-level cross-site
    # navigation tests actual transport, independently of cookie inspection APIs.
    page.goto(named["origin"].replace("vaultbridge.example.test", "foreign.invalid.test") + "/health/live")
    page.evaluate(
        "url => {const a = document.createElement('a'); a.href = url; "
        "a.textContent = 'Cross-site navigation'; document.body.append(a)}",
        named["url"] + "/health/live",
    )
    named["deliveries"].clear()
    page.get_by_role("link", name="Cross-site navigation").click()
    assert named["deliveries"]
    assert COOKIE.encode() + b"=" not in named["deliveries"][0][1]


def test_unlock_restore_selection_logout(named_context, named):
    page = open_page(named_context, named)
    login(page)
    cookie = session_cookie(named_context)
    assert len(cookie) == 1 and cookie[0]["httpOnly"] and cookie[0]["secure"] and cookie[0]["sameSite"] == "Strict"
    before = journal(page)
    page.reload()
    expect(page.locator("#index-status")).to_contain_text("default: disabled")
    assert session_cookie(named_context) == cookie and journal(page) == before
    page.get_by_role("button", name="Choose multiple spaces").click()
    page.locator('#space-options input[value="research"]').check()
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#active-selection")).to_have_text("Active selection: default")
    page.get_by_role("button", name="Choose multiple spaces").click()
    page.locator('#space-options input[value="research"]').check()
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#index-status")).to_contain_text("research: disabled")
    page.get_by_role("button", name="Choose multiple spaces").click()
    for box in page.locator("#space-options input").all():
        box.uncheck()
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#active-selection")).to_have_text("No spaces selected")
    expect(page.locator("#refresh-status")).to_be_disabled()
    stored = page.evaluate("Object.fromEntries(Object.entries(localStorage))")
    assert set(stored) <= {INTENT, JOURNAL}
    assert all(len(value.encode()) <= 512 for value in stored.values())
    assert all(word not in json.dumps(stored) for word in (KEY, "research", "default", cookie[0]["value"]))
    page.get_by_role("button", name="Log out", exact=True).click()
    expect(page.locator("#session-message")).to_contain_text("removal confirmed")
    assert not session_cookie(named_context)
    expect(page.locator("#spaces")).to_be_hidden()
    assert not page.locator("#capabilities").text_content() and not page.locator("#index-status").text_content()
    page.reload()
    expect(page.locator("#unlock-form")).to_be_visible()
    assert all(method == "GET" or path.endswith("/ui/session") for method, path in named["inventory"])


def test_delayed_get_headers_cannot_resurrect(named_context, named):
    a = open_page(named_context, named)
    login(a)
    b = open_page(named_context, named)
    expect(b.locator("#spaces")).to_be_visible()
    b.evaluate("AbortController.prototype.abort = function() {}")
    barrier = named["barrier"]
    barrier.arm("GET")
    b.evaluate("window.dispatchEvent(new Event('focus'))")
    assert barrier.entered.wait(10)
    assert b"set-cookie" not in barrier.headers
    a.get_by_role("button", name="Log out", exact=True).click()
    expect(a.locator("#session-message")).to_contain_text("removal confirmed")
    assert not session_cookie(named_context)
    barrier.release.set()
    expect(b.locator("#spaces")).to_be_hidden()
    b.reload()
    expect(b.locator("#unlock-form")).to_be_visible()
    assert not session_cookie(named_context)
    response = named_context.request.get(named["url"] + "/ui/session", headers={"X-VaultBridge-UI-Request": "1"})
    assert response.status == 401 and "set-cookie" not in response.headers


@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize("termination", ["close", "reload"])
def test_orphan_mutation_quarantined(named_context, named, method, termination):
    a = open_page(named_context, named)
    if method == "DELETE":
        login(a)
    barrier = named["barrier"]
    barrier.arm(method)
    if method == "POST":
        a.locator("#api-key").fill(KEY)
        a.get_by_role("button", name="Unlock", exact=True).click()
    else:
        a.get_by_role("button", name="Log out", exact=True).click()
    assert barrier.entered.wait(10)
    if termination == "close":
        a.close()
    else:
        a.reload()
    b = open_page(named_context, named)
    expect(b.locator("#session-message")).to_contain_text("Session outcome uncertain")
    assert journal(b)["phase"] in {"posting", "deleting", "uncertain"}
    barrier.release.set()
    b.reload()
    expect(b.locator("#session-message")).to_contain_text("Session outcome uncertain")
    expect(b.locator("#spaces")).to_be_hidden()
    expect(b.locator("#unlock-form")).to_be_hidden()


def test_live_post_then_logout_orders_cookie_mutations(named_context, named):
    a = open_page(named_context, named)
    login(a)
    b = open_page(named_context, named)
    expect(b.locator("#spaces")).to_be_visible()
    b.get_by_role("button", name="Log out", exact=True).click()
    expect(b.locator("#unlock-form")).to_be_visible()
    barrier = named["barrier"]
    barrier.arm("POST")
    b.locator("#api-key").fill(KEY)
    b.get_by_role("button", name="Unlock", exact=True).click()
    assert barrier.entered.wait(10)
    # A's observed posting record clears content, but explicit logout can queue.
    a.evaluate("window.dispatchEvent(new Event('focus'))")
    # Restore waits for the live issuer lock. Logout publishes intent first.
    a.locator("#logout").click()
    assert journal(b)["phase"] == "posting"
    barrier.release.set()
    expect(a.locator("#session-message")).to_contain_text("removal confirmed")
    assert not session_cookie(named_context)
    expect(b.locator("#spaces")).to_be_hidden()


@pytest.mark.parametrize("failure", ["locks", "rejected-locks", "storage", "insecure", "broadcast"])
def test_required_browser_features(named_context, named, failure):
    scripts = {
        "locks": "Object.defineProperty(navigator, 'locks', {value: undefined})",
        "rejected-locks": (
            "Object.defineProperty(navigator, 'locks', {value: {request: () => Promise.reject(Error('test'))}})"
        ),
        "storage": "Storage.prototype.getItem = () => {throw Error('test')}",
        "insecure": "Object.defineProperty(window, 'isSecureContext', {value: false})",
        "broadcast": "Object.defineProperty(window, 'BroadcastChannel', {value: undefined})",
    }
    named_context.add_init_script(scripts[failure])
    page = open_page(named_context, named)
    if failure == "broadcast":
        login(page)
    else:
        expect(page.locator("#session-message")).to_contain_text("Named browser sessions unavailable")
        assert not any(path.endswith("/ui/session") or "/api/" in path for _, path in named["inventory"])


def test_failed_delete_blocks_login_until_explicit_retry(named_context, named):
    page = open_page(named_context, named)
    login(page)
    barrier = named["barrier"]
    barrier.arm("DELETE", 429)
    barrier.release.set()
    page.get_by_role("button", name="Log out", exact=True).click()
    expect(page.locator("#session-message")).to_contain_text("removal not confirmed")
    assert session_cookie(named_context) and journal(page)["phase"] == "logout_pending"
    page.reload()
    expect(page.locator("#session-message")).to_contain_text("removal not confirmed")
    page.get_by_role("button", name="Retry logout", exact=True).click()
    expect(page.locator("#session-message")).to_contain_text("removal confirmed")
    assert not session_cookie(named_context)


def test_same_hostname_other_port_receives_cookie_other_hostname_does_not(named_context, named):
    page = open_page(named_context, named)
    login(page)
    received = []

    async def receiver(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                await send({"type": event["type"] + ".complete"})
                if event["type"] == "lifespan.shutdown":
                    return
        else:
            headers = dict(scope["headers"])
            received.append((headers.get(b"host", b""), scope["path"], headers.get(b"cookie", b"")))
            from starlette.responses import PlainTextResponse

            await PlainTextResponse("Disposable receiver", headers={"Connection": "close"})(scope, receive, send)

    with serve(receiver, named["cert"], named["key"]) as port:
        page.goto(f"https://vaultbridge.example.test:{port}/probe")
        matching = [
            value
            for host, path, value in received
            if host == f"vaultbridge.example.test:{port}".encode() and path == "/probe"
        ]
        assert matching and all(COOKIE.encode() + b"=" in value for value in matching)
        page.goto(f"https://other.example.test:{port}/probe")
        matching = [
            value
            for host, path, value in received
            if host == f"other.example.test:{port}".encode() and path == "/probe"
        ]
        assert matching and all(COOKIE.encode() + b"=" not in value for value in matching)
        page.close()
        # Drain the browser's native connection pool before stopping the TLS
        # receiver. Closing a document alone can leave speculative connections.
        named_context.close()


def test_old_401_cannot_delete_new_login(named_context, named):
    a = open_page(named_context, named)
    login(a)
    b = open_page(named_context, named)
    expect(b.locator("#spaces")).to_be_visible()
    b.evaluate("AbortController.prototype.abort = function() {}")
    barrier = named["barrier"]
    barrier.arm("GET", 401)
    b.evaluate("window.dispatchEvent(new Event('focus'))")
    assert barrier.entered.wait(10)
    assert b"set-cookie" not in barrier.headers
    a.get_by_role("button", name="Log out", exact=True).click()
    expect(a.locator("#unlock-form")).to_be_visible()
    login(a)
    current = session_cookie(named_context)
    revision = journal(a)["revision"]
    deletes = sum(method == "DELETE" for method, _ in named["inventory"])
    barrier.release.set()
    b.reload()
    expect(b.locator("#spaces")).to_be_visible()
    assert session_cookie(named_context) == current and journal(a)["revision"] == revision
    assert sum(method == "DELETE" for method, _ in named["inventory"]) == deletes


def test_queued_old_login_cancelled_by_logout_intent(named_context, named):
    a = open_page(named_context, named)
    b = open_page(named_context, named)
    c = open_page(named_context, named)
    barrier = named["barrier"]
    barrier.arm("POST")
    a.locator("#api-key").fill(KEY)
    a.get_by_role("button", name="Unlock", exact=True).click()
    assert barrier.entered.wait(10)
    b.locator("#api-key").fill(KEY)
    b.get_by_role("button", name="Unlock", exact=True).click()
    # Explicit intent is synchronous, before waiting for the issuer's lock.
    c.locator("#logout").click()
    c.wait_for_function("key => localStorage.getItem(key) !== null", arg=INTENT)
    barrier.release.set()
    expect(c.locator("#session-message")).to_contain_text("removal confirmed")
    expect(b.locator("#spaces")).to_be_hidden()
    assert sum(method == "POST" for method, path in named["inventory"] if path.endswith("/ui/session")) == 1
    assert not session_cookie(named_context)


@pytest.mark.parametrize(
    "status,message",
    [(403, "Reload spaces explicitly"), (429, "Rate limited"), (500, "Spaces unavailable"),
     (503, "Spaces unavailable"), (401, "removal confirmed")],
)
def test_selected_status_failure_states(named_context, named, status, message):
    page = open_page(named_context, named)
    login(page)
    barrier = named["barrier"]
    barrier.arm("GET", status, path="/api/v1/knowledge/spaces/status")
    barrier.release.set()
    page.get_by_role("button", name="Refresh status", exact=True).click()
    if status == 401:
        expect(page.locator("#session-message")).to_contain_text(message)
        expect(page.locator("#spaces")).to_be_hidden()
        assert not session_cookie(named_context)
    else:
        expect(page.locator("#spaces-message")).to_contain_text(message)
        expect(page.locator("#index-status")).to_be_empty()
        assert session_cookie(named_context)
        if status == 403:
            expect(page.locator("#active-selection")).to_have_text("No spaces selected")
        else:
            expect(page.locator("#active-selection")).to_have_text("Active selection: default")


PROTECTED_401_BODIES = {
    "json": ('{"detail":"untrusted-401-body"}', "application/json"),
    "empty": ("", "application/json"),
    "malformed": ("not-json-untrusted-401-body", "application/json"),
    "truncated": ('{"detail":"untrusted-401-body', "application/json"),
    "non-json": ("<html>untrusted-401-body</html>", "text/html"),
}


def observe_protected_reads(page):
    # Observe completion and actual controller memory using the real modules;
    # do not replace their session, parsing, authorization or rendering behavior.
    page.evaluate("""async () => {
      const {ScopedClient}=await import(new URL('./assets/scoped-client.js', document.baseURI));
      const {Spaces}=await import(new URL('./assets/spaces.js', document.baseURI));
      window.protectedReads=[];
      window.protectedResponses=[];
      const nativeFetch=window.fetch;
      window.fetch=(...args) => nativeFetch(...args).then(response => {
        if (new URL(response.url).pathname.includes('/api/v1/knowledge/spaces'))
          protectedResponses.push(response.status);
        return response;
      });
      const get=ScopedClient.prototype.get, reset=Spaces.prototype.reset;
      ScopedClient.prototype.get=async function(...args) {
        const row={done:false}; protectedReads.push(row);
        try { return await get.apply(this,args); } finally { row.done=true; }
      };
      Spaces.prototype.reset=function() {
        reset.call(this);
        window.erasedPrivate={choices:this.choices.length, selected:this.selected.length};
      };
      AbortController.prototype.abort=function() {};
    }""")


def assert_protected_ui_erased(page):
    expect(page.locator("#spaces")).to_be_hidden()
    for selector in ("#capabilities", "#space-options", "#index-status", "#spaces-message"):
        expect(page.locator(selector)).to_be_empty()
    expect(page.locator("#active-selection")).to_have_text("No spaces selected")
    expect(page.locator("#single-space")).to_have_value("")
    expect(page.locator("#single-space option")).to_have_count(1)
    expect(page.locator("#selection-form")).to_be_hidden()
    expect(page.locator("#api-key")).to_have_value("")
    assert page.evaluate("erasedPrivate") == {"choices": 0, "selected": 0}
    assert "untrusted-401-body" not in page.locator("body").inner_text()


def protected_read_target(page, named, operation):
    if operation == "discovery":
        return named["url"] + "/api/v1/knowledge/spaces", page.locator("#reload-spaces")
    return named["url"] + "/api/v1/knowledge/spaces/status?spaces=default", page.locator("#refresh-status")


def fulfill_protected_401(route, body_kind):
    body, content_type = PROTECTED_401_BODIES[body_kind]
    route.fulfill(status=401, body=body, content_type=content_type, headers={"Cache-Control": "no-store"})


def hold_protected_401(page, target):
    held = []
    page.evaluate("window.protected401Held=false")

    def intercept(route):
        held.append(route)
        route.request.frame.evaluate("window.protected401Held=true")

    page.route(target, intercept, times=1)
    return held


@pytest.mark.parametrize("operation", ["discovery", "status"])
@pytest.mark.parametrize("body_kind", PROTECTED_401_BODIES)
def test_current_protected_read_401_erases_before_cleanup(named_context, named, operation, body_kind):
    owner = named_context.new_page()
    owner.goto(named["url"] + "/health/live")
    page = open_page(named_context, named)
    login(page)
    page.locator("#choose-multiple").click()
    expect(page.locator("#space-options input")).to_have_count(2)
    observe_protected_reads(page)
    original, clean = session_cookie(named_context), journal(page)
    owner.evaluate("""lock => {
      window.lockEntered=false;
      void navigator.locks.request(lock, () => new Promise(resolve => {
        window.release401Lock=resolve; window.lockEntered=true;
      }));
    }""", LOCK)
    owner.wait_for_function("() => window.lockEntered")
    barrier = named["barrier"]
    barrier.arm("DELETE")
    target, trigger = protected_read_target(page, named, operation)
    page.route(target, lambda route: fulfill_protected_401(route, body_kind), times=1)
    try:
        trigger.click()
        # The native mutation lock is unavailable: erasure cannot depend on
        # acquiring it, a complete JSON body, or asynchronous cookie deletion.
        assert_protected_ui_erased(page)
        page.wait_for_function("""async lock =>
          (await navigator.locks.query()).pending.some(row=>row.name===lock)""", arg=LOCK)
        assert not barrier.entered.is_set()
        assert session_cookie(named_context) == original and journal(page) == clean
        assert not any(method == "DELETE" for method, _ in named["inventory"])
        owner.evaluate("release401Lock()")
        assert barrier.entered.wait(10)
        assert_protected_ui_erased(page)
        assert journal(page)["phase"] == "deleting" and session_cookie(named_context) == original
        barrier.release.set()
        page.wait_for_function("() => window.protectedReads[0].done")
        expect(page.locator("#session-message")).to_contain_text("removal confirmed")
        assert_protected_ui_erased(page)
        assert not session_cookie(named_context) and journal(page)["phase"] == "locked"
        assert sum(method == "DELETE" for method, _ in named["inventory"]) == 1
    finally:
        owner.evaluate("release401Lock()")
        barrier.release.set()


@pytest.mark.parametrize("reauthenticate_elsewhere", [False, True], ids=["same_tab", "other_tab"])
@pytest.mark.parametrize("operation", ["discovery", "status"])
@pytest.mark.parametrize("body_kind", PROTECTED_401_BODIES)
def test_stale_protected_read_401_after_reauthentication(
    named_context, named, operation, body_kind, reauthenticate_elsewhere
):
    page = open_page(named_context, named)
    login(page)
    observe_protected_reads(page)
    actor = page
    if reauthenticate_elsewhere:
        # Keep the old tab's epoch/snapshot unchanged: the shared intent/revision
        # check, independently of notifications and epochs, must fence its 401.
        page.evaluate(DROP_NOTIFICATIONS)
        actor = open_page(named_context, named)
        expect(actor.locator("#index-status")).to_contain_text("default: disabled")
    target, trigger = protected_read_target(page, named, operation)
    held = hold_protected_401(page, target)
    trigger.click()
    page.wait_for_function("() => window.protected401Held")
    assert len(held) == 1
    actor.locator("#logout").click()
    expect(actor.locator("#session-message")).to_contain_text("removal confirmed")
    login(actor)
    current, clean = session_cookie(named_context), journal(actor)
    deletes = sum(method == "DELETE" for method, _ in named["inventory"])
    fulfill_protected_401(held.pop(), body_kind)
    page.wait_for_function("() => window.protectedReads[0].done")
    expect(actor.locator("#spaces")).to_be_visible()
    expect(actor.locator("#index-status")).to_contain_text("default: disabled")
    expect(actor.locator("#active-selection")).to_have_text("Active selection: default")
    if reauthenticate_elsewhere:
        assert_protected_ui_erased(page)
    assert session_cookie(named_context) == current and journal(actor) == clean
    assert sum(method == "DELETE" for method, _ in named["inventory"]) == deletes


@pytest.mark.parametrize("operation", ["discovery", "status"])
def test_native_protected_read_401_truncated_body(named_context, named, operation):
    owner = named_context.new_page()
    owner.goto(named["url"] + "/health/live")
    page = open_page(named_context, named)
    login(page)
    observe_protected_reads(page)
    original, clean = session_cookie(named_context), journal(page)
    owner.evaluate("""lock => {
      window.lockEntered=false;
      void navigator.locks.request(lock, () => new Promise(resolve => {
        window.release401Lock=resolve; window.lockEntered=true;
      }));
    }""", LOCK)
    owner.wait_for_function("() => window.lockEntered")
    target, trigger = protected_read_target(page, named, operation)
    barrier = named["barrier"]
    barrier.arm("GET", 401, path=urlsplit(target).path, stage="body", truncate=True)
    try:
        trigger.click()
        assert barrier.entered.wait(10)
        assert b"set-cookie" not in barrier.headers
        # Engines may expose fetch's Response only after receiving body bytes.
        # Cut the actual HTTP body, then synchronize on the native fetch result.
        barrier.release.set()
        assert barrier.finished.wait(10)
        page.wait_for_function("() => window.protectedResponses.includes(401)", timeout=5000)
        assert_protected_ui_erased(page)
        page.wait_for_function("""async lock =>
          (await navigator.locks.query()).pending.some(row=>row.name===lock)""", arg=LOCK)
        assert session_cookie(named_context) == original and journal(page) == clean
        assert not any(method == "DELETE" for method, _ in named["inventory"])
        owner.evaluate("release401Lock()")
        page.wait_for_function("() => window.protectedReads[0].done")
        assert_protected_ui_erased(page)
        assert not session_cookie(named_context) and journal(page)["phase"] == "locked"
        assert sum(method == "DELETE" for method, _ in named["inventory"]) == 1
    finally:
        owner.evaluate("release401Lock()")
        barrier.release.set()


@pytest.mark.parametrize("body_kind", PROTECTED_401_BODIES)
def test_delayed_protected_read_401_during_logout(named_context, named, body_kind):
    page = open_page(named_context, named)
    login(page)
    observe_protected_reads(page)
    target, trigger = protected_read_target(page, named, "status")
    held = hold_protected_401(page, target)
    trigger.click()
    page.wait_for_function("() => window.protected401Held")
    assert len(held) == 1
    original = session_cookie(named_context)
    barrier = named["barrier"]
    barrier.arm("DELETE")
    page.locator("#logout").click()
    assert barrier.entered.wait(10)
    deleting = journal(page)
    fulfill_protected_401(held.pop(), body_kind)
    page.wait_for_function("() => window.protectedReads[0].done")
    assert_protected_ui_erased(page)
    assert journal(page) == deleting and session_cookie(named_context) == original
    assert sum(method == "DELETE" for method, _ in named["inventory"]) == 1
    barrier.release.set()
    expect(page.locator("#session-message")).to_contain_text("removal confirmed")
    assert not session_cookie(named_context) and journal(page)["phase"] == "locked"
    assert sum(method == "DELETE" for method, _ in named["inventory"]) == 1


@pytest.mark.parametrize("delete_status,phase", [(429, "logout_pending"), (503, "uncertain")])
def test_protected_read_401_failed_coordinated_delete(named_context, named, delete_status, phase):
    page = open_page(named_context, named)
    login(page)
    observe_protected_reads(page)
    original = session_cookie(named_context)
    target, trigger = protected_read_target(page, named, "status")
    page.route(target, lambda route: fulfill_protected_401(route, "truncated"), times=1)
    barrier = named["barrier"]
    barrier.arm("DELETE", delete_status)
    barrier.release.set()
    trigger.click()
    page.wait_for_function("() => window.protectedReads[0].done")
    assert_protected_ui_erased(page)
    assert journal(page)["phase"] == phase and session_cookie(named_context) == original
    expect(page.locator("#session-message")).to_contain_text(
        "removal not confirmed" if delete_status == 429 else "Session outcome uncertain")
    assert sum(method == "DELETE" for method, _ in named["inventory"]) == 1
    page.reload()
    expect(page.locator("#spaces")).to_be_hidden()
    expect(page.locator("#unlock-form")).to_be_hidden()
    assert session_cookie(named_context) == original and journal(page)["phase"] == phase
    assert sum(method == "DELETE" for method, _ in named["inventory"]) == 1
    if delete_status == 429:
        page.get_by_role("button", name="Retry logout", exact=True).click()
        expect(page.locator("#session-message")).to_contain_text("removal confirmed")
        assert not session_cookie(named_context) and journal(page)["phase"] == "locked"
        assert sum(method == "DELETE" for method, _ in named["inventory"]) == 2


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_protected_read_non_json_non_401_is_safe_unavailability(named_context, named, status):
    page = open_page(named_context, named)
    login(page)
    observe_protected_reads(page)
    original, clean = session_cookie(named_context), journal(page)
    target, trigger = protected_read_target(page, named, "status")
    page.route(target, lambda route: route.fulfill(
        status=status, content_type="text/html", body="<html>untrusted-error-body</html>"), times=1)
    trigger.click()
    page.wait_for_function("() => window.protectedReads[0].done")
    expect(page.locator("#spaces-message")).to_contain_text("Spaces unavailable")
    expect(page.locator("#spaces")).to_be_visible()
    expect(page.locator("#active-selection")).to_have_text("Active selection: default")
    expect(page.locator("#index-status")).to_be_empty()
    assert "untrusted-error-body" not in page.locator("body").inner_text()
    assert session_cookie(named_context) == original and journal(page) == clean
    assert not any(method == "DELETE" for method, _ in named["inventory"])


def test_cookie_without_history_is_not_silently_adopted(named_context, named, browser):
    page = open_page(named_context, named)
    login(page)
    isolated = browser.new_context(ignore_https_errors=True, proxy=named["proxy"])
    try:
        isolated.add_cookies(session_cookie(named_context))
        start = len(named["inventory"])
        b = open_page(isolated, named)
        expect(b.locator("#unlock-form")).to_be_visible()
        assert not any(path.endswith("/ui/session") or "/api/" in path for _, path in named["inventory"][start:])
        expect(b.locator("#spaces")).to_be_hidden()
    finally:
        isolated.close()


def test_responsive_keyboard_selection(named_context, named):
    page = open_page(named_context, named)
    assert page.locator(".app-header__identity").count() == 0
    assert page.locator("h1").evaluate("e => getComputedStyle(e).overflowWrap") == "normal"
    page.set_viewport_size({"width": 320, "height": 700})
    page.locator("#api-key").focus()
    page.keyboard.type(KEY)
    page.keyboard.press("Enter")
    expect(page.locator("#spaces")).to_be_visible()
    expect(page.locator("#index-status")).to_contain_text("default: disabled")
    page.locator("#choose-multiple").focus()
    page.keyboard.press("Enter")
    page.locator('#space-options input[value="research"]').focus()
    page.keyboard.press("Space")
    page.get_by_role("button", name="Apply", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator("#index-status")).to_contain_text("research: disabled")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for width in (320, 375, 1280):
        page.set_viewport_size({"width": width, "height": 800})
        expect(page.locator("h1")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#choose-multiple").focus()
        expect(page.locator("#choose-multiple")).to_be_focused()
        assert page.locator("#choose-multiple").evaluate("e => getComputedStyle(e).outlineStyle") != "none"


def test_stale_selected_status_never_overwrites_selection(named_context, named):
    page = open_page(named_context, named)
    login(page)
    page.evaluate("AbortController.prototype.abort = function() {}")
    barrier = named["barrier"]
    barrier.arm("GET", path="/api/v1/knowledge/spaces/status")
    page.get_by_role("button", name="Refresh status", exact=True).click()
    assert barrier.entered.wait(10)
    page.locator("#single-space").select_option("research")
    expect(page.locator("#index-status")).to_contain_text("research: disabled")
    barrier.release.set()
    expect(page.locator("#active-selection")).to_have_text("Active selection: research")
    expect(page.locator("#index-status")).not_to_contain_text("default:")


@pytest.mark.parametrize("phase", ["posting", "deleting", "uncertain", "corrupt"])
def test_durable_journal_never_expires_into_permission(named_context, named, phase):
    page = open_page(named_context, named)
    login(page)
    value = journal(page)
    value["phase"] = phase
    value["mutation"] = "a" * 32
    page.evaluate("args => localStorage.setItem(args[0], args[1])", [JOURNAL, json.dumps(value)])
    start = len(named["inventory"])
    page.reload()
    expect(page.locator("#session-message")).to_contain_text(
        "Named browser sessions unavailable" if phase == "corrupt" else "Session outcome uncertain"
    )
    assert not any(path.endswith("/ui/session") or "/api/" in path for _, path in named["inventory"][start:])
    expect(page.locator("#spaces")).to_be_hidden()


@pytest.mark.parametrize("prefix", ["", "/proxy"])
@pytest.mark.parametrize("denied", [("default",), ("default", "research")])
def test_denied_default_and_empty_discovery(browser, tmp_path, prefix, denied):
    with named_server(tmp_path, prefix, denied=denied) as server:
        context = browser.new_context(ignore_https_errors=True, proxy=server["proxy"])
        try:
            page = open_page(context, server)
            page.locator("#api-key").fill(KEY)
            page.get_by_role("button", name="Unlock", exact=True).click()
            expect(page.locator("#spaces")).to_be_visible()
            expect(page.locator("#active-selection")).to_have_text("No spaces selected")
            if len(denied) == 2:
                expect(page.locator("#spaces-message")).to_have_text("No readable spaces available")
                assert page.locator("#single-space option").count() == 1
            else:
                expect(page.locator("#single-space option")).to_have_count(2)
                page.locator("#single-space").select_option("research")
                expect(page.locator("#index-status")).to_contain_text("research: disabled")
        finally:
            context.close()


def test_new_logout_intent_fences_older_delete_completion(named_context, named):
    a = open_page(named_context, named)
    login(a)
    b = open_page(named_context, named)
    expect(b.locator("#spaces")).to_be_visible()
    barrier = named["barrier"]
    barrier.arm("DELETE")
    a.get_by_role("button", name="Log out", exact=True).click()
    assert barrier.entered.wait(10)
    first = a.evaluate("key => localStorage.getItem(key)", INTENT)
    b.locator("#logout").click()
    b.wait_for_function("args => localStorage.getItem(args[0]) !== args[1]", arg=[INTENT, first])
    barrier.release.set()
    expect(b.locator("#session-message")).to_contain_text("removal confirmed")
    expect(a.locator("#session-message")).not_to_contain_text("removal confirmed")
    assert not session_cookie(named_context)


def test_hidden_post_completion_waits_for_validation_before_render(named_context, named):
    page = open_page(named_context, named)
    barrier = named["barrier"]
    barrier.arm("POST")
    page.locator("#api-key").fill(KEY)
    page.get_by_role("button", name="Unlock", exact=True).click()
    assert barrier.entered.wait(10)
    # Deterministic visibility branch injection; this does not qualify BFCache
    # or actual suspended-page behavior in a deployment/browser profile.
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {get: () => true, configurable: true}); "
        "document.dispatchEvent(new Event('visibilitychange'))"
    )
    barrier.release.set()
    page.wait_for_function("key => JSON.parse(localStorage.getItem(key)).phase === 'restorable'", arg=JOURNAL)
    expect(page.locator("#spaces")).to_be_hidden()
    assert session_cookie(named_context)
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {get: () => false, configurable: true}); "
        "document.dispatchEvent(new Event('visibilitychange'))"
    )
    expect(page.locator("#spaces")).to_be_visible()
    expect(page.locator("#index-status")).to_contain_text("default: disabled")


@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize("failure", ["tcp", "partial-body"])
def test_native_mutation_transport_failure_quarantines(named_context, named, method, failure):
    named_context.add_init_script("""
      window.mutationEvidence=[];
      const mutationFetch=window.fetch;
      window.fetch=(url,options={}) => mutationFetch(url,options).then(response => {
        if (['POST','DELETE'].includes(options.method)) {
          mutationEvidence.push({event:'headers',method:options.method,status:response.status});
          const parse=response.json.bind(response);
          response.json=()=>parse().then(value=>{
            mutationEvidence.push({event:'complete-json',method:options.method});return value;
          },error=>{mutationEvidence.push({event:'body-failed',method:options.method});throw error});
        }
        return response;
      },error=>{mutationEvidence.push({event:'fetch-failed',method:options.method});throw error});
    """)
    page = open_page(named_context, named)
    if method == "DELETE":
        login(page)
    barrier = named["barrier"]
    observed = []
    page.on("requestfailed", lambda request: observed.append(dict(method=request.method, error=request.failure))
            if request.url.endswith("/ui/session") else None)
    barrier.arm(method, stage="body" if failure == "partial-body" else "headers", truncate=failure == "partial-body")
    if method == "POST":
        page.locator("#api-key").fill(KEY)
        page.get_by_role("button", name="Unlock", exact=True).click()
    else:
        page.locator("#logout").click()
    assert barrier.entered.wait(10)
    if failure == "partial-body":
        # The real handler has sent its headers; native jar effects are checked
        # after the browser reports the truncated-body failure below.
        assert b"set-cookie" in barrier.headers
    else:
        ack = named["proxy_server"].disconnect()
        assert ack["outstanding"] == 0 and not named["proxy_server"].connections
        assert named["proxy_server"].events[ack["sequence"]]["event"] == "disconnect-ack"
        # The blocked response is released only AFTER native socket disposal.
    barrier.release.set()
    expect(page.locator("#session-message")).to_contain_text("Session outcome uncertain")
    assert journal(page)["phase"] == "uncertain"
    assert observed and observed[-1]["method"] == method
    facts = page.evaluate("mutationEvidence")
    assert not any(row["event"] == "complete-json" and row["method"] == method for row in facts)
    assert any(row["event"] in {"fetch-failed", "body-failed"} and row["method"] == method for row in facts)
    if failure == "tcp":
        assert bool(session_cookie(named_context)) == (method == "DELETE")
        proxy = named["proxy_server"]
        assert not any(row["event"] == "forwarded" and row["generation"] == ack["generation"]
                       for row in proxy.events[ack["sequence"] + 1:])
        proxy.recover()
    if failure == "partial-body":
        assert bool(session_cookie(named_context)) == (method == "POST")
    start = len(named["inventory"])
    uncertain = journal(page)
    page.reload()
    expect(page.locator("#session-message")).to_contain_text("Session outcome uncertain")
    assert not any(path.endswith("/ui/session") or "/api/" in path for _, path in named["inventory"][start:])
    sibling = open_page(named_context, named)
    for locked in (page, sibling):
        expect(locked.locator("#session-message")).to_contain_text("Session outcome uncertain")
        expect(locked.locator("#unlock-form")).to_be_hidden()
        expect(locked.locator("#spaces")).to_be_hidden()
        assert journal(locked) == uncertain
    assert not any(path.endswith("/ui/session") or "/api/" in path for _, path in named["inventory"][start:])
    assert sum(verb == method and path.endswith("/ui/session") for verb, path in named["inventory"]) == 1
    print("Native ambiguous mutation:", json.dumps(dict(method=method, failure=failure, native=observed,
          observations=facts, cookie_present=bool(session_cookie(named_context)), phase=uncertain["phase"])))


def test_native_complete_delete_settles_normally(named_context, named):
    page = open_page(named_context, named)
    login(page)
    with page.expect_response(lambda response: response.request.method == "DELETE") as response:
        page.locator("#logout").click()
    assert response.value.status == 200 and response.value.json() == {"authenticated": False}
    expect(page.locator("#session-message")).to_contain_text("removal confirmed")
    assert journal(page)["phase"] == "locked" and not session_cookie(named_context)
    assert sum(method == "DELETE" and path.endswith("/ui/session") for method, path in named["inventory"]) == 1
    page.reload()
    expect(page.locator("#unlock-form")).to_be_visible()
    expect(page.locator("#spaces")).to_be_hidden()


def test_missing_notifications_fences_before_protected_fetch(named_context, named):
    a = open_page(named_context, named)
    login(a)
    b = named_context.new_page()
    b.add_init_script(DROP_NOTIFICATIONS)
    b.goto(named["url"] + "/ui/")
    expect(b.locator("#index-status")).to_contain_text("default: disabled")
    a.locator("#logout").click()
    expect(a.locator("#session-message")).to_contain_text("removal confirmed")
    assert not session_cookie(named_context)
    start = len(named["inventory"])
    b.locator("#refresh-status").click()
    expect(b.locator("#spaces")).to_be_hidden()
    assert not any("/api/" in path for _, path in named["inventory"][start:])


def test_missing_notifications_rechecks_after_native_json(named_context, named):
    a = open_page(named_context, named)
    login(a)
    b = named_context.new_page()
    b.add_init_script(DROP_NOTIFICATIONS + OBSERVE_JSON)
    barrier = named["barrier"]
    barrier.arm("GET", stage="body")
    b.goto(named["url"] + "/ui/")
    assert barrier.entered.wait(10)
    assert b"set-cookie" not in barrier.headers
    a.locator("#logout").click()
    expect(a.locator("#session-message")).to_contain_text("removal confirmed")
    clean = journal(a)
    barrier.release.set()
    b.wait_for_function("sessionJSON.includes(200)")
    expect(b.locator("#spaces")).to_be_hidden()
    expect(b.locator("#unlock-form")).to_be_visible()
    assert journal(b) == clean and not session_cookie(named_context)


@pytest.mark.parametrize("old_status", [200, 401])
def test_native_old_json_after_rotation_and_new_login(named_context, named, old_status):
    from pydantic import SecretStr

    a = open_page(named_context, named)
    login(a)
    if old_status == 401:
        settings = named["app"].state.settings
        named["app"].state.settings = settings.model_copy(
            update={"api_key": SecretStr("rotated-synthetic-key"), "previous_api_key": SecretStr(KEY)}
        )
    b = named_context.new_page()
    b.add_init_script(DROP_NOTIFICATIONS + OBSERVE_JSON)
    barrier = named["barrier"]
    barrier.arm("GET", stage="body")
    b.goto(named["url"] + "/ui/")
    assert barrier.entered.wait(10)
    assert b"set-cookie" not in barrier.headers
    a.locator("#logout").click()
    expect(a.locator("#unlock-form")).to_be_visible()
    login(a)  # Previous-key unlock signs with the effective new current key.
    cookies, clean = session_cookie(named_context), journal(a)
    deletes = sum(method == "DELETE" for method, _ in named["inventory"])
    barrier.release.set()
    b.wait_for_function("status => sessionJSON.includes(status)", arg=old_status)
    b.evaluate("lock => navigator.locks.request(lock, () => true)", LOCK)
    expect(b.locator("#spaces")).to_be_hidden()
    assert session_cookie(named_context) == cookies and journal(a) == clean
    assert sum(method == "DELETE" for method, _ in named["inventory"]) == deletes


def test_two_deliberate_logins_serialize_without_automatic_replay(named_context, named):
    a = open_page(named_context, named)
    b = open_page(named_context, named)
    barrier = named["barrier"]
    barrier.arm("POST")
    a.locator("#api-key").fill(KEY)
    a.get_by_role("button", name="Unlock", exact=True).click()
    assert barrier.entered.wait(10)
    posting = journal(a)
    b.locator("#api-key").fill(KEY)
    b.get_by_role("button", name="Unlock", exact=True).click()
    expect(b.locator("#session-message")).to_contain_text("pending")
    assert sum(method == "POST" for method, _ in named["inventory"]) == 1
    barrier.release.set()
    expect(b.locator("#spaces")).to_be_visible()
    assert journal(b)["phase"] == "restorable" and journal(b)["revision"] != posting["revision"]
    assert sum(method == "POST" for method, _ in named["inventory"]) == 2
    assert len(session_cookie(named_context)) == 1


def test_login_during_live_delete_is_not_replayed_after_completion(named_context, named):
    a = open_page(named_context, named)
    b = named_context.new_page()
    b.add_init_script(DROP_NOTIFICATIONS)
    b.goto(named["url"] + "/ui/")
    expect(b.locator("#unlock-form")).to_be_visible()
    login(a)
    barrier = named["barrier"]
    barrier.arm("DELETE")
    a.locator("#logout").click()
    assert barrier.entered.wait(10)
    b.locator("#api-key").fill(KEY)
    b.get_by_role("button", name="Unlock", exact=True).click()
    assert sum(method == "POST" for method, _ in named["inventory"]) == 1
    barrier.release.set()
    expect(a.locator("#session-message")).to_contain_text("removal confirmed")
    b.evaluate("lock => navigator.locks.request(lock, () => true)", LOCK)
    expect(b.locator("#spaces")).to_be_hidden()
    assert not session_cookie(named_context)
    assert sum(method == "POST" for method, _ in named["inventory"]) == 1
    assert journal(b)["phase"] == "locked"


def test_native_context_partition_isolates_cookie_storage_and_locks(named_context, named, browser):
    a = open_page(named_context, named)
    login(a)
    clean, cookies = journal(a), session_cookie(named_context)
    isolated = browser.new_context(ignore_https_errors=True, proxy=named["proxy"])
    try:
        b = open_page(isolated, named)
        expect(b.locator("#unlock-form")).to_be_visible()
        assert not session_cookie(isolated)
        assert b.evaluate("key => localStorage.getItem(key)", JOURNAL) is None
        a.evaluate(
            "lock => {window.testLockHeld=false; navigator.locks.request(lock, async () => {"
            "window.testLockHeld=true; await new Promise(resolve=>window.releaseTestLock=resolve);});}",
            LOCK,
        )
        a.wait_for_function("testLockHeld")
        try:
            # Context B has a different native lock/storage/cookie partition.
            assert b.evaluate("lock => navigator.locks.request(lock, () => true)", LOCK)
            login(b)
            assert len(session_cookie(isolated)) == 1
            assert journal(b) != clean
            assert journal(a) == clean and session_cookie(named_context) == cookies
        finally:
            a.evaluate("releaseTestLock()")
    finally:
        isolated.close()


@pytest.mark.parametrize("receiver_host", ["vaultbridge.example.test", "other.example.test"])
def test_native_other_origin_locks_and_journal_do_not_fence_cookie_delivery(named_context, named, receiver_host):
    from starlette.responses import HTMLResponse

    async def receiver(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                await send({"type": event["type"] + ".complete"})
                if event["type"] == "lifespan.shutdown":
                    return
        else:
            await HTMLResponse("<!doctype html><title>Origin probe</title>", headers={"Connection": "close"})(
                scope, receive, send
            )

    a = open_page(named_context, named)
    login(a)
    clean, cookie = journal(a), session_cookie(named_context)
    with serve(receiver, named["cert"], named["key"]) as port:
        b = named_context.new_page()
        b.goto(f"https://{receiver_host}:{port}/probe")
        assert b.evaluate("keys => keys.map(key=>localStorage.getItem(key))", [INTENT, JOURNAL]) == [None, None]
        delivered_scope = [c for c in named_context.cookies([b.url]) if c["name"] == COOKIE]
        assert bool(delivered_scope) == (receiver_host == "vaultbridge.example.test")
        a.evaluate(
            "lock => {window.originLockHeld=false; navigator.locks.request(lock, async()=>{"
            "originLockHeld=true; await new Promise(resolve=>window.releaseOriginLock=resolve);});}",
            LOCK,
        )
        a.wait_for_function("originLockHeld")
        try:
            assert b.evaluate("lock=>navigator.locks.request(lock,{ifAvailable:true},held=>!!held)", LOCK)
            assert journal(a) == clean and session_cookie(named_context) == cookie
        finally:
            a.evaluate("releaseOriginLock()")
            # Stop native speculative connections before the TLS listener.
            named_context.close()


@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize("prefix", ["", "/proxy"])
def test_native_tls_termination_profile(browser, tmp_path, prefix, trusted):
    with named_server(tmp_path, prefix, termination=True, trust_proxy=trusted) as server:
        context = browser.new_context(ignore_https_errors=True, proxy=server["proxy"])
        try:
            page = open_page(context, server)
            if trusted:
                login(page)
                assert session_cookie(context)[0]["secure"]
                assert all(
                    row["scheme"] == "https" for row in server["observations"] if row["path"].endswith("/ui/session")
                )
                page.reload()
                expect(page.locator("#spaces")).to_be_visible()
                page.locator("#logout").click()
                expect(page.locator("#session-message")).to_contain_text("removal confirmed")
                assert not session_cookie(context)
            else:
                page.locator("#api-key").fill(KEY)
                page.get_by_role("button", name="Unlock", exact=True).click()
                expect(page.locator("#session-message")).to_contain_text("Unlock rejected")
                assert not session_cookie(context)
                assert all(
                    row["scheme"] == "http" for row in server["observations"] if row["path"].endswith("/ui/session")
                )
        finally:
            context.close()


@pytest.mark.parametrize("prefix", ["", "/proxy"])
def test_native_insecure_context_is_public_only(browser, tmp_path, prefix):
    with named_server(tmp_path, prefix, plain=True) as server:
        context = browser.new_context(proxy=server["proxy"])
        try:
            page = open_page(context, server)
            assert page.evaluate("isSecureContext") is False
            expect(page.locator("#session-message")).to_contain_text("Named browser sessions unavailable")
            assert not any(path.endswith("/ui/session") or "/api/" in path for _, path in server["inventory"])
            assert not session_cookie(context)
        finally:
            context.close()


def test_native_public_bfcache_erases_unsubmitted_key(playwright, named):
    # Full Chromium supports native BFCache admission of this no-store public
    # document. Its authenticated variant has no-store JS network requests and
    # is tested separately through native history/revalidation on every engine.
    # Keep every production cache/security header; do not synthesize pageshow.
    browser = playwright.chromium.launch(
        channel="chromium",
        ignore_default_args=["--disable-back-forward-cache"],
        args=["--enable-features=CacheControlNoStoreEnterBackForwardCache:level/restore-unless-cookie-change"],
    )
    context = browser.new_context(ignore_https_errors=True, proxy=named["proxy"])
    try:
        context.add_init_script(
            "window.nativePageshow=[]; addEventListener('pageshow', e=>nativePageshow.push(e.persisted));"
        )
        page = open_page(context, named)
        page.locator("#api-key").fill(KEY)
        page.evaluate(
            "url=>{const a=document.createElement('a'); a.id='leave'; a.href=url; "
            "a.textContent='Leave'; document.body.append(a)}",
            named["url"] + "/health/live",
        )
        page.locator("#leave").click()
        page.go_back(wait_until="commit")
        page.wait_for_function("nativePageshow.includes(true)")
        assert page.evaluate("nativePageshow") == [False, True]
        expect(page.locator("#api-key")).to_have_value("")
        expect(page.locator("#spaces")).to_be_hidden()
        assert not session_cookie(context)
        assert not any(path.endswith("/ui/session") for _, path in named["inventory"])
    finally:
        context.close()
        browser.close()


def test_native_history_return_blanks_until_validation(playwright, browser_name, named, record_testsuite_property):
    options = {"ignore_default_args": ["--disable-back-forward-cache"]} if browser_name == "chromium" else {}
    browser = getattr(playwright, browser_name).launch(**options)
    context = browser.new_context(ignore_https_errors=True, proxy=named["proxy"])
    try:
        context.add_init_script(
            "window.nativePageshow=[]; addEventListener('pageshow', e=>nativePageshow.push(e.persisted));"
        )
        page = open_page(context, named)
        login(page)
        clean, cookie = journal(page), session_cookie(context)
        page.evaluate(
            "url=>{const a=document.createElement('a'); a.id='leave'; a.href=url; "
            "a.textContent='Leave'; document.body.append(a)}",
            named["url"] + "/health/live",
        )
        page.locator("#leave").click()
        barrier = named["barrier"]
        barrier.arm("GET", repeat=True)
        page.go_back(wait_until="commit")
        assert barrier.entered.wait(10)
        assert all(b"set-cookie" not in headers for headers in barrier.captured_headers)
        expect(page.locator("#spaces")).to_be_hidden()
        assert session_cookie(context) == cookie and journal(page) == clean
        page.wait_for_function("nativePageshow.length > 0")
        record_testsuite_property(
            "native-pageshow-" + browser_name + ("-prefix" if named["url"].endswith("/proxy") else "-root"),
            json.dumps(page.evaluate("nativePageshow")),
        )
        barrier.release.set()
        expect(page.locator("#spaces")).to_be_visible()
        assert session_cookie(context) == cookie and journal(page) == clean
    finally:
        named["barrier"].release.set()
        context.close()
        browser.close()


def test_native_storage_quota_blocks_cookie_issuance(named_context, named):
    page = open_page(named_context, named)
    quota = page.evaluate("""() => {
      let low=0, high=16*1024*1024, observed=false;
      while (low+1<high) {
        const size=Math.floor((low+high)/2);
        try { localStorage.setItem('test-only-storage-pressure', 'q'.repeat(size)); low=size; }
        catch(error) { if (error.name!=='QuotaExceededError') throw error; observed=true; high=size; }
      }
      return observed;
    }""")
    assert quota, "Native storage quota was not reached within the bounded fixture allocation"
    start = len(named["inventory"])
    page.locator("#api-key").fill(KEY)
    page.get_by_role("button", name="Unlock", exact=True).click()
    expect(page.locator("#session-message")).to_contain_text("Named browser sessions unavailable")
    assert not any(path.endswith("/ui/session") for _, path in named["inventory"][start:])
    assert not session_cookie(named_context)
    expect(page.locator("#spaces")).to_be_hidden()


def test_control_record_write_failure_after_native_cookie_quarantines(named_context, named):
    page = open_page(named_context, named)
    barrier = named["barrier"]
    barrier.arm("POST", stage="body")
    page.locator("#api-key").fill(KEY)
    page.get_by_role("button", name="Unlock", exact=True).click()
    assert barrier.entered.wait(10)
    # Storage failure injection after an actual production issuer sent headers.
    # This complements native quota and partition cases; no cookie is mocked.
    page.evaluate("() => {Storage.prototype.setItem = () => {throw new DOMException('test', 'SecurityError')};}")
    barrier.release.set()
    expect(page.locator("#session-message")).to_contain_text("Session outcome uncertain")
    assert session_cookie(named_context)
    assert journal(page)["phase"] == "posting"
    page.reload()
    expect(page.locator("#session-message")).to_contain_text("Session outcome uncertain")
    expect(page.locator("#spaces")).to_be_hidden()


def test_native_current_401_rotation_cleanup_and_previous_key_unlock(named_context, named):
    from pydantic import SecretStr

    page = open_page(named_context, named)
    login(page)
    original = session_cookie(named_context)
    settings = named["app"].state.settings
    named["app"].state.settings = settings.model_copy(
        update={"api_key": SecretStr("new-current-synthetic-key"), "previous_api_key": SecretStr(KEY)}
    )
    with page.expect_response(lambda r: "/knowledge/spaces/status" in r.url) as failed:
        page.locator("#refresh-status").click()
    assert failed.value.status == 401 and failed.value.header_value("set-cookie") is None
    expect(page.locator("#session-message")).to_contain_text("removal confirmed")
    assert not session_cookie(named_context)
    login(page)
    assert session_cookie(named_context)[0]["value"] != original[0]["value"]
    assert journal(page)["phase"] == "restorable"


def test_native_browser_profile_reopen_without_renewal(playwright, browser_name, named, tmp_path):
    browser_type = getattr(playwright, browser_name)
    profile = tmp_path / "disposable-browser-profile"
    options = dict(user_data_dir=str(profile), ignore_https_errors=True, proxy=named["proxy"])
    first = browser_type.launch_persistent_context(**options)
    try:
        page = open_page(first, named)
        login(page)
        cookie, clean = session_cookie(first), journal(page)
    finally:
        first.close()
    second = browser_type.launch_persistent_context(**options)
    try:
        page = second.new_page()
        with page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith("/ui/session")) as restored:
            page.goto(named["url"] + "/ui/")
        expect(page.locator("#spaces")).to_be_visible()
        assert restored.value.status == 200 and restored.value.header_value("set-cookie") is None
        assert session_cookie(second) == cookie and journal(page) == clean
    finally:
        second.close()
