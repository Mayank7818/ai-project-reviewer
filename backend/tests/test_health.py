"""Smoke tests for the health endpoints.

Run from the `backend/` directory:  pytest
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

client = TestClient(create_app())
PREFIX = get_settings().api_v1_prefix


def test_health_returns_ok() -> None:
    response = client.get(f"{PREFIX}/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app_name"]
    assert body["version"]
    assert body["timestamp"]


def test_ready_reports_dependencies_without_leaking_secrets() -> None:
    response = client.get(f"{PREFIX}/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ready", "degraded"}
    assert set(body["dependencies"]) == {
        "github_token_configured",
        "ollama_configured",
    }
    # Values must be booleans - never the tokens themselves.
    assert all(isinstance(v, bool) for v in body["dependencies"].values())


def test_unknown_route_uses_the_shared_error_shape() -> None:
    response = client.get(f"{PREFIX}/does-not-exist")

    assert response.status_code == 404
    assert "error" in response.json()
