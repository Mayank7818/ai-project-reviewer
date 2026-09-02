"""Tests for domain classification of repository files."""

from __future__ import annotations

import pytest

from app.services.analysis.classifier import (
    BACKEND,
    CONFIGURATION,
    DATABASE,
    DOCUMENTATION,
    DOMAINS,
    FRONTEND,
    INFRASTRUCTURE,
    SECURITY,
    SOURCE_CODE,
    TESTING,
    UNKNOWN,
    classify_by_content,
    classify_file,
    domain_priority,
    summarise_domains,
)


@pytest.mark.parametrize(
    ("path", "domain"),
    [
        # documentation
        ("README.md", DOCUMENTATION),
        ("docs/architecture.md", DOCUMENTATION),
        ("CHANGELOG", DOCUMENTATION),
        # frontend
        ("frontend/src/App.jsx", FRONTEND),
        ("src/components/Button.tsx", FRONTEND),
        ("web/index.html", FRONTEND),
        ("src/styles.css", FRONTEND),
        # backend
        ("backend/app/main.py", BACKEND),
        ("api/handlers/user.py", BACKEND),
        ("server/routes/index.go", BACKEND),
        # database
        ("migrations/001_init.sql", DATABASE),
        ("app/models/user.py", DATABASE),
        ("prisma/schema.prisma", DATABASE),
        ("alembic/versions/abc.py", DATABASE),
        # configuration
        ("package.json", CONFIGURATION),
        ("requirements.txt", CONFIGURATION),
        ("pyproject.toml", CONFIGURATION),
        ("vite.config.js", CONFIGURATION),
        (".env.example", CONFIGURATION),
        # testing
        ("tests/test_api.py", TESTING),
        ("src/__tests__/app.test.js", TESTING),
        ("spec/user_spec.rb", TESTING),
        ("conftest.py", TESTING),
        ("component.spec.ts", TESTING),
        # infrastructure
        ("Dockerfile", INFRASTRUCTURE),
        ("docker-compose.yml", INFRASTRUCTURE),
        (".github/workflows/ci.yml", INFRASTRUCTURE),
        ("terraform/main.tf", INFRASTRUCTURE),
        ("k8s/deployment.yaml", INFRASTRUCTURE),
        # security
        ("app/auth/jwt.py", SECURITY),
        ("src/security/permissions.py", SECURITY),
        ("auth.py", SECURITY),
        # generic source
        ("src/utils.py", SOURCE_CODE),
        ("lib/helpers.rs", SOURCE_CODE),
        # unknown
        ("data.xyz", UNKNOWN),
    ],
)
def test_path_classification(path: str, domain: str) -> None:
    assert classify_file(path) == domain


def test_every_result_is_a_declared_domain() -> None:
    for path in ["a.py", "b.unknownext", "", "x/y/z"]:
        assert classify_file(path) in DOMAINS


# --- precedence ---------------------------------------------------------------


def test_tests_win_over_their_location() -> None:
    """A test file is a test file wherever it lives."""
    assert classify_file("backend/app/tests/test_service.py") == TESTING
    assert classify_file("frontend/src/__tests__/App.test.jsx") == TESTING


def test_frontend_extension_wins_over_a_backend_directory() -> None:
    assert classify_file("backend/templates/Widget.tsx") == FRONTEND


def test_models_directory_is_database_not_backend() -> None:
    assert classify_file("backend/app/models/order.py") == DATABASE


# --- content evidence ---------------------------------------------------------


def test_content_refines_a_generic_source_file() -> None:
    react = (
        "import React from 'react'\n"
        "import { useState } from 'react'\n"
        "export default function Widget() { return <div /> }\n"
    )
    assert classify_file("src/widget.js", react) == FRONTEND


def test_content_identifies_backend_code() -> None:
    backend = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/health')\n"
        "async def health() -> dict:\n"
        "    return {}\n"
    )
    assert classify_file("src/anything.py", backend) == BACKEND


def test_content_identifies_database_code() -> None:
    db = (
        "from sqlalchemy import Column, ForeignKey\n"
        "Base = declarative_base()\n"
        "session.execute('SELECT 1 FROM users')\n"
    )
    assert classify_file("src/store.py", db) == DATABASE


def test_a_single_weak_signal_does_not_reclassify() -> None:
    """One passing mention must not override the path."""
    assert classify_by_content("# TODO: add auth later\n") is None
    assert classify_file("src/utils.py", "# maybe use jwt here") == SOURCE_CODE


def test_content_never_overrides_a_confident_path() -> None:
    """A README full of React talk is still documentation."""
    readme = "import React from 'react'\nuseState useEffect ReactDOM createRoot\n"
    assert classify_file("README.md", readme) == DOCUMENTATION


def test_empty_content_is_safe() -> None:
    assert classify_by_content("") is None
    assert classify_file("src/empty.py", "") == SOURCE_CODE


# --- helpers ------------------------------------------------------------------


def test_priority_orders_documentation_before_unknown() -> None:
    assert domain_priority(DOCUMENTATION) < domain_priority(UNKNOWN)
    assert domain_priority(CONFIGURATION) < domain_priority(TESTING)
    assert domain_priority("nonsense") == domain_priority(UNKNOWN)


def test_summarise_counts_by_domain_descending() -> None:
    counts = summarise_domains(
        {
            "a.py": BACKEND,
            "b.py": BACKEND,
            "c.md": DOCUMENTATION,
            "d.py": BACKEND,
            "e.md": DOCUMENTATION,
        }
    )

    assert counts == {BACKEND: 3, DOCUMENTATION: 2}
    assert list(counts)[0] == BACKEND
