"""Extract structural facts from source files, with real line numbers.

This is the foundation of the evidence system. Everything here is derived
mechanically from the file text, so a fact produced by this module is *true* by
construction - the model is never asked to recall or guess a line number, it is
handed one.

Deliberately not a compiler. Python uses the standard library's `ast` because it
is free and exact; every other language uses line-oriented regular expressions,
which is enough to recover declarations, imports and route registrations without
pretending to understand the grammar.

Nothing here performs I/O.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

#: Files above this are structurally scanned but not deeply parsed, to keep a
#: single pathological file from dominating the analysis.
MAX_PARSE_CHARS = 200_000

#: Caps per file, so one enormous module cannot flood the digest.
MAX_ITEMS_PER_KIND = 40


@dataclass(frozen=True)
class CodeSymbol:
    """A declaration found in a file, with the line it was found on."""

    name: str
    kind: str          # function | class | method | route | import
    line: int          # 1-indexed, real
    detail: str = ""   # signature fragment, HTTP method + path, etc.


@dataclass
class FileStructure:
    """Everything mechanically extracted from one file."""

    path: str
    language: str
    line_count: int
    imports: list[CodeSymbol] = field(default_factory=list)
    functions: list[CodeSymbol] = field(default_factory=list)
    classes: list[CodeSymbol] = field(default_factory=list)
    methods: list[CodeSymbol] = field(default_factory=list)
    routes: list[CodeSymbol] = field(default_factory=list)
    #: Signal name -> line numbers where it was observed.
    signals: dict[str, list[int]] = field(default_factory=dict)
    parse_error: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.imports or self.functions or self.classes or self.routes or self.signals
        )


# --- language detection -------------------------------------------------------

_LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".swift": "swift", ".scala": "scala", ".sh": "shell", ".bash": "shell",
    ".sql": "sql", ".vue": "vue", ".svelte": "svelte",
}


def detect_language(path: str) -> str:
    return _LANGUAGE_BY_EXTENSION.get(PurePosixPath(path).suffix.lower(), "unknown")


# --- cross-language signal patterns -------------------------------------------
# These answer the "does this file do X?" questions in Feature 2 without needing
# to understand the language. Every match records a real line number.

_SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "database_query": re.compile(
        r"\b(SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE)\b"
        r"|\.(execute|executemany|query|raw|find|findOne|aggregate)\s*\(",
        re.I,
    ),
    "orm_model": re.compile(
        r"\b(declarative_base|DeclarativeBase|models\.Model|mongoose\.Schema|"
        r"@Entity|Column\s*\(|ForeignKey\s*\(|relationship\s*\()"
    ),
    "authentication": re.compile(
        r"\b(jwt|jsonwebtoken|OAuth2|oauth2|bcrypt|argon2|scrypt|pbkdf2|"
        r"verify_password|hash_password|check_password|authenticate|passport)\b",
        re.I,
    ),
    "authorization": re.compile(
        r"\b(@login_required|@requires_auth|@permission_required|has_permission|"
        r"is_admin|check_permission|current_user|get_current_user|RBAC|scopes?\s*=)\b"
    ),
    "external_api_call": re.compile(
        r"\b(requests\.(get|post|put|patch|delete)|httpx\.(get|post|AsyncClient)|"
        r"urllib\.request|fetch\s*\(|axios\.(get|post|put|patch|delete)|"
        r"http\.Get|HttpClient|WebClient)\b"
    ),
    "subprocess": re.compile(
        r"\b(subprocess\.(run|call|Popen|check_output)|os\.(system|popen)|"
        r"exec\s*\(|eval\s*\(|child_process|execSync|spawnSync|Runtime\.getRuntime)\b"
    ),
    "file_io": re.compile(
        r"\b(open\s*\(|Path\s*\(|readFile|writeFile|createReadStream|"
        r"os\.remove|shutil\.|fs\.(unlink|rm|readdir))\b"
    ),
    "env_config": re.compile(
        r"\b(os\.environ|os\.getenv|process\.env|dotenv|BaseSettings|"
        r"System\.getenv|std::env::var)\b"
    ),
    "logging": re.compile(r"\b(logger\.|logging\.|console\.(log|error|warn)|log\.(info|error))"),
    "async_operation": re.compile(r"\b(async\s+def|await\s+|asyncio\.|Promise\.|goroutine|go\s+func)\b"),
    "caching": re.compile(r"\b(lru_cache|cache|redis|memcache|memo)\w*\b", re.I),
}

#: Route declarations, keyed by the framework style they belong to.
_ROUTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # FastAPI / Flask: @app.get("/path"), @router.post("/path")
    re.compile(r"@\s*(?P<obj>\w+)\.(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*[\"'](?P<path>[^\"']+)"),
    # Express / Koa: app.get('/path', ...), router.post("/path", ...)
    re.compile(r"\b(?P<obj>app|router|api)\.(?P<method>get|post|put|patch|delete|use)\s*\(\s*[\"'](?P<path>[^\"']+)"),
    # Django: path('route/', view), re_path(...)
    re.compile(r"\b(?P<method>path|re_path|url)\s*\(\s*[\"'](?P<path>[^\"']*)"),
    # Spring: @GetMapping("/path")
    re.compile(r"@(?P<method>Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*[\"'](?P<path>[^\"']+)"),
    # Go chi/gin: r.GET("/path", handler)
    re.compile(r"\b\w+\.(?P<method>GET|POST|PUT|PATCH|DELETE)\s*\(\s*[\"'](?P<path>[^\"']+)"),
)


def _scan_signals(content: str) -> dict[str, list[int]]:
    """Record which lines exhibit each cross-language signal."""
    signals: dict[str, list[int]] = {}

    for line_number, line in enumerate(content.splitlines(), start=1):
        # Skip obvious comment-only lines: a mention inside a comment is not
        # evidence that the code does the thing.
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*", "/*", "<!--")):
            continue

        for name, pattern in _SIGNAL_PATTERNS.items():
            if pattern.search(line):
                lines = signals.setdefault(name, [])
                if len(lines) < MAX_ITEMS_PER_KIND:
                    lines.append(line_number)

    return signals


def _scan_routes(content: str) -> list[CodeSymbol]:
    """Find HTTP route declarations across common frameworks."""
    routes: list[CodeSymbol] = []
    seen: set[tuple[str, int]] = set()

    for line_number, line in enumerate(content.splitlines(), start=1):
        if line.strip().startswith(("#", "//", "*")):
            continue

        for pattern in _ROUTE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue

            route_path = match.group("path")
            method = match.group("method").upper()
            key = (route_path, line_number)
            if key in seen:
                continue
            seen.add(key)

            routes.append(
                CodeSymbol(
                    name=route_path,
                    kind="route",
                    line=line_number,
                    detail=f"{method} {route_path}",
                )
            )
            break  # one route per line is enough

        if len(routes) >= MAX_ITEMS_PER_KIND:
            break

    return routes


# --- Python: exact, via the standard library ----------------------------------


def _extract_python(content: str, structure: FileStructure) -> None:
    """Parse Python with `ast`, which gives exact names and line numbers."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        # A file we cannot parse is reported honestly rather than guessed at.
        structure.parse_error = f"syntax error on line {exc.lineno}"
        return

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                structure.imports.append(
                    CodeSymbol(alias.name, "import", node.lineno)
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or "."
            structure.imports.append(CodeSymbol(module, "import", node.lineno))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(
                base.id for base in node.bases if isinstance(base, ast.Name)
            )
            structure.classes.append(
                CodeSymbol(node.name, "class", node.lineno, f"({bases})" if bases else "")
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async " if isinstance(child, ast.AsyncFunctionDef) else ""
                    structure.methods.append(
                        CodeSymbol(
                            f"{node.name}.{child.name}",
                            "method",
                            child.lineno,
                            f"{prefix}({_python_args(child)})",
                        )
                    )

    # Module-level functions only: methods are already captured above.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            structure.functions.append(
                CodeSymbol(node.name, "function", node.lineno, f"{prefix}({_python_args(node)})")
            )

    _trim(structure)


def _python_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return ", ".join(arg.arg for arg in node.args.args)


# --- everything else: line-oriented patterns ----------------------------------

_DECLARATION_PATTERNS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "javascript": (
        ("import", re.compile(r"^\s*import\s+(?:.+?\s+from\s+)?[\"'](?P<name>[^\"']+)[\"']")),
        ("import", re.compile(r"^\s*(?:const|let|var)\s+.+?=\s*require\s*\(\s*[\"'](?P<name>[^\"']+)")),
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>\w+)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>\w+)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>")),
    ),
    "go": (
        ("import", re.compile(r"^\s*(?:import\s+)?[\"'](?P<name>[\w./-]+)[\"']\s*$")),
        ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)\s*\(")),
        ("class", re.compile(r"^\s*type\s+(?P<name>\w+)\s+struct\b")),
    ),
    "rust": (
        ("import", re.compile(r"^\s*use\s+(?P<name>[\w:]+)")),
        ("function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>\w+)")),
        ("class", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(?P<name>\w+)")),
    ),
    "java": (
        ("import", re.compile(r"^\s*import\s+(?P<name>[\w.]+)\s*;")),
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|abstract\s+|final\s+)*(?:class|interface|enum)\s+(?P<name>\w+)")),
        ("method", re.compile(r"^\s*(?:public|private|protected)\s+[\w<>\[\],\s]+\s+(?P<name>\w+)\s*\(")),
    ),
    "ruby": (
        ("import", re.compile(r"^\s*require(?:_relative)?\s+[\"'](?P<name>[^\"']+)")),
        ("class", re.compile(r"^\s*(?:class|module)\s+(?P<name>\w+)")),
        ("function", re.compile(r"^\s*def\s+(?P<name>[\w?!]+)")),
    ),
    "php": (
        ("import", re.compile(r"^\s*(?:use|require|include)\s+[\"']?(?P<name>[\w\\/.]+)")),
        ("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+(?P<name>\w+)")),
        ("function", re.compile(r"^\s*(?:public|private|protected)?\s*function\s+(?P<name>\w+)")),
    ),
    "csharp": (
        ("import", re.compile(r"^\s*using\s+(?P<name>[\w.]+)\s*;")),
        ("class", re.compile(r"^\s*(?:public|internal|private)?\s*(?:sealed\s+|abstract\s+)?(?:class|interface|record|struct)\s+(?P<name>\w+)")),
    ),
    "sql": (
        ("class", re.compile(r"^\s*CREATE\s+(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w.\"`]+)", re.I)),
        ("function", re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+(?P<name>[\w.]+)", re.I)),
    ),
}

# TypeScript, JSX, Vue and Svelte are close enough to JavaScript for this
# purpose - reuse its patterns rather than duplicating them.
for _alias in ("typescript", "vue", "svelte"):
    _DECLARATION_PATTERNS[_alias] = _DECLARATION_PATTERNS["javascript"]

_KIND_TO_FIELD = {
    "import": "imports",
    "function": "functions",
    "class": "classes",
    "method": "methods",
}


def _extract_with_patterns(
    content: str, structure: FileStructure, language: str
) -> None:
    """Line-oriented extraction for non-Python languages."""
    patterns = _DECLARATION_PATTERNS.get(language)
    if not patterns:
        return

    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(("//", "#", "*", "/*")):
            continue

        for kind, pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue
            target = getattr(structure, _KIND_TO_FIELD[kind])
            if len(target) < MAX_ITEMS_PER_KIND:
                target.append(CodeSymbol(match.group("name"), kind, line_number))
            break

    _trim(structure)


def _trim(structure: FileStructure) -> None:
    """Apply per-kind caps so one huge file cannot dominate the digest."""
    for attribute in ("imports", "functions", "classes", "methods", "routes"):
        items = getattr(structure, attribute)
        if len(items) > MAX_ITEMS_PER_KIND:
            setattr(structure, attribute, items[:MAX_ITEMS_PER_KIND])


def extract_structure(path: str, content: str) -> FileStructure:
    """Extract declarations, routes and behavioural signals from one file.

    Args:
        path: Repository-relative path, used for language detection.
        content: File text.

    Returns:
        A `FileStructure`. Never raises - an unparseable file reports
        `parse_error` and returns whatever the pattern scan could recover.
    """
    language = detect_language(path)
    structure = FileStructure(
        path=path,
        language=language,
        line_count=content.count("\n") + 1 if content else 0,
    )

    if not content or len(content) > MAX_PARSE_CHARS:
        return structure

    if language == "python":
        _extract_python(content, structure)
    else:
        _extract_with_patterns(content, structure, language)

    structure.routes = _scan_routes(content)
    structure.signals = _scan_signals(content)

    return structure


def extract_all(files: dict[str, str]) -> list[FileStructure]:
    """Extract structure for a mapping of path -> content."""
    return [extract_structure(path, content) for path, content in files.items()]


def aggregate_signals(structures: list[FileStructure]) -> dict[str, list[str]]:
    """Roll signals up across files: signal name -> `path:line` references.

    This is what lets the analysis say "authentication code appears in these
    three files" with citations that were never guessed.
    """
    aggregated: dict[str, list[str]] = {}
    for structure in structures:
        for signal, lines in structure.signals.items():
            references = aggregated.setdefault(signal, [])
            for line in lines[:3]:  # a few citations per file is plenty
                if len(references) < 20:
                    references.append(f"{structure.path}:{line}")
    return aggregated
