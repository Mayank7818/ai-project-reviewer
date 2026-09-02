"""Deterministic relevance scoring for repository files.

Step 2 ranked files by a coarse tier, which produced a specific failure: for
`psf/requests` the fifteen-file budget filled with root manifests, CI workflows,
`tests/` and `docs/conf.py`, and only two library modules survived - the two
smallest ones. The core of the library was never retrieved, so later steps
reported Python as only partially evidenced.

Three causes, all fixed here:

* configuration files at *any* depth outranked all source code
* shallower paths won, so `tests/` and `docs/` beat `src/requests/`
* within a tier the *smallest* file won, which actively selects stubs

Scoring is a pure function of the path and size. No model is involved, and the
same tree always produces the same order - the evidence pipeline depends on
that reproducibility.

Nothing here performs I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.services.github.file_filter import (
    MANIFEST_FILENAMES,
    TIER_LABELS,
    classify,
    is_secondary_path,
)

# --- bands --------------------------------------------------------------------

HIGH, MEDIUM, LOW = "high", "medium", "low"

#: Band thresholds. Exposed so the repository map and the tests agree on where
#: the boundaries are.
HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 40


def band_for(score: int) -> str:
    if score >= HIGH_THRESHOLD:
        return HIGH
    if score >= MEDIUM_THRESHOLD:
        return MEDIUM
    return LOW


# --- base scores --------------------------------------------------------------
# Chosen so that, at a tight budget, a root manifest still outranks core source
# (a README explains the project in a way one module cannot), while an entry
# point outranks everything - it is where the program actually starts.

SCORE_ENTRYPOINT = 95
SCORE_ROOT_MANIFEST = 92
SCORE_CORE_SOURCE = 80
SCORE_ROOT_SOURCE = 76
SCORE_DOMAIN_SOURCE = 70      # models, database, services, auth
SCORE_OTHER_SOURCE = 66
SCORE_NESTED_MANIFEST = 58
SCORE_CONFIG = 46
SCORE_TEST = 42
SCORE_DOC = 24
SCORE_UNKNOWN = 20

#: Directories that hold the code a project is actually built from.
CORE_DIRECTORIES: frozenset[str] = frozenset(
    {
        "src", "app", "lib", "backend", "server", "api", "core", "pkg",
        "internal", "cmd", "services", "handlers", "controllers", "routes",
        "endpoints", "components", "pages", "modules", "domain",
    }
)

#: Directories the specification calls MEDIUM: real code, but supporting rather
#: than central.
DOMAIN_DIRECTORIES: frozenset[str] = frozenset(
    {
        "models", "model", "entities", "repositories", "schemas", "db",
        "database", "migrations", "auth", "authentication", "authorization",
        "security", "middleware", "utils", "helpers", "adapters",
    }
)

TEST_DIRECTORIES: frozenset[str] = frozenset(
    {"test", "tests", "spec", "specs", "__tests__", "e2e", "cypress", "playwright"}
)

DOC_DIRECTORIES: frozenset[str] = frozenset({"docs", "doc", "documentation", "wiki"})

SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
        ".java", ".kt", ".rb", ".php", ".cs", ".c", ".h", ".cpp", ".hpp",
        ".swift", ".scala", ".ex", ".exs", ".clj", ".sh", ".sql", ".vue",
        ".svelte", ".graphql", ".proto",
    }
)

CONFIG_EXTENSIONS: frozenset[str] = frozenset(
    {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json", ".properties"}
)

DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst", ".mdx", ".adoc", ".txt"})

#: Filenames that mark a real entry point wherever they sit.
ENTRYPOINT_FILENAMES: frozenset[str] = frozenset(
    {
        "main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "__main__.py",
        "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts",
        "app.js", "app.ts", "app.jsx", "app.tsx", "main.jsx", "main.tsx",
        "main.go", "main.rs", "program.cs", "__init__.py",
    }
)

# --- adjustments --------------------------------------------------------------

#: A file under examples/, fixtures/ or similar is legitimate but illustrative.
SECONDARY_PENALTY = 30

#: Depth costs a little, so a root module beats a deeply buried one - but far
#: less than it used to, or `tests/` would beat `src/requests/` again.
DEPTH_PENALTY = 3
MAX_DEPTH_PENALTY = 12

#: A file too small to say anything, or so large it is probably generated.
TINY_BYTES = 400
HUGE_BYTES = 60_000
TINY_PENALTY = 12
HUGE_PENALTY = 8

#: Maximum boost a query term can contribute. Query awareness re-orders what is
#: already relevant; it can never promote an excluded or irrelevant file.
QUERY_BOOST_PATH = 18
QUERY_BOOST_FILENAME = 25
MAX_QUERY_BOOST = 30


@dataclass(frozen=True)
class Relevance:
    """A file's deterministic relevance, with the reason it scored that way."""

    score: int
    band: str
    reason: str
    query_boost: int = 0


def _segments(path: str) -> tuple[str, ...]:
    return tuple(part.lower() for part in PurePosixPath(path).parts)


def _filename(path: str) -> str:
    return PurePosixPath(path).name.lower()


def _extension(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def _base_score(path: str) -> tuple[int, str]:
    """The score a path earns before adjustments."""
    filename = _filename(path)
    extension = _extension(path)
    directories = set(_segments(path)[:-1])
    depth = len(_segments(path)) - 1

    if directories & TEST_DIRECTORIES or filename.startswith("test_"):
        return SCORE_TEST, "test file"

    if filename in MANIFEST_FILENAMES or filename.startswith(("dockerfile", "docker-compose")):
        if depth == 0:
            return SCORE_ROOT_MANIFEST, "root manifest"
        return SCORE_NESTED_MANIFEST, "nested manifest"

    if extension in SOURCE_EXTENSIONS:
        if filename in ENTRYPOINT_FILENAMES:
            return SCORE_ENTRYPOINT, "entry point"
        if directories & DOMAIN_DIRECTORIES:
            return SCORE_DOMAIN_SOURCE, "domain source (models, db, services)"
        if directories & CORE_DIRECTORIES:
            return SCORE_CORE_SOURCE, "core source"
        if depth == 0:
            return SCORE_ROOT_SOURCE, "root source"
        return SCORE_OTHER_SOURCE, "source"

    if directories & DOC_DIRECTORIES or extension in DOC_EXTENSIONS:
        return SCORE_DOC, "documentation"

    if extension in CONFIG_EXTENSIONS:
        return SCORE_CONFIG, "configuration"

    return SCORE_UNKNOWN, "unclassified"


def normalise_query_terms(terms: list[str] | None) -> tuple[str, ...]:
    """Reduce caller-supplied terms to lowercase tokens worth matching.

    Short tokens are dropped: a two-letter term would match almost any path and
    make the boost meaningless.
    """
    if not terms:
        return ()

    tokens: list[str] = []
    for term in terms:
        for piece in re.split(r"[^a-z0-9+#.]+", str(term).lower()):
            cleaned = piece.strip(".")
            if len(cleaned) >= 3 and cleaned not in tokens:
                tokens.append(cleaned)
    return tuple(tokens)


def _query_boost(path: str, terms: tuple[str, ...]) -> int:
    """Boost a file whose path or name matches what the caller is asking about."""
    if not terms:
        return 0

    lowered = path.lower()
    filename = _filename(path)
    boost = 0

    for term in terms:
        if term in filename:
            boost += QUERY_BOOST_FILENAME
        elif term in lowered:
            boost += QUERY_BOOST_PATH

    return min(boost, MAX_QUERY_BOOST)


def score_path(path: str, size_bytes: int = 0, terms: tuple[str, ...] = ()) -> Relevance:
    """Score one file. Pure, deterministic, and independent of any model.

    Args:
        path: Repository-relative path.
        size_bytes: Size from the Git tree, used only for the stub/bulk penalty.
        terms: Normalised query terms, from `normalise_query_terms`.

    Returns:
        A `Relevance` carrying the score, its band and a one-line reason.
    """
    score, reason = _base_score(path)

    depth = len(_segments(path)) - 1
    score -= min(depth * DEPTH_PENALTY, MAX_DEPTH_PENALTY)

    if is_secondary_path(path):
        score -= SECONDARY_PENALTY
        reason = f"{reason}, in an examples/fixtures directory"

    # A 200-byte module says nothing; a 90 KB one is usually exhaustive rather
    # than explanatory. Neither is disqualifying, both are mild penalties.
    if 0 < size_bytes < TINY_BYTES:
        score -= TINY_PENALTY
    elif size_bytes > HUGE_BYTES:
        score -= HUGE_PENALTY

    boost = _query_boost(path, terms)
    score += boost
    if boost:
        reason = f"{reason}, matches the query"

    score = max(0, min(100, score))
    return Relevance(score=score, band=band_for(score), reason=reason, query_boost=boost)


def sort_key(path: str, size_bytes: int, terms: tuple[str, ...] = ()) -> tuple:
    """Ordering key for retrieval: most relevant first.

    Ties break on shallower path, then **larger** file, then path. Larger is
    deliberate and is the third fix: the old ordering preferred the smallest
    file in a tier, which is how `hooks.py` (800 bytes) was retrieved from
    `psf/requests` while `sessions.py` (30 KB) was not.
    """
    relevance = score_path(path, size_bytes, terms)
    depth = len(_segments(path)) - 1
    return (-relevance.score, depth, -size_bytes, path)


def describe_tier(tier: int) -> str:
    """The Step 2 tier label, kept so the two vocabularies stay aligned."""
    return TIER_LABELS.get(tier, "other")


def tier_of(path: str) -> int:
    """Re-exported so callers need only import this module."""
    return classify(path)
