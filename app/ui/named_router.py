"""Isolated named shell and explicit nonrenewing session boundary (ADR 0011)."""

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.api.dependencies import enforce_rate_limit
from app.core.http_security import verify_api_key
from app.core.ui_session import UI_SESSION_COOKIE_NAME, guard_named_ui_request, validate_ui_session_token
from app.ui.router import (
    _ASSET_ROOT,
    _UI_ROOT,
    UI_SECURITY_HEADERS,
    UISessionRequest,
    _clear_session_cookie,
    _set_session_cookie,
    redirect_to_dashboard,
)

NAMED_ASSETS = {
    "app.css": "text/css", "vaultbridge-logo.webp": "image/webp",
    "named-app.js": "text/javascript", "named-session.js": "text/javascript",
    "scoped-client.js": "text/javascript", "spaces.js": "text/javascript",
    "browse.js": "text/javascript", "named-search.js": "text/javascript",
    "note-reader.js": "text/javascript", "coverage.js": "text/javascript",
}
NAMED_UI_PATHS = frozenset({"/ui", "/ui/", "/ui/session"} | {
    "/ui/assets/" + name for name in NAMED_ASSETS
})
router = APIRouter(include_in_schema=False)
HEADERS = {**UI_SECURITY_HEADERS, "Cache-Control": "no-store"}


@router.api_route("/ui/session", methods=["POST", "GET", "DELETE"])
async def session(request: Request):
    settings = request.app.state.settings
    try:
        enforce_rate_limit(request, settings, request.app.state.rate_limiter)
        guard_named_ui_request(request)
        if request.method == "POST":
            types = request.headers.getlist("content-type")
            if len(types) != 1 or not re.fullmatch(
                    r'application/json(?:\s*;\s*charset=(?:[a-z0-9._-]+|"[a-z0-9._-]+"))?\s*',
                    types[0], re.IGNORECASE):
                raise HTTPException(415, "invalid_session_request")
            try:
                payload = UISessionRequest.model_validate_json(await request.body())
            except ValueError:
                raise HTTPException(422, "Invalid session request") from None
            verify_api_key(payload.api_key.get_secret_value(), settings)
            response = JSONResponse({"authenticated": True}, headers=HEADERS)
            _set_session_cookie(response, request, settings)
            return response
        if request.method == "DELETE":
            response = JSONResponse({"authenticated": False}, headers=HEADERS)
            _clear_session_cookie(response, request)
            return response
        if not validate_ui_session_token(request.cookies.get(UI_SESSION_COOKIE_NAME), settings):
            raise HTTPException(401, "Authentication required")
        return JSONResponse({"authenticated": True}, headers=HEADERS)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers={**(exc.headers or {}), **HEADERS})


router.add_api_route("/ui", redirect_to_dashboard, methods=["GET", "HEAD"])


@router.api_route("/ui/", methods=["GET", "HEAD"])
def dashboard():
    return FileResponse(_UI_ROOT / "named.html", media_type="text/html", headers=HEADERS)


def _asset(name, media_type):
    def serve():
        return FileResponse(_ASSET_ROOT / name, media_type=media_type, headers=UI_SECURITY_HEADERS)
    return serve


for _name, _media_type in NAMED_ASSETS.items():
    router.add_api_route("/ui/assets/" + _name, _asset(_name, _media_type), methods=["GET", "HEAD"])
