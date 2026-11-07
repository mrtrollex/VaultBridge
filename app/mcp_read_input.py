"""Validate explicit JSON before SDK coercion discards duplicate keys.

Valid legacy messages pass through unchanged. The rejection marker is consumed before
policy/admission and cannot select or authorize a space.
"""

import asyncio
import json
import math
from contextlib import contextmanager, suppress

import anyio
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

from app.services.knowledge_spaces import SpaceError
from app.services.scoped_budget import CancellationToken
from app.space_read_transport import SCOPE_FIELDS, InvalidReadRequest, parse_resource_uri

INVALID_SCOPED_JSON = "__vaultbridge_invalid_scoped_json"
HTTP_READ_LIFETIME = "vaultbridge.mcp_http_read_lifetime"


def _rejection_message(message):
    """Build only a rejection; never serialize the supplied envelope or metadata."""
    identifier = message.get("id", 0)
    if type(identifier) not in (str, int):
        identifier = 0
    if type(identifier) is str:
        try:
            identifier.encode("utf-8")
        except UnicodeError:
            identifier = 0
    original_params = message.get("params")
    original_params = original_params if isinstance(original_params, dict) else {}
    name = next((name for name in (
        "read_note", "list_notes", "search_notes", "related_notes", "duplicate_candidates",
        "note_links", "note_backlinks", "knowledge_hygiene_scan",
    ) if name == original_params.get("name")), "read_note")
    method = "resources/read" if message.get("method") == "resources/read" else "tools/call"
    uri = original_params.get("uri")
    if type(uri) is not str:
        uri = "vaultbridge://invalid"
    try:
        uri.encode("utf-8")
    except UnicodeError:
        uri = "vaultbridge://invalid"
    # Preserve the scalar routing identity for the SDK's MCP-Name header check.
    # The marker is consumed before URI lookup, validation or owner execution.
    params = ({"uri": uri, INVALID_SCOPED_JSON: True} if method == "resources/read"
              else {"name": name, "arguments": {"scope": True, INVALID_SCOPED_JSON: True}})
    meta = original_params.get("_meta")
    if isinstance(meta, dict):
        version = next((version for version in (*HANDSHAKE_PROTOCOL_VERSIONS, *MODERN_PROTOCOL_VERSIONS)
                        if version == meta.get("io.modelcontextprotocol/protocolVersion")), None)
        if version is not None:
            params["_meta"] = {"io.modelcontextprotocol/protocolVersion": version,
                               "io.modelcontextprotocol/clientCapabilities": {}}
    return json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params},
                      ensure_ascii=True, allow_nan=False) + "\n"


def normalize_scoped_message(raw):
    duplicate, scoped, resource, nonfinite = False, False, False, False

    def object_pairs(pairs):
        nonlocal duplicate, scoped, resource
        result = {}
        for key, value in pairs:
            # The FIRST parser sees every object member, including members of
            # overwritten params/arguments/URI/method. Capture all selection
            # evidence before collapsing; the final dictionary is not evidence
            # that the original envelope omitted selection.
            duplicate = duplicate or key in result
            scoped = scoped or key in SCOPE_FIELDS
            scoped = scoped or (key == "uri" and isinstance(value, str) and value.startswith("vaultbridge://space/"))
            resource = resource or (key == "method" and value == "resources/read")
            result[key] = value
        return result

    def number(value):
        nonlocal nonfinite
        result = float(value)
        nonfinite = nonfinite or not math.isfinite(result)
        return result

    def constant(_):
        nonlocal nonfinite
        nonfinite = True
        return None

    message = {}
    try:
        message = json.loads(raw, object_pairs_hook=object_pairs, parse_float=number, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        if not ((scoped or resource) and (duplicate or nonfinite)):
            return raw  # Existing SDK protocol/parse error boundary, before rejection.
    else:
        if not isinstance(message, dict) or not (scoped or resource):
            return raw
        invalid = duplicate or nonfinite
        if not invalid and message.get("method") == "resources/read":
            params = message.get("params")
            uri = params.get("uri") if isinstance(params, dict) else None
            try:
                # Keep the old template's spelling/validation in its existing
                # owner. Qualified identifiers must survive the SDK unchanged.
                if not isinstance(uri, str) or not uri.startswith("vaultbridge://note/"):
                    parse_resource_uri(uri)
            except (InvalidReadRequest, SpaceError):
                invalid = True
        if not invalid:
            return raw

    # Irreversible rejection, outside every original-input fallback.
    try:
        serialized = _rejection_message(message)
        return serialized.encode("utf-8") if isinstance(raw, bytes) else serialized
    except Exception:
        # Abort this input before SDK dispatch if even safe construction fails.
        # Never resume the original envelope or expose a construction exception.
        raise InvalidReadRequest("Invalid MCP request.") from None


class StrictScopedLines:
    def __init__(self, source):
        self.source = source

    async def __aiter__(self):
        async for line in self.source:
            yield normalize_scoped_message(line)


class _HTTPReadLifetime:
    """One HTTP request, with cancellation limited to its enrolled reads.

    The SDK carries the Request in per-message metadata on both protocol paths.
    Writes are never enrolled or cancelled by this read-only bridge.
    """

    def __init__(self):
        self.closed = False
        self._reads = {}

    @contextmanager
    def read(self):
        token = CancellationToken()
        self._reads[id(token)] = token
        if self.closed:
            token.cancel()
        try:
            yield token
        finally:
            self._reads.pop(id(token))

    def disconnect(self):
        self.closed = True
        for token in self._reads.values():
            token.cancel()

    async def is_disconnected(self):
        return self.closed


class StrictScopedHTTPInput:
    """Bounded lookahead; SDK size, security and protocol checks still apply."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        lifetime = _HTTPReadLifetime()
        scope = {**scope, HTTP_READ_LIFETIME: lifetime}
        queue = asyncio.Queue(maxsize=1)
        pump = None

        async def receive_pump():
            while True:
                item = await receive()
                if item["type"] == "http.disconnect":
                    lifetime.disconnect()
                await queue.put(item)
                if item["type"] == "http.disconnect":
                    return

        async def owned_receive():
            nonlocal pump
            if pump is None:
                pump = asyncio.create_task(receive_pump(), name="vaultbridge-mcp-http-receive")
            if lifetime.closed and queue.empty():
                return {"type": "http.disconnect"}
            return await queue.get()

        pending, loaded = [], False

        async def checked_receive():
            nonlocal loaded
            if not loaded:
                loaded = True
                size = 0
                while True:
                    item = await owned_receive()
                    pending.append(item)
                    size += len(item.get("body", b""))
                    if item["type"] != "http.request" or not item.get("more_body") or size > 4 * 1024 * 1024:
                        break
                if item["type"] == "http.request" and not item.get("more_body") and size <= 4 * 1024 * 1024:
                    body = b"".join(part.get("body", b"") for part in pending)
                    normalized = normalize_scoped_message(body)
                    if normalized != body:
                        pending[:] = [{"type": "http.request", "body": normalized, "more_body": False}]
            return pending.pop(0) if pending else await owned_receive()

        async def connected_send(message):
            if not lifetime.closed:
                await send(message)

        try:
            await self.app(scope, checked_receive, connected_send)
        finally:
            if pump is not None:
                with anyio.CancelScope(shield=True):
                    pump.cancel()
                    with suppress(asyncio.CancelledError):
                        await pump
