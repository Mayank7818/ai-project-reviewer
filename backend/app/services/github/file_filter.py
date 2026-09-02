"""Decide which repository files are worth retrieving, and stop before the
retrieval becomes expensive.

Two jobs, both pure functions over the Git tree so they are trivial to test:

1. **Exclude** anything that cannot help a reviewer - dependency directories,
   build output, binaries, media, lockfiles, minified bundles, secret material.
2. **Rank** what remains, so that when the file budget runs out the files we
   kept are the ones that actually explain the project.

Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

# --- Exclusions ---------------------------------------------------------------

#: Directory names that are dependency trees, build output or tool caches.
#: Matched against every segment of a path, at any depth.
IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        # dependencies / vendored code
        "node_modules", "bower_components", "vendor", "site-packages",
        "venv", ".venv", "env", "virtualenv", "pods", ".bundle",
        # build output
        "dist", "build", "out", "target", "bin", "obj", "_build",
        ".next", ".nuxt", ".output", ".svelte-kit", ".parcel-cache",
        # caches / tooling
        ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", ".tox", ".nox", ".gradle", ".terraform", ".serverless",
        ".cache", ".turbo", "coverage", "htmlcov", ".nyc_output",
        # editors / os
        ".idea", ".vscode", ".vs",
        # generated assets
        "generated", "__generated__", "logs",
    }
)

#: Extensions that are binary, media, archived or otherwise unreadable as text.
IGNORED_EXTENSIONS: frozenset[str] = frozenset(
    {
        # images
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
        ".svg", ".psd", ".ai",
        # video / audio
        ".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".ogg",
        ".flac", ".m4a",
        # archives
        ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".rar", ".7z", ".jar",
        # compiled / binary
        ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".obj", ".lib",
        ".pyc", ".pyo", ".pyd", ".class", ".wasm", ".node",
        # fonts
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
        # binary documents
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        # data blobs
        ".db", ".sqlite", ".sqlite3", ".parquet", ".avro", ".pkl", ".pickle",
        ".h5", ".hdf5", ".npy", ".npz", ".onnx", ".pt", ".pth", ".ckpt",
        # source maps
        ".map",
    }
)

#: Generated dependency manifests. Enormous, machine-written, zero insight.
IGNORED_FILENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
        "poetry.lock", "pipfile.lock", "cargo.lock", "composer.lock",
        "gemfile.lock", "go.sum", "flake.lock",
        ".ds_store", "thumbs.db",
    }
)

#: Files that carry live credentials. Never retrieved at all - not redacted,
#: not truncated, simply never requested. See `is_secret_material`.
SECRET_FILENAMES: frozenset[str] = frozenset(
    {
        "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials",
        "secrets.yml", "secrets.yaml", "secrets.json", ".npmrc", ".pypirc",
        ".netrc", ".htpasswd", "service-account.json",
    }
)

SECRET_EXTENSIONS: frozenset[str] = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".ppk", ".asc"}
)

#: `.env` templates are published on purpose and describe the configuration
#: surface, which is genuinely useful. Every other `.env*` file is treated as
#: live secret material.
ENV_TEMPLATE_SUFFIXES: tuple[str, ...] = (
    ".example", ".sample", ".template", ".dist", ".defaults"
)


# --- Prioritisation -----------------------------------------------------------
# Lower tier number == retrieved first. When the budget runs out, high-tier
# files are the ones dropped.

#: Tier 0 - the files that describe what the project *is* and how it is built.
MANIFEST_FILENAMES: frozenset[str] = frozenset(
    {
        "readme.md", "readme.rst", "readme.txt", "readme",
        "package.json", "requirements.txt", "requirements-dev.txt",
        "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "environment.yml",
        "dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "compose.yml", "compose.yaml", "containerfile",
        "go.mod", "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
        "composer.json", "gemfile", "makefile", "cmakelists.txt",
        "pubspec.yaml", "mix.exs", "build.sbt", "deno.json",
    }
)

#: Tier 1 - conventional application entry points.
ENTRYPOINT_FILENAMES: frozenset[str] = frozenset(
    {
        "main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "__main__.py",
        "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts",
        "app.js", "app.ts", "app.jsx", "app.tsx", "main.jsx", "main.tsx",
        "main.go", "main.rs", "program.cs",
    }
)

#: Tier 2 - configuration that reveals architecture and tooling.
CONFIG_EXTENSIONS: frozenset[str] = frozenset(
    {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json", ".properties"}
)

CONFIG_FILENAME_HINTS: tuple[str, ...] = (
    "config", "settings", "tsconfig", "vite.config", "next.config",
    "webpack.config", "rollup.config", "babel.config", "jest.config",
    "tailwind.config", "eslint", "prettier", "alembic", "nginx", "procfile",
)

#: Tier 3 - ordinary source code.
SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
        ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".c", ".h", ".cpp",
        ".hpp", ".cc", ".swift", ".scala", ".m", ".mm", ".r", ".jl", ".lua",
        ".dart", ".ex", ".exs", ".clj", ".sh", ".bash", ".ps1", ".sql",
        ".vue", ".svelte", ".graphql", ".gql", ".proto", ".tf", ".hcl",
    }
)

#: Tier 4 - prose documentation beyond the README.
DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst", ".mdx", ".adoc", ".txt"})

#: Directories holding illustrative rather than production code. Not excluded -
#: they are legitimate repository content - but demoted, because a library's own
#: source explains it far better than twenty example projects do. Without this,
#: a repo like pallets/click fills the entire budget with examples/*/README and
#: examples/*/pyproject.toml and the real source is never seen.
SECONDARY_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        "example", "examples", "sample", "samples", "demo", "demos",
        "fixture", "fixtures", "testdata", "template", "templates",
        "benchmark", "benchmarks", "playground", "scratch",
    }
)

#: Added to the sort tier of anything under a secondary directory. Large enough
#: to push it below every primary-tier file, without excluding it outright.
SECONDARY_TIER_PENALTY = 10

TIER_MANIFEST, TIER_ENTRYPOINT, TIER_CONFIG, TIER_SOURCE, TIER_DOC = 0, 1, 2, 3, 4
TIER_OTHER = 5

#: Human-readable labels, surfaced in the API response so the frontend can show
#: *why* a file was chosen without duplicating this logic.
TIER_LABELS: dict[int, str] = {
    TIER_MANIFEST: "manifest",
    TIER_ENTRYPOINT: "entrypoint",
    TIER_CONFIG: "config",
    TIER_SOURCE: "source",
    TIER_DOC: "docs",
    TIER_OTHER: "other",
}


@dataclass(frozen=True)
class CandidateFile:
    """A tree entry that survived filtering, with its retrieval priority."""

    path: str
    size_bytes: int
    tier: int
    #: Sort key: tier, then shallower paths, then smaller files. Shallow files
    #: are usually the architectural ones; small files cost less budget.
    sort_key: tuple[int, int, int, str]

    @property
    def category(self) -> str:
        return TIER_LABELS.get(self.tier, "other")


def _segments(path: str) -> tuple[str, ...]:
    return tuple(PurePosixPath(path).parts)


def _filename(path: str) -> str:
    return PurePosixPath(path).name.lower()


def _extension(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def is_ignored_directory(path: str) -> bool:
    """True if any *directory* segment of `path` is an excluded directory."""
    return any(
        segment.lower() in IGNORED_DIRECTORIES for segment in _segments(path)[:-1]
    )


def is_secondary_path(path: str) -> bool:
    """True if `path` lives under an example/demo/fixture directory."""
    return any(
        segment.lower() in SECONDARY_PATH_SEGMENTS for segment in _segments(path)[:-1]
    )


def _is_minified(filename: str) -> bool:
    """Minified bundles are source-derived and unreadable - skip them."""
    return ".min." in filename or filename.endswith((".bundle.js", ".chunk.js"))


def is_secret_material(path: str) -> bool:
    """True if the file is likely to contain live credentials.

    `.env.example` and friends are explicitly allowed: they are published
    deliberately, contain placeholders rather than values, and document the
    configuration surface.
    """
    filename = _filename(path)

    if filename in SECRET_FILENAMES or _extension(path) in SECRET_EXTENSIONS:
        return True

    if filename.startswith(".env"):
        return not filename.endswith(ENV_TEMPLATE_SUFFIXES)

    return False


def should_skip(path: str, size_bytes: int, max_file_size_bytes: int) -> str | None:
    """Return a machine-readable skip reason, or None to keep the file.

    Returning the *reason* (rather than a bare bool) lets the API report an
    honest, auditable summary of what was left out and why.
    """
    filename = _filename(path)

    if is_ignored_directory(path):
        return "ignored_directory"
    if filename in IGNORED_FILENAMES:
        return "generated_file"
    if is_secret_material(path):
        return "secret_material"
    if _extension(path) in IGNORED_EXTENSIONS:
        return "binary_or_media"
    if _is_minified(filename):
        return "minified_bundle"
    if size_bytes > max_file_size_bytes:
        return "too_large"
    return None


def classify(path: str) -> int:
    """Assign a retrieval priority tier to a kept file."""
    filename = _filename(path)
    extension = _extension(path)
    depth = len(_segments(path)) - 1

    if filename in MANIFEST_FILENAMES:
        return TIER_MANIFEST
    # A Dockerfile or compose file may be suffixed, e.g. "Dockerfile.prod".
    if filename.startswith(("dockerfile", "docker-compose")):
        return TIER_MANIFEST
    if filename in ENTRYPOINT_FILENAMES and depth <= 3:
        return TIER_ENTRYPOINT
    if extension in CONFIG_EXTENSIONS or any(
        hint in filename for hint in CONFIG_FILENAME_HINTS
    ):
        return TIER_CONFIG
    if extension in SOURCE_EXTENSIONS:
        return TIER_SOURCE
    if extension in DOC_EXTENSIONS:
        return TIER_DOC
    # Unknown extension: keep it, but only if nothing better needs the budget.
    return TIER_OTHER


#: Share of the file budget reserved for actual source code, so configuration
#: and documentation cannot crowd it out. Without this, `psf/requests` filled
#: fifteen slots with manifests, CI workflows, tests and docs, leaving two
#: library modules - the two smallest ones.
SOURCE_RESERVATION_SHARE = 0.4

#: The reservation only applies once the budget is big enough to divide. At a
#: very tight budget the plain relevance order is the right answer, and this
#: also preserves Step 2's behaviour for small selections.
MIN_FILES_FOR_RESERVATION = 6

#: Tiers that count as source for the reservation.
_SOURCE_TIERS = (TIER_ENTRYPOINT, TIER_SOURCE)


def select_files(
    tree_entries: list[dict],
    *,
    max_files: int,
    max_file_size_bytes: int,
    max_total_content_bytes: int,
    query_terms: list[str] | None = None,
) -> tuple[list[CandidateFile], dict[str, int]]:
    """Choose which files to download from a Git tree listing.

    Ordering is by deterministic relevance (see `relevance.py`), and a share of
    the budget is reserved for source code so configuration and documentation
    cannot displace the code the analysis actually needs.

    Args:
        tree_entries: Raw entries from GitHub's tree API. Only `type == "blob"`
            entries are considered; each needs `path` and (usually) `size`.
        max_files: Hard cap on how many files are ever downloaded.
        max_file_size_bytes: Any single file larger than this is skipped.
        max_total_content_bytes: Cumulative budget across all selected files.
        query_terms: Optional terms to bias ranking toward - job skills, or the
            subject of an interview question. Re-orders what is already
            relevant; never promotes an excluded file.

    Returns:
        `(selected, skip_counts)` - the ranked, budget-bounded selection, and a
        tally of skip reasons suitable for reporting to the client.
    """
    from app.services.github import relevance as relevance_module

    terms = relevance_module.normalise_query_terms(query_terms)
    skip_counts: dict[str, int] = {}
    candidates: list[CandidateFile] = []
    seen: set[str] = set()

    def note(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    for entry in tree_entries:
        # Only blobs are files; "tree" is a directory and "commit" a submodule.
        if entry.get("type") != "blob":
            continue

        path = entry.get("path") or ""
        # A path can only be selected once, whatever the tree contains.
        if not path or path in seen:
            continue
        seen.add(path)

        # GitHub omits `size` for symlink entries; treat unknown as zero so the
        # file is still considered rather than silently dropped.
        size = int(entry.get("size") or 0)

        reason = should_skip(path, size, max_file_size_bytes)
        if reason:
            note(reason)
            continue

        candidates.append(
            CandidateFile(
                path=path,
                size_bytes=size,
                tier=classify(path),
                sort_key=relevance_module.sort_key(path, size, terms),
            )
        )

    candidates.sort(key=lambda candidate: candidate.sort_key)

    selected: list[CandidateFile] = []
    chosen: set[str] = set()
    total_bytes = 0

    def take(candidate: CandidateFile) -> bool:
        """Select a candidate if it fits both budgets."""
        nonlocal total_bytes
        if candidate.path in chosen or len(selected) >= max_files:
            return False
        if total_bytes + candidate.size_bytes > max_total_content_bytes:
            return False
        selected.append(candidate)
        chosen.add(candidate.path)
        total_bytes += candidate.size_bytes
        return True

    # --- pass 1: the reserved source quota ----------------------------------
    if max_files >= MIN_FILES_FOR_RESERVATION:
        quota = int(max_files * SOURCE_RESERVATION_SHARE)
        for candidate in candidates:
            if len(selected) >= quota:
                break
            if candidate.tier in _SOURCE_TIERS:
                take(candidate)

    # --- pass 2: everything else, in relevance order ------------------------
    for candidate in candidates:
        if candidate.path in chosen:
            continue
        if len(selected) >= max_files:
            note("file_count_limit")
            continue
        if not take(candidate):
            # Did not fit the byte budget; a later, smaller file still might.
            note("total_size_limit")

    # Report in relevance order rather than selection order, so the caller sees
    # the ranking rather than the two-pass mechanics.
    selected.sort(key=lambda candidate: candidate.sort_key)
    return selected, skip_counts
