from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.notes import router as notes_router
from app.api.search import router as search_router
from app.core.config import Settings
from app.semantic import SemanticIndex, semantic_index_from_settings
from app.services.vault import (
    NoteConflictError,
    NoteNotFoundError,
    NoteTooLargeError,
    VaultService,
    VaultServiceError,
    VaultValidationError,
)

APP_TITLE = "VaultBridge"
APP_VERSION = "0.1.0"
APP_DESCRIPTION = "Self-hosted REST and semantic search API for an Obsidian vault."


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
    semantic_index: SemanticIndex | None = None,
    vault_service: VaultService | None = None,
) -> FastAPI:
    app_settings = settings if settings is not None else Settings.from_env()
    app_semantic_index = (
        semantic_index if semantic_index is not None else semantic_index_from_settings(app_settings)
    )
    app_vault_service = (
        vault_service
        if vault_service is not None
        else VaultService(vault_root=app_settings.vault_path, max_note_bytes=app_settings.max_note_bytes)
    )

    application = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        description=APP_DESCRIPTION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = app_settings
    application.state.semantic_index = app_semantic_index
    application.state.vault_service = app_vault_service
    application.add_exception_handler(VaultServiceError, handle_vault_service_error)
    application.include_router(health_router)
    application.include_router(notes_router)
    application.include_router(search_router)
    return application


app = create_app()
