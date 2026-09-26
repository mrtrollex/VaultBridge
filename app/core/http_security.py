from __future__ import annotations

import hmac

from fastapi import HTTPException, status

from app.core.config import Settings
from app.services.rate_limiter import FixedWindowRateLimiter


def _verify_presented_api_key(
    presented: bytes,
    settings: Settings,
    *,
    prefix: bytes = b"",
) -> None:
    current_api_key = settings.api_key.get_secret_value()
    if not current_api_key:
        raise HTTPException(status_code=500, detail="Server API_KEY is not configured")

    current_matches = hmac.compare_digest(presented, prefix + current_api_key.encode("utf-8"))
    previous_api_key = settings.previous_api_key.get_secret_value()
    previous_matches = bool(previous_api_key) and hmac.compare_digest(
        presented,
        prefix + previous_api_key.encode("utf-8"),
    )
    if not (current_matches | previous_matches):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def verify_api_key(api_key: str, settings: Settings) -> None:
    """Apply the shared current/previous raw API-key credential contract."""
    _verify_presented_api_key(api_key.encode("utf-8"), settings)


def verify_bearer_authorization(authorization: str | None, settings: Settings) -> None:
    """Apply the shared current/previous API-key Bearer contract."""
    _verify_presented_api_key((authorization or "").encode("utf-8"), settings, prefix=b"Bearer ")


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
