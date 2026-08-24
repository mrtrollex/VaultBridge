from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app.core.logging import log_event, request_id_context

logger = logging.getLogger("vaultbridge.http")

_REQUEST_ID_HEADER = b"x-request-id"

ASGIMessage = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]


def _route_template(scope: MutableMapping[str, Any]) -> str | None:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else None


class RequestObservabilityMiddleware:
    """Add request correlation and safe request lifecycle events."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        method = scope.get("method")
        started_at = time.perf_counter()
        status_code: int | None = None

        async def send_with_request_id(message: ASGIMessage) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _REQUEST_ID_HEADER
                ]
                headers.append((_REQUEST_ID_HEADER, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with request_id_context(request_id):
            log_event(
                logger,
                logging.INFO,
                "request_started",
                "HTTP request started",
                method=method,
            )
            try:
                await self.app(scope, receive, send_with_request_id)
            except BaseException as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "request_failed",
                    "HTTP request failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    method=method,
                    route=_route_template(scope),
                    status_code=status_code,
                    duration_ms=max(0.0, round((time.perf_counter() - started_at) * 1000, 3)),
                    error_type=type(exc).__name__,
                )
                raise
            else:
                log_event(
                    logger,
                    logging.INFO,
                    "request_completed",
                    "HTTP request completed",
                    method=method,
                    route=_route_template(scope),
                    status_code=status_code,
                    duration_ms=max(0.0, round((time.perf_counter() - started_at) * 1000, 3)),
                )
