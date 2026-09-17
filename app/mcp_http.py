from __future__ import annotations

import logging

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.core.http_security import enforce_peer_rate_limit, verify_bearer_authorization
from app.mcp_server import create_mcp_server
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.rate_limiter import FixedWindowRateLimiter
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService

MCP_HTTP_PATH = "/mcp"


class MCPHTTPAccessMiddleware:
    """Authenticate and rate-limit the one mounted MCP HTTP endpoint."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        rate_limiter: FixedWindowRateLimiter,
    ) -> None:
        self.app = app
        self.settings = settings
        self.rate_limiter = rate_limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("path") != MCP_HTTP_PATH:
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return

        headers = Headers(scope=scope)
        client = scope.get("client")
        peer_host = client[0] if client is not None else "unknown-peer"
        try:
            enforce_peer_rate_limit(peer_host, self.settings, self.rate_limiter)
            verify_bearer_authorization(headers.get("authorization"), self.settings)
        except HTTPException as exc:
            await JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=exc.headers,
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)


def create_mcp_http_transport(
    *,
    settings: Settings,
    vault_service: VaultService,
    semantic_search_service: SemanticSearchService,
    duplicate_candidate_service: DuplicateCandidateService,
    rate_limiter: FixedWindowRateLimiter,
) -> tuple[MCPServer, ASGIApp]:
    """Create the mounted SDK app over the live application's service objects."""
    server = create_mcp_server(
        settings=settings,
        vault_service=vault_service,
        semantic_search_service=semantic_search_service,
        duplicate_candidate_service=duplicate_candidate_service,
        transport="streamable-http",
    )
    security = TransportSecuritySettings(
        allowed_hosts=list(settings.mcp_http_allowed_hosts),
        allowed_origins=list(settings.mcp_http_allowed_origins),
    )

    # The SDK logs rejected raw Host/Origin values at warning level. Keep that
    # external diagnostic out of VaultBridge logs because those headers are untrusted.
    logging.getLogger("mcp.server.transport_security").setLevel(logging.CRITICAL)

    sdk_app = server.streamable_http_app(
        streamable_http_path=MCP_HTTP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=security,
    )
    protected_app = MCPHTTPAccessMiddleware(
        sdk_app,
        settings=settings,
        rate_limiter=rate_limiter,
    )
    return server, protected_app
