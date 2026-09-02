"""Health and readiness endpoints.

These are the only endpoints that exist so far. They give the frontend (and any
future container orchestrator) a cheap, dependency-free way to confirm the API
is reachable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Return static service identity plus the current server time.

    Deliberately touches no external service, so it stays fast and cannot fail
    because of an upstream outage.
    """
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(settings: Settings = Depends(get_settings)) -> ReadinessResponse:
    """Report which optional integrations are configured.

    Returns booleans only - never the credentials or URLs themselves. Neither
    integration is used yet, so an unconfigured one is reported as 'degraded'
    rather than as an error.

    Note this reports *configuration*, not reachability: whether the local
    Ollama server actually answers is checked in the Ollama step.
    """
    dependencies = {
        "github_token_configured": bool(settings.github_token),
        "ollama_configured": bool(settings.ollama_base_url and settings.ollama_model),
    }
    return ReadinessResponse(
        status="ready" if all(dependencies.values()) else "degraded",
        dependencies=dependencies,
    )
