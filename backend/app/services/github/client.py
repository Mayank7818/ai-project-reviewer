"""Thin async HTTP client for the GitHub REST API.

Its single responsibility is turning HTTP outcomes into the application's own
error types, so no other module has to know about status codes, rate-limit
headers or httpx exceptions.

Authentication is optional by design: with no token GitHub still serves public
repositories (at 60 requests/hour per IP instead of 5000). The token is read
from configuration, sent only in the outbound `Authorization` header, and is
never logged or returned in any response.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import (
    ExternalServiceError,
    GitHubAuthError,
    GitHubRateLimitError,
    RepositoryNotFoundError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Pinned API version, so a future GitHub default change cannot break parsing.
_API_VERSION = "2022-11-28"


class GitHubClient:
    """Async wrapper around the GitHub REST API.

    Use as an async context manager so the underlying connection pool is always
    closed:

        async with GitHubClient(settings) as client:
            data = await client.get_repository("owner", "repo")
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.github_api_base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # --- lifecycle -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "ai-project-reviewer",
        }
        # Only attach auth when a token is actually configured, so the app keeps
        # working unauthenticated.
        if self._settings.github_token:
            headers["Authorization"] = f"Bearer {self._settings.github_token}"
        return headers

    async def __aenter__(self) -> GitHubClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=httpx.Timeout(self._settings.github_timeout_seconds),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def is_authenticated(self) -> bool:
        """Whether a token is configured. Never exposes the token itself."""
        return bool(self._settings.github_token)

    # --- request plumbing ----------------------------------------------------

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any | None:
        """Perform a GET and map every failure onto an application error.

        Args:
            path: API path, e.g. `/repos/owner/repo`.
            params: Optional query parameters.
            allow_404: When True a missing resource returns None instead of
                raising - used for genuinely optional resources like a README.

        Raises:
            RepositoryNotFoundError, GitHubRateLimitError, GitHubAuthError,
            ExternalServiceError.
        """
        if self._client is None:
            raise RuntimeError("GitHubClient must be used as an async context manager.")

        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            logger.warning("GitHub request timed out: %s", path)
            raise ExternalServiceError(
                "GitHub did not respond in time. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            # Covers DNS failure, connection refused, TLS errors, etc. The
            # message is deliberately generic - no internal detail is leaked.
            logger.warning("GitHub request failed (%s): %s", type(exc).__name__, path)
            raise ExternalServiceError(
                "Could not reach GitHub. Check your network connection."
            ) from exc

        if response.status_code == 200:
            return response.json()

        if response.status_code == 404:
            if allow_404:
                return None
            raise RepositoryNotFoundError(
                "Repository not found. Check the URL, and note that private "
                "repositories are not supported."
            )

        if response.status_code in (403, 429):
            self._raise_for_403(response)

        if response.status_code == 401:
            raise GitHubAuthError()

        if response.status_code == 451:
            raise RepositoryNotFoundError(
                "This repository is unavailable for legal reasons."
            )

        logger.warning(
            "Unexpected GitHub status %s for %s", response.status_code, path
        )
        raise ExternalServiceError(
            f"GitHub returned an unexpected response ({response.status_code})."
        )

    def _raise_for_403(self, response: httpx.Response) -> None:
        """Distinguish a rate limit from an ordinary permission denial.

        GitHub uses 403 for both, so the `x-ratelimit-remaining` header is what
        actually tells them apart.
        """
        remaining = response.headers.get("x-ratelimit-remaining")
        reset_header = response.headers.get("x-ratelimit-reset")

        is_rate_limited = remaining == "0" or "rate limit" in response.text.lower()

        if not is_rate_limited:
            raise RepositoryNotFoundError(
                "Access to this repository is forbidden. Only public "
                "repositories are supported."
            )

        details: dict[str, Any] = {"authenticated": self.is_authenticated}
        if reset_header and reset_header.isdigit():
            reset_at = datetime.fromtimestamp(int(reset_header), tz=timezone.utc)
            details["resets_at"] = reset_at.isoformat()

        raise GitHubRateLimitError(details=details)

    # --- endpoints -----------------------------------------------------------

    async def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        """`GET /repos/{owner}/{repo}` - core repository metadata."""
        return await self._get(f"/repos/{owner}/{repo}")

    async def get_readme(self, owner: str, repo: str) -> str | None:
        """`GET /repos/{owner}/{repo}/readme` - decoded README text, if any.

        Returns None when the repository has no README, which is a normal
        condition rather than an error.
        """
        payload = await self._get(f"/repos/{owner}/{repo}/readme", allow_404=True)
        if not payload:
            return None
        return _decode_content(payload)

    async def get_tree(
        self, owner: str, repo: str, branch: str
    ) -> tuple[list[dict[str, Any]], bool]:
        """`GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1`.

        One request returns the whole file listing with sizes - paths only, no
        content - which is what makes bounded selection possible before any file
        is downloaded.

        Returns:
            `(entries, truncated)`. GitHub sets `truncated` on very large trees.
        """
        payload = await self._get(
            f"/repos/{owner}/{repo}/git/trees/{branch}",
            params={"recursive": "1"},
            allow_404=True,
        )
        if not payload:
            # An empty repository has no tree for its default branch.
            return [], False
        return payload.get("tree", []), bool(payload.get("truncated"))

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str
    ) -> str | None:
        """`GET /repos/{owner}/{repo}/contents/{path}` - decoded file text.

        Returns None if the file vanished between the tree listing and this
        request, or if it turns out not to be UTF-8 text.
        """
        payload = await self._get(
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
            allow_404=True,
        )
        if not payload or payload.get("type") != "file":
            return None
        return _decode_content(payload)

    async def get_languages(self, owner: str, repo: str) -> dict[str, int]:
        """`GET /repos/{owner}/{repo}/languages` - bytes of code per language."""
        payload = await self._get(f"/repos/{owner}/{repo}/languages", allow_404=True)
        return payload or {}


def _decode_content(payload: dict[str, Any]) -> str | None:
    """Decode a base64 `content` field from the GitHub contents API.

    Returns None for anything that is not valid UTF-8 text: a binary file that
    slipped past the extension filter must not reach the response as mojibake.
    """
    raw = payload.get("content")
    if not raw:
        return None

    encoding = payload.get("encoding", "base64")
    if encoding != "base64":
        return raw if isinstance(raw, str) else None

    try:
        decoded = base64.b64decode(raw)
    except (ValueError, TypeError):
        return None

    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
