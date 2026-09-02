"""A structured map of a repository's files.

Built once from the Git tree - paths and sizes only, no content - and then
enriched with the symbols Step 4 extracts from whatever was actually fetched.
Cached alongside the analysis so repeated job matches and interviews reuse it
instead of re-fetching the tree.

The map is what makes retrieval explainable: for any file it records why it was
scored where it was, and whether it was retrieved, so a user can see that
`src/requests/sessions.py` was ranked highly and fetched while
`docs/user/advanced.rst` was ranked low and skipped.

Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.services.github import relevance
from app.services.github.file_filter import (
    MANIFEST_FILENAMES,
    TIER_LABELS,
    classify,
    should_skip,
)


@dataclass
class RepositoryFile:
    """One file in the repository, with everything known about it."""

    path: str
    extension: str
    size_bytes: int
    #: Analysis domain: backend, frontend, database, testing, …
    domain: str
    #: Step 2 retrieval tier label: manifest, entrypoint, config, source, docs.
    tier: str
    relevance_score: int
    relevance_band: str
    relevance_reason: str
    is_manifest: bool = False
    #: True once the file's content was actually fetched.
    retrieved: bool = False
    #: Why it was not retrieved, when it was not.
    skip_reason: str | None = None
    #: Symbols extracted by Step 4, populated after retrieval.
    symbols: list[str] = field(default_factory=list)
    line_count: int = 0

    @property
    def depth(self) -> int:
        return len(PurePosixPath(self.path).parts) - 1


@dataclass
class RepositoryMap:
    """Every candidate file in a repository, ranked."""

    repository: str = ""
    files: list[RepositoryFile] = field(default_factory=list)
    total_tree_entries: int = 0
    tree_truncated: bool = False
    #: The query terms this map was ranked against, if any.
    query_terms: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.files)

    def get(self, path: str) -> RepositoryFile | None:
        return next((item for item in self.files if item.path == path), None)

    @property
    def retrieved(self) -> list[RepositoryFile]:
        return [item for item in self.files if item.retrieved]

    def by_band(self, band: str) -> list[RepositoryFile]:
        return [item for item in self.files if item.relevance_band == band]

    def band_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.files:
            counts[item.relevance_band] = counts.get(item.relevance_band, 0) + 1
        return dict(sorted(counts.items()))

    def retrieved_band_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.retrieved:
            counts[item.relevance_band] = counts.get(item.relevance_band, 0) + 1
        return dict(sorted(counts.items()))

    def top(self, limit: int = 20) -> list[RepositoryFile]:
        return self.files[:limit]


def build_map(
    tree_entries: list[dict],
    *,
    repository: str = "",
    max_file_size_bytes: int = 100_000,
    query_terms: list[str] | None = None,
    tree_truncated: bool = False,
) -> RepositoryMap:
    """Rank every file in a Git tree, before anything is downloaded.

    Excluded files (dependency trees, binaries, secret material) are recorded
    with their skip reason rather than dropped, so the map explains the whole
    tree rather than only its survivors.

    Args:
        tree_entries: Raw entries from GitHub's tree API.
        repository: `owner/repo`, for reporting.
        max_file_size_bytes: The per-file cap, used to mark oversized files.
        query_terms: Optional terms to bias ranking toward.
        tree_truncated: Whether GitHub truncated the listing.
    """
    # Imported here rather than at module level: `app.services.analysis` imports
    # `github.service`, which imports this module, so a top-level import would
    # close a cycle. The dependency direction is fine; only the timing is not.
    from app.services.analysis.classifier import classify_by_path

    terms = relevance.normalise_query_terms(query_terms)
    files: list[RepositoryFile] = []
    seen: set[str] = set()

    for entry in tree_entries:
        if entry.get("type") != "blob":
            continue

        path = entry.get("path") or ""
        # A tree can legitimately repeat nothing, but a defensive de-duplication
        # here guarantees the map - and therefore the selection - has no
        # duplicates whatever the API returns.
        if not path or path in seen:
            continue
        seen.add(path)

        size = int(entry.get("size") or 0)
        skip_reason = should_skip(path, size, max_file_size_bytes)
        scored = relevance.score_path(path, size, terms)
        filename = PurePosixPath(path).name.lower()
        tier = classify(path)

        files.append(
            RepositoryFile(
                path=path,
                extension=PurePosixPath(path).suffix.lower(),
                size_bytes=size,
                domain=classify_by_path(path) or "unknown",
                tier=TIER_LABELS.get(tier, "other"),
                relevance_score=scored.score,
                relevance_band=scored.band,
                relevance_reason=scored.reason,
                is_manifest=(
                    filename in MANIFEST_FILENAMES
                    or filename.startswith(("dockerfile", "docker-compose"))
                ),
                skip_reason=skip_reason,
            )
        )

    files.sort(key=lambda item: relevance.sort_key(item.path, item.size_bytes, terms))

    return RepositoryMap(
        repository=repository,
        files=files,
        total_tree_entries=len(tree_entries),
        tree_truncated=tree_truncated,
        query_terms=terms,
    )


def mark_retrieved(repository_map: RepositoryMap, paths: list[str]) -> None:
    """Record which files were actually downloaded."""
    wanted = set(paths)
    for item in repository_map.files:
        if item.path in wanted:
            item.retrieved = True


def enrich_with_symbols(repository_map: RepositoryMap, structures: list) -> None:
    """Attach the symbols Step 4 extracted to the files they came from.

    Only the names are stored - line numbers stay with the structures
    themselves, which is what the evidence validator checks citations against.
    Duplicating them here would create a second source of truth.
    """
    for structure in structures:
        entry = repository_map.get(structure.path)
        if entry is None:
            continue

        names = [
            symbol.name
            for group in (
                structure.classes,
                structure.functions,
                structure.methods,
                structure.routes,
            )
            for symbol in group
        ]
        entry.symbols = names[:25]
        entry.line_count = structure.line_count
