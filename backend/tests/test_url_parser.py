"""Tests for GitHub URL parsing and validation."""

from __future__ import annotations

import pytest

from app.core.exceptions import InvalidRepositoryUrlError
from app.services.github.url_parser import parse_repo_url


@pytest.mark.parametrize(
    ("url", "owner", "repo"),
    [
        ("https://github.com/tiangolo/fastapi", "tiangolo", "fastapi"),
        ("http://github.com/tiangolo/fastapi", "tiangolo", "fastapi"),
        ("github.com/tiangolo/fastapi", "tiangolo", "fastapi"),
        ("https://www.github.com/tiangolo/fastapi", "tiangolo", "fastapi"),
        ("https://github.com/tiangolo/fastapi/", "tiangolo", "fastapi"),
        ("https://github.com/tiangolo/fastapi.git", "tiangolo", "fastapi"),
        ("  https://github.com/tiangolo/fastapi  ", "tiangolo", "fastapi"),
        # Copied straight from the browser while browsing a branch or file.
        ("https://github.com/tiangolo/fastapi/tree/master", "tiangolo", "fastapi"),
        ("https://github.com/psf/requests/blob/main/README.md", "psf", "requests"),
        # Names legitimately containing dots, dashes and underscores.
        ("https://github.com/my-org/my.repo_name", "my-org", "my.repo_name"),
    ],
)
def test_accepts_valid_repository_urls(url: str, owner: str, repo: str) -> None:
    ref = parse_repo_url(url)

    assert ref.owner == owner
    assert ref.repo == repo
    assert ref.full_name == f"{owner}/{repo}"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not-a-url",
        "https://gitlab.com/owner/repo",          # wrong host
        "https://bitbucket.org/owner/repo",
        "https://github.com/",                    # no owner or repo
        "https://github.com/only-owner",          # missing repo
        "https://notgithub.com/owner/repo",
        "https://github.com.evil.com/owner/repo",  # lookalike host
        "https://github.com/../../etc/passwd",     # traversal attempt
        "https://github.com/owner/repo\nHost: evil",  # header injection attempt
    ],
)
def test_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(InvalidRepositoryUrlError):
        parse_repo_url(url)


def test_rejects_absurdly_long_urls() -> None:
    with pytest.raises(InvalidRepositoryUrlError):
        parse_repo_url("https://github.com/owner/" + "a" * 400)


def test_error_message_is_actionable() -> None:
    with pytest.raises(InvalidRepositoryUrlError) as exc_info:
        parse_repo_url("https://gitlab.com/owner/repo")

    assert "github.com/owner/repository" in str(exc_info.value)
