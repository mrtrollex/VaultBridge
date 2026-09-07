from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, TypeVar
from urllib.parse import quote
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.core.logging import configure_application_logging, log_event
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.rate_limiter import FixedWindowRateLimiter
from app.services.semantic_search import (
    IndexState,
    SemanticSearchService,
    SemanticSearchUnavailableError,
    semantic_search_service_from_settings,
)
from app.services.vault import (
    NoteNotFoundError,
    NoteTooLargeError,
    VaultService,
    VaultValidationError,
)

MCP_SERVER_NAME = "VaultBridge"
MCP_SERVER_VERSION = "1.1.0"
MCP_CLIENT_ID = "stdio-process"
NOTE_RESOURCE_TEMPLATE = "vaultbridge://note/{path}"
NOTE_MIME_TYPE = "text/markdown"
RELATED_CANDIDATE_OVERFETCH_FACTOR = 3
RELATED_CANDIDATE_LIMIT = 50

Folder = Annotated[str, Field(max_length=500)]
NotePath = Annotated[str, Field(min_length=1, max_length=700)]
LiteralQuery = Annotated[str, Field(min_length=1, max_length=300)]
RelatedText = Annotated[str, Field(min_length=2, max_length=4000)]
DuplicateTitle = Annotated[str, Field(min_length=1, max_length=180)]
DuplicateText = Annotated[str, Field(max_length=4000)]
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


class SemanticIndexRebuildingError(RuntimeError):
    """The persisted semantic index is currently owned by a rebuilding process."""


T = TypeVar("T")


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
    messages = {
        "invalid_path": "invalid_path: Invalid vault-relative path.",
        "note_not_found": "note_not_found: Note not found.",
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
        sanitized["content"] = [{"type": "text", "text": _safe_tool_error(text)}]
        sanitized.pop("structuredContent", None)
        return sanitized
    return result


class VaultBridgeMCPAdapter:
    def __init__(
        self,
        *,
        settings: Settings,
        vault_service: VaultService,
        semantic_search_service: SemanticSearchService,
        duplicate_candidate_service: DuplicateCandidateService,
        rate_limiter: FixedWindowRateLimiter,
    ) -> None:
        self.settings = settings
        self.vault_service = vault_service
        self.semantic_search_service = semantic_search_service
        self.duplicate_candidate_service = duplicate_candidate_service
        self.rate_limiter = rate_limiter

    def _execute(self, operation: str, action: Callable[[], T], *, result_count: Callable[[T], int]) -> T:
        request_id = uuid4().hex
        started_at = time.perf_counter()
        try:
            if self.settings.rate_limit_enabled:
                decision = self.rate_limiter.check(MCP_CLIENT_ID)
                if not decision.allowed:
                    retry_after = decision.retry_after_seconds or 1
                    raise ToolError(
                        f"rate_limited: Operation limit exceeded; retry in {retry_after} seconds."
                    )
            result = action()
        except ToolError:
            self._log_failure(operation, request_id, started_at, "rate_limited")
            raise
        except VaultValidationError as exc:
            self._log_failure(operation, request_id, started_at, "invalid_path")
            raise ToolError("invalid_path: Invalid vault-relative path.") from exc
        except NoteNotFoundError as exc:
            self._log_failure(operation, request_id, started_at, "note_not_found")
            raise ToolError("note_not_found: Note not found.") from exc
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
            self._log_failure(operation, request_id, started_at, "validation_error")
            raise ToolError("validation_error: Tool arguments are invalid.") from exc
        except Exception as exc:
            self._log_failure(
                operation,
                request_id,
                started_at,
                "internal_error",
                error_type=type(exc).__name__,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            raise ToolError("internal_error: The operation could not be completed.") from exc

        log_event(
            logger,
            logging.INFO,
            "mcp_operation_completed",
            "MCP operation completed",
            request_id=request_id,
            transport="stdio",
            operation=operation,
            result_count=result_count(result),
            duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
        )
        return result

    @staticmethod
    def _log_failure(
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
            transport="stdio",
            operation=operation,
            failure_class=failure_class,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
            error_type=error_type,
            exc_info=exc_info,
        )

    def _require_semantic_index(self) -> None:
        try:
            if self.semantic_search_service.use_persisted_index_for_read_only_search():
                return
            inspection = self.semantic_search_service.inspect_persisted_index()
        except Exception as exc:
            raise SemanticSearchUnavailableError from exc
        if inspection.state is IndexState.INDEXING:
            raise SemanticIndexRebuildingError
        raise SemanticSearchUnavailableError

    def list_notes(self, *, folder: str, limit: int) -> ListNotesResult:
        def action() -> ListNotesResult:
            notes = self.vault_service.list_notes(folder=folder, limit=limit)
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
            note = self.vault_service.read_note(path)
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
            results = self.vault_service.search_notes(query=query, folder=folder, limit=limit)
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
            canonical_folder = ""
            if folder:
                existing_folder = self.vault_service.existing_relative_path(folder)
                if existing_folder is None:
                    return RelatedNotesResult(text=text, results=[])
                canonical_folder = _canonical_service_path(existing_folder)

            self._require_semantic_index()
            candidate_limit = min(RELATED_CANDIDATE_LIMIT, limit * RELATED_CANDIDATE_OVERFETCH_FACTOR)
            results = self.semantic_search_service.search(
                text,
                folder=canonical_folder,
                limit=candidate_limit,
                min_score=min_score,
            )
            verified: list[RelatedNoteItem] = []
            for result in results:
                verified_path = self.vault_service.verify_existing_markdown_path(
                    result.path,
                    folder=canonical_folder,
                )
                if verified_path is None:
                    continue
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
            self._require_semantic_index()
            candidates = self.duplicate_candidate_service.find_candidates(
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

    def read_note_resource(self, *, path: str, raw_uri: str) -> str:
        def action() -> str:
            if raw_uri != note_resource_uri(path):
                raise VaultValidationError
            return self.vault_service.read_note(path).content

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
    rate_limiter: FixedWindowRateLimiter | None = None,
) -> MCPServer:
    app_settings = settings if settings is not None else Settings.from_env()
    app_vault_service = vault_service or VaultService(
        vault_root=app_settings.vault_path,
        max_note_bytes=app_settings.max_note_bytes,
    )
    app_semantic_service = semantic_search_service or semantic_search_service_from_settings(app_settings)
    app_duplicate_service = duplicate_candidate_service or DuplicateCandidateService(
        vault_service=app_vault_service,
        semantic_search_service=app_semantic_service,
    )
    app_rate_limiter = rate_limiter or FixedWindowRateLimiter(
        requests=app_settings.rate_limit_requests,
        window_seconds=app_settings.rate_limit_window_seconds,
        max_clients=1,
    )
    adapter = VaultBridgeMCPAdapter(
        settings=app_settings,
        vault_service=app_vault_service,
        semantic_search_service=app_semantic_service,
        duplicate_candidate_service=app_duplicate_service,
        rate_limiter=app_rate_limiter,
    )
    server = MCPServer(
        MCP_SERVER_NAME,
        description="Read-only local access to a contained Obsidian Markdown vault.",
        version=MCP_SERVER_VERSION,
        log_level="CRITICAL",
        middleware=[_privacy_error_middleware],
    )

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

    return server


def main() -> int:
    configure_application_logging()
    try:
        settings = Settings.from_env()
        vault_service = VaultService(
            vault_root=settings.vault_path,
            max_note_bytes=settings.max_note_bytes,
        )
        if not vault_service.vault_available():
            raise RuntimeError("vault unavailable")
        server = create_mcp_server(settings=settings, vault_service=vault_service)
    except Exception as exc:
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
        log_event(
            logger,
            logging.INFO,
            "mcp_server_stopped",
            "VaultBridge MCP stdio server stopped",
            transport="stdio",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
