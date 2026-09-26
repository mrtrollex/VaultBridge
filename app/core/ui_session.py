from __future__ import annotations

import base64
import hashlib
import hmac
import time

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


class UISessionNoStoreMiddleware:
    """Set one no-store header on every response for the UI-session paths."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in _UI_SESSION_PATHS:
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
