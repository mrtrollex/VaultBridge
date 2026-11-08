"""Scoped branches run before legacy coercion, behind the same HTTP protection."""

import json
import re

from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.api.dependencies import enforce_rate_limit, get_space_operations, require_auth
from app.read_execution import run_async_read, run_read
from app.services.knowledge_spaces import SpaceError
from app.space_read_transport import (
    InvalidReadRequest,
    execute_scoped,
    has_scope,
    public_error,
    raw_has_scope,
    strict_json,
)

_READS = {"read": "read", "list": "list", "search": "literal", "related": "related",
          "duplicates": "duplicates", "links": "links", "backlinks": "backlinks", "scan": "hygiene",
          "query": "query"}
_READS.update({"notes": "create", "append": "append"})


def _query(request):
    data = {}
    for key in request.query_params:
        values = request.query_params.getlist(key)
        if key == "spaces":
            data[key] = values
        elif len(values) != 1:
            if key in {"space", "scope", "space_id"}:
                raise SpaceError("invalid_scope")
            raise InvalidReadRequest()
        else:
            data[key] = values[0]
    if "limit" in data:
        if not re.fullmatch(r"[+-]?[0-9]+", data["limit"]):
            raise InvalidReadRequest()
        data["limit"] = int(data["limit"])
    return data


class SpaceReadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        operation = _READS.get(self.path.rsplit("/", 1)[-1])

        async def dispatch(request):
            scoped = operation == "query" or any(
                key in request.query_params for key in ("space", "spaces", "scope", "space_id"))
            raw = None
            if request.method == "POST":
                raw = await request.body()
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    data = None
                scoped = scoped or has_scope(data) or (data is None and raw_has_scope(raw))
            if not scoped:
                try:
                    if (operation is not None
                            and request.app.state.settings.knowledge_spaces_json is not None):
                        return await run_async_read(lambda: handler(request), disconnected=request.is_disconnected)
                    return await handler(request)
                except SpaceError as exc:
                    code, status, _ = public_error(exc)
                    return JSONResponse({"detail": code}, status_code=status)
            settings = request.app.state.settings
            # Same direct-peer limiter once, followed by the same Bearer/session
            # verifier. Named cookies are fenced in the shared dependency.
            try:
                enforce_rate_limit(request, settings, request.app.state.rate_limiter)
                require_auth(request.headers.get("authorization"), settings, request,
                             request.headers.get("x-vaultbridge-ui-request"))
                if operation is None:
                    raise SpaceError("invalid_scope")
                if request.method == "POST":
                    data = strict_json(raw)
                    if request.query_params:
                        raise SpaceError("invalid_scope")
                else:
                    data = _query(request)
                from app.space_write_transport import execute_note_write

                execute = execute_note_write if operation in {"create", "append"} else execute_scoped
                result = await run_read(lambda token: execute(
                    get_space_operations(request), operation, data, cancel=token,
                ), disconnected=request.is_disconnected)
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                    headers={**(exc.headers or {}), "Cache-Control": "no-store"})
            except Exception as exc:
                code, status, _ = public_error(exc)
                return JSONResponse({"detail": code}, status_code=status, headers={"Cache-Control": "no-store"})

        if operation not in {"query", "create", "append"}:
            return dispatch

        async def query_dispatch(request):
            try:
                response = await dispatch(request)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            response.headers["Cache-Control"] = "no-store"
            return response

        return query_dispatch
