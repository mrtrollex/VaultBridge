from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
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
    SemanticSearchService,
    SynchronizationCancelledError,
    semantic_search_service_from_settings,
)
from app.services.vault import VaultService

EXIT_SUCCESS = 0
EXIT_INTEGRITY_PROBLEM = 1
EXIT_CLI_FAILURE = 2


class _CheckOnlyEmbedder(Embedder):
    """Prevent the read-only check command from constructing or using FastEmbed."""

    def embed(self, texts: Sequence[str]) -> NoReturn:
        del texts
        raise RuntimeError("The index check command cannot generate embeddings")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VaultBridge administrative operations")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="inspect or rebuild the semantic index")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    index_commands.add_parser("check", help="inspect semantic index integrity without changing it")
    index_commands.add_parser("rebuild", help="discard derived data and rebuild from Markdown")
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


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        configure_application_logging()
        app_settings = settings or Settings.from_env()
        if args.index_command == "check":
            return run_index_check(app_settings)
        return run_index_rebuild(app_settings)
    except ValidationError:
        print("VaultBridge configuration is invalid.", file=sys.stderr)
        return EXIT_CLI_FAILURE
    except Exception as exc:
        print(f"VaultBridge CLI failed ({type(exc).__name__}).", file=sys.stderr)
        return EXIT_CLI_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
