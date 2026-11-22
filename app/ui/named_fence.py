"""Explicit named UI gate, before body/session work or mounted MCP dispatch."""

from starlette._utils import get_route_path
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from app.core.ui_session import NAMED_COOKIE_READ_ROUTES
from app.ui.named_router import NAMED_UI_PATHS


class NamedUIFence:
    def __init__(self, app, *, named):
        self.app, self.named = app, named

    async def __call__(self, scope, receive, send):
        if self.named and scope["type"] == "http":
            path = get_route_path(scope)
            if (path in {"/api/v1/knowledge/spaces", "/api/v1/knowledge/spaces/status"}
                    and scope["method"] != "GET"):
                await JSONResponse({"detail": "Method Not Allowed"}, status_code=405,
                                   headers={"Cache-Control": "no-store", "Allow": "GET"})(scope, receive, send)
                return
            if path == "/ui" or path.startswith("/ui/"):
                methods = {"POST", "GET", "DELETE"} if path == "/ui/session" else {"GET", "HEAD"}
                if path not in NAMED_UI_PATHS or scope["method"] not in methods:
                    code = 404 if path not in NAMED_UI_PATHS else 405
                    await JSONResponse({"detail": "Not Found" if code == 404 else "Method Not Allowed"},
                                       status_code=code, headers={"Cache-Control": "no-store",
                                           **({"Allow": ", ".join(sorted(methods))} if code == 405 else {})})(
                                               scope, receive, send)
                    return
            if path in NAMED_UI_PATHS or path.rstrip("/") in {route for _, route in NAMED_COOKIE_READ_ROUTES}:
                async def no_store(message):
                    if message["type"] == "http.response.start":
                        message = {**message, "headers": list(message.get("headers", []))}
                        MutableHeaders(scope=message)["Cache-Control"] = "no-store"
                    await send(message)
                await self.app(scope, receive, no_store)
                return
        await self.app(scope, receive, send)
