"""Native HTTP cookie qualification, with an application-independent control.

Assertions deliberately remain strict on every engine/platform. A deficient
engine is failed qualification, never an xfail or an application auth fallback.
"""

import json

import pytest
from starlette.responses import HTMLResponse

from tests.e2e.named_server import COOKIE, named_server, serve
from tests.e2e.test_named_dashboard import login, open_page


@pytest.mark.parametrize("prefix", ["", "/proxy"], ids=["root-https", "prefix-https"])
@pytest.mark.parametrize("issuer", ["application", "control"])
def test_native_cookie_scope_and_samesite(browser, new_context, tmp_path, prefix, issuer):
    received = []
    control_name = "synthetic_strict_control"

    async def receiver(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                await send({"type": event["type"] + ".complete"})
                if event["type"] == "lifespan.shutdown":
                    return
        else:
            received.append(
                {
                    "path": scope["path"],
                    "query": scope["query_string"],
                    "method": scope["method"],
                    "headers": dict(scope["headers"]),
                }
            )
            response = HTMLResponse(
                "<!doctype html><title>Disposable cookie receiver</title>",
                headers={"Connection": "close", "Cache-Control": "no-store"},
            )
            if scope["path"] == "/issue":
                response.set_cookie(
                    control_name,
                    "synthetic-only",
                    max_age=604800,
                    secure=True,
                    httponly=True,
                    samesite="strict",
                    path="/",
                )
            await response(scope, receive, send)

    with named_server(tmp_path, prefix) as deployment:
        with serve(receiver, deployment["cert"], deployment["key"]) as tls_port, serve(receiver) as http_port:
            context = new_context(ignore_https_errors=True, proxy=deployment["proxy"])
            try:
                page = open_page(context, deployment)
                receiver_origin = f"https://vaultbridge.example.test:{tls_port}"
                if issuer == "application":
                    with page.expect_response(
                        lambda r: r.request.method == "POST" and r.url.endswith("/ui/session")
                    ) as issued:
                        login(page)
                    response = issued.value
                    cookie_name = COOKIE
                    destination = deployment["url"] + "/health/live"
                    other_port = receiver_origin + "/probe"
                else:
                    response = page.goto(receiver_origin + "/issue")
                    cookie_name = control_name
                    destination = receiver_origin + "/probe"
                    other_port = deployment["url"] + "/health/live"
                header = response.header_value("set-cookie").lower()
                assert all(
                    flag in header for flag in ("httponly", "secure", "samesite=strict", "path=/", "max-age=604800")
                )
                assert "domain=" not in header
                (cookie,) = [c for c in context.cookies() if c["name"] == cookie_name]
                assert cookie["httpOnly"] and cookie["secure"] and cookie["domain"] == "vaultbridge.example.test"
                sibling = f"https://other.example.test:{tls_port}/probe"
                foreign = f"https://foreign.invalid.test:{tls_port}/probe"
                insecure = f"http://vaultbridge.example.test:{http_port}/probe"
                cases = [
                    ("same-host-other-port-navigation", destination, other_port, "navigation", True),
                    ("different-host-same-site-navigation", destination, sibling, "navigation", False),
                    ("same-site-sibling-to-issuer-navigation", sibling, destination, "navigation", True),
                    ("cross-site-navigation", foreign, destination, "navigation", False),
                    ("same-host-other-port-fetch", destination, other_port, "fetch", True),
                    ("same-site-sibling-to-issuer-fetch", sibling, destination, "fetch", True),
                    ("cross-site-fetch", foreign, destination, "fetch", False),
                    ("different-host-same-site-fetch", destination, sibling, "fetch", False),
                    ("secure-cookie-over-http", destination, insecure, "navigation", False),
                ]
                facts, mismatches = [], []
                for name, source, target, action, expected in cases:
                    page.goto(source)
                    assert cookie_name not in page.evaluate("document.cookie")
                    target += "?case=" + name
                    if action == "navigation":
                        page.evaluate(
                            "url => {const a=document.createElement('a'); a.id='probe-link'; "
                            "a.href=url; a.textContent='Navigate'; document.body.append(a)}",
                            target,
                        )
                        page.locator("#probe-link").click()
                    else:
                        page.evaluate(
                            "url => fetch(url,{credentials:'include',mode:'no-cors',cache:'no-store'}).then(r=>r.type)",
                            target,
                        )
                    observations = deployment["observations"] + received
                    (observed,) = [r for r in observations if r["query"] == ("case=" + name).encode()]
                    # Assert actual receiving-server headers, not document.cookie
                    # or a browser inspection API's interpretation of SameSite.
                    headers = observed["headers"]
                    present = cookie_name.encode() + b"=" in headers.get(b"cookie", b"")
                    facts.append(
                        dict(
                            case=name,
                            cookie_received=present,
                            origin=headers.get(b"origin", b"").decode(),
                            fetch_site=headers.get(b"sec-fetch-site", b"").decode(),
                        )
                    )
                    if present != expected:
                        mismatches.append(name)
                print(
                    json.dumps(
                        dict(issuer=issuer, browser=browser.version, stored_same_site=cookie["sameSite"], cases=facts),
                        sort_keys=True,
                    )
                )
                assert not mismatches, f"Native cookie delivery violated Strict/host-only/Secure: {mismatches}"
                assert cookie["sameSite"] == "Strict"
            finally:
                context.close()
