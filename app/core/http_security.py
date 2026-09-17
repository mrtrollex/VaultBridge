from __future__ import annotations

import hmac

from fastapi import HTTPException, status

from app.core.config import Settings
from app.services.rate_limiter import FixedWindowRateLimiter


def verify_bearer_authorization(authorization: str | None, settings: Settings) -> None:
    """Apply the shared current/previous API-key Bearer contract."""
    api_key = settings.api_key.get_secret_value()
    if not api_key:
        raise HTTPException(status_code=500, detail="Server API_KEY is not configured")

    presented = (authorization or "").encode("utf-8")
    current_matches = hmac.compare_digest(presented, f"Bearer {api_key}".encode("utf-8"))
    previous_api_key = settings.previous_api_key.get_secret_value()
    previous_matches = bool(previous_api_key) and hmac.compare_digest(
        presented,
        f"Bearer {previous_api_key}".encode("utf-8"),
    )
    if not (current_matches | previous_matches):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def enforce_peer_rate_limit(
    peer_host: str,
    settings: Settings,
    rate_limiter: FixedWindowRateLimiter,
) -> None:
    """Apply the shared protected-HTTP peer budget."""
    if not settings.rate_limit_enabled or not settings.api_key.get_secret_value():
        return

    decision = rate_limiter.check(peer_host)
    if not decision.allowed:
        headers = (
            {"Retry-After": str(decision.retry_after_seconds)}
            if decision.retry_after_seconds is not None
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers=headers,
        )
