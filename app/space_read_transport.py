"""Strict explicit-scope wire validation and allowlisted read projection.

Permissions and owner dispatch live in SpaceOperations. Omitted requests retain
their transport's existing validation and serializers.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from typing import Generic, Literal, TypeVar
from urllib.parse import quote, unquote_to_bytes

from pydantic import BaseModel, ConfigDict, ValidationError

from app.knowledge_hygiene_transport import KnowledgeHygieneScanRequest, serialize_hygiene_result
from app.services.knowledge_hygiene import KnowledgeHygieneError
from app.services.knowledge_query import KnowledgeQueryError
from app.services.knowledge_spaces import Dialect, QualifiedPath, ReadScope, SpaceError, SpaceId
from app.services.semantic_search import SemanticSearchUnavailableError
from app.services.vault import (
    NoteConflictError,
    NoteNotFoundError,
    NoteTooLargeError,
    NoteUnavailableError,
    VaultValidationError,
)

PLURAL_READS = frozenset({"list", "literal", "related"})
SCOPE_FIELDS = frozenset({"space", "spaces", "scope", "space_id"})
T = TypeVar("T")


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QualifiedIdentityOutput(_Output):
    space_id: str
    path: str


class NoteMatchOutput(_Output):
    identity: QualifiedIdentityOutput
    title: str
    snippet: str | None
    modified: str | None


class SemanticMatchOutput(_Output):
    identity: QualifiedIdentityOutput
    title: str
    score: float
    semantic_score: float
    lexical_score: float
    snippet: str
    heading: str | None


class MCPNoteMatchOutput(NoteMatchOutput):
    resource_uri: str


class MCPSemanticMatchOutput(SemanticMatchOutput):
    resource_uri: str


class CoverageOutput(_Output):
    space_id: str
    state: Literal["complete", "partial", "unavailable"]
    reasons: list[Literal["root_unavailable", "index_unavailable", "discovery_limit", "path_limit",
                          "alias_limit", "content_limit", "relationship_limit", "semantic_limit", "note_unavailable"]]
    semantic_index_basis: Literal[
        "none", "compatible_ready", "compatible_previous_refresh", "compatible_previous_error"
    ]
    enumerated_paths: int
    evaluated_paths: int
    returned_candidates: int


class ScopedReadOutput(_Output, Generic[T]):
    items: list[T]
    coverage: list[CoverageOutput]
    state: Literal["complete", "partial"]
    ordering: Literal["canonical_path", "modified", "semantic"]
    result_limited: bool


class OneSpaceOutput(_Output, Generic[T]):
    space_id: str
    result: T


def schema_union(legacy, scoped):
    legacy, scoped = deepcopy(legacy), deepcopy(scoped)
    definitions = {**legacy.pop("$defs", {}), **scoped.pop("$defs", {})}
    return {"anyOf": [legacy, scoped], **({"$defs": definitions} if definitions else {})}


def scoped_request_schema(operation, legacy):
    """Use the same conditional wire contract in REST and MCP discovery."""
    legacy = deepcopy(legacy)
    definitions = legacy.pop("$defs", {})
    key = "spaces" if operation in PLURAL_READS else "space"
    sid = {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"}
    scope = ({"type": "array", "minItems": 1, "maxItems": 8, "items": sid} if key == "spaces" else sid)
    properties = deepcopy(legacy.get("properties", {}))
    if operation in PLURAL_READS:
        properties["limit"] = {"type": "integer", "minimum": 1, "maximum": 100,
                               "default": {"list": 50, "literal": 10, "related": 5}[operation]}
        properties["folder"] = {"type": "string", "minLength": 1, "description": "At most 1,024 UTF-8 bytes"}
        if operation == "literal":
            properties["query"] = {"type": "string", "minLength": 1, "description": "At most 8,192 UTF-8 bytes"}
        if operation == "related":
            properties["text"] = {"type": "string", "minLength": 1, "description": "At most 4,096 UTF-8 bytes"}
            properties["min_score"] = {"type": "number", "minimum": 0, "maximum": 1, "default": 0.28}
    if "path" in properties:
        properties["path"] = {"type": "string", "minLength": 1, "description": "At most 1,024 UTF-8 bytes"}
    if operation == "duplicates":
        properties.pop("min_score", None)
    properties[key] = scope
    scoped = {"type": "object", "additionalProperties": False, "properties": properties,
              "required": [*legacy.get("required", []), key]}
    legacy["not"] = {"anyOf": [{"required": [field]} for field in SCOPE_FIELDS]}
    combined = schema_union(legacy, scoped)
    if definitions:
        combined["$defs"] = definitions
    # Retain default property metadata for old MCP clients inspecting defaults;
    # actual validation is the two complete alternatives above.
    combined.update(type="object", properties={**deepcopy(legacy.get("properties", {})), key: scope})
    for name in ("folder", "limit", "query", "text", "min_score", "path"):
        if name in combined["properties"]:
            combined["properties"][name] = {"default": combined["properties"][name]["default"]} if (
                "default" in combined["properties"][name]) else {}
    return combined


class InvalidReadRequest(ValueError):
    pass


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise InvalidReadRequest()
            result[key] = value
        return result

    def constant(_):
        raise InvalidReadRequest()

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidReadRequest() from None


def has_scope(data):
    return isinstance(data, dict) and bool(SCOPE_FIELDS & data.keys())


def raw_has_scope(raw):
    for match in re.finditer(rb'("(?:[^"\\]|\\.)*")\s*:', raw):
        try:
            if json.loads(match.group(1)) in SCOPE_FIELDS:
                return True
        except (ValueError, UnicodeError, TypeError):
            continue
    return False


def _text(value, maximum, *, minimum=1):
    if type(value) is not str:
        raise InvalidReadRequest()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise InvalidReadRequest() from None
    if not minimum <= size <= maximum:
        raise InvalidReadRequest()
    return value


def _integer(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise InvalidReadRequest()
    return value


def selection(operation, data):
    key = "spaces" if operation in PLURAL_READS else "space"
    if SCOPE_FIELDS & data.keys() != {key}:
        raise SpaceError("invalid_scope")
    try:
        if key == "space":
            return SpaceId(data[key])
        values = data[key]
        if type(values) is not list or not 1 <= len(values) <= 8:
            raise SpaceError("invalid_scope")
        return ReadScope(tuple(SpaceId(value) for value in values))
    except (TypeError, ValueError):
        raise SpaceError("invalid_scope") from None


def parse_scoped(operation, data):
    if type(data) is not dict:
        raise InvalidReadRequest()
    selected = selection(operation, data)
    fields = {
        "read": {"path"}, "list": {"folder", "limit"},
        "literal": {"query", "folder", "limit"},
        "related": {"text", "folder", "limit", "min_score"},
        "duplicates": {"title", "text", "folder", "limit", "min_score"},
        "links": {"path"}, "backlinks": {"path"},
        "hygiene": set(KnowledgeHygieneScanRequest.model_fields),
    }[operation]
    if data.keys() - fields - {"space", "spaces"}:
        raise InvalidReadRequest()
    arguments = {key: value for key, value in data.items() if key in fields}
    if operation == "hygiene":
        try:
            return selected, {"request": KnowledgeHygieneScanRequest.model_validate(arguments).to_domain()}
        except ValidationError:
            raise InvalidReadRequest() from None
    if operation in {"read", "links", "backlinks"}:
        return selected, {"path": QualifiedPath(selected, _text(arguments.get("path"), 1024))}
    if operation == "duplicates":
        # Existing character bounds, with strict types and Unicode encoding.
        for key, default, maximum, minimum in (("title", None, 180, 1), ("text", "", 4000, 0),
                                                ("folder", "", 500, 0)):
            value = arguments.get(key, default)
            _text(value, maximum * 4, minimum=minimum)
            if len(value) > maximum:
                raise InvalidReadRequest()
            arguments[key] = value.strip() if key == "title" else value
        if not arguments["title"]:
            raise InvalidReadRequest()
        arguments["limit"] = _integer(arguments.get("limit", 5), 20)
        if "min_score" in arguments:
            raise SpaceError("unsupported_capability")
        return selected, arguments
    arguments["limit"] = _integer(arguments.get("limit", {"list": 50, "literal": 10, "related": 5}[operation]), 100)
    if "folder" in arguments:
        arguments["folder"] = _text(arguments["folder"], 1024)
    if operation == "literal":
        arguments["text"] = _text(arguments.pop("query", None), 8192)
    if operation == "related":
        arguments["text"] = _text(arguments.get("text"), 4096)
        score = arguments.get("min_score", 0.28)
        if type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1:
            raise InvalidReadRequest()
        arguments["min_score"] = float(score)
    return selected, arguments


def scoped_resource_uri(space_id, path):
    sid = SpaceId(space_id).value
    return f"vaultbridge://space/{sid}/note/{quote(path, safe='', encoding='utf-8', errors='strict')}"


def parse_resource_uri(uri):
    if type(uri) is not str:
        raise InvalidReadRequest()
    match = re.fullmatch(r"vaultbridge://space/([a-z][a-z0-9_-]{0,63})/note/([^/?#]+)", uri)
    if match is None:
        raise InvalidReadRequest()
    sid, component = match.groups()
    try:
        path = unquote_to_bytes(component).decode("utf-8", errors="strict")
        if re.search(r"%[0-9a-fA-F]{2}", path) or scoped_resource_uri(sid, path) != uri:
            raise InvalidReadRequest()
        return QualifiedPath(SpaceId(sid), path)
    except (ValueError, UnicodeError):
        raise InvalidReadRequest() from None


def project_scoped(result, *, resource_uris=False):
    items = []
    for item in result.items:
        sid, path = item.identity.space_id.value, item.identity.canonical_relative_path
        row = {"identity": {"space_id": sid, "path": path}, "title": item.title}
        if hasattr(item, "score"):
            row.update({key: round(getattr(item, key), 4) for key in ("score", "semantic_score", "lexical_score")})
            if not all(math.isfinite(row[key]) for key in ("score", "semantic_score", "lexical_score")):
                raise ValueError("invalid semantic result")
            row.update(snippet=item.snippet, heading=item.heading)
        else:
            row.update(snippet=item.snippet, modified=item.modified)
        if resource_uris:
            row["resource_uri"] = scoped_resource_uri(sid, path)
        items.append(row)
    return {
        "items": items,
        "coverage": [{"space_id": row.space_id.value, "state": row.state, "reasons": list(row.reasons),
                      "semantic_index_basis": row.semantic_index_basis, "enumerated_paths": row.enumerated_paths,
                      "evaluated_paths": row.evaluated_paths, "returned_candidates": row.returned_candidates}
                     for row in result.coverage],
        "state": result.state, "ordering": result.ordering, "result_limited": result.result_limited,
    }


def execute_scoped(operations, operation, data, *, resource_uris=False, cancel=None):
    selected, arguments = parse_scoped(operation, data)
    if operation in PLURAL_READS:
        method = {"list": operations.scoped_list, "literal": operations.scoped_literal_search,
                  "related": operations.scoped_semantic_search}[operation]
        result = method(scope=selected, cancel=cancel, **arguments)
        return project_scoped(result, resource_uris=resource_uris)
    if operation == "read":
        note = operations.read_note(arguments["path"], cancel=cancel)
        result = {"path": note.note.path, "content": note.note.content}
        sid = note.identity.space_id.value
        if resource_uris:
            result.update(resource_uri=scoped_resource_uri(sid, note.identity.canonical_relative_path),
                          mime_type="text/markdown")
    elif operation in {"links", "backlinks"}:
        from app.api.relationships import NoteBacklinksResponse, NoteLinksResponse

        method = operations.outgoing if operation == "links" else operations.backlinks
        envelope = method(arguments["path"], origin=Dialect.OBSIDIAN_WIKILINK, cancel=cancel)
        sid = envelope.space_id.value
        key, model, fields = (("links", NoteLinksResponse, ("target", "heading", "alias", "state", "resolved_path"))
                              if operation == "links" else
                              ("backlinks", NoteBacklinksResponse, ("source_path", "target", "heading", "alias")))
        result = model.model_validate({key: [{name: getattr(row, name) for name in fields}
                                            for row in envelope.result]}).model_dump(mode="json")
    elif operation == "duplicates":
        envelope = operations.duplicates(selected, cancel=cancel, **arguments)
        sid = envelope.space_id.value
        fields = ("path", "title", "match_type", "score", "semantic_score", "lexical_score", "snippet", "heading")
        rows = [{name: getattr(row, name) for name in fields} for row in envelope.result]
        if resource_uris:
            for row in rows:
                row["resource_uri"] = scoped_resource_uri(sid, row["path"])
        result = {"title": arguments["title"], "results": rows}
    else:
        envelope = operations.hygiene(selected, cancel=cancel, **arguments)
        sid = envelope.space_id.value
        result = serialize_hygiene_result(envelope.result).model_dump(mode="json")
    return {"space_id": sid, "result": result}


def public_error(exc):
    """Return code, HTTP status, CLI exit, never exception text."""
    if isinstance(exc, SpaceError):
        code = {"unknown_or_denied_space": "scope_not_allowed", "unsupported_capability": "capability_unavailable",
                "unsupported_dialect": "capability_unavailable", "indexing_disabled": "capability_unavailable",
                "incompatible_semantic_configuration": "capability_unavailable",
                "unavailable_space": "space_unavailable"}.get(exc.category, exc.category)
    elif isinstance(exc, KnowledgeHygieneError):
        code = {"invalid_request": "invalid_request", "scan_unavailable": "space_unavailable"}.get(
            exc.reason, "internal_error")
    elif isinstance(exc, NoteUnavailableError):
        code = "space_unavailable"
    elif isinstance(exc, SemanticSearchUnavailableError):
        code = "semantic_unavailable"
    elif isinstance(exc, KnowledgeQueryError):
        code = {"invalid_request": "invalid_request", "unsafe_scope": "invalid_path",
                "semantic_unavailable": "semantic_unavailable"}.get(exc.reason, "internal_error")
    else:
        code = next((code for kind, code in ((InvalidReadRequest, "invalid_request"),
                    (VaultValidationError, "invalid_path"), (NoteNotFoundError, "note_not_found"),
                    (NoteConflictError, "note_conflict"), (NoteTooLargeError, "size_limit"))
                     if isinstance(exc, kind)), "internal_error")
    status = {"invalid_scope": 422, "invalid_request": 422, "scope_not_allowed": 403,
              "capability_unavailable": 409, "invalid_path": 400, "note_not_found": 404,
              "note_conflict": 409, "size_limit": 413, "space_unavailable": 503,
              "semantic_unavailable": 503, "operation_cancelled": 503}.get(code, 500)
    return code, status, 1 if status in {404, 413, 503} or code == "note_conflict" else 2
