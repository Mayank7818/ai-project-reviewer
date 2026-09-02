"""Response schemas for the health-check endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness payload returned by `GET {prefix}/health`."""

    status: str = Field(..., description="'ok' when the service is running.")
    app_name: str = Field(..., description="Human-readable application name.")
    version: str = Field(..., description="Deployed application version.")
    environment: str = Field(..., description="development | staging | production")
    timestamp: datetime = Field(..., description="Server time (UTC) of the check.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "ok",
                "app_name": "AI Project Reviewer",
                "version": "0.1.0",
                "environment": "development",
                "timestamp": "2026-01-01T12:00:00Z",
            }
        }
    }


class ReadinessResponse(BaseModel):
    """Readiness payload: is the service able to serve real traffic yet?

    `dependencies` reports whether optional integrations are configured. It
    reports configuration only - no secret values are ever included.
    """

    status: str = Field(..., description="'ready' or 'degraded'.")
    dependencies: dict[str, bool] = Field(
        ..., description="Integration name -> whether credentials are configured."
    )
