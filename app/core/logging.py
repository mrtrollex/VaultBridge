from __future__ import annotations

import json
import logging
import sys
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, TextIO

APPLICATION_LOGGER_NAME = "vaultbridge"
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_EVENT = "application_log"

_CONTEXT_FIELDS = (
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
    extra.update({key: value for key, value in context.items() if value is not None})
    try:
        logger.log(level, message, extra=extra, exc_info=exc_info)
    except Exception:
        return
