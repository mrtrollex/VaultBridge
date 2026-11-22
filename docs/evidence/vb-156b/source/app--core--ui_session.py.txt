from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette._utils import get_route_path
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings

UI_SESSION_COOKIE_NAME = "vaultbridge_ui_session"
UI_SESSION_LIFETIME_SECONDS = 7 * 24 * 60 * 60
UI_REQUEST_HEADER = "X-VaultBridge-UI-Request"
UI_REQUEST_HEADER_VALUE = "1"

_TOKEN_VERSION = "v1"
_SIGNING_KEY_CONTEXT = b"VaultBridge UI session signing key v1"
_MAX_TOKEN_LENGTH = 160
_UI_SESSION_PATHS = frozenset(("/ui/session", "/ui/session/"))

# ADR 0011: methods and application paths, never a prefix grant.
NAMED_COOKIE_READ_ROUTES = frozenset({
    ("GET", "/api/v1/notes/list"), ("GET", "/api/v1/notes/read"),
    ("POST", "/api/v1/notes/search"), ("POST", "/api/v1/notes/related"),
    ("POST", "/api/v1/knowledge/query"), ("GET", "/api/v1/notes/links"),
    ("GET", "/api/v1/notes/backlinks"), ("GET", "/api/v1/knowledge/spaces"),
    ("GET", "/api/v1/knowledge/spaces/status"),
})


def _origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or value != f"{parsed.scheme}://{parsed.netloc}" or parsed.netloc.endswith(":")
            or any(char.isspace() or char == "," for char in value)):
        raise ValueError
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


def guard_named_ui_request(request):
    """Static browser boundary; called once after the direct-peer limiter."""
    headers = request.headers
    valid = headers.getlist("x-vaultbridge-ui-request") == [UI_REQUEST_HEADER_VALUE]
    origins = headers.getlist("origin")
    sites = headers.getlist("sec-fetch-site")
    valid = valid and len(origins) <= 1 and (not sites or sites == ["same-origin"])
    if origins:
        try:
            valid = valid and _origin(origins[0]) == _origin(f"{request.url.scheme}://{request.url.netloc}")
        except (ValueError, UnicodeError):
            valid = False
    if not valid:
        raise HTTPException(403, "invalid_ui_request")


def named_cookie_authenticated(request, settings):
    if (request.method, get_route_path(request.scope)) not in NAMED_COOKIE_READ_ROUTES:
        return False
    if not validate_ui_session_token(request.cookies.get(UI_SESSION_COOKIE_NAME), settings):
        return False
    # Missing marker stays an authentication failure. Duplicate markers are a
    # guard rejection even if their first value happens to be valid.
    if not request.headers.getlist("x-vaultbridge-ui-request"):
        return False
    guard_named_ui_request(request)
    return True


class UISessionNoStoreMiddleware:
    """Set one no-store header on every response for the UI-session paths."""

    def __init__(self, app: ASGIApp, *, named=False) -> None:
        self.app = app
        self.named = named

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = get_route_path(scope) if scope["type"] == "http" else ""
        named_private = self.named and (path == "/ui" or path.startswith("/ui/") or
                                       path.rstrip("/") in {route for _, route in NAMED_COOKIE_READ_ROUTES})
        if scope["type"] != "http" or (not named_private and scope.get("path") not in _UI_SESSION_PATHS):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", []))}
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)


def _signing_key(settings: Settings) -> bytes | None:
    api_key = settings.api_key.get_secret_value()
    if not api_key:
        return None
    return hmac.new(api_key.encode("utf-8"), _SIGNING_KEY_CONTEXT, hashlib.sha256).digest()


def create_ui_session_token(settings: Settings, *, now: int | None = None) -> str:
    signing_key = _signing_key(settings)
    if signing_key is None:
        raise ValueError("Current API key is required for UI sessions")
    issued_at = int(time.time()) if now is None else now
    payload = f"{_TOKEN_VERSION}.{issued_at}.{issued_at + UI_SESSION_LIFETIME_SECONDS}"
    signature = hmac.new(signing_key, payload.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{payload}.{encoded_signature}"


def validate_ui_session_token(
    token: str | None,
    settings: Settings,
    *,
    now: int | None = None,
) -> bool:
    if token is None or not token or len(token) > _MAX_TOKEN_LENGTH or not token.isascii():
        return False
    parts = token.split(".")
    if len(parts) != 4:
        return False
    version, issued_text, expires_text, encoded_signature = parts
    if version != _TOKEN_VERSION or not issued_text.isdigit() or not expires_text.isdigit():
        return False
    if len(issued_text) > 12 or len(expires_text) > 12 or len(encoded_signature) != 43:
        return False

    issued_at = int(issued_text)
    expires_at = int(expires_text)
    current_time = int(time.time()) if now is None else now
    if (
        issued_at > current_time
        or expires_at <= current_time
        or expires_at - issued_at != UI_SESSION_LIFETIME_SECONDS
    ):
        return False

    signing_key = _signing_key(settings)
    if signing_key is None:
        return False
    payload = f"{version}.{issued_text}.{expires_text}"
    expected_signature = base64.urlsafe_b64encode(
        hmac.new(signing_key, payload.encode("ascii"), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(encoded_signature, expected_signature)
