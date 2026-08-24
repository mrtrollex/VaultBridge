from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterator, TextIO

APPLICATION_LOGGER_NAME = "vaultbridge"
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_EVENT = "application_log"

_CONTEXT_FIELDS = (
    "request_id",
    "method",
    "route",
    "status_code",
    "operation",
    "note_path",
    "indexed_notes",
    "unchanged_notes",
    "removed_notes",
    "queued_notes",
    "duration_ms",
    "index_state",
    "full_sync_required",
    "error_type",
)
_HANDLER_MARKER = "_vaultbridge_json_handler"
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_REQUEST_ID: ContextVar[str | None] = ContextVar("vaultbridge_request_id", default=None)


def current_request_id() -> str | None:
    """Return the request ID bound to the current execution context, if any."""
    return _REQUEST_ID.get()


@contextmanager
def request_id_context(request_id: str) -> Iterator[None]:
    """Bind one request ID and restore the previous context on exit."""
    token = _REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


def _utc_timestamp(created: float) -> str:
    return (
        datetime.fromtimestamp(created, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _safe_json_value(item)
            for key, item in value.items()
            if isinstance(key, (str, int, float, bool))
        }
    return type(value).__name__


def _safe_note_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or windows_path.drive
        or windows_path.is_absolute()
        or ".." in path.parts
    ):
        return None
    return path.as_posix()


def _safe_request_id(value: Any) -> str | None:
    if not isinstance(value, str) or _REQUEST_ID_PATTERN.fullmatch(value) is None:
        return None
    return value


def _safe_method(value: Any) -> str | None:
    if not isinstance(value, str) or not value.isascii() or not value.isalpha():
        return None
    normalized = value.upper()
    return normalized if len(normalized) <= 16 else None


def _safe_route(value: Any) -> str | None:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 500:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value


def _exception_payload(record: logging.LogRecord) -> dict[str, Any] | None:
    if not record.exc_info or not record.exc_info[0]:
        return None
    exception_type, _exception, exception_traceback = record.exc_info
    frames = [
        {
            "file": frame.filename.replace("\\", "/").rsplit("/", 1)[-1],
            "line": frame.lineno,
            "function": frame.name,
        }
        for frame in traceback.extract_tb(exception_traceback)
    ]
    payload: dict[str, Any] = {"type": exception_type.__name__}
    if frames:
        payload["frames"] = frames
    return payload


class JsonLogFormatter(logging.Formatter):
    """Serialize one safe VaultBridge application record as one JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _utc_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", DEFAULT_EVENT),
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if field == "request_id" and value is None:
                value = current_request_id()
            if field == "request_id":
                value = _safe_request_id(value)
            if field == "method":
                value = _safe_method(value)
            if field == "route":
                value = _safe_route(value)
            if field == "note_path":
                value = _safe_note_path(value)
            if value is not None:
                payload[field] = _safe_json_value(value)

        exception = _exception_payload(record)
        if exception is not None:
            payload.setdefault("error_type", exception["type"])
            payload["exception"] = exception

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_application_logging(
    *,
    level: int = DEFAULT_LOG_LEVEL,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure only VaultBridge application loggers, leaving Uvicorn untouched."""
    application_logger = logging.getLogger(APPLICATION_LOGGER_NAME)
    application_logger.setLevel(level)
    application_logger.propagate = False

    for handler in application_logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            handler.setLevel(level)
            return application_logger

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(JsonLogFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    application_logger.addHandler(handler)
    return application_logger


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    *,
    exc_info: Any = None,
    **context: Any,
) -> None:
    """Emit a structured event without allowing logging failures to affect application behavior."""
    extra = {"event": event}
    if context.get("request_id") is None:
        active_request_id = current_request_id()
        if active_request_id is not None:
            extra["request_id"] = active_request_id
    extra.update({key: value for key, value in context.items() if value is not None})
    try:
        logger.log(level, message, extra=extra, exc_info=exc_info)
    except Exception:
        return
