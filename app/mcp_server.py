from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, TypeVar
from urllib.parse import quote
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.server.mcpserver.tools.base import Tool
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS, CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.core.logging import configure_application_logging, log_event
from app.knowledge_hygiene_transport import (
    KnowledgeHygieneScanRequest,
    KnowledgeHygieneScanResponse,
    serialize_hygiene_result,
)
from app.mcp_read_input import HTTP_READ_LIFETIME, INVALID_SCOPED_JSON, StrictScopedLines
from app.read_execution import run_async_read, run_read
from app.services.adapter_composition import legacy_operations, validate_legacy_operations, validate_private_operations
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.indexer import BackgroundSemanticIndexer
from app.services.knowledge_hygiene import KnowledgeHygieneError, KnowledgeHygieneService
from app.services.knowledge_spaces import SpaceError
from app.services.note_writes import (
    clean_note_tags,
    clean_note_title,
)
from app.services.rate_limiter import FixedWindowRateLimiter
from app.services.relationships import RelationshipService
from app.services.semantic_search import (
    IndexState,
    SemanticSearchService,
    SemanticSearchUnavailableError,
    semantic_search_service_from_settings,
)
from app.services.space_operations import SpaceOperations
from app.services.space_runtime import compose_space_runtime
from app.services.vault import (
    NoteConflictError,
    NoteNotFoundError,
    NoteTooLargeError,
    VaultService,
    VaultValidationError,
)
from app.space_read_transport import (
    InvalidReadRequest,
    MCPNoteMatchOutput,
    MCPSemanticMatchOutput,
    ScopedReadOutput,
    execute_scoped,
    has_scope,
    parse_resource_uri,
    public_error,
    scoped_resource_uri,
)

MCP_SERVER_NAME = "VaultBridge"
MCP_SERVER_VERSION = "1.5.0-rc.1"
MCP_CLIENT_ID = "stdio-process"
MCPTransport = Literal["stdio", "streamable-http"]
NOTE_RESOURCE_TEMPLATE = "vaultbridge://note/{path}"
SPACE_NOTE_RESOURCE_TEMPLATE = "vaultbridge://space/{space_id}/note/{path}"
NOTE_MIME_TYPE = "text/markdown"
RELATED_CANDIDATE_OVERFETCH_FACTOR = 3
RELATED_CANDIDATE_LIMIT = 50

Folder = Annotated[str, Field(max_length=500)]
NotePath = Annotated[str, Field(min_length=1, max_length=700)]
LiteralQuery = Annotated[str, Field(min_length=1, max_length=300)]
RelatedText = Annotated[str, Field(min_length=2, max_length=4000)]
DuplicateTitle = Annotated[str, Field(min_length=1, max_length=180)]
DuplicateText = Annotated[str, Field(max_length=4000)]
CreateTitle = Annotated[str, Field(min_length=1, max_length=180)]
CreateContent = Annotated[str, Field(max_length=800_000)]
CreateTags = Annotated[list[str], Field(max_length=30)]
AppendContent = Annotated[str, Field(min_length=1, max_length=800_000)]
DedupeKey = Annotated[str, Field(max_length=120)]
ListLimit = Annotated[int, Field(ge=1, le=200)]
SearchLimit = Annotated[int, Field(ge=1, le=50)]
SemanticLimit = Annotated[int, Field(ge=1, le=20)]
MinimumScore = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]

logger = logging.getLogger("vaultbridge.mcp")
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
CREATE_NOTE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
APPEND_NOTE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


class MCPResult(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoteListItem(MCPResult):
    path: str
    title: str
    modified: str
    resource_uri: str


class ListNotesResult(MCPResult):
    folder: str
    notes: list[NoteListItem]


class ReadNoteResult(MCPResult):
    path: str
    content: str
    mime_type: str
    resource_uri: str


class LiteralSearchItem(MCPResult):
    path: str
    title: str
    snippet: str
    resource_uri: str


class SearchNotesResult(MCPResult):
    query: str
    results: list[LiteralSearchItem]


class RelatedNoteItem(MCPResult):
    path: str
    title: str
    score: float
    semantic_score: float
    lexical_score: float
    snippet: str
    heading: str | None
    resource_uri: str


class RelatedNotesResult(MCPResult):
    text: str
    results: list[RelatedNoteItem]


class DuplicateCandidateItem(MCPResult):
    path: str
    title: str
    match_type: str
    score: float | None
    semantic_score: float | None
    lexical_score: float | None
    snippet: str | None
    heading: str | None
    resource_uri: str


class DuplicateCandidatesResult(MCPResult):
    title: str
    results: list[DuplicateCandidateItem]


class NoteLinkItem(MCPResult):
    target: str
    heading: str | None
    alias: str | None
    state: Literal["resolved", "unresolved"]
    resolved_path: str | None


class NoteLinksResult(MCPResult):
    links: list[NoteLinkItem]


class NoteBacklinkItem(MCPResult):
    source_path: str
    target: str
    heading: str | None
    alias: str | None


class NoteBacklinksResult(MCPResult):
    backlinks: list[NoteBacklinkItem]


class NoteWriteResult(MCPResult):
    success: bool
    path: str
    status: Literal["created", "unchanged", "appended", "already_applied"]


class SemanticIndexRebuildingError(RuntimeError):
    """The persisted semantic index is currently owned by a rebuilding process."""


T = TypeVar("T")


class VaultBridgeMCPServer(MCPServer):
    async def run_stdio_async(self):
        # Keep the SDK's standard-stream claim/diversion/restore ownership; only
        # its line iterator gains validation before duplicate keys are dropped.
        import sys

        import anyio
        from mcp.server.stdio import _claim_fd, _open_stdin_diversion, _UnownedTextWrapper, stdio_server

        stdin, restore = _claim_fd(0, sys.stdin, "rb", _open_stdin_diversion)
        try:
            # Retain malformed bytes until strict Query/scope classification.
            source = anyio.wrap_file(_UnownedTextWrapper(stdin, encoding="utf-8", errors="surrogateescape"))
            async with stdio_server(stdin=StrictScopedLines(source)) as (read_stream, write_stream):
                await self._lowlevel_server.run(
                    read_stream, write_stream, self._lowlevel_server.create_initialization_options(),
                )
        finally:
            if restore is not None:
                restore()


def _canonical_service_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
    ):
        raise RuntimeError("service returned an unsafe path")
    return posix_path.as_posix()


def note_resource_uri(path: str) -> str:
    canonical_path = _canonical_service_path(path)
    return f"vaultbridge://note/{quote(canonical_path, safe='')}"


def _safe_tool_error(text: str) -> str:
    for code in ("invalid_scope", "invalid_request", "scope_not_allowed", "capability_unavailable",
                 "invalid_path", "note_not_found", "note_conflict", "size_limit", "space_unavailable",
                 "semantic_unavailable", "operation_cancelled", "internal_error"):
        if re.fullmatch(r"(?:Error executing tool [a-z_]+: )?" + code + r": Operation failed\.", text):
            return f"{code}: Operation failed."
    messages = {
        "hygiene_unavailable": "hygiene_unavailable: A bounded hygiene scan is unavailable.",
        "invalid_path": "invalid_path: Invalid vault-relative path.",
        "note_not_found": "note_not_found: Note not found.",
        "note_conflict": "note_conflict: A note with this title already exists.",
        "validation_error": "validation_error: Tool arguments or requested content are invalid.",
        "semantic_index_unavailable": (
            "semantic_index_unavailable: A compatible ready semantic index is unavailable."
        ),
        "semantic_index_rebuilding": (
            "semantic_index_rebuilding: The semantic index is being rebuilt; retry later."
        ),
        "internal_error": "internal_error: The operation could not be completed.",
    }
    for code, message in messages.items():
        if f"{code}:" in text:
            return message
    if "rate_limited:" in text:
        match = re.search(r"retry in (\d+) seconds", text)
        retry_after = min(86_400, max(1, int(match.group(1)))) if match else 1
        return f"rate_limited: Operation limit exceeded; retry in {retry_after} seconds."
    return messages["validation_error"] if "validation error" in text.casefold() else messages["internal_error"]


async def _privacy_error_middleware(
    context: ServerRequestContext[Any, Any],
    call_next: CallNext,
) -> HandlerResult:
    # Validate the raw argument object before the SDK can coerce or drop fields.
    hygiene_call = (
        context.method == "tools/call" and isinstance(context.params, dict)
        and context.params.get("name") == "knowledge_hygiene_scan"
    )
    if hygiene_call:
        try:
            KnowledgeHygieneScanRequest.model_validate(context.params.get("arguments", {}))
        except ValidationError:
            return CallToolResult(is_error=True, content=[
                TextContent(type="text", text="validation_error: Tool arguments are invalid."),
            ])
    try:
        result = await call_next(context)
    except MCPError as exc:
        if context.method != "resources/read":
            raise
        if "rate_limited:" in exc.message:
            message = _safe_tool_error(exc.message)
            raise MCPError(code=INTERNAL_ERROR, message=message) from None
        message = "note_resource_unavailable: Note resource is invalid or unavailable."
        code = INVALID_PARAMS if exc.code == INVALID_PARAMS else INTERNAL_ERROR
        raise MCPError(code=code, message=message) from None

    if context.method == "tools/call" and isinstance(result, dict) and result.get("isError"):
        content = result.get("content")
        text = ""
        if isinstance(content, list) and content and isinstance(content[0], dict):
            candidate = content[0].get("text")
            text = candidate if isinstance(candidate, str) else ""
        sanitized = dict(result)
        message = _safe_tool_error(text)
        if hygiene_call and message.startswith("validation_error:"):
            message = "validation_error: Tool arguments are invalid."
        sanitized["content"] = [{"type": "text", "text": message}]
        sanitized.pop("structuredContent", None)
        return sanitized
    return result


def _scoped_read_middleware(adapter):
    operations = {"list_notes": "list", "read_note": "read", "search_notes": "literal",
                  "related_notes": "related", "duplicate_candidates": "duplicates",
                  "note_links": "links", "note_backlinks": "backlinks", "knowledge_hygiene_scan": "hygiene",
                  "knowledge_query": "query"}
    if adapter is not None and adapter.settings.mcp_write_enabled:
        operations.update(create_note="create", append_note="append")

    @contextmanager
    def read_lifetime(context):
        request = context.request
        lifetime = request.scope.get(HTTP_READ_LIFETIME) if request is not None else None
        if lifetime is None:
            yield {}
        else:
            with lifetime.read() as token:
                yield {"cancel": token, "disconnected": lifetime.is_disconnected}

    async def dispatch(context, call_next):
        params = context.params if isinstance(context.params, dict) else {}
        if INVALID_SCOPED_JSON in params:
            raise MCPError(code=INVALID_PARAMS,
                           message="note_resource_unavailable: Note resource is invalid or unavailable.")
        arguments = params.get("arguments", {})
        if (params.get("name") in {"create_note", "append_note"}
                and not adapter.settings.mcp_write_enabled):
            return await _privacy_error_middleware(context, call_next)
        if context.method == "tools/call" and (has_scope(arguments) or params.get("name") == "knowledge_query"):
            name = params.get("name")
            try:
                if name not in operations:
                    raise SpaceError("invalid_scope")

                def action(token):
                    def execute():
                        # Convert inside the action so _execute never applies a
                        # legacy exception category to an explicit scoped call.
                        try:
                            if type(arguments) is not dict or INVALID_SCOPED_JSON in arguments:
                                raise InvalidReadRequest()
                            if operations[name] in {"create", "append"}:
                                from app.space_write_transport import execute_note_write

                                return execute_note_write(adapter.operations, operations[name], arguments, cancel=token)
                            return execute_scoped(adapter.operations, operations[name], arguments,
                                                  resource_uris=True, cancel=token)
                        except Exception as exc:
                            code, _, _ = public_error(exc)
                            raise ToolError(f"{code}: Operation failed.") from None
                    return adapter._execute(name, execute, result_count=lambda _: 0)

                with read_lifetime(context) as cancellation:
                    result = await run_read(action, **cancellation)
                return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))],
                                      structured_content=result)
            except Exception as exc:
                code = str(exc).split(":", 1)[0] if isinstance(exc, ToolError) else public_error(exc)[0]
                return CallToolResult(
                    is_error=True, content=[TextContent(type="text", text=f"{code}: Operation failed.")]
                )
        if context.method == "resources/read" or (context.method == "tools/call"
                and params.get("name") in operations and operations[params["name"]] not in {"create", "append"}):
            with read_lifetime(context) as cancellation:
                return await run_async_read(lambda: _privacy_error_middleware(context, call_next), **cancellation)
        return await _privacy_error_middleware(context, call_next)

    return dispatch


class VaultBridgeMCPAdapter:
    def __init__(
        self,
        *,
        settings: Settings,
        vault_service: VaultService,
        semantic_search_service: SemanticSearchService,
        duplicate_candidate_service: DuplicateCandidateService,
        relationship_service: RelationshipService,
        knowledge_hygiene_service: KnowledgeHygieneService,
        semantic_indexer: BackgroundSemanticIndexer | None,
        transport: MCPTransport,
        operation_rate_limiter: FixedWindowRateLimiter | None,
        operations: SpaceOperations,
    ) -> None:
        if settings.knowledge_spaces_json is not None:
            validate_private_operations(operations, settings=settings, vault=vault_service,
                semantic=semantic_search_service, duplicates=duplicate_candidate_service,
                relationships=relationship_service, hygiene=knowledge_hygiene_service, indexer=semantic_indexer)
        self.settings = settings
        self.operations = operations
        self.vault_service = vault_service
        self.semantic_search_service = semantic_search_service
        self.duplicate_candidate_service = duplicate_candidate_service
        self.relationship_service = relationship_service
        self.knowledge_hygiene_service = knowledge_hygiene_service
        self.semantic_indexer = semantic_indexer
        self.transport = transport
        self.operation_rate_limiter = operation_rate_limiter

    def _execute(self, operation: str, action: Callable[[], T], *, result_count: Callable[[T], int]) -> T:
        request_id = uuid4().hex
        started_at = time.perf_counter()
        try:
            if self.settings.rate_limit_enabled and self.operation_rate_limiter is not None:
                decision = self.operation_rate_limiter.check(MCP_CLIENT_ID)
                if not decision.allowed:
                    retry_after = decision.retry_after_seconds or 1
                    raise ToolError(
                        f"rate_limited: Operation limit exceeded; retry in {retry_after} seconds."
                    )
            result = action()
        except ToolError as exc:
            code = _safe_tool_error(str(exc)).split(":", 1)[0]
            self._log_failure(operation, request_id, started_at, code)
            raise
        except SpaceError as exc:
            code, _, _ = public_error(exc)
            self._log_failure(operation, request_id, started_at, code)
            raise ToolError(f"{code}: Operation failed.") from None
        except KnowledgeHygieneError as exc:
            code = {"invalid_request": "validation_error", "scan_unavailable": "hygiene_unavailable"}.get(
                exc.reason, "internal_error",
            )
            self._log_failure(operation, request_id, started_at, code)
            message = (
                "validation_error: Tool arguments are invalid."
                if code == "validation_error" else _safe_tool_error(f"{code}:")
            )
            raise ToolError(message) from None
        except VaultValidationError as exc:
            self._log_failure(operation, request_id, started_at, "invalid_path")
            raise ToolError("invalid_path: Invalid vault-relative path.") from exc
        except NoteNotFoundError as exc:
            self._log_failure(operation, request_id, started_at, "note_not_found")
            raise ToolError("note_not_found: Note not found.") from exc
        except NoteConflictError as exc:
            self._log_failure(operation, request_id, started_at, "note_conflict")
            raise ToolError("note_conflict: A note with this title already exists.") from exc
        except NoteTooLargeError as exc:
            self._log_failure(operation, request_id, started_at, "validation_error")
            raise ToolError("validation_error: Requested note exceeds the configured limit.") from exc
        except SemanticIndexRebuildingError as exc:
            self._log_failure(operation, request_id, started_at, "semantic_index_rebuilding")
            raise ToolError(
                "semantic_index_rebuilding: The semantic index is being rebuilt; retry later."
            ) from exc
        except SemanticSearchUnavailableError as exc:
            self._log_failure(operation, request_id, started_at, "semantic_index_unavailable")
            raise ToolError(
                "semantic_index_unavailable: A compatible ready semantic index is unavailable."
            ) from exc
        except (ValueError, ValidationError) as exc:
            if operation == "knowledge_hygiene_scan":
                self._log_failure(operation, request_id, started_at, "internal_error",
                                  error_type=type(exc).__name__)
                raise ToolError("internal_error: The operation could not be completed.") from None
            self._log_failure(operation, request_id, started_at, "validation_error")
            raise ToolError("validation_error: Tool arguments are invalid.") from exc
        except Exception as exc:
            self._log_failure(
                operation,
                request_id,
                started_at,
                "internal_error",
                error_type=type(exc).__name__,
                exc_info=None
                if operation == "knowledge_hygiene_scan" or self.settings.knowledge_spaces_json is not None
                else (type(exc), exc, exc.__traceback__),
            )
            raise ToolError("internal_error: The operation could not be completed.") from exc

        log_event(
            logger,
            logging.INFO,
            "mcp_operation_completed",
            "MCP operation completed",
            request_id=request_id,
            transport=self.transport,
            operation=operation,
            result_count=result_count(result),
            duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
        )
        return result

    def _log_failure(
        self,
        operation: str,
        request_id: str,
        started_at: float,
        failure_class: str,
        *,
        error_type: str | None = None,
        exc_info: Any = None,
    ) -> None:
        log_event(
            logger,
            logging.WARNING if error_type is None else logging.ERROR,
            "mcp_operation_failed",
            "MCP operation failed",
            request_id=request_id,
            transport=self.transport,
            operation=operation,
            failure_class=failure_class,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
            error_type=error_type,
            exc_info=exc_info,
        )

    def _require_semantic_index(self, service=None) -> None:
        semantic = service if service is not None else self.semantic_search_service
        try:
            if self.settings.knowledge_spaces_json is not None:
                if semantic.probe_search_availability():
                    return
                raise SemanticSearchUnavailableError()
            if self.transport == "stdio":
                if semantic.use_persisted_index_for_read_only_search():
                    return
                inspection = semantic.inspect_persisted_index()
            else:
                inspection = semantic.inspect_index()
                if inspection.search_available:
                    return
        except Exception as exc:
            raise SemanticSearchUnavailableError from exc
        if inspection.state is IndexState.INDEXING:
            raise SemanticIndexRebuildingError
        raise SemanticSearchUnavailableError

    def knowledge_hygiene_scan(self, request: KnowledgeHygieneScanRequest) -> KnowledgeHygieneScanResponse:
        return self._execute(
            "knowledge_hygiene_scan",
            lambda: serialize_hygiene_result(
                self.operations.compatibility_read("hygiene", request=request.to_domain())
            ),
            result_count=lambda result: len(result.findings),
        )

    def list_notes(self, *, folder: str, limit: int) -> ListNotesResult:
        def action() -> ListNotesResult:
            notes = self.operations.compatibility_read("list", folder=folder, limit=limit)
            return ListNotesResult(
                folder=folder,
                notes=[
                    NoteListItem(
                        path=(path := _canonical_service_path(note.path)),
                        title=note.title,
                        modified=note.modified,
                        resource_uri=note_resource_uri(path),
                    )
                    for note in notes
                ],
            )

        return self._execute("list_notes", action, result_count=lambda result: len(result.notes))

    def read_note(self, *, path: str) -> ReadNoteResult:
        def action() -> ReadNoteResult:
            note = self.operations.compatibility_read("read", path=path)
            canonical_path = _canonical_service_path(note.path)
            return ReadNoteResult(
                path=canonical_path,
                content=note.content,
                mime_type=NOTE_MIME_TYPE,
                resource_uri=note_resource_uri(canonical_path),
            )

        return self._execute("read_note", action, result_count=lambda _result: 1)

    def search_notes(self, *, query: str, folder: str, limit: int) -> SearchNotesResult:
        def action() -> SearchNotesResult:
            results = self.operations.compatibility_read("literal", query=query, folder=folder, limit=limit)
            return SearchNotesResult(
                query=query,
                results=[
                    LiteralSearchItem(
                        path=(path := _canonical_service_path(result.path)),
                        title=result.title,
                        snippet=result.snippet,
                        resource_uri=note_resource_uri(path),
                    )
                    for result in results
                ],
            )

        return self._execute("search_notes", action, result_count=lambda result: len(result.results))

    def related_notes(
        self,
        *,
        text: str,
        folder: str,
        limit: int,
        min_score: float,
    ) -> RelatedNotesResult:
        def action() -> RelatedNotesResult:
            results = self.operations.compatibility_read(
                "related", text=text, folder=folder, limit=limit, min_score=min_score,
                prepare=self._require_semantic_index,
            )
            verified: list[RelatedNoteItem] = []
            for verified_path, result in results:
                canonical_path = _canonical_service_path(verified_path)
                verified.append(
                    RelatedNoteItem(
                        path=canonical_path,
                        title=PurePosixPath(canonical_path).stem,
                        score=result.score,
                        semantic_score=result.semantic_score,
                        lexical_score=result.lexical_score,
                        snippet=result.snippet,
                        heading=result.heading,
                        resource_uri=note_resource_uri(canonical_path),
                    )
                )
                if len(verified) >= limit:
                    break
            return RelatedNotesResult(text=text, results=verified)

        return self._execute("related_notes", action, result_count=lambda result: len(result.results))

    def duplicate_candidates(
        self,
        *,
        title: str,
        text: str,
        folder: str,
        limit: int,
        min_score: float,
    ) -> DuplicateCandidatesResult:
        def action() -> DuplicateCandidatesResult:
            candidates = self.operations.compatibility_read(
                "duplicates", prepare=self._require_semantic_index,
                title=title,
                text=text,
                folder=folder,
                limit=limit,
                min_score=min_score,
            )
            return DuplicateCandidatesResult(
                title=title,
                results=[
                    DuplicateCandidateItem(
                        path=(path := _canonical_service_path(candidate.path)),
                        title=candidate.title,
                        match_type=candidate.match_type,
                        score=candidate.score,
                        semantic_score=candidate.semantic_score,
                        lexical_score=candidate.lexical_score,
                        snippet=candidate.snippet,
                        heading=candidate.heading,
                        resource_uri=note_resource_uri(path),
                    )
                    for candidate in candidates
                ],
            )

        return self._execute(
            "duplicate_candidates",
            action,
            result_count=lambda result: len(result.results),
        )

    def note_links(self, *, path: str) -> NoteLinksResult:
        def action() -> NoteLinksResult:
            relationships = self.operations.compatibility_read("links", path=path)
            return NoteLinksResult(
                links=[
                    NoteLinkItem(
                        target=relationship.target,
                        heading=relationship.heading,
                        alias=relationship.alias,
                        state=relationship.state,
                        resolved_path=(
                            _canonical_service_path(relationship.resolved_path)
                            if relationship.resolved_path is not None
                            else None
                        ),
                    )
                    for relationship in relationships
                ]
            )

        return self._execute("note_links", action, result_count=lambda result: len(result.links))

    def note_backlinks(self, *, path: str) -> NoteBacklinksResult:
        def action() -> NoteBacklinksResult:
            backlinks = self.operations.compatibility_read("backlinks", path=path)
            return NoteBacklinksResult(
                backlinks=[
                    NoteBacklinkItem(
                        source_path=_canonical_service_path(backlink.source_path),
                        target=backlink.target,
                        heading=backlink.heading,
                        alias=backlink.alias,
                    )
                    for backlink in backlinks
                ]
            )

        return self._execute(
            "note_backlinks",
            action,
            result_count=lambda result: len(result.backlinks),
        )

    def create_note(
        self,
        *,
        title: str,
        folder: str,
        content: str,
        tags: list[str],
    ) -> NoteWriteResult:
        def action() -> NoteWriteResult:
            result = self.operations.compatibility_write("create", canonical_enqueue=True, request=dict(
                title=clean_note_title(title),
                folder=folder,
                content=content,
                tags=clean_note_tags(tags),
            ))
            canonical_path = _canonical_service_path(result.path)
            return NoteWriteResult(success=True, path=canonical_path, status=result.status)

        return self._execute("create_note", action, result_count=lambda _result: 1)

    def append_note(
        self,
        *,
        path: str,
        content: str,
        dedupe_key: str | None,
    ) -> NoteWriteResult:
        def action() -> NoteWriteResult:
            result = self.operations.compatibility_write("append", canonical_enqueue=True, request=dict(
                path=path,
                content=content,
                dedupe_key=dedupe_key,
            ))
            canonical_path = _canonical_service_path(result.path)
            return NoteWriteResult(success=True, path=canonical_path, status=result.status)

        return self._execute("append_note", action, result_count=lambda _result: 1)

    def read_note_resource(self, *, path: str, raw_uri: str) -> str:
        def action() -> str:
            if raw_uri != note_resource_uri(path):
                raise VaultValidationError
            return self.operations.compatibility_read("read", path=path).content

        try:
            return self._execute("read_note_resource", action, result_count=lambda _result: 1)
        except ToolError as exc:
            message = _safe_tool_error(str(exc))
            if message.startswith("rate_limited:"):
                raise ResourceError(message) from exc
            raise ResourceNotFoundError(
                "note_resource_unavailable: Note resource is invalid or unavailable."
            ) from exc


def create_mcp_server(
    *,
    settings: Settings | None = None,
    vault_service: VaultService | None = None,
    semantic_search_service: SemanticSearchService | None = None,
    duplicate_candidate_service: DuplicateCandidateService | None = None,
    relationship_service: RelationshipService | None = None,
    knowledge_hygiene_service: KnowledgeHygieneService | None = None,
    semantic_indexer: BackgroundSemanticIndexer | None = None,
    rate_limiter: FixedWindowRateLimiter | None = None,
    transport: MCPTransport = "stdio",
    space_operations: SpaceOperations | None = None,
) -> MCPServer:
    app_settings = settings if settings is not None else Settings.from_env()
    if app_settings.knowledge_spaces_json is not None:
        if any(owner is not None for owner in (
            vault_service, semantic_search_service, duplicate_candidate_service,
            relationship_service, knowledge_hygiene_service, semantic_indexer,
        )) or transport not in {"stdio", "streamable-http"} or (
            (space_operations is None) != (transport == "stdio")
        ):
            raise SpaceError("invalid_configuration", reason="named_serving_unsupported")
        runtime = None
        if space_operations is None:
            runtime = compose_space_runtime(app_settings)
            space_operations = runtime.operations
        validate_private_operations(space_operations, settings=app_settings)
        limiter = rate_limiter
        if transport == "stdio" and limiter is None:
            limiter = FixedWindowRateLimiter(
                requests=app_settings.rate_limit_requests,
                window_seconds=app_settings.rate_limit_window_seconds, max_clients=1,
            )

        @asynccontextmanager
        async def owned_lifespan(_server):
            try:
                await run_read(lambda _cancel: runtime.start())
                yield
            finally:
                runtime.begin_shutdown()
                await run_read(lambda _cancel: runtime.shutdown())

        adapter = VaultBridgeMCPAdapter(
            settings=app_settings, operations=space_operations, vault_service=None,
            semantic_search_service=None, duplicate_candidate_service=None, relationship_service=None,
            knowledge_hygiene_service=None, semantic_indexer=None, transport=transport,
            operation_rate_limiter=limiter,
        )
        server = _read_server(adapter, lifespan=owned_lifespan if runtime is not None else None)
        server._vaultbridge_runtime = runtime
        return server
    app_vault_service = vault_service or VaultService(
        vault_root=app_settings.vault_path,
        max_note_bytes=app_settings.max_note_bytes,
    )
    app_semantic_service = semantic_search_service or semantic_search_service_from_settings(app_settings)
    app_duplicate_service = duplicate_candidate_service or DuplicateCandidateService(
        vault_service=app_vault_service,
        semantic_search_service=app_semantic_service,
    )
    app_relationship_service = relationship_service or RelationshipService(app_vault_service)
    app_hygiene_service = (
        knowledge_hygiene_service if knowledge_hygiene_service is not None else KnowledgeHygieneService(
            vault_service=app_vault_service,
            relationship_service=app_relationship_service,
            duplicate_candidate_service=app_duplicate_service,
            semantic_search_service=app_semantic_service,
        )
    )
    if app_settings.mcp_write_enabled and semantic_indexer is None:
        raise ValueError("MCP writes require an owned semantic indexer")
    operation_rate_limiter = rate_limiter
    if transport == "stdio" and operation_rate_limiter is None:
        operation_rate_limiter = FixedWindowRateLimiter(
            requests=app_settings.rate_limit_requests,
            window_seconds=app_settings.rate_limit_window_seconds,
            max_clients=1,
        )
    adapter = VaultBridgeMCPAdapter(
        settings=app_settings,
        vault_service=app_vault_service,
        semantic_search_service=app_semantic_service,
        duplicate_candidate_service=app_duplicate_service,
        relationship_service=app_relationship_service,
        knowledge_hygiene_service=app_hygiene_service,
        semantic_indexer=semantic_indexer,
        transport=transport,
        operation_rate_limiter=operation_rate_limiter,
        operations=(validate_legacy_operations(
            app_settings, space_operations, vault=app_vault_service, semantic=app_semantic_service,
            relationships=app_relationship_service, duplicates=app_duplicate_service, hygiene=app_hygiene_service,
        ) if space_operations is not None else legacy_operations(
            app_settings, indexer=semantic_indexer, vault=app_vault_service, semantic=app_semantic_service,
            relationships=app_relationship_service, duplicates=app_duplicate_service, hygiene=app_hygiene_service,
        )),
    )
    return _read_server(adapter)


def create_private_read_server(*, settings, operations, transport="stdio", rate_limiter=None, **raw_owners):
    validate_private_operations(operations, settings=settings, **raw_owners)
    limiter = rate_limiter
    if transport == "stdio" and limiter is None:
        limiter = FixedWindowRateLimiter(requests=settings.rate_limit_requests,
                                        window_seconds=settings.rate_limit_window_seconds, max_clients=1)
    adapter = VaultBridgeMCPAdapter(
        settings=settings, operations=operations, vault_service=None, semantic_search_service=None,
        duplicate_candidate_service=None, relationship_service=None, knowledge_hygiene_service=None,
        semantic_indexer=None, transport=transport, operation_rate_limiter=limiter,
    )
    return _read_server(adapter)


def _read_server(adapter, *, lifespan=None):
    app_settings = adapter.settings
    defaults = KnowledgeHygieneScanRequest()

    def knowledge_hygiene_scan(
        groups: list[str] = defaults.groups,
        finding_limit: int = defaults.finding_limit,
        duplicate_source_limit: int = defaults.duplicate_source_limit,
        semantic_candidates: bool = defaults.semantic_candidates,
        inspect_derived_index: bool = defaults.inspect_derived_index,
    ) -> KnowledgeHygieneScanResponse:
        """Run bounded, read-only Knowledge Hygiene diagnostics over canonical Markdown notes.

        Results may be partial; duplicate candidates are advisory and semantic candidate evidence
        is currently unavailable. Does not repair notes or refresh indexes.
        """
        request = KnowledgeHygieneScanRequest(
            groups=groups, finding_limit=finding_limit, duplicate_source_limit=duplicate_source_limit,
            semantic_candidates=semantic_candidates, inspect_derived_index=inspect_derived_index,
        )
        return adapter.knowledge_hygiene_scan(request)

    hygiene_tool = Tool.from_function(
        knowledge_hygiene_scan, annotations=READ_ONLY_ANNOTATIONS, structured_output=True,
    )
    hygiene_tool.parameters = KnowledgeHygieneScanRequest.model_json_schema()
    server = VaultBridgeMCPServer(
        MCP_SERVER_NAME,
        description=(
            "Contained read/write access to an Obsidian Markdown vault."
            if app_settings.mcp_write_enabled
            else "Read-only local access to a contained Obsidian Markdown vault."
        ),
        version=MCP_SERVER_VERSION,
        log_level="CRITICAL",
        middleware=[_scoped_read_middleware(adapter)],
        tools=[hygiene_tool],
        lifespan=lifespan,
    )
    server._vaultbridge_operations = adapter.operations

    @server.tool(annotations=READ_ONLY_ANNOTATIONS, structured_output=True)
    def knowledge_query() -> ScopedReadOutput[MCPNoteMatchOutput | MCPSemanticMatchOutput]:
        """Run a bounded policy-authorized Knowledge Query over selected spaces."""
        raise ToolError("internal_error: Operation failed.")  # Raw middleware owns dispatch.

    @server.tool(annotations=READ_ONLY_ANNOTATIONS, structured_output=True)
    def list_notes(folder: Folder = "", limit: ListLimit = 50) -> ListNotesResult:
        """List contained Markdown notes in a vault-relative folder."""
        return adapter.list_notes(folder=folder, limit=limit)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS, structured_output=True)
    def read_note(path: NotePath) -> ReadNoteResult:
        """Read one contained Markdown note by its vault-relative path."""
        return adapter.read_note(path=path)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS, structured_output=True)
    def search_notes(
        query: LiteralQuery,
        folder: Folder = "",
        limit: SearchLimit = 10,
    ) -> SearchNotesResult:
        """Search note titles and Markdown content literally; this is not semantic search."""
        return adapter.search_notes(query=query, folder=folder, limit=limit)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS, structured_output=True)
    def related_notes(
        text: RelatedText,
        folder: Folder = "",
        limit: SemanticLimit = 5,
        min_score: MinimumScore = 0.28,
    ) -> RelatedNotesResult:
        """Find related live notes using the existing local hybrid semantic index."""
        return adapter.related_notes(text=text, folder=folder, limit=limit, min_score=min_score)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS, structured_output=True)
    def duplicate_candidates(
        title: DuplicateTitle,
        text: DuplicateText = "",
        folder: Folder = "",
        limit: SemanticLimit = 5,
        min_score: MinimumScore = 0.28,
    ) -> DuplicateCandidatesResult:
        """Return advisory duplicate candidates without changing any note."""
        return adapter.duplicate_candidates(
            title=title,
            text=text,
            folder=folder,
            limit=limit,
            min_score=min_score,
        )

    @server.tool(annotations=READ_ONLY_ANNOTATIONS, structured_output=True)
    def note_links(path: NotePath) -> NoteLinksResult:
        """List outgoing relationships from one contained Markdown note."""
        return adapter.note_links(path=path)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS, structured_output=True)
    def note_backlinks(path: NotePath) -> NoteBacklinksResult:
        """List verified backlinks to one contained Markdown note."""
        return adapter.note_backlinks(path=path)

    if app_settings.mcp_write_enabled:

        @server.tool(annotations=CREATE_NOTE_ANNOTATIONS, structured_output=True)
        def create_note(
            title: CreateTitle,
            folder: Folder = "Inbox",
            content: CreateContent = "",
            tags: CreateTags = [],
        ) -> NoteWriteResult:
            """Create a contained Markdown note without overwriting an existing note."""
            return adapter.create_note(title=title, folder=folder, content=content, tags=tags)

        @server.tool(annotations=APPEND_NOTE_ANNOTATIONS, structured_output=True)
        def append_note(
            path: NotePath,
            content: AppendContent,
            dedupe_key: DedupeKey | None = None,
        ) -> NoteWriteResult:
            """Append Markdown to one existing contained note."""
            return adapter.append_note(path=path, content=content, dedupe_key=dedupe_key)

    @server.resource(
        NOTE_RESOURCE_TEMPLATE,
        name="note",
        description="Read one contained Markdown note by canonical VaultBridge Resource URI.",
        mime_type=NOTE_MIME_TYPE,
    )
    def read_note_resource(path: str, ctx: Context) -> str:
        raw_params = ctx.request_context.params or {}
        raw_uri = raw_params.get("uri")
        if not isinstance(raw_uri, str):
            raise ResourceNotFoundError(
                "note_resource_unavailable: Note resource is invalid or unavailable."
            )
        return adapter.read_note_resource(path=path, raw_uri=raw_uri)

    @server.resource(
        SPACE_NOTE_RESOURCE_TEMPLATE, name="space_note", mime_type=NOTE_MIME_TYPE,
        description="Read one policy-authorized qualified Markdown note.",
    )
    def read_space_note_resource(space_id: str, path: str, ctx: Context) -> str:
        uri = (ctx.request_context.params or {}).get("uri")
        def action():
            qualified = parse_resource_uri(uri)
            result = adapter.operations.read_note(qualified)
            if scoped_resource_uri(
                result.identity.space_id.value, result.identity.canonical_relative_path
            ) != uri:
                raise VaultValidationError()
            return result.note.content
        try:
            return adapter._execute("read_note_resource", action, result_count=lambda _: 1)
        except Exception:
            raise ResourceNotFoundError(
                "note_resource_unavailable: Note resource is invalid or unavailable."
            ) from None

    from app.space_read_transport import (
        MCPNoteMatchOutput,
        MCPSemanticMatchOutput,
        OneSpaceOutput,
        ScopedReadOutput,
        schema_union,
        scoped_request_schema,
    )
    schemas = {
        "list_notes": ("list", ScopedReadOutput[MCPNoteMatchOutput]),
        "search_notes": ("literal", ScopedReadOutput[MCPNoteMatchOutput]),
        "related_notes": ("related", ScopedReadOutput[MCPSemanticMatchOutput]),
        "read_note": ("read", OneSpaceOutput[ReadNoteResult]),
        "duplicate_candidates": ("duplicates", OneSpaceOutput[DuplicateCandidatesResult]),
        "note_links": ("links", OneSpaceOutput[NoteLinksResult]),
        "note_backlinks": ("backlinks", OneSpaceOutput[NoteBacklinksResult]),
        "knowledge_hygiene_scan": ("hygiene", OneSpaceOutput[KnowledgeHygieneScanResponse]),
    }
    if app_settings.mcp_write_enabled:
        schemas.update(create_note=("create", OneSpaceOutput[NoteWriteResult]),
                       append_note=("append", OneSpaceOutput[NoteWriteResult]))
    from app.knowledge_query_transport import knowledge_query_schema

    query_tool = server._tool_manager.get_tool("knowledge_query")
    query_tool.parameters = knowledge_query_schema()
    query_tool.fn_metadata.output_schema = schema_union(
        ScopedReadOutput[MCPNoteMatchOutput].model_json_schema(),
        ScopedReadOutput[MCPSemanticMatchOutput].model_json_schema(),
    )
    for name, (operation, model) in schemas.items():
        tool = server._tool_manager.get_tool(name)
        tool.parameters = scoped_request_schema(operation, tool.parameters)
        tool.fn_metadata.output_schema = schema_union(tool.fn_metadata.output_schema, model.model_json_schema())

    return server


def main() -> int:
    configure_application_logging()
    semantic_indexer: BackgroundSemanticIndexer | None = None
    try:
        settings = Settings.from_env()
        if settings.knowledge_spaces_json is not None:
            return _run_named_stdio(settings)
        vault_service = VaultService(
            vault_root=settings.vault_path,
            max_note_bytes=settings.max_note_bytes,
        )
        if not vault_service.vault_available():
            raise RuntimeError("vault unavailable")
        semantic_service = semantic_search_service_from_settings(settings)
        if settings.mcp_write_enabled:
            semantic_indexer = BackgroundSemanticIndexer(
                semantic_service.sync,
                semantic_service.sync_paths,
            )
        server = create_mcp_server(
            settings=settings,
            vault_service=vault_service,
            semantic_search_service=semantic_service,
            semantic_indexer=semantic_indexer,
        )
    except Exception as exc:
        if semantic_indexer is not None:
            semantic_indexer.shutdown()
        log_event(
            logger,
            logging.ERROR,
            "mcp_startup_failed",
            "VaultBridge MCP stdio startup failed",
            transport="stdio",
            error_type=type(exc).__name__,
        )
        return 2

    log_event(
        logger,
        logging.INFO,
        "mcp_server_started",
        "VaultBridge MCP stdio server started",
        transport="stdio",
    )
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "mcp_server_failed",
            "VaultBridge MCP stdio server failed",
            transport="stdio",
            error_type=type(exc).__name__,
        )
        return 1
    finally:
        operations = getattr(server, "_vaultbridge_operations", None)
        if operations is not None and operations._admission is not None:
            operations._admission.begin_shutdown()
            operations._admission.drain()
        if semantic_indexer is not None:
            semantic_indexer.shutdown()
        log_event(
            logger,
            logging.INFO,
            "mcp_server_stopped",
            "VaultBridge MCP stdio server stopped",
            transport="stdio",
        )
    return 0


def _run_named_stdio(settings):
    """The SDK lifespan starts/drains the sole runtime, including clean stdin EOF."""
    try:
        server = create_mcp_server(settings=settings)
    except Exception:
        log_event(logger, logging.ERROR, "mcp_startup_failed", "VaultBridge MCP stdio startup failed",
                  transport="stdio")
        return 2
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        log_event(logger, logging.ERROR, "mcp_server_failed", "VaultBridge MCP stdio server failed",
                  transport="stdio")
        return 1
    if server._vaultbridge_runtime._state != "stopped":
        log_event(logger, logging.ERROR, "mcp_server_failed", "VaultBridge MCP stdio cleanup is incomplete",
                  transport="stdio")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
