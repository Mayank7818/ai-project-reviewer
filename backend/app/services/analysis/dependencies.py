"""Parse dependency manifests into a structured inventory.

Entirely deterministic: the technologies a project uses are stated plainly in
its manifests, so there is no reason to ask a language model to guess them. The
model receives this inventory as *fact* and reasons about it, rather than
inferring dependencies from import statements.

Scope limit, per Feature 7: this identifies what is declared. It makes no claim
about whether any version is vulnerable, and performs no database lookups.

Nothing here performs I/O.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

#: Cap per manifest so a project with 400 dependencies cannot flood the prompt.
MAX_DEPENDENCIES_PER_FILE = 60


@dataclass(frozen=True)
class Dependency:
    """One declared dependency."""

    name: str
    version: str = ""
    dev: bool = False


@dataclass
class ManifestReport:
    """Dependencies declared by a single manifest file."""

    path: str
    ecosystem: str                       # npm | pypi | go | cargo | maven | gradle
    dependencies: list[Dependency] = field(default_factory=list)
    parse_error: str | None = None

    @property
    def runtime_names(self) -> list[str]:
        return [item.name for item in self.dependencies if not item.dev]


#: Filename -> ecosystem. Matched case-insensitively on the basename.
MANIFEST_ECOSYSTEMS: dict[str, str] = {
    "package.json": "npm",
    "requirements.txt": "pypi",
    "requirements-dev.txt": "pypi",
    "pyproject.toml": "pypi",
    "pipfile": "pypi",
    "setup.py": "pypi",
    "go.mod": "go",
    "cargo.toml": "cargo",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "composer.json": "composer",
    "gemfile": "rubygems",
}


def is_manifest(path: str) -> bool:
    """True if `path` is a dependency manifest this module can parse."""
    return PurePosixPath(path).name.lower() in MANIFEST_ECOSYSTEMS


def _truncate(dependencies: list[Dependency]) -> list[Dependency]:
    return dependencies[:MAX_DEPENDENCIES_PER_FILE]


# --- per-ecosystem parsers ----------------------------------------------------


def _parse_package_json(content: str) -> tuple[list[Dependency], str | None]:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return [], "not valid JSON"
    if not isinstance(data, dict):
        return [], "not a JSON object"

    dependencies: list[Dependency] = []
    for key, is_dev in (("dependencies", False), ("devDependencies", True)):
        section = data.get(key)
        if isinstance(section, dict):
            dependencies.extend(
                Dependency(name=str(name), version=str(version), dev=is_dev)
                for name, version in section.items()
            )
    return dependencies, None


#: `package==1.2.3`, `package>=1.0`, `package[extra]~=2.0`, bare `package`.
_REQUIREMENT_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(?P<version>[<>=!~^].*)?$"
)


def _parse_requirements(content: str) -> tuple[list[Dependency], str | None]:
    dependencies: list[Dependency] = []
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        # Skip blanks, pip flags (-r, --index-url) and VCS/URL installs, whose
        # package name is not reliably recoverable from the line.
        if not line or line.startswith("-") or "://" in line:
            continue
        match = _REQUIREMENT_LINE.match(line)
        if match:
            dependencies.append(
                Dependency(
                    name=match.group("name"),
                    version=(match.group("version") or "").strip(),
                )
            )
    return dependencies, None


def _parse_pyproject(content: str) -> tuple[list[Dependency], str | None]:
    """Read PEP 621 `dependencies` and Poetry's `[tool.poetry.dependencies]`.

    Uses targeted regex rather than a TOML parser: only two well-known shapes
    matter here, and this keeps the module dependency-free.
    """
    dependencies: list[Dependency] = []

    # PEP 621: dependencies = ["fastapi>=0.100", "httpx"]
    array_match = re.search(
        r"^\s*dependencies\s*=\s*\[(?P<body>.*?)\]", content, re.S | re.M
    )
    if array_match:
        for entry in re.findall(r"[\"']([^\"']+)[\"']", array_match.group("body")):
            parsed, _ = _parse_requirements(entry)
            dependencies.extend(parsed)

    # Poetry: [tool.poetry.dependencies] followed by `name = "^1.0"` lines.
    poetry_match = re.search(
        r"\[tool\.poetry\.dependencies\](?P<body>.*?)(?=^\[|\Z)", content, re.S | re.M
    )
    if poetry_match:
        for line in poetry_match.group("body").splitlines():
            entry = re.match(r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*=\s*(?P<version>.+)$", line)
            if entry and entry.group("name").lower() != "python":
                dependencies.append(
                    Dependency(
                        name=entry.group("name"),
                        version=entry.group("version").strip().strip("\"'{}"),
                    )
                )

    return dependencies, None


def _parse_go_mod(content: str) -> tuple[list[Dependency], str | None]:
    dependencies: list[Dependency] = []

    block = re.search(r"require\s*\((?P<body>.*?)\)", content, re.S)
    lines = block.group("body").splitlines() if block else content.splitlines()

    for raw_line in lines:
        line = raw_line.split("//", 1)[0].strip()
        if not line or line.startswith(("module", "go ", "require (", ")")):
            continue
        entry = re.match(r"^(?:require\s+)?(?P<name>[\w./-]+)\s+(?P<version>v[\w.+-]+)", line)
        if entry:
            dependencies.append(
                Dependency(name=entry.group("name"), version=entry.group("version"))
            )
    return dependencies, None


def _parse_cargo(content: str) -> tuple[list[Dependency], str | None]:
    dependencies: list[Dependency] = []
    for header, is_dev in (
        (r"\[dependencies\]", False),
        (r"\[dev-dependencies\]", True),
    ):
        section = re.search(rf"{header}(?P<body>.*?)(?=^\[|\Z)", content, re.S | re.M)
        if not section:
            continue
        for line in section.group("body").splitlines():
            entry = re.match(r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*=\s*(?P<version>.+)$", line)
            if entry:
                dependencies.append(
                    Dependency(
                        name=entry.group("name"),
                        version=entry.group("version").strip().strip("\"'"),
                        dev=is_dev,
                    )
                )
    return dependencies, None


def _parse_pom(content: str) -> tuple[list[Dependency], str | None]:
    dependencies: list[Dependency] = []
    for block in re.findall(r"<dependency>(.*?)</dependency>", content, re.S):
        artifact = re.search(r"<artifactId>\s*([^<]+?)\s*</artifactId>", block)
        group = re.search(r"<groupId>\s*([^<]+?)\s*</groupId>", block)
        version = re.search(r"<version>\s*([^<]+?)\s*</version>", block)
        if artifact:
            name = artifact.group(1)
            if group:
                name = f"{group.group(1)}:{name}"
            dependencies.append(
                Dependency(name=name, version=version.group(1) if version else "")
            )
    return dependencies, None


def _parse_gradle(content: str) -> tuple[list[Dependency], str | None]:
    dependencies: list[Dependency] = []
    pattern = re.compile(
        r"^\s*(?P<scope>implementation|api|compileOnly|runtimeOnly|testImplementation)"
        r"[\s(]+[\"'](?P<coord>[^\"']+)[\"']",
        re.M,
    )
    for match in pattern.finditer(content):
        coordinate = match.group("coord")
        parts = coordinate.split(":")
        name = ":".join(parts[:2]) if len(parts) >= 2 else coordinate
        version = parts[2] if len(parts) >= 3 else ""
        dependencies.append(
            Dependency(
                name=name,
                version=version,
                dev=match.group("scope").startswith("test"),
            )
        )
    return dependencies, None


def _parse_composer(content: str) -> tuple[list[Dependency], str | None]:
    return _parse_package_json(
        content.replace('"require-dev"', '"devDependencies"').replace('"require"', '"dependencies"')
    )


def _parse_gemfile(content: str) -> tuple[list[Dependency], str | None]:
    dependencies = [
        Dependency(name=match.group("name"))
        for match in re.finditer(r"^\s*gem\s+[\"'](?P<name>[^\"']+)", content, re.M)
    ]
    return dependencies, None


_PARSERS = {
    "package.json": _parse_package_json,
    "requirements.txt": _parse_requirements,
    "requirements-dev.txt": _parse_requirements,
    "pyproject.toml": _parse_pyproject,
    "pipfile": _parse_pyproject,
    "setup.py": _parse_requirements,
    "go.mod": _parse_go_mod,
    "cargo.toml": _parse_cargo,
    "pom.xml": _parse_pom,
    "build.gradle": _parse_gradle,
    "build.gradle.kts": _parse_gradle,
    "composer.json": _parse_composer,
    "gemfile": _parse_gemfile,
}


def parse_manifest(path: str, content: str) -> ManifestReport | None:
    """Parse one manifest file. Returns None if `path` is not a manifest."""
    filename = PurePosixPath(path).name.lower()
    ecosystem = MANIFEST_ECOSYSTEMS.get(filename)
    if ecosystem is None:
        return None

    report = ManifestReport(path=path, ecosystem=ecosystem)

    parser = _PARSERS.get(filename)
    if parser is None:
        return report

    try:
        dependencies, error = parser(content)
    except Exception as exc:  # noqa: BLE001 - a malformed manifest must not fail the run
        return ManifestReport(
            path=path,
            ecosystem=ecosystem,
            parse_error=f"could not be parsed ({type(exc).__name__})",
        )

    report.dependencies = _truncate(dependencies)
    report.parse_error = error
    return report


def analyse_dependencies(files: dict[str, str]) -> list[ManifestReport]:
    """Parse every manifest in a path -> content mapping."""
    reports = [
        report
        for path, content in files.items()
        if (report := parse_manifest(path, content)) is not None
    ]
    reports.sort(key=lambda item: (item.path.count("/"), item.path))
    return reports


#: Recognisable package name -> the technology it implies. Used to name
#: technologies from evidence rather than from the model's memory.
_TECHNOLOGY_HINTS: dict[str, str] = {
    "react": "React", "react-dom": "React", "next": "Next.js", "vue": "Vue",
    "svelte": "Svelte", "@angular/core": "Angular", "vite": "Vite",
    "webpack": "Webpack", "tailwindcss": "Tailwind CSS", "typescript": "TypeScript",
    "express": "Express", "koa": "Koa", "@nestjs/core": "NestJS",
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
    "uvicorn": "Uvicorn", "gunicorn": "Gunicorn", "starlette": "Starlette",
    "pydantic": "Pydantic", "sqlalchemy": "SQLAlchemy", "alembic": "Alembic",
    "psycopg2": "PostgreSQL", "psycopg2-binary": "PostgreSQL", "asyncpg": "PostgreSQL",
    "pymysql": "MySQL", "mysqlclient": "MySQL", "pymongo": "MongoDB",
    "mongoose": "MongoDB", "redis": "Redis", "prisma": "Prisma",
    "sequelize": "Sequelize", "typeorm": "TypeORM",
    "requests": "requests", "httpx": "httpx", "axios": "axios",
    "pytest": "pytest", "jest": "Jest", "vitest": "Vitest", "mocha": "Mocha",
    "celery": "Celery", "boto3": "AWS SDK", "openai": "OpenAI SDK",
    "anthropic": "Anthropic SDK", "langchain": "LangChain", "ollama": "Ollama",
    "numpy": "NumPy", "pandas": "pandas", "torch": "PyTorch",
    "tensorflow": "TensorFlow", "scikit-learn": "scikit-learn",
    "github.com/gin-gonic/gin": "Gin", "github.com/labstack/echo": "Echo",
    "org.springframework.boot": "Spring Boot",
}


def infer_technologies(reports: list[ManifestReport]) -> list[str]:
    """Name technologies from declared dependencies - evidence, not recall."""
    technologies: list[str] = []
    for report in reports:
        for dependency in report.dependencies:
            key = dependency.name.lower()
            technology = _TECHNOLOGY_HINTS.get(key)
            if technology is None:
                # Maven/Gradle coordinates: try the group prefix too.
                technology = next(
                    (
                        value
                        for hint, value in _TECHNOLOGY_HINTS.items()
                        if ":" in key and key.startswith(hint)
                    ),
                    None,
                )
            if technology and technology not in technologies:
                technologies.append(technology)
    return technologies
