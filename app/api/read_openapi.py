"""Describe existing read extensions without changing paths or operation IDs."""

from copy import deepcopy

from app.space_read_transport import (
    NoteMatchOutput,
    OneSpaceOutput,
    ScopedReadOutput,
    SemanticMatchOutput,
    schema_union,
    scoped_request_schema,
)


def install_read_openapi(application):
    original = application.openapi

    def openapi():
        schema = original()
        if getattr(application.state, "read_openapi_schema", None) is schema:
            return schema
        from app.api.relationships import NoteBacklinksResponse, NoteLinksResponse
        from app.api.search import DuplicateCandidatesResponse
        from app.knowledge_hygiene_transport import KnowledgeHygieneScanResponse

        one = {"duplicates": DuplicateCandidatesResponse, "links": NoteLinksResponse,
               "backlinks": NoteBacklinksResponse, "scan": KnowledgeHygieneScanResponse}
        reads = {"read": "read", "list": "list", "search": "literal", "related": "related",
                 "duplicates": "duplicates", "links": "links", "backlinks": "backlinks", "scan": "hygiene"}
        components = schema.setdefault("components", {}).setdefault("schemas", {})

        def register(value):
            value = deepcopy(value)
            definitions = value.pop("$defs", {})
            def references(item):
                if isinstance(item, dict):
                    return {key: (val.replace("#/$defs/", "#/components/schemas/") if key == "$ref"
                                  else references(val)) for key, val in item.items()}
                if isinstance(item, list):
                    return [references(val) for val in item]
                return item
            components.update({key: references(val) for key, val in definitions.items()})
            return references(value)

        for path, methods in schema["paths"].items():
            if path == "/api/v1/knowledge/query":
                from app.knowledge_query_transport import knowledge_query_schema

                endpoint = methods["post"]
                endpoint["requestBody"]["content"]["application/json"]["schema"] = knowledge_query_schema()
                for status in (400, 403, 409, 422, 503, 500):
                    endpoint["responses"][str(status)] = {
                        "description": "Privacy-safe scoped error",
                        "content": {"application/json": {"schema": {
                            "type": "object", "additionalProperties": False, "required": ["detail"],
                            "properties": {"detail": {"type": "string", "enum": {
                                400: ["invalid_path"], 403: ["scope_not_allowed"],
                                409: ["capability_unavailable"], 422: ["invalid_scope", "invalid_request"],
                                503: ["space_unavailable", "semantic_unavailable", "operation_cancelled"],
                                500: ["internal_error"],
                            }[status]}},
                        }}},
                    }
                continue
            leaf = path.rsplit("/", 1)[-1]
            if leaf not in reads or not (path.startswith("/notes/") or path.startswith("/api/v1/notes/")
                                         or path == "/api/v1/knowledge/hygiene/scan"):
                continue
            operation = reads[leaf]
            endpoint = methods["get" if "get" in methods else "post"]
            if "requestBody" in endpoint:
                body = endpoint["requestBody"]["content"]["application/json"]
                legacy = deepcopy(body["schema"])
                if "$ref" in legacy:
                    legacy = deepcopy(components[legacy["$ref"].rsplit("/", 1)[-1]])
                body["schema"] = scoped_request_schema(operation, legacy)
            else:
                field = "spaces" if operation == "list" else "space"
                sid = {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"}
                endpoint.setdefault("parameters", []).append({
                    "name": field, "in": "query", "required": False,
                    "schema": {"type": "array", "items": sid, "minItems": 1, "maxItems": 8}
                    if field == "spaces" else sid,
                    **({"style": "form", "explode": True} if field == "spaces" else {}),
                })
            if operation in {"list", "literal", "related"}:
                model = ScopedReadOutput[SemanticMatchOutput if operation == "related" else NoteMatchOutput]
                scoped = register(model.model_json_schema())
            elif leaf in one:
                scoped = register(OneSpaceOutput[one[leaf]].model_json_schema())
            else:
                # Read's exact two fields differ from the write NoteResult.
                legacy = {"type": "object", "additionalProperties": False,
                          "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                          "required": ["path", "content"]}
                scoped = {"type": "object", "additionalProperties": False,
                          "properties": {"space_id": {"type": "string"}, "result": legacy},
                          "required": ["space_id", "result"]}
            response = endpoint["responses"]["200"]["content"]["application/json"]
            response["schema"] = schema_union(response["schema"], scoped)
        application.state.read_openapi_schema = schema
        return schema

    application.openapi = openapi
