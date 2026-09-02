"""Application entry point.

Uses the factory pattern (`create_app`) rather than a module-level `app` built
by side effects: tests can spin up an isolated instance, and startup order stays
explicit and readable.

Run with:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hook.

    Later this is where the shared httpx client and LLM client get created once
    and reused, instead of being rebuilt per request.
    """
    settings = get_settings()
    logger.info(
        "Starting %s v%s (environment=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(debug=settings.debug)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Analyzes a public GitHub repository and generates a technical "
            "review plus project-specific interview questions."
        ),
        # Interactive docs are a development convenience, not a production
        # surface - switch them off outside development.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    # The React dev server runs on a different origin, so the browser needs
    # explicit permission. Origins come from config and are never "*".
    #
    # Credentials are off because nothing here uses them: the API has no cookies,
    # no sessions in the browser, and no Authorization header. Leaving the flag
    # on would grant a permission the application does not need, and it is the
    # flag that makes a mistaken wildcard origin dangerous.
    #
    # Methods and headers are listed rather than wildcarded for the same reason -
    # this API only ever answers GET and POST with JSON.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
        max_age=600,
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        """Tiny landing payload so hitting the bare host is not a 404."""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "health": f"{settings.api_v1_prefix}/health",
        }

    return app


app = create_app()
