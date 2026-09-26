from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, SecretStr

from app.api.dependencies import enforce_rate_limit, get_settings
from app.core.config import Settings
from app.core.http_security import verify_api_key
from app.core.ui_session import (
    UI_SESSION_COOKIE_NAME,
    UI_SESSION_LIFETIME_SECONDS,
    create_ui_session_token,
    validate_ui_session_token,
)

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
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_INVALID_SESSION_REQUEST = {"detail": "Invalid session request"}


class UISessionRoute(APIRoute):
    """Keep all UI-session responses non-cacheable and validation errors private."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def private_session_handler(request: Request) -> Response:
            try:
                return await route_handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    content=_INVALID_SESSION_REQUEST,
                    headers=_NO_STORE_HEADERS,
                )
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers={**(exc.headers or {}), **_NO_STORE_HEADERS},
                )

        return private_session_handler


router = APIRouter(include_in_schema=False)
session_router = APIRouter(include_in_schema=False, route_class=UISessionRoute)


class UISessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https"


def _set_session_cookie(response: JSONResponse, request: Request, settings: Settings) -> None:
    expires_at = int(datetime.now(tz=timezone.utc).timestamp()) + UI_SESSION_LIFETIME_SECONDS
    response.set_cookie(
        key=UI_SESSION_COOKIE_NAME,
        value=create_ui_session_token(settings),
        max_age=UI_SESSION_LIFETIME_SECONDS,
        expires=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        path="/",
        secure=_is_https(request),
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(response: JSONResponse, request: Request) -> None:
    response.delete_cookie(
        key=UI_SESSION_COOKIE_NAME,
        path="/",
        secure=_is_https(request),
        httponly=True,
        samesite="strict",
    )


@session_router.post("/ui/session", dependencies=[Depends(enforce_rate_limit)])
def create_session(
    payload: UISessionRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    verify_api_key(payload.api_key.get_secret_value(), settings)
    response = JSONResponse({"authenticated": True}, headers=_NO_STORE_HEADERS)
    _set_session_cookie(response, request, settings)
    return response


@session_router.get("/ui/session")
def restore_session(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    if not validate_ui_session_token(request.cookies.get(UI_SESSION_COOKIE_NAME), settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers=_NO_STORE_HEADERS,
        )
    response = JSONResponse({"authenticated": True}, headers=_NO_STORE_HEADERS)
    _set_session_cookie(response, request, settings)
    return response


@session_router.delete("/ui/session")
def delete_session(request: Request) -> JSONResponse:
    response = JSONResponse({"authenticated": False}, headers=_NO_STORE_HEADERS)
    _clear_session_cookie(response, request)
    return response


router.include_router(session_router)


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


@router.api_route("/ui/assets/overview.js", methods=["GET", "HEAD"], name="ui_overview_script")
def overview_script() -> FileResponse:
    return FileResponse(
        _ASSET_ROOT / "overview.js",
        media_type="text/javascript",
        headers=UI_SECURITY_HEADERS,
    )


@router.api_route("/ui/assets/search.js", methods=["GET", "HEAD"], name="ui_search_script")
def search_script() -> FileResponse:
    return FileResponse(
        _ASSET_ROOT / "search.js",
        media_type="text/javascript",
        headers=UI_SECURITY_HEADERS,
    )


@router.api_route(
    "/ui/assets/vaultbridge-logo.webp",
    methods=["GET", "HEAD"],
    name="ui_brand_logo",
)
def brand_logo() -> FileResponse:
    return FileResponse(
        _ASSET_ROOT / "vaultbridge-logo.webp",
        media_type="image/webp",
        headers=UI_SECURITY_HEADERS,
    )
