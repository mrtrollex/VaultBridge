"""One strict wire request for REST, MCP and structured CLI Knowledge Query.

Only constructs domain values. Predicate semantics, authorization, execution and
budgets remain in the existing scoped query service.
"""

from app.services._scoped_federation import validate_scoped_query
from app.services.knowledge_query import OMITTED, KnowledgeQuery, MetadataPredicate
from app.services.knowledge_spaces import Dialect, QualifiedPath, SpaceError, SpaceId
from app.services.scoped_reads import QualifiedRelationshipPredicate, ScopedKnowledgeQuery
from app.space_read_transport import SCOPE_FIELDS, InvalidReadRequest, _text, selection


def qualified_path(data):
    if type(data) is not dict or data.keys() != {"space_id", "path"}:
        raise InvalidReadRequest()
    return QualifiedPath(SpaceId(data["space_id"]), _text(data["path"], 1024))


def _collection(data, key, maximum):
    values = data.get(key, [])
    if type(values) is not list or len(values) > maximum:
        raise InvalidReadRequest()
    return values


def parse_knowledge_query(data):
    if type(data) is not dict:
        raise InvalidReadRequest()
    if (SCOPE_FIELDS & data.keys()) - {"spaces"}:
        raise SpaceError("invalid_scope")
    scope = selection("literal", {"spaces": data["spaces"]}) if "spaces" in data else None
    fields = {"spaces", "semantic_text", "literal_text", "folder", "paths", "tags",
              "metadata", "relationships", "limit"}
    if data.keys() - fields:
        raise InvalidReadRequest()
    arguments = {key: data[key] for key in ("semantic_text", "literal_text", "folder", "limit") if key in data}
    if any(value is None for value in arguments.values()):
        raise InvalidReadRequest()
    # Counts are charged on raw arrays, before any domain normalization.
    paths = tuple(qualified_path(row) for row in _collection(data, "paths", 64))
    tags = tuple(_collection(data, "tags", 16))
    metadata = []
    for row in _collection(data, "metadata", 16):
        if (type(row) is not dict or not {"key", "operator"} <= row.keys()
                or row.keys() - {"key", "operator", "value"}):
            raise InvalidReadRequest()
        metadata.append(MetadataPredicate(row["key"], row["operator"], row.get("value", OMITTED)))
    relationships = []
    for row in _collection(data, "relationships", 16):
        if (type(row) is not dict or not {"direction", "other"} <= row.keys()
                or row.keys() - {"direction", "other", "origin"}):
            raise InvalidReadRequest()
        try:
            origin = Dialect(row["origin"]) if "origin" in row else None
        except (ValueError, TypeError):
            raise InvalidReadRequest() from None
        relationships.append(QualifiedRelationshipPredicate(row["direction"], qualified_path(row["other"]), origin))
    request = ScopedKnowledgeQuery(scope, KnowledgeQuery(**arguments, tags=tags, metadata=tuple(metadata)),
                                   paths, tuple(relationships))
    validate_scoped_query(request)
    return request


def knowledge_query_schema():
    """The same discoverable logical schema on REST and both MCP transports."""
    sid = {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"}

    def text(maximum, minimum=1):
        return {"type": "string", "minLength": minimum,
                "description": f"At most {maximum:,} UTF-8 bytes; no coercion or invalid Unicode."}

    def array(items, maximum):
        return {"type": "array", "items": items, "maxItems": maximum, "default": []}

    path = {"type": "object", "additionalProperties": False, "required": ["space_id", "path"],
            "properties": {"space_id": sid, "path": text(1024)}}
    scalar = {"anyOf": [text(1024, 0), {"type": "boolean"}, {"type": "null"},
                        {"type": "integer", "minimum": -(2**63), "maximum": 2**63 - 1},
                        {"type": "number", "description": "Finite portable float; typed comparison."}]}
    metadata = {"type": "object", "additionalProperties": False, "required": ["key", "operator"],
                "properties": {"key": text(256, 0), "operator": {"enum": [
                    "exists", "equals", "not_equals", "sequence_contains"]}, "value": scalar},
                "allOf": [{"if": {"properties": {"operator": {"const": "exists"}}},
                           "then": {"not": {"required": ["value"]}}, "else": {"required": ["value"]}}]}
    relationship = {"type": "object", "additionalProperties": False, "required": ["direction", "other"],
                    "properties": {"direction": {"enum": ["incoming", "outgoing"]}, "other": path,
                                   "origin": {"enum": ["obsidian_wikilink", "markdown_link"]}}}
    return {"type": "object", "additionalProperties": False,
            "description": "Scoped Knowledge Query; omitted spaces selects default only. Domain validation applies; "
                           "empty requests are invalid. Arrays are bounded before normalization. "
                           "Tags, metadata and relationships use AND; paths use OR within selected scope. "
                           "Omitted relationship origin requires both dialects. Duplicate keys are rejected.",
            "properties": {"spaces": {"type": "array", "items": sid, "minItems": 1, "maxItems": 8},
                           "semantic_text": text(4096), "literal_text": text(8192), "folder": text(1024),
                           "paths": array(path, 64), "tags": array(text(1024), 16),
                           "metadata": array(metadata, 16), "relationships": array(relationship, 16),
                           "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}}
