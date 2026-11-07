"""Named mode has no Dashboard, assets or sessions, including old cookies."""

from starlette._utils import get_route_path
from starlette.responses import JSONResponse


class NamedUIFence:
    def __init__(self, app, *, named):
        self.app, self.named = app, named

    async def __call__(self, scope, receive, send):
        if self.named and scope["type"] == "http":
            path = get_route_path(scope)
            if path == "/ui" or path.startswith("/ui/"):
                await JSONResponse({"detail": "Not Found"}, status_code=404,
                                   headers={"Cache-Control": "no-store"})(scope, receive, send)
                return
        await self.app(scope, receive, send)
