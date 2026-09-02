"""Tests for secret redaction in retrieved file content.

The strings below are fabricated, format-valid examples - not real credentials.
"""

from __future__ import annotations

import pytest

from app.services.github.redaction import REDACTED, redact_secrets


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("token = ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "github_token"),
        ("KEY=sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4", "openai_key"),
        ("aws = AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("k = AIza" + "SyD1234567890abcdefghijklmnopqrstuv", "google_api_key"),
        ("slack: xoxb-1234567890-abcdefghijkl", "slack_token"),
        ("stripe = sk_live_" + "a1b2c3d4e5f6g7h8i9j0", "stripe_key"),
    ],
)
def test_masks_provider_tokens(content: str, kind: str) -> None:
    redacted, kinds = redact_secrets(content)

    assert REDACTED in redacted
    assert kind in kinds
    # No fragment of the original token survives.
    assert "ghp_A1b2" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted or kind != "aws_access_key"


def test_masks_private_key_blocks() -> None:
    content = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAvGVi1234567890\n"
        "-----END RSA PRIVATE KEY-----"
    )

    redacted, kinds = redact_secrets(content)

    assert "private_key_block" in kinds
    assert "MIIEowIBAAKCAQEA" not in redacted


def test_masks_credentials_in_connection_strings() -> None:
    content = 'DB = "postgresql://appuser:S3cr3tP4ssw0rd@db.internal:5432/app"'

    redacted, kinds = redact_secrets(content)

    assert "S3cr3tP4ssw0rd" not in redacted
    assert "url_credentials" in kinds
    # Scheme, user and host are preserved - a reviewer still learns the shape.
    assert "postgresql://appuser:" in redacted
    assert "db.internal:5432/app" in redacted


def test_masks_hardcoded_assignments() -> None:
    content = 'API_KEY = "a7Fk29Lm4Xq8Zt6Bv3Nc1Wp5"'

    redacted, kinds = redact_secrets(content)

    assert "a7Fk29Lm4Xq8Zt6Bv3Nc1Wp5" not in redacted
    assert "assigned_secret" in kinds
    # The variable name survives, so the code stays readable.
    assert "API_KEY" in redacted


@pytest.mark.parametrize(
    "content",
    [
        'API_KEY = os.getenv("API_KEY")',
        'api_key = os.environ["OPENAI_API_KEY"]',
        'const token = process.env.GITHUB_TOKEN',
        'SECRET_KEY = "your-secret-key-here"',
        'password = "changeme"',
        'API_KEY="<your-api-key>"',
        'token: ${GITHUB_TOKEN}',
        'password = settings.db_password',
    ],
)
def test_leaves_placeholders_and_env_lookups_intact(content: str) -> None:
    """These lines are exactly what a reviewer needs to see - do not mangle them."""
    redacted, kinds = redact_secrets(content)

    assert redacted == content
    assert kinds == []


def test_handles_empty_content() -> None:
    assert redact_secrets("") == ("", [])


def test_ordinary_code_is_untouched() -> None:
    content = "def add(a: int, b: int) -> int:\n    return a + b\n"

    redacted, kinds = redact_secrets(content)

    assert redacted == content
    assert kinds == []
