from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias

import yaml
from yaml.events import (
    AliasEvent,
    CollectionStartEvent,
    DocumentEndEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    NodeEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
    StreamEndEvent,
    StreamStartEvent,
)

MAX_FRONTMATTER_BYTES = 65_536
MAX_SCALAR_BYTES = 8_192
MAX_MAPPING_KEY_BYTES = 256
MAX_CONTAINER_DEPTH = 8
MAX_ITEMS = 1_024

FrontmatterReason: TypeAlias = Literal[
    "malformed_envelope",
    "invalid_yaml",
    "non_mapping_root",
    "duplicate_key",
    "multiple_documents",
    "disallowed_yaml_feature",
    "non_string_key",
    "unsupported_value",
    "frontmatter_too_large",
    "scalar_too_large",
    "mapping_key_too_large",
    "container_too_deep",
    "too_many_items",
]
FrontmatterState: TypeAlias = Literal["absent", "valid", "invalid"]
PortableScalar: TypeAlias = str | None | bool | int | float
PortableValue: TypeAlias = PortableScalar | Mapping[str, "PortableValue"] | tuple["PortableValue", ...]

_NULL_PATTERN = re.compile(r"^(?:~|null|Null|NULL)?$")
_BOOL_PATTERN = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_INT_PATTERN = re.compile(r"^[-+]?(?:0o[0-7]+|0x[0-9a-fA-F]+|[0-9]+)$")
_FLOAT_PATTERN = re.compile(
    r"^[-+]?(?:(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|"
    r"(?:\.inf|\.Inf|\.INF))$|^(?:\.nan|\.NaN|\.NAN)$"
)


@dataclass(frozen=True, slots=True)
class FrontmatterDiagnostic:
    """One bounded diagnostic that contains no frontmatter source data."""

    reason: FrontmatterReason
    line: int | None = None
    column: int | None = None

    def __post_init__(self) -> None:
        if self.line is not None and self.line < 1:
            raise ValueError("line must be one-based")
        if self.column is not None and self.column < 1:
            raise ValueError("column must be one-based")


@dataclass(frozen=True, slots=True)
class FrontmatterResult:
    """Immutable absent, valid, or invalid frontmatter result."""

    state: FrontmatterState
    metadata: Mapping[str, PortableValue] | None = None
    diagnostic: FrontmatterDiagnostic | None = None

    def __post_init__(self) -> None:
        valid_shape = {
            "absent": self.metadata is None and self.diagnostic is None,
            "valid": self.metadata is not None and self.diagnostic is None,
            "invalid": self.metadata is None and self.diagnostic is not None,
        }
        if not valid_shape[self.state]:
            raise ValueError("frontmatter result fields do not match its state")


class _InvalidFrontmatter(Exception):
    def __init__(
        self,
        reason: FrontmatterReason,
        event: NodeEvent | None = None,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        if event is not None:
            line = event.start_mark.line + 1
            column = event.start_mark.column + 1
        self.line = line
        self.column = column


@dataclass(slots=True)
class _BuildState:
    payload: str
    item_count: int = 0

    def count_item(self, event: NodeEvent) -> None:
        self.item_count += 1
        if self.item_count > MAX_ITEMS:
            raise _InvalidFrontmatter("too_many_items", event)


class FrontmatterParser:
    """Parse bounded YAML frontmatter from already-decoded Markdown content."""

    @staticmethod
    def parse(markdown: str) -> FrontmatterResult:
        if not isinstance(markdown, str):
            raise TypeError("markdown must be decoded text")

        envelope = _extract_envelope(markdown)
        if envelope is None:
            return FrontmatterResult(state="absent")
        if isinstance(envelope, FrontmatterDiagnostic):
            return FrontmatterResult(state="invalid", diagnostic=envelope)

        try:
            metadata = _parse_payload(envelope)
        except _InvalidFrontmatter as exc:
            return FrontmatterResult(
                state="invalid",
                diagnostic=FrontmatterDiagnostic(
                    reason=exc.reason,
                    line=exc.line,
                    column=exc.column,
                ),
            )
        return FrontmatterResult(state="valid", metadata=metadata)


def _extract_envelope(markdown: str) -> str | FrontmatterDiagnostic | None:
    start = 1 if markdown.startswith("\ufeff") else 0
    opener_end, payload_start = _line_bounds(markdown, start)
    if opener_end - start != 3 or markdown[start:opener_end] != "---":
        return None
    if payload_start is None:
        return FrontmatterDiagnostic(reason="malformed_envelope")

    envelope_bytes = _bounded_utf8_size(markdown, start, payload_start, MAX_FRONTMATTER_BYTES)
    position = payload_start
    while position <= len(markdown):
        line_start = position
        line_end, next_position = _line_bounds(markdown, position)
        is_closer = line_end - line_start == 3 and markdown[line_start:line_end] in {"---", "..."}
        segment_end = len(markdown) if next_position is None else next_position
        if envelope_bytes <= MAX_FRONTMATTER_BYTES:
            envelope_bytes += _bounded_utf8_size(
                markdown,
                position,
                segment_end,
                MAX_FRONTMATTER_BYTES - envelope_bytes,
            )
        if is_closer:
            if envelope_bytes > MAX_FRONTMATTER_BYTES:
                return FrontmatterDiagnostic(reason="frontmatter_too_large")
            return markdown[payload_start:line_start]
        if next_position is None:
            break
        position = next_position

    return FrontmatterDiagnostic(reason="malformed_envelope")


def _line_bounds(text: str, start: int) -> tuple[int, int | None]:
    newline = text.find("\n", start)
    if newline < 0:
        return len(text), None
    end = newline
    if end > start and text[end - 1] == "\r":
        end -= 1
    return end, newline + 1


def _bounded_utf8_size(text: str, start: int, end: int, limit: int) -> int:
    """Count at most one byte beyond a limit without materializing encoded source."""
    size = 0
    for index in range(start, end):
        codepoint = ord(text[index])
        if codepoint <= 0x7F:
            size += 1
        elif codepoint <= 0x7FF:
            size += 2
        elif codepoint <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > limit:
            return limit + 1
    return size


def _parse_payload(payload: str) -> Mapping[str, PortableValue]:
    try:
        events = iter(yaml.parse(payload, Loader=yaml.BaseLoader))
        if not isinstance(next(events), StreamStartEvent):
            raise _InvalidFrontmatter("invalid_yaml")
        event = next(events)
        if isinstance(event, StreamEndEvent):
            return MappingProxyType({})
        if not isinstance(event, DocumentStartEvent):
            raise _InvalidFrontmatter("invalid_yaml")
        if event.version is not None or event.tags is not None:
            raise _InvalidFrontmatter("disallowed_yaml_feature")

        root_event = next(events)
        _reject_disallowed_event(root_event)
        if not isinstance(root_event, MappingStartEvent):
            raise _InvalidFrontmatter("non_mapping_root", root_event)

        state = _BuildState(payload=payload)
        metadata = _build_mapping(events, root_event, state, depth=1)
        if not isinstance(next(events), DocumentEndEvent):
            raise _InvalidFrontmatter("invalid_yaml")
        tail = next(events)
        if isinstance(tail, DocumentStartEvent):
            raise _InvalidFrontmatter("multiple_documents")
        if not isinstance(tail, StreamEndEvent):
            raise _InvalidFrontmatter("invalid_yaml")
        return metadata
    except StopIteration as exc:
        raise _InvalidFrontmatter("invalid_yaml") from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise _InvalidFrontmatter(
            "invalid_yaml",
            line=None if mark is None else mark.line + 1,
            column=None if mark is None else mark.column + 1,
        ) from exc


def _build_node(
    events: Iterator[object],
    event: NodeEvent,
    state: _BuildState,
    *,
    depth: int,
    mapping_key: bool = False,
) -> PortableValue:
    _reject_disallowed_event(event)
    if mapping_key and not isinstance(event, ScalarEvent):
        raise _InvalidFrontmatter("non_string_key", event)
    if isinstance(event, ScalarEvent):
        return _build_scalar(event, state, mapping_key=mapping_key)
    if isinstance(event, MappingStartEvent):
        return _build_mapping(events, event, state, depth=depth)
    if isinstance(event, SequenceStartEvent):
        return _build_sequence(events, event, state, depth=depth)
    raise _InvalidFrontmatter("invalid_yaml", event)


def _build_mapping(
    events: Iterator[object],
    event: MappingStartEvent,
    state: _BuildState,
    *,
    depth: int,
) -> Mapping[str, PortableValue]:
    _check_depth(event, depth)
    result: dict[str, PortableValue] = {}
    while True:
        key_event = next(events)
        if isinstance(key_event, MappingEndEvent):
            return MappingProxyType(result)
        if not isinstance(key_event, NodeEvent):
            raise _InvalidFrontmatter("invalid_yaml")
        state.count_item(key_event)
        key = _build_node(events, key_event, state, depth=depth + 1, mapping_key=True)
        if not isinstance(key, str):
            raise _InvalidFrontmatter("non_string_key", key_event)
        if key in result:
            raise _InvalidFrontmatter("duplicate_key", key_event)
        value_event = next(events)
        if not isinstance(value_event, NodeEvent):
            raise _InvalidFrontmatter("invalid_yaml")
        result[key] = _build_node(events, value_event, state, depth=depth + 1)


def _build_sequence(
    events: Iterator[object],
    event: SequenceStartEvent,
    state: _BuildState,
    *,
    depth: int,
) -> tuple[PortableValue, ...]:
    _check_depth(event, depth)
    result: list[PortableValue] = []
    while True:
        item_event = next(events)
        if isinstance(item_event, SequenceEndEvent):
            return tuple(result)
        if not isinstance(item_event, NodeEvent):
            raise _InvalidFrontmatter("invalid_yaml")
        state.count_item(item_event)
        result.append(_build_node(events, item_event, state, depth=depth + 1))


def _build_scalar(
    event: ScalarEvent,
    state: _BuildState,
    *,
    mapping_key: bool,
) -> PortableScalar:
    source = state.payload[event.start_mark.index : event.end_mark.index]
    if len(source.encode("utf-8")) > MAX_SCALAR_BYTES:
        raise _InvalidFrontmatter("scalar_too_large", event)

    is_plain = event.style is None and event.implicit[0]
    if mapping_key and is_plain and event.value == "<<":
        raise _InvalidFrontmatter("disallowed_yaml_feature", event)
    value = _resolve_core_scalar(event.value, plain=is_plain)
    if mapping_key:
        if not isinstance(value, str):
            raise _InvalidFrontmatter("non_string_key", event)
        if len(value.encode("utf-8")) > MAX_MAPPING_KEY_BYTES:
            raise _InvalidFrontmatter("mapping_key_too_large", event)
    return value


def _resolve_core_scalar(value: str, *, plain: bool) -> PortableScalar:
    if not plain:
        return value
    if _NULL_PATTERN.fullmatch(value):
        return None
    if _BOOL_PATTERN.fullmatch(value):
        return value.lower() == "true"
    if _INT_PATTERN.fullmatch(value):
        sign = -1 if value.startswith("-") else 1
        unsigned = value[1:] if value[:1] in {"-", "+"} else value
        if unsigned.startswith("0o"):
            return sign * int(unsigned[2:], 8)
        if unsigned.startswith("0x"):
            return sign * int(unsigned[2:], 16)
        return sign * _parse_decimal_digits(unsigned)
    if _FLOAT_PATTERN.fullmatch(value):
        if ".inf" in value.lower() or ".nan" in value.lower():
            raise _InvalidFrontmatter("unsupported_value")
        number = float(value)
        if not math.isfinite(number):
            raise _InvalidFrontmatter("unsupported_value")
        return number
    return value


def _parse_decimal_digits(digits: str) -> int:
    """Parse a bounded decimal without depending on Python's text-to-int digit limit."""
    number = 0
    for start in range(0, len(digits), 9):
        chunk = digits[start : start + 9]
        number = number * (10 ** len(chunk)) + int(chunk)
    return number


def _reject_disallowed_event(event: NodeEvent) -> None:
    if isinstance(event, AliasEvent):
        raise _InvalidFrontmatter("disallowed_yaml_feature", event)
    if isinstance(event, CollectionStartEvent | ScalarEvent):
        if event.anchor is not None or event.tag is not None:
            raise _InvalidFrontmatter("disallowed_yaml_feature", event)


def _check_depth(event: NodeEvent, depth: int) -> None:
    if depth > MAX_CONTAINER_DEPTH:
        raise _InvalidFrontmatter("container_too_deep", event)
