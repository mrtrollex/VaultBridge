"""Bounded portable intake; Markdown is the only retry authority."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.services.frontmatter import FrontmatterSerializationError, serialize_frontmatter
from app.services.semantic_search import SemanticIndexOperationError, SynchronizationCancelledError
from app.services.vault import AtomicCaptureError, VaultService

_REQUIRED = frozenset({"content", "state", "capture_id", "captured_at"})
_OPTIONAL = ("title", "source", "tags", "capture_type")
_FIXED = frozenset({"capture_id", "capture_state", "captured_at", *_OPTIONAL})
_FIELDS = _REQUIRED | set(_OPTIONAL) | {"metadata"}
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|\+00:00)\Z")


class CaptureValidationError(ValueError):
    def __init__(self, category="invalid_request"):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class CaptureResult:
    category: str
    committed: bool = False
    capture_id: str | None = None
    path: str | None = None
    index_state: str | None = None


def _text(value, bound):
    if type(value) is not str:
        raise CaptureValidationError()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise CaptureValidationError() from None
    if size > bound:
        raise CaptureValidationError("size_limit")
    return value


class CaptureService:
    """Compose intake facts, delegate atomic Markdown creation, then enqueue indexing."""

    def __init__(self, vault_service: VaultService, *, enqueue: Callable[[str], bool] | None = None):
        self.vault_service = vault_service
        self.enqueue = enqueue

    def _compose(self, request):
        if not isinstance(request, Mapping) or not _REQUIRED <= request.keys() or request.keys() - _FIELDS:
            raise CaptureValidationError()
        content = _text(request["content"], 65_536)
        if not content.strip():
            raise CaptureValidationError()
        state, capture_id, timestamp = (request[field] for field in ("state", "capture_id", "captured_at"))
        if any(type(value) is not str for value in (state, capture_id, timestamp)):
            raise CaptureValidationError()
        if state not in ("inbox", "draft") or not _ID.fullmatch(capture_id) or not _TIME.fullmatch(timestamp):
            raise CaptureValidationError()
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            raise CaptureValidationError() from None
        metadata = {"capture_id": capture_id, "capture_state": state, "captured_at": timestamp[:19] + "Z"}
        for field, bound in (("title", 256), ("source", 2048), ("tags", 1024), ("capture_type", 64)):
            if field not in request:
                continue
            value = request[field]
            if field == "tags":
                if type(value) not in (list, tuple):
                    raise CaptureValidationError()
                if len(value) > 16:
                    raise CaptureValidationError("size_limit")
                metadata[field] = [_text(tag, bound) for tag in value]
            else:
                metadata[field] = _text(value, bound)
        extra = request.get("metadata", {})
        if not isinstance(extra, Mapping):
            raise CaptureValidationError()
        for key, value in extra.items():
            if type(key) is not str or key in metadata or key in _FIXED:
                raise CaptureValidationError()
            metadata[key] = value
        try:
            markdown = (serialize_frontmatter(metadata) + "\n" + content).encode("utf-8")
        except FrontmatterSerializationError as exc:
            size_reasons = {
                "scalar_too_large",
                "mapping_key_too_large",
                "container_too_deep",
                "too_many_items",
                "frontmatter_too_large",
            }
            raise CaptureValidationError("size_limit" if exc.reason in size_reasons else "invalid_request") from None
        if len(markdown) > self.vault_service.max_note_bytes:
            raise CaptureValidationError("size_limit")
        return capture_id, markdown

    def capture(self, request: Mapping[str, object]) -> CaptureResult:
        try:
            capture_id, markdown = self._compose(request)
        except CaptureValidationError as exc:
            return CaptureResult(exc.category)
        try:
            result = self.vault_service.create_capture_if_absent(capture_id=capture_id, markdown=markdown)
        except AtomicCaptureError as exc:
            return CaptureResult(exc.category)
        index_state = "index_unavailable"
        if self.enqueue is not None:
            try:
                if self.enqueue(result.path):
                    index_state = "index_pending"
            except sqlite3.ProgrammingError:
                raise
            except (OSError, sqlite3.DatabaseError, SemanticIndexOperationError, SynchronizationCancelledError):
                pass
        return CaptureResult(result.status, True, capture_id, result.path, index_state)
