"""Classify repository files by the part of the system they belong to.

This is *domain* classification, and it is deliberately separate from the
retrieval tier in `github/file_filter.py`:

    tier   -> how much does this file explain the project?  (retrieval order)
    domain -> which part of the system is it?               (analysis grouping)

They answer different questions. `package.json` is a high-priority manifest
(tier 0) *and* a configuration file (domain "configuration"); `src/App.tsx` is
ordinary source (tier 3) *and* frontend. Keeping them apart means Step 2's
retrieval behaviour is untouched.

Path evidence is trusted first because it is cheap and reliable. Content
evidence is consulted only when the path is inconclusive, and it must clear a
threshold of several independent signals so that one stray word cannot
misclassify a file.

Nothing here performs I/O.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

# --- the ten domains ----------------------------------------------------------

DOCUMENTATION = "documentation"
FRONTEND = "frontend"
BACKEND = "backend"
DATABASE = "database"
CONFIGURATION = "configuration"
TESTING = "testing"
INFRASTRUCTURE = "infrastructure"
SECURITY = "security"
SOURCE_CODE = "source_code"
UNKNOWN = "unknown"

DOMAINS: tuple[str, ...] = (
    DOCUMENTATION, FRONTEND, BACKEND, DATABASE, CONFIGURATION,
    TESTING, INFRASTRUCTURE, SECURITY, SOURCE_CODE, UNKNOWN,
)

#: How much each domain tends to explain how the project actually works.
#: Used to order the evidence digest; lower sorts first.
DOMAIN_PRIORITY: dict[str, int] = {
    DOCUMENTATION: 0,
    CONFIGURATION: 1,
    BACKEND: 2,
    FRONTEND: 3,
    DATABASE: 4,
    SECURITY: 5,
    INFRASTRUCTURE: 6,
    SOURCE_CODE: 7,
    TESTING: 8,
    UNKNOWN: 9,
}


# --- path evidence ------------------------------------------------------------

_DOC_EXTENSIONS = {".md", ".rst", ".mdx", ".adoc", ".txt"}
_DOC_DIRS = {"docs", "doc", "documentation", "wiki"}

_FRONTEND_EXTENSIONS = {".jsx", ".tsx", ".vue", ".svelte", ".css", ".scss", ".sass", ".less", ".html"}
_FRONTEND_DIRS = {"frontend", "client", "web", "ui", "www", "public", "static", "assets", "components", "pages", "views"}

_BACKEND_DIRS = {"backend", "server", "api", "app", "services", "routes", "controllers", "handlers", "endpoints", "middleware", "core"}

_DATABASE_DIRS = {"migrations", "migration", "alembic", "models", "entities", "repositories", "schema", "schemas", "db", "database", "seeds", "prisma"}
_DATABASE_EXTENSIONS = {".sql", ".prisma"}

_TESTING_DIRS = {"test", "tests", "spec", "specs", "__tests__", "e2e", "cypress", "playwright", "testing"}

_INFRA_DIRS = {".github", ".gitlab", ".circleci", "ci", "deploy", "deployment", "k8s", "kubernetes", "helm", "terraform", "ansible", "charts", ".azure", ".buildkite"}
_INFRA_FILENAMES = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml",
    "compose.yaml", "containerfile", "procfile", "makefile", "vagrantfile",
    "jenkinsfile", "netlify.toml", "vercel.json", "fly.toml", "railway.json",
}
_INFRA_EXTENSIONS = {".tf", ".tfvars", ".hcl"}

_SECURITY_DIRS = {"auth", "authentication", "authorization", "security", "permissions", "oauth", "identity"}

_CONFIG_EXTENSIONS = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json", ".properties", ".env"}
_CONFIG_FILENAME_HINTS = (
    "config", "settings", "tsconfig", "vite.config", "next.config",
    "webpack.config", "rollup.config", "babel.config", "jest.config",
    "tailwind.config", "eslint", "prettier", "pyproject", "setup.cfg",
)
_MANIFEST_FILENAMES = {
    "package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml",
    "setup.py", "setup.cfg", "pipfile", "go.mod", "cargo.toml", "pom.xml",
    "build.gradle", "build.gradle.kts", "composer.json", "gemfile",
    "environment.yml", "pubspec.yaml",
}

_SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb",
    ".php", ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".scala", ".ex",
    ".exs", ".clj", ".sh", ".bash", ".ps1", ".lua", ".dart", ".r",
}


# --- content evidence ---------------------------------------------------------
# Consulted only when the path is inconclusive. Each domain needs at least
# `_CONTENT_SIGNAL_THRESHOLD` distinct matches, so one incidental mention of the
# word "auth" in a comment cannot reclassify an entire file.

_CONTENT_SIGNAL_THRESHOLD = 2

_CONTENT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    FRONTEND: (
        re.compile(r"\bfrom\s+['\"]react['\"]", re.I),
        re.compile(r"\buseState\s*\(|\buseEffect\s*\("),
        re.compile(r"\bReactDOM\b|\bcreateRoot\b"),
        re.compile(r"\bdocument\.(getElementById|querySelector)\b"),
        re.compile(r"\bexport\s+default\s+function\s+[A-Z]"),
        re.compile(r"<[A-Z][A-Za-z0-9]*\s*[/>]"),
    ),
    BACKEND: (
        re.compile(r"\b(FastAPI|Flask|Django|express|Koa|NestFactory|Gin|Echo|Spring)\b"),
        re.compile(r"@(app|router|api)\.(get|post|put|patch|delete)\s*\(", re.I),
        re.compile(r"\bapp\.(get|post|put|patch|delete|use|listen)\s*\("),
        re.compile(r"\b(HTTPException|status_code|request\.body|res\.json)\b"),
        re.compile(r"\basync\s+def\s+\w+\s*\([^)]*\)\s*(->|:)"),
    ),
    DATABASE: (
        re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE)\b"),
        re.compile(r"\b(sqlalchemy|SQLAlchemy|prisma|mongoose|sequelize|TypeORM|gorm)\b"),
        re.compile(r"\b(Column|ForeignKey|relationship|declarative_base|Base\.metadata)\b"),
        re.compile(r"\b(session|cursor|conn)\.(query|execute|commit)\s*\("),
        re.compile(r"\b(createTable|addColumn|migrate|alembic)\b", re.I),
    ),
    SECURITY: (
        re.compile(r"\b(jwt|JWT|OAuth|oauth2|bcrypt|argon2|scrypt|pbkdf2)\b"),
        re.compile(r"\b(authenticate|authorize|login|logout|verify_password|hash_password)\b", re.I),
        re.compile(r"\b(Depends\(get_current_user\)|@login_required|@requires_auth|passport)\b"),
        re.compile(r"\b(csrf|CORS|Access-Control-Allow-Origin|Authorization\s*:)\b", re.I),
        re.compile(r"\b(permission|role|scope)s?\s*[:=]", re.I),
    ),
    TESTING: (
        re.compile(r"\b(def\s+test_|it\s*\(|describe\s*\(|@pytest|assert\s)"),
        re.compile(r"\b(unittest|pytest|jest|mocha|vitest|testify)\b"),
        re.compile(r"\bexpect\s*\([^)]*\)\s*\.(to|toBe|toEqual)"),
    ),
}


def _segments(path: str) -> tuple[str, ...]:
    return tuple(part.lower() for part in PurePosixPath(path).parts)


def _filename(path: str) -> str:
    return PurePosixPath(path).name.lower()


def _extension(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def classify_by_path(path: str) -> str | None:
    """Classify using the path alone. Returns None when inconclusive.

    Order matters: the checks run most-specific first, because a file such as
    `tests/api/test_routes.py` is testing, not backend, and
    `backend/app/models/user.py` is database, not generic backend.
    """
    filename = _filename(path)
    extension = _extension(path)
    directories = set(_segments(path)[:-1])

    # Testing wins outright: a test file is a test file wherever it lives.
    if directories & _TESTING_DIRS:
        return TESTING
    if filename.startswith("test_") or filename.startswith("test."):
        return TESTING
    if re.search(r"[._](test|spec)\.[a-z]+$", filename):
        return TESTING
    if filename.startswith("conftest."):
        return TESTING

    # Infrastructure: build/deploy machinery.
    if directories & _INFRA_DIRS or filename in _INFRA_FILENAMES:
        return INFRASTRUCTURE
    if filename.startswith("dockerfile") or filename.startswith("docker-compose"):
        return INFRASTRUCTURE
    if extension in _INFRA_EXTENSIONS:
        return INFRASTRUCTURE

    # Dependency manifests, before documentation: `requirements.txt` ends in
    # `.txt` but is a manifest, not prose.
    if filename in _MANIFEST_FILENAMES:
        return CONFIGURATION

    # Documentation.
    if extension in _DOC_EXTENSIONS or directories & _DOC_DIRS:
        return DOCUMENTATION
    if filename.startswith("readme") or filename in {"license", "changelog", "contributing"}:
        return DOCUMENTATION

    # Security-specific areas.
    if directories & _SECURITY_DIRS:
        return SECURITY
    if re.match(r"^(auth|authentication|authorization|security|permissions?)\.[a-z]+$", filename):
        return SECURITY

    # Database.
    if extension in _DATABASE_EXTENSIONS or directories & _DATABASE_DIRS:
        return DATABASE

    # Remaining configuration.
    if filename.startswith(".env"):
        return CONFIGURATION
    if any(hint in filename for hint in _CONFIG_FILENAME_HINTS):
        return CONFIGURATION
    if extension in _CONFIG_EXTENSIONS:
        return CONFIGURATION

    # Frontend before generic backend: a .tsx under backend/ is still frontend.
    if extension in _FRONTEND_EXTENSIONS:
        return FRONTEND
    if directories & _FRONTEND_DIRS:
        return FRONTEND

    if directories & _BACKEND_DIRS:
        return BACKEND

    if extension in _SOURCE_EXTENSIONS:
        return SOURCE_CODE

    return None


def classify_by_content(content: str) -> str | None:
    """Classify from content signals. Returns None unless one domain wins clearly.

    Requires at least `_CONTENT_SIGNAL_THRESHOLD` distinct pattern matches, and
    a strict winner, so ambiguous files stay unclassified rather than being
    assigned a domain on thin evidence.
    """
    if not content:
        return None

    # Only the head of the file is inspected: imports and declarations live at
    # the top, and this keeps the scan cheap on large files.
    sample = content[:8_000]

    scores: dict[str, int] = {}
    for domain, patterns in _CONTENT_PATTERNS.items():
        hits = sum(1 for pattern in patterns if pattern.search(sample))
        if hits >= _CONTENT_SIGNAL_THRESHOLD:
            scores[domain] = hits

    if not scores:
        return None

    best = max(scores.values())
    winners = [domain for domain, score in scores.items() if score == best]
    return winners[0] if len(winners) == 1 else None


def classify_file(path: str, content: str = "") -> str:
    """Assign one of the ten domains to a file.

    Args:
        path: Repository-relative path.
        content: File text, if available. Used only to break a path-level tie.

    Returns:
        One of `DOMAINS`. Never raises.
    """
    by_path = classify_by_path(path)

    # A generic source file is a weak result - let strong content evidence
    # refine it into frontend/backend/database/security.
    if by_path in (None, SOURCE_CODE):
        by_content = classify_by_content(content)
        if by_content and by_content != TESTING:
            return by_content
        if by_content == TESTING and by_path is None:
            return TESTING

    return by_path or UNKNOWN


def domain_priority(domain: str) -> int:
    """Ordering weight for a domain. Lower means 'show this to the model first'."""
    return DOMAIN_PRIORITY.get(domain, DOMAIN_PRIORITY[UNKNOWN])


def summarise_domains(paths_with_domains: dict[str, str]) -> dict[str, int]:
    """Count files per domain, for the analysis summary."""
    counts: dict[str, int] = {}
    for domain in paths_with_domains.values():
        counts[domain] = counts.get(domain, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
