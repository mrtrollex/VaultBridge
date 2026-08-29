from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)

_UI_ROOT = Path(__file__).resolve().parent
_ASSET_ROOT = _UI_ROOT / "assets"

UI_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
UI_SECURITY_HEADERS = {
    "Content-Security-Policy": UI_CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@router.api_route("/ui", methods=["GET", "HEAD"], name="ui_redirect")
def redirect_to_dashboard(request: Request) -> RedirectResponse:
    root_path = str(request.scope.get("root_path", "")).rstrip("/")
    return RedirectResponse(
        url=f"{root_path}/ui/",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers=UI_SECURITY_HEADERS,
    )


@router.api_route("/ui/", methods=["GET", "HEAD"], name="ui_dashboard")
def dashboard() -> FileResponse:
    return FileResponse(
        _UI_ROOT / "index.html",
        media_type="text/html",
        headers=UI_SECURITY_HEADERS,
    )


@router.api_route("/ui/assets/app.css", methods=["GET", "HEAD"], name="ui_stylesheet")
def stylesheet() -> FileResponse:
    return FileResponse(
        _ASSET_ROOT / "app.css",
        media_type="text/css",
        headers=UI_SECURITY_HEADERS,
    )


@router.api_route("/ui/assets/app.js", methods=["GET", "HEAD"], name="ui_script")
def script() -> FileResponse:
    return FileResponse(
        _ASSET_ROOT / "app.js",
        media_type="text/javascript",
        headers=UI_SECURITY_HEADERS,
    )
