"""Orchestrates a bounded retrieval of a public GitHub repository.

The sequence, and why it is in this order:

    1. metadata  -> confirms the repo exists and gives us the default branch
    2. tree      -> one request for every path + size, still zero file content
    3. select    -> rank and cut down to the configured budget (no I/O)
    4. fetch     -> download only the survivors, concurrently but politely
    5. redact    -> mask credential-shaped strings before anything is returned

Because selection happens against the tree listing, the number of files
downloaded is bounded *before* any download starts. A 50,000-file monorepo costs
the same handful of requests as a tutorial project.

No AI or analysis happens here - this step only retrieves.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.core.cache import TTLStore
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.github.client import GitHubClient
from app.services.github.file_filter import CandidateFile, select_files
from app.services.github.repository_map import (
    RepositoryMap,
    build_map,
    mark_retrieved,
)
from app.services.github.redaction import redact_secrets
from app.services.github.url_parser import RepoRef, parse_repo_url

logger = get_logger(__name__)


@dataclass
class RetrievedFile:
    """One repository file, ready to hand to the API layer."""

    path: str
    size_bytes: int
    category: str
    content: str
    truncated: bool = False
    redacted: bool = False
    redaction_kinds: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """Everything gathered for one repository."""

    repository: dict[str, Any]
    readme: str | None
    files: list[RetrievedFile]
    tree_paths: list[str]
    tree_total_entries: int
    tree_truncated: bool
    skipped: dict[str, int]
    languages: dict[str, int]
    #: Step 7. Every candidate file in the tree, ranked, whether or not it was
    #: retrieved. Cached so later job matches and interviews reuse it.
    repository_map: RepositoryMap = field(default_factory=RepositoryMap)


#: Retrieval is cached because a single user journey touches the same repository
#: several times - analyse it, match a job against it, interview about it - and
#: the unauthenticated GitHub allowance is sixty requests an hour. One retrieval
#: costs about twenty of them, so re-fetching would exhaust the quota in three
#: repositories.
#:
#: One hour, to match GitHub's own rate-limit window: within a single window a
#: repository should never be paid for twice. Fifteen minutes was too short to
#: survive the journey it exists to serve - one analysis takes about eleven
#: minutes on two CPU cores, so the entry expired before the user got back from
#: reading their own results, and the follow-up cost another nineteen requests
#: out of sixty.
#:
#: This caches a download, not a decision. A repository that changed mid-window
#: is seen stale for at most an hour, which is the right trade when the
#: alternative is exhausting the quota in three repositories.
_RETRIEVAL_TTL_SECONDS = 60 * 60
_retrieval_cache: TTLStore[RetrievalResult] = TTLStore(
    max_entries=10, ttl_seconds=_RETRIEVAL_TTL_SECONDS
)


def get_retrieval_cache() -> TTLStore[RetrievalResult]:
    """The per-repository retrieval cache, keyed by repository and query bias."""
    return _retrieval_cache


def reset_retrieval_cache() -> None:
    """Clear it. Used by tests to guarantee isolation."""
    _retrieval_cache.clear()


class GitHubService:
    """High-level repository retrieval, built on `GitHubClient`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def retrieve(
        self, github_url: str, *, query_terms: list[str] | None = None
    ) -> RetrievalResult:
        """Fetch metadata, README, structure and a bounded set of files.

        Args:
            github_url: Raw URL as submitted by the user.

        Raises:
            InvalidRepositoryUrlError: The URL is not a GitHub repository URL.
            RepositoryNotFoundError: No such public repository.
            GitHubRateLimitError / GitHubAuthError / ExternalServiceError:
                Upstream problems, already normalised by the client.
        """
        ref: RepoRef = parse_repo_url(github_url)
        settings = self._settings

        # Keyed by the query bias as well as the repository: terms change which
        # files are selected, so a biased retrieval must not be served from an
        # unbiased one.
        cache_key = f"{ref.full_name}|{','.join(sorted(query_terms or []))}"
        cached = _retrieval_cache.get(cache_key)
        if cached is not None:
            logger.info(
                "Retrieval cache hit for %s (%d files, no GitHub requests)",
                ref.full_name,
                len(cached.files),
            )
            return cached

        async with GitHubClient(settings) as client:
            # Metadata first: it validates existence and yields the branch that
            # every subsequent request needs.
            metadata = await client.get_repository(ref.owner, ref.repo)
            default_branch = metadata.get("default_branch") or "main"

            # README and languages are independent of the tree, so overlap them.
            readme, languages, (tree_entries, tree_truncated) = await asyncio.gather(
                client.get_readme(ref.owner, ref.repo),
                client.get_languages(ref.owner, ref.repo),
                client.get_tree(ref.owner, ref.repo, default_branch),
            )

            repository_map = build_map(
                tree_entries,
                repository=ref.full_name,
                max_file_size_bytes=settings.max_file_size_bytes,
                query_terms=query_terms,
                tree_truncated=tree_truncated,
            )

            selected, skipped = select_files(
                tree_entries,
                max_files=settings.effective_max_files,
                max_file_size_bytes=settings.max_file_size_bytes,
                max_total_content_bytes=settings.max_total_content_bytes,
                query_terms=query_terms,
            )
            mark_retrieved(repository_map, [item.path for item in selected])

            logger.info(
                "%s: %d tree entries -> %d files selected (%s)",
                ref.full_name,
                len(tree_entries),
                len(selected),
                "truncated tree" if tree_truncated else "complete tree",
            )

            files = await self._fetch_files(client, ref, default_branch, selected)

        if readme:
            readme = self._prepare_readme(readme)

        result = RetrievalResult(
            repository=metadata,
            readme=readme,
            files=files,
            tree_paths=self._collect_tree_paths(tree_entries),
            tree_total_entries=len(tree_entries),
            tree_truncated=tree_truncated,
            skipped=skipped,
            languages=languages,
            repository_map=repository_map,
        )
        # Later stages enrich `repository_map` in place with the symbols they
        # extract. That is safe to share: the same repository always produces the
        # same map, so re-enrichment writes the same values.
        _retrieval_cache.put(cache_key, result)
        return result

    # --- internals -----------------------------------------------------------

    async def _fetch_files(
        self,
        client: GitHubClient,
        ref: RepoRef,
        branch: str,
        selected: list[CandidateFile],
    ) -> list[RetrievedFile]:
        """Download the selected files concurrently, with a concurrency cap.

        The semaphore keeps us from firing dozens of simultaneous requests at
        GitHub, which would burn the rate limit and risk secondary throttling.
        """
        semaphore = asyncio.Semaphore(self._settings.max_concurrent_file_requests)

        async def fetch_one(candidate: CandidateFile) -> RetrievedFile | None:
            async with semaphore:
                content = await client.get_file_content(
                    ref.owner, ref.repo, candidate.path, branch
                )
            if content is None:
                # Binary, deleted, or non-UTF-8 - skip rather than guess.
                return None
            return self._build_file(candidate, content)

        results = await asyncio.gather(
            *(fetch_one(candidate) for candidate in selected)
        )
        return [item for item in results if item is not None]

    def _build_file(self, candidate: CandidateFile, content: str) -> RetrievedFile:
        """Truncate to the per-file cap, then redact, then package."""
        limit = self._settings.max_file_size_bytes
        truncated = len(content) > limit
        if truncated:
            content = content[:limit] + "\n\n... [truncated by AI Project Reviewer]"

        content, kinds = redact_secrets(content)

        return RetrievedFile(
            path=candidate.path,
            size_bytes=candidate.size_bytes,
            category=candidate.category,
            content=content,
            truncated=truncated,
            redacted=bool(kinds),
            redaction_kinds=kinds,
        )

    def _prepare_readme(self, readme: str) -> str:
        """Apply the same size cap and redaction pass to the README."""
        limit = self._settings.max_file_size_bytes
        if len(readme) > limit:
            readme = readme[:limit] + "\n\n... [truncated by AI Project Reviewer]"
        redacted, _ = redact_secrets(readme)
        return redacted

    def _collect_tree_paths(self, tree_entries: list[dict[str, Any]]) -> list[str]:
        """Build a capped, noise-free view of the repository structure.

        Paths only - never content - so this stays cheap. Excluded directories
        are dropped so the structure shows the project, not its dependencies.
        """
        from app.services.github.file_filter import is_ignored_directory

        paths = [
            entry["path"]
            for entry in tree_entries
            if entry.get("path") and not is_ignored_directory(entry["path"])
        ]
        paths.sort()
        return paths[: self._settings.max_tree_entries_returned]


def get_github_service() -> GitHubService:
    """FastAPI dependency provider for `GitHubService`."""
    return GitHubService(get_settings())
