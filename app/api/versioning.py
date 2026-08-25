from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

API_V1_PREFIX = "/api/v1"


def versioned_api_route(
    router: APIRouter,
    path: str,
    *,
    operation_id: str,
    methods: list[str],
    **route_kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register legacy and v1 paths for one shared application endpoint."""

    def decorator(endpoint: Callable[..., Any]) -> Callable[..., Any]:
        router.add_api_route(
            path,
            endpoint,
            operation_id=operation_id,
            methods=methods,
            **route_kwargs,
        )
        router.add_api_route(
            f"{API_V1_PREFIX}{path}",
            endpoint,
            operation_id=f"{operation_id}V1",
            methods=methods,
            **route_kwargs,
        )
        return endpoint

    return decorator
