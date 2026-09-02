"""Production must not be able to start misconfigured.

Each case below is a mistake that stays silent until it matters: debug logging
that records third-party request detail, a CORS list that lets any site call the
API, or an empty one that locks out the application's own frontend. A refused
boot is loud and cheap. The alternative is a quiet mistake in production.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def settings(**overrides) -> Settings:
    """Build settings from arguments alone, ignoring any local .env file."""
    return Settings(_env_file=None, **overrides)


PRODUCTION = {"environment": "production", "debug": False, "cors_origins": ["https://app.example"]}


# --- what production refuses --------------------------------------------------


def test_production_refuses_debug_logging() -> None:
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        settings(**{**PRODUCTION, "debug": True})


def test_production_refuses_a_wildcard_origin() -> None:
    with pytest.raises(ValidationError, match="must not contain"):
        settings(**{**PRODUCTION, "cors_origins": ["*"]})


def test_production_refuses_an_empty_origin_list() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS is empty"):
        settings(**{**PRODUCTION, "cors_origins": []})


# --- what production accepts --------------------------------------------------


def test_a_correct_production_configuration_starts() -> None:
    config = settings(**PRODUCTION)

    assert config.is_production is True
    assert config.debug is False
    assert config.cors_origins == ["https://app.example"]


def test_several_origins_are_accepted_from_one_environment_string() -> None:
    config = settings(
        **{**PRODUCTION, "cors_origins": "https://app.example, https://www.app.example"}
    )

    assert config.cors_origins == ["https://app.example", "https://www.app.example"]


# --- development is left alone ------------------------------------------------


def test_development_may_keep_debug_on() -> None:
    config = settings(environment="development", debug=True)

    assert config.is_production is False
    assert config.debug is True


def test_the_guard_only_applies_to_production() -> None:
    """Staging is not production; it is allowed to be permissive."""
    config = settings(environment="staging", debug=True, cors_origins=["*"])

    assert config.is_production is False


# --- secrets stay out of the configuration surface ----------------------------


def test_no_secret_has_a_committed_default() -> None:
    config = settings()

    assert config.github_token == ""


def test_the_token_is_never_part_of_a_settings_repr() -> None:
    """A settings object reaches logs and tracebacks; a token must not ride along."""
    config = settings(github_token="ghp_notarealtokenbutlongenough1234")

    assert "ghp_notarealtokenbutlongenough1234" not in repr(config.model_dump(exclude={"github_token"}))


# --- CORS_ORIGINS arrives as a string from every deployment platform ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://a.example,https://b.example", ["https://a.example", "https://b.example"]),
        ("https://a.example, https://b.example", ["https://a.example", "https://b.example"]),
        ("https://a.example", ["https://a.example"]),
        ('["https://a.example", "https://b.example"]', ["https://a.example", "https://b.example"]),
        ("https://a.example,,  ,https://b.example", ["https://a.example", "https://b.example"]),
    ],
)
def test_cors_origins_parses_every_form_a_platform_supplies(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    """The regression that would have broken the first real deployment.

    pydantic-settings JSON-decodes a complex-typed environment variable before
    any validator runs, so the comma-separated form that every platform's
    environment UI produces raised SettingsError at import time and the process
    never started. `NoDecode` hands the raw string to the validator instead.
    """
    monkeypatch.setenv("CORS_ORIGINS", raw)

    config = Settings(_env_file=None, environment="production", debug=False)

    assert config.cors_origins == expected


def test_cors_origins_from_the_environment_still_meets_the_production_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "*")

    with pytest.raises(ValidationError, match="must not contain"):
        Settings(_env_file=None, environment="production", debug=False)
