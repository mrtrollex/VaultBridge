"""Explicit test composition for Slice A. No production entry point calls it."""

from app.services.adapter_composition import validate_private_operations


def create_private_read_app(*, settings, operations, rate_limiter=None, **raw_owners):
    from app.api.health import router as health
    from app.api.knowledge_hygiene import router as hygiene
    from app.api.knowledge_query import router as query
    from app.api.notes import router as notes
    from app.api.read_openapi import install_read_openapi
    from app.api.relationships import router as relationships
    from app.api.search import router as search
    from app.main import VaultBridgeApplication, handle_vault_service_error
    from app.services.rate_limiter import FixedWindowRateLimiter
    from app.services.vault import VaultServiceError
    from app.ui.named_fence import NamedUIFence
    from app.ui.router import router as ui

    validate_private_operations(operations, settings=settings, **raw_owners)
    application = VaultBridgeApplication(docs_url=None, redoc_url=None, openapi_url=None)
    application.state.settings = settings
    application.state.space_operations = operations
    application.state.rate_limiter = rate_limiter or FixedWindowRateLimiter(
        requests=settings.rate_limit_requests, window_seconds=settings.rate_limit_window_seconds,
        max_clients=settings.rate_limit_max_clients,
    )
    application.add_middleware(NamedUIFence, named=True)
    application.add_exception_handler(VaultServiceError, handle_vault_service_error)
    for router in (health, notes, search, relationships, hygiene, query, ui):
        application.include_router(router)
    install_read_openapi(application)
    return application
