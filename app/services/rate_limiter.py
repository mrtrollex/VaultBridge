from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int | None = None


@dataclass
class _ClientWindow:
    started_at: float
    request_count: int


class FixedWindowRateLimiter:
    """Thread-safe, bounded, process-local fixed-window rate limiter."""

    def __init__(
        self,
        *,
        requests: int,
        window_seconds: int,
        max_clients: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests <= 0:
            raise ValueError("requests must be greater than zero")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
        if max_clients <= 0:
            raise ValueError("max_clients must be greater than zero")

        self.requests = requests
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._clock = clock
        self._clients: OrderedDict[str, _ClientWindow] = OrderedDict()
        self._lock = Lock()

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def check(self, client_id: str) -> RateLimitDecision:
        now = self._clock()
        with self._lock:
            self._remove_stale_clients(now)
            window = self._clients.get(client_id)
            if window is None:
                self._make_capacity()
                self._clients[client_id] = _ClientWindow(started_at=now, request_count=1)
                return RateLimitDecision(allowed=True)

            self._clients.move_to_end(client_id)
            if window.request_count < self.requests:
                window.request_count += 1
                return RateLimitDecision(allowed=True)

            remaining = self.window_seconds - (now - window.started_at)
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=max(1, math.ceil(remaining)),
            )

    def _remove_stale_clients(self, now: float) -> None:
        stale_clients = [
            client_id
            for client_id, window in self._clients.items()
            if now - window.started_at >= self.window_seconds
        ]
        for client_id in stale_clients:
            del self._clients[client_id]

    def _make_capacity(self) -> None:
        if len(self._clients) >= self.max_clients:
            self._clients.popitem(last=False)
