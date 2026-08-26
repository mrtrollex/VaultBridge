from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

from app.api.health import router as health_router
from app.api.notes import router as notes_router
from app.api.search import router as search_router
from app.core.config import Settings
from app.core.logging import configure_application_logging, log_event
from app.core.observability import RequestObservabilityMiddleware
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.filesystem_watcher import SemanticFilesystemWatcher
from app.services.indexer import BackgroundSemanticIndexer
from app.services.rate_limiter import FixedWindowRateLimiter
from app.services.semantic_search import (
    SemanticSearchService,
    semantic_search_service_from_settings,
)
from app.services.vault import (
    NoteConflictError,
    NoteNotFoundError,
    NoteTooLargeError,
    VaultService,
    VaultServiceError,
    VaultValidationError,
)

APP_TITLE = "VaultBridge"
APP_VERSION = "1.0.0"
APP_DESCRIPTION = "Self-hosted REST and semantic search API for an Obsidian vault."

configure_application_logging()
logger = logging.getLogger("vaultbridge.application")


class VaultBridgeApplication(FastAPI):
    """Keep request observability outside FastAPI's server-error boundary."""

    def build_middleware_stack(self) -> ASGIApp:
        return RequestObservabilityMiddleware(super().build_middleware_stack())


@asynccontextmanager
async def lifespan(application: FastAPI):
    semantic_indexer: BackgroundSemanticIndexer = application.state.semantic_indexer
    semantic_watcher: SemanticFilesystemWatcher | None = getattr(
        application.state,
        "semantic_watcher",
        None,
    )
    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "application_starting",
        "VaultBridge application is starting",
    )
    try:
        semantic_indexer.start()
        if semantic_watcher is not None:
            semantic_watcher.start()
    except BaseException as exc:
        log_event(
            logger,
            logging.ERROR,
            "application_start_failed",
            "VaultBridge application startup failed",
            exc_info=(type(exc), exc, exc.__traceback__),
            error_type=type(exc).__name__,
        )
        try:
            if semantic_watcher is not None:
                await asyncio.to_thread(semantic_watcher.stop)
        finally:
            await asyncio.to_thread(semantic_indexer.shutdown)
        raise
    log_event(
        logger,
        logging.INFO,
        "application_started",
        "VaultBridge application started",
        duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
        full_sync_required=semantic_indexer.requires_full_sync,
    )
    try:
        yield
    finally:
        shutdown_started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "application_stopping",
            "VaultBridge application is stopping",
        )
        shutdown_error: BaseException | None = None
        if semantic_watcher is not None:
            try:
                await asyncio.to_thread(semantic_watcher.stop)
            except BaseException as exc:
                shutdown_error = exc
        try:
            await asyncio.to_thread(semantic_indexer.shutdown)
        except BaseException as exc:
            shutdown_error = shutdown_error or exc
        if shutdown_error is not None:
            log_event(
                logger,
                logging.ERROR,
                "application_shutdown_failed",
                "VaultBridge application shutdown failed",
                exc_info=(
                    type(shutdown_error),
                    shutdown_error,
                    shutdown_error.__traceback__,
                ),
                error_type=type(shutdown_error).__name__,
            )
            raise shutdown_error
        log_event(
            logger,
            logging.INFO,
            "application_stopped",
            "VaultBridge application stopped",
            duration_ms=round((time.perf_counter() - shutdown_started_at) * 1000, 3),
        )


async def handle_vault_service_error(_request: Request, exc: VaultServiceError) -> JSONResponse:
    status_codes = {
        VaultValidationError: status.HTTP_400_BAD_REQUEST,
        NoteNotFoundError: status.HTTP_404_NOT_FOUND,
        NoteConflictError: status.HTTP_409_CONFLICT,
        NoteTooLargeError: 413,
    }
    return JSONResponse(status_code=status_codes[type(exc)], content={"detail": str(exc)})


def create_app(
    *,
    settings: Settings | None = None,
    duplicate_candidate_service: DuplicateCandidateService | None = None,
    semantic_search_service: SemanticSearchService | None = None,
    semantic_indexer: BackgroundSemanticIndexer | None = None,
    semantic_watcher: SemanticFilesystemWatcher | None = None,
    rate_limiter: FixedWindowRateLimiter | None = None,
    vault_service: VaultService | None = None,
) -> FastAPI:
    app_settings = settings if settings is not None else Settings.from_env()
    app_semantic_search_service = (
        semantic_search_service
        if semantic_search_service is not None
        else semantic_search_service_from_settings(app_settings)
    )
    app_vault_service = (
        vault_service
        if vault_service is not None
        else VaultService(vault_root=app_settings.vault_path, max_note_bytes=app_settings.max_note_bytes)
    )
    app_duplicate_candidate_service = (
        duplicate_candidate_service
        if duplicate_candidate_service is not None
        else DuplicateCandidateService(
            vault_service=app_vault_service,
            semantic_search_service=app_semantic_search_service,
        )
    )
    app_semantic_indexer = (
        semantic_indexer
        if semantic_indexer is not None
        else BackgroundSemanticIndexer(
            app_semantic_search_service.sync,
            app_semantic_search_service.sync_paths,
        )
    )
    app_rate_limiter = (
        rate_limiter
        if rate_limiter is not None
        else FixedWindowRateLimiter(
            requests=app_settings.rate_limit_requests,
            window_seconds=app_settings.rate_limit_window_seconds,
            max_clients=app_settings.rate_limit_max_clients,
        )
    )
    app_semantic_watcher = semantic_watcher
    if app_semantic_watcher is None and app_settings.semantic_watch_enabled:
        app_semantic_watcher = SemanticFilesystemWatcher(
            vault_service=app_vault_service,
            semantic_indexer=app_semantic_indexer,
            semantic_data_path=app_settings.semantic_data_path,
            debounce_seconds=app_settings.semantic_watch_debounce_seconds,
        )

    application = VaultBridgeApplication(
        title=APP_TITLE,
        version=APP_VERSION,
        description=APP_DESCRIPTION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.duplicate_candidate_service = app_duplicate_candidate_service
    application.state.semantic_search_service = app_semantic_search_service
    application.state.semantic_indexer = app_semantic_indexer
    application.state.semantic_watcher = app_semantic_watcher
    application.state.rate_limiter = app_rate_limiter
    application.state.vault_service = app_vault_service
    application.add_exception_handler(VaultServiceError, handle_vault_service_error)
    application.include_router(health_router)
    application.include_router(notes_router)
    application.include_router(search_router)
    return application


app = create_app()
