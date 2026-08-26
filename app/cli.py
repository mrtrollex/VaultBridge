from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import NoReturn, TextIO

from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import configure_application_logging
from app.repositories.semantic import ImmutableIndexInspectionUnavailableError
from app.services.semantic_search import (
    Embedder,
    IndexState,
    SemanticIndexInspection,
    SemanticIndexOperationError,
    SemanticResult,
    SemanticSearchService,
    SemanticSearchUnavailableError,
    SynchronizationCancelledError,
    semantic_search_service_from_settings,
)
from app.services.vault import NoteSearchResult, VaultService, VaultServiceError, VaultValidationError

EXIT_SUCCESS = 0
EXIT_INTEGRITY_PROBLEM = 1
EXIT_CLI_FAILURE = 2
SEARCH_LIMIT_MAX = 50
RELATED_LIMIT_MAX = 20
RELATED_CANDIDATE_LIMIT = 50
RELATED_CANDIDATE_OVERFETCH_FACTOR = 3
CLI_SNIPPET_LENGTH = 320


class _CheckOnlyEmbedder(Embedder):
    """Prevent the read-only check command from constructing or using FastEmbed."""

    def embed(self, texts: Sequence[str]) -> NoReturn:
        del texts
        raise RuntimeError("The index check command cannot generate embeddings")


def _bounded_integer(value: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
    return parsed


def _search_limit(value: str) -> int:
    return _bounded_integer(value, minimum=1, maximum=SEARCH_LIMIT_MAX)


def _related_limit(value: str) -> int:
    return _bounded_integer(value, minimum=1, maximum=RELATED_LIMIT_MAX)


def _minimum_score(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not -1.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between -1.0 and 1.0")
    return parsed


def _non_empty_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local VaultBridge vault and semantic-index operations")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="show persisted vault and semantic-index status")
    status.set_defaults(action="status")

    index = commands.add_parser(
        "index",
        help="synchronize the semantic index; existing check/rebuild subcommands remain available",
        description=(
            "Synchronize changed Markdown into the derived semantic index. "
            "Stop the serving VaultBridge process before this offline write operation."
        ),
    )
    index.set_defaults(action="index")
    index_commands = index.add_subparsers(dest="index_command")
    index_check = index_commands.add_parser(
        "check",
        help="inspect semantic index integrity without changing it",
    )
    index_check.set_defaults(action="index_check")
    index_rebuild = index_commands.add_parser(
        "rebuild",
        help="discard derived data and rebuild from Markdown",
    )
    index_rebuild.set_defaults(action="index_rebuild")

    reindex = commands.add_parser(
        "reindex",
        help="discard derived semantic data and rebuild it from Markdown",
        description=(
            "Force a clean rebuild of derived semantic data. "
            "Stop the serving VaultBridge process before this offline write operation."
        ),
    )
    reindex.set_defaults(action="reindex")

    search = commands.add_parser("search", help="search Markdown titles and content literally")
    search.add_argument("query", type=_non_empty_text, help="literal text to find")
    search.add_argument("--folder", default="", help="vault-relative folder")
    search.add_argument("--limit", type=_search_limit, default=10, help="results from 1 to 50")
    search.set_defaults(action="search")

    related = commands.add_parser("related", help="find semantically related live Markdown notes")
    related.add_argument("text", type=_non_empty_text, help="text or concept to compare")
    related.add_argument("--folder", default="", help="vault-relative folder")
    related.add_argument("--limit", type=_related_limit, default=5, help="results from 1 to 20")
    related.add_argument(
        "--min-score",
        type=_minimum_score,
        default=0.28,
        help="minimum semantic cosine score from -1.0 to 1.0",
    )
    related.set_defaults(action="related")
    return parser


def _vault_label(vault_root, available: bool) -> str:
    if available:
        return "OK"
    try:
        if not vault_root.exists():
            return "missing"
        if not vault_root.is_dir():
            return "not a directory"
    except OSError:
        pass
    return "unavailable"


def _database_label(status: SemanticIndexInspection) -> str:
    if status.storage_error:
        return "unreadable"
    if status.storage_exists is False:
        return "missing"
    if status.storage_exists is None:
        return "unavailable"
    return "OK"


def _schema_label(status: SemanticIndexInspection) -> str:
    if status.storage_error:
        return "unavailable"
    if status.storage_exists is False:
        return "not present"
    return "usable" if status.storage_initialized else "unusable"


def _signature_label(status: SemanticIndexInspection) -> str:
    if status.storage_error or not status.storage_initialized:
        return "unavailable"
    if status.index_signature is None:
        return "missing"
    return "compatible" if status.signature_compatible else "incompatible"


def _persisted_state_label(status: SemanticIndexInspection) -> str:
    if status.persisted_state is None:
        if status.signature_compatible and status.semantic_chunks:
            return "not recorded (compatible legacy index)"
        return "not recorded"
    return status.persisted_state


def _integrity_summary(vault_available: bool, status: SemanticIndexInspection) -> str:
    if not vault_available:
        return "vault unavailable"
    if status.search_available:
        return "healthy"
    if status.storage_error:
        return "database unreadable or corrupt"
    if status.storage_exists is False:
        return "database missing"
    if not status.storage_initialized:
        return "schema unusable"
    if status.index_signature is None:
        return "index signature missing"
    if not status.signature_compatible:
        return "index signature incompatible"
    if status.persisted_state == IndexState.INDEXING.value:
        return "indexing incomplete"
    if status.persisted_state == IndexState.ERROR.value:
        return "persisted index error"
    if status.state is IndexState.UNINITIALIZED:
        return "no completed index"
    return "semantic search unavailable"


def run_index_check(
    settings: Settings,
    *,
    output: TextIO | None = None,
    service: SemanticSearchService | None = None,
) -> int:
    """Run the read-only index integrity check and return its process exit code."""
    stream = output or sys.stdout
    vault_service = VaultService(
        vault_root=settings.vault_path,
        max_note_bytes=settings.max_note_bytes,
    )
    semantic_service = service or semantic_search_service_from_settings(
        settings,
        embedder=_CheckOnlyEmbedder(),
    )
    vault_available = vault_service.vault_available()
    try:
        status = semantic_service.inspect_persisted_index()
    except ImmutableIndexInspectionUnavailableError:
        print("VaultBridge semantic index", file=stream)
        print(f"Vault:                    {_vault_label(settings.vault_path, vault_available)}", file=stream)
        print("Persisted inspection:     unavailable (SQLite WAL/SHM sidecars present)", file=stream)
        print("Live process availability: use /health and /health/ready", file=stream)
        print("Status:                   stop VaultBridge before running index check", file=stream)
        return EXIT_INTEGRITY_PROBLEM
    summary = _integrity_summary(vault_available, status)

    print("VaultBridge semantic index", file=stream)
    print(f"Vault:                    {_vault_label(settings.vault_path, vault_available)}", file=stream)
    print(f"Database:                 {_database_label(status)}", file=stream)
    print(f"Schema:                   {_schema_label(status)}", file=stream)
    print(f"Signature:                {_signature_label(status)}", file=stream)
    print(f"Persisted state:          {_persisted_state_label(status)}", file=stream)
    print(f"Standalone searchable:    {'yes' if status.search_available else 'no'}", file=stream)
    print("Live process availability: use /health and /health/ready", file=stream)
    print(f"Stored notes:             {status.indexed_notes}", file=stream)
    print(f"Stored chunks:            {status.semantic_chunks}", file=stream)
    print(f"Stored last full sync:    {status.last_successful_sync or 'never'}", file=stream)
    print(f"Status:                   {summary}", file=stream)
    return EXIT_SUCCESS if vault_available and status.search_available else EXIT_INTEGRITY_PROBLEM


def run_status(
    settings: Settings,
    *,
    output: TextIO | None = None,
    service: SemanticSearchService | None = None,
) -> int:
    """Show the existing read-only persisted index inspection."""
    return run_index_check(settings, output=output, service=service)


def _vault_service(settings: Settings) -> VaultService:
    return VaultService(
        vault_root=settings.vault_path,
        max_note_bytes=settings.max_note_bytes,
    )


def _validated_folder(vault_service: VaultService, folder: str) -> str:
    if not folder.strip():
        return ""
    path = vault_service.resolve_path(folder)
    try:
        if not path.is_dir():
            raise VaultValidationError("Folder is unavailable")
        relative = path.relative_to(vault_service.vault_root).as_posix()
    except (OSError, ValueError) as exc:
        raise VaultValidationError("Folder is unavailable") from exc
    return "" if relative == "." else relative


def _private_output_values(settings: Settings) -> tuple[str, ...]:
    values = (
        settings.api_key.get_secret_value(),
        settings.previous_api_key.get_secret_value(),
        str(settings.vault_path),
        str(settings.semantic_data_path),
    )
    return tuple(value for value in values if value)


def _safe_display(value: str, limit: int, *, private_values: Sequence[str] = ()) -> str:
    for private_value in private_values:
        value = value.replace(private_value, "[redacted]")
    printable = "".join(character if character.isprintable() else " " for character in value)
    return " ".join(printable.split())[:limit]


def _print_literal_results(
    results: Sequence[NoteSearchResult],
    stream: TextIO,
    *,
    private_values: Sequence[str] = (),
) -> None:
    if not results:
        print("No literal matches.", file=stream)
        return
    print(f"Search results: {len(results)}", file=stream)
    for position, result in enumerate(results, start=1):
        path = result.path.replace("\\", "/")
        print(f"{position}. {_safe_display(path, 700, private_values=private_values)}", file=stream)
        print(f"   {_safe_display(result.title, 180, private_values=private_values)}", file=stream)
        print(
            f"   {_safe_display(result.snippet, CLI_SNIPPET_LENGTH, private_values=private_values)}",
            file=stream,
        )


def _print_related_results(
    results: Sequence[tuple[str, SemanticResult]],
    stream: TextIO,
    *,
    private_values: Sequence[str] = (),
) -> None:
    if not results:
        print("No related notes.", file=stream)
        return
    print(f"Related results: {len(results)}", file=stream)
    for position, (verified_path, result) in enumerate(results, start=1):
        print(
            f"{position}. {_safe_display(verified_path, 700, private_values=private_values)}",
            file=stream,
        )
        title = PurePosixPath(verified_path.replace("\\", "/")).stem
        print(f"   {_safe_display(title, 180, private_values=private_values)}", file=stream)
        print(f"   score: {result.score:.3f}", file=stream)
        if result.heading:
            print(
                f"   heading: {_safe_display(result.heading, 200, private_values=private_values)}",
                file=stream,
            )
        print(
            f"   {_safe_display(result.snippet, CLI_SNIPPET_LENGTH, private_values=private_values)}",
            file=stream,
        )


def run_index(
    settings: Settings,
    *,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    service: SemanticSearchService | None = None,
) -> int:
    """Synchronize the current derived index without discarding compatible data first."""
    stream = output or sys.stdout
    error_stream = error_output or sys.stderr
    vault_service = _vault_service(settings)
    if not vault_service.vault_available():
        print("Index not started: configured vault is unavailable.", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    semantic_service = service or semantic_search_service_from_settings(settings)
    print("Synchronizing semantic index...", file=stream)
    try:
        result = semantic_service.sync()
        status = semantic_service.inspect_index()
    except sqlite3.ProgrammingError:
        raise
    except (
        OSError,
        sqlite3.DatabaseError,
        SemanticIndexOperationError,
        SynchronizationCancelledError,
        UnicodeError,
    ) as exc:
        print(f"Status: failed ({type(exc).__name__})", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    if status.state is not IndexState.READY or not status.search_available:
        print("Status: failed (index did not become ready)", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    print(f"Indexed notes: {result['indexed']}", file=stream)
    print(f"Unchanged notes: {result['unchanged']}", file=stream)
    print(f"Removed notes: {result['removed']}", file=stream)
    print(f"Chunks: {status.semantic_chunks}", file=stream)
    print(f"Last full sync: {status.last_successful_sync or 'never'}", file=stream)
    print("Status: ready", file=stream)
    return EXIT_SUCCESS


def run_index_rebuild(
    settings: Settings,
    *,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    service: SemanticSearchService | None = None,
) -> int:
    """Run an explicit full rebuild and return its process exit code."""
    stream = output or sys.stdout
    error_stream = error_output or sys.stderr
    vault_service = VaultService(
        vault_root=settings.vault_path,
        max_note_bytes=settings.max_note_bytes,
    )
    if not vault_service.vault_available():
        print("Rebuild not started: configured vault is unavailable.", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    semantic_service = service or semantic_search_service_from_settings(settings)
    print("Rebuilding semantic index...", file=stream)
    try:
        result = semantic_service.rebuild()
    except sqlite3.ProgrammingError:
        raise
    except (
        OSError,
        sqlite3.DatabaseError,
        SemanticIndexOperationError,
        SynchronizationCancelledError,
        UnicodeError,
    ) as exc:
        print(f"Status: failed ({type(exc).__name__})", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    status = semantic_service.inspect_index()
    if status.state is not IndexState.READY or not status.search_available:
        print("Status: failed (index did not become ready)", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    print(f"Indexed notes: {result['indexed']}", file=stream)
    print(f"Chunks: {status.semantic_chunks}", file=stream)
    print(f"Last full sync: {status.last_successful_sync or 'never'}", file=stream)
    print("Status: ready", file=stream)
    return EXIT_SUCCESS


def run_reindex(
    settings: Settings,
    *,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    service: SemanticSearchService | None = None,
) -> int:
    """Run the existing clean-rebuild behavior through a friendly top-level command."""
    return run_index_rebuild(
        settings,
        output=output,
        error_output=error_output,
        service=service,
    )


def run_search(
    settings: Settings,
    query: str,
    *,
    folder: str = "",
    limit: int = 10,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    vault_service: VaultService | None = None,
) -> int:
    """Run the existing literal vault search and print bounded relative-path results."""
    stream = output or sys.stdout
    error_stream = error_output or sys.stderr
    service = vault_service or _vault_service(settings)
    if not service.vault_available():
        print("Search not started: configured vault is unavailable.", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    try:
        relative_folder = _validated_folder(service, folder)
        results = service.search_notes(
            query=query,
            folder=relative_folder,
            limit=sys.maxsize,
        )
    except (OSError, UnicodeError, VaultServiceError):
        print("Search failed: folder or vault input is invalid or unavailable.", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    ordered = sorted(
        results,
        key=lambda result: (
            result.path.replace("\\", "/").casefold(),
            result.path.replace("\\", "/"),
        ),
    )[:limit]
    _print_literal_results(
        ordered,
        stream,
        private_values=_private_output_values(settings),
    )
    return EXIT_SUCCESS


def run_related(
    settings: Settings,
    text: str,
    *,
    folder: str = "",
    limit: int = 5,
    min_score: float = 0.28,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    service: SemanticSearchService | None = None,
    vault_service: VaultService | None = None,
) -> int:
    """Search an existing compatible semantic index and display only verified live notes."""
    stream = output or sys.stdout
    error_stream = error_output or sys.stderr
    live_vault = vault_service or _vault_service(settings)
    if not live_vault.vault_available():
        print("Related search not started: configured vault is unavailable.", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    try:
        relative_folder = _validated_folder(live_vault, folder)
        semantic_service = service or semantic_search_service_from_settings(settings)
        if not semantic_service.use_persisted_index_for_read_only_search():
            print(
                "Related search unavailable: no compatible searchable semantic index.",
                file=error_stream,
            )
            return EXIT_INTEGRITY_PROBLEM
        candidate_limit = min(
            RELATED_CANDIDATE_LIMIT,
            limit * RELATED_CANDIDATE_OVERFETCH_FACTOR,
        )
        candidates = semantic_service.search(
            text,
            folder=relative_folder,
            limit=candidate_limit,
            min_score=min_score,
        )
    except sqlite3.ProgrammingError:
        raise
    except (
        OSError,
        sqlite3.DatabaseError,
        SemanticIndexOperationError,
        SemanticSearchUnavailableError,
        UnicodeError,
        VaultServiceError,
    ) as exc:
        print(f"Related search failed ({type(exc).__name__}).", file=error_stream)
        return EXIT_INTEGRITY_PROBLEM

    verified_results: list[tuple[str, SemanticResult]] = []
    for result in candidates:
        verified_path = live_vault.verify_existing_markdown_path(
            result.path,
            folder=relative_folder,
        )
        if verified_path is None:
            continue
        verified_results.append((verified_path, result))
        if len(verified_results) >= limit:
            break

    _print_related_results(
        verified_results,
        stream,
        private_values=_private_output_values(settings),
    )
    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        configure_application_logging()
        app_settings = settings or Settings.from_env()
        if args.action == "status":
            return run_status(app_settings)
        if args.action == "index_check":
            return run_index_check(app_settings)
        if args.action == "index_rebuild":
            return run_index_rebuild(app_settings)
        if args.action == "index":
            return run_index(app_settings)
        if args.action == "reindex":
            return run_reindex(app_settings)
        if args.action == "search":
            return run_search(
                app_settings,
                args.query,
                folder=args.folder,
                limit=args.limit,
            )
        return run_related(
            app_settings,
            args.text,
            folder=args.folder,
            limit=args.limit,
            min_score=args.min_score,
        )
    except ValidationError:
        print("VaultBridge configuration is invalid.", file=sys.stderr)
        return EXIT_CLI_FAILURE
    except Exception as exc:
        print(f"VaultBridge CLI failed ({type(exc).__name__}).", file=sys.stderr)
        return EXIT_CLI_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
