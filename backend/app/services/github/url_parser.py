"""Parse and validate GitHub repository URLs.

Runs server-side on every request. The identical check in the React app is a
convenience for fast feedback only - this module is the actual boundary, since
anything reaching the API may not have come from our frontend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.exceptions import InvalidRepositoryUrlError

# GitHub owner/repo naming rules: letters, digits, hyphen, underscore, dot.
# Accepts an optional scheme, optional "www.", an optional ".git" suffix, a
# trailing slash, and a trailing /tree/<branch> or /blob/... that users often
# copy straight from the browser address bar.
_REPO_PATTERN = re.compile(
    r"""^
    (?:https?://)?
    (?:www\.)?
    github\.com/
    (?P<owner>[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38}))
    /
    (?P<repo>[A-Za-z0-9._-]{1,100}?)
    (?:\.git)?
    (?:/(?:tree|blob|commits?)/[^\s]*)?
    /?
    $""",
    re.VERBOSE | re.IGNORECASE,
)

# Path segments GitHub itself reserves - never valid as an owner or repo name.
_RESERVED_NAMES = {".", "..", ".git", ".github"}


@dataclass(frozen=True)
class RepoRef:
    """A validated reference to a GitHub repository."""

    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_repo_url(raw_url: str) -> RepoRef:
    """Extract `(owner, repo)` from a GitHub URL.

    Args:
        raw_url: Anything the user typed.

    Returns:
        A validated `RepoRef`.

    Raises:
        InvalidRepositoryUrlError: The string is not a usable GitHub repo URL.
    """
    url = (raw_url or "").strip()

    if not url:
        raise InvalidRepositoryUrlError("A GitHub repository URL is required.")

    # Reject control characters and whitespace outright: they cannot appear in a
    # legitimate URL and are a classic way to smuggle something into a request.
    if any(char.isspace() or ord(char) < 32 for char in url):
        raise InvalidRepositoryUrlError(
            "The URL contains invalid whitespace or control characters."
        )

    if len(url) > 300:
        raise InvalidRepositoryUrlError("The URL is unreasonably long.")

    match = _REPO_PATTERN.match(url)
    if not match:
        raise InvalidRepositoryUrlError(
            "Enter a public GitHub repository URL in the form "
            "https://github.com/owner/repository.",
            details={"received": url[:120]},
        )

    owner = match.group("owner")
    repo = match.group("repo")

    if owner.lower() in _RESERVED_NAMES or repo.lower() in _RESERVED_NAMES:
        raise InvalidRepositoryUrlError("That is not a real repository name.")

    # Defence in depth against path traversal: these are interpolated into
    # GitHub API paths, so they must not be able to escape their segment.
    if any(token in value for value in (owner, repo) for token in ("..", "/", "\\")):
        raise InvalidRepositoryUrlError("That is not a real repository name.")

    return RepoRef(owner=owner, repo=repo)
