"""Mask credential-shaped strings before file content leaves the backend.

This is the *second* line of defence. The first is `file_filter.is_secret_material`,
which never downloads known credential files at all. This module handles the
messier reality: a hardcoded key committed inside an ordinary source file.

Design notes:
- It is intentionally conservative about *what* it replaces (the value only,
  never the surrounding code), so redacted files stay readable and reviewable.
- It cannot be perfect. Pattern matching will never catch every possible secret,
  so it reduces exposure rather than eliminating it. Everything here operates on
  public repository content, which is already world-readable.

Nothing here performs I/O.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

#: Provider-specific token formats. These are unambiguous enough to replace
#: wholesale wherever they appear.
_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # Credentials embedded in a connection string, e.g. postgres://user:pw@host.
    (
        "url_credentials",
        re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/\s@]{1,64}):[^@/\s]{1,128}@"),
    ),
)

#: `KEY = "value"` style assignments. Only the value is replaced, and only when
#: it looks like a real secret rather than a placeholder (see `_is_placeholder`).
_ASSIGNMENT_PATTERN = re.compile(
    r"""(?P<prefix>
            \b[A-Za-z0-9_.\-]*
            (?:api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key|
               private[_-]?key|client[_-]?secret|auth|credential)
            [A-Za-z0-9_.\-]*
            \s*[:=]\s*
        )
        (?P<quote>["']?)
        (?P<value>[^\s"',;)]{6,200})
        (?P=quote)
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Values that are obviously not real credentials. Redacting these would only
#: make the file harder to read while protecting nothing.
_PLACEHOLDER_HINTS: tuple[str, ...] = (
    "your", "example", "changeme", "change_me", "placeholder", "dummy", "fake",
    "sample", "test", "xxx", "todo", "none", "null", "true", "false", "insert",
    "replace", "here", "redacted", "secret_key_here", "abc123", "<", "${",
    "process.env", "os.environ", "os.getenv", "getenv", "env.", "settings.",
    "config.", "self.", "this.",
)


def _is_placeholder(value: str) -> bool:
    """True if `value` is a template, a variable reference, or an empty string.

    Keeps `API_KEY = os.getenv("API_KEY")` and `token: <your-token>` intact,
    which is exactly the code a reviewer needs to see.
    """
    stripped = value.strip().strip("\"'")

    if not stripped or len(stripped) < 8:
        return True

    lowered = stripped.lower()
    if any(hint in lowered for hint in _PLACEHOLDER_HINTS):
        return True

    # A value with no digits and no mixed case is far more likely to be prose or
    # an identifier than a generated credential.
    has_digit = any(char.isdigit() for char in stripped)
    has_mixed_case = not stripped.islower() and not stripped.isupper()
    return not (has_digit or has_mixed_case)


def redact_secrets(content: str) -> tuple[str, list[str]]:
    """Replace credential-shaped substrings in `content`.

    Args:
        content: Raw text of a repository file.

    Returns:
        `(redacted_content, kinds)` where `kinds` lists the distinct categories
        that were masked - useful for telling the user *that* something was
        redacted without revealing what.
    """
    if not content:
        return content, []

    kinds: list[str] = []

    def record(kind: str) -> None:
        if kind not in kinds:
            kinds.append(kind)

    for kind, pattern in _TOKEN_PATTERNS:
        if kind == "url_credentials":
            # Preserve scheme and username so the connection string still shows
            # which service is being used - only the password is removed.
            content, count = pattern.subn(
                lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED}@",
                content,
            )
        else:
            content, count = pattern.subn(REDACTED, content)
        if count:
            record(kind)

    def _mask_assignment(match: re.Match[str]) -> str:
        value = match.group("value")
        if _is_placeholder(value):
            return match.group(0)
        record("assigned_secret")
        quote = match.group("quote")
        return f"{match.group('prefix')}{quote}{REDACTED}{quote}"

    content = _ASSIGNMENT_PATTERN.sub(_mask_assignment, content)

    return content, kinds
