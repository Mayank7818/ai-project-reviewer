"""Turn Step 4 evidence into question seeds.

This module is the reason the interview cannot drift into generic questions.

A *seed* is an askable fact that already carries its own evidence: a route that
exists at a real line, a class that was actually parsed, a confirmed security
finding, a dependency that is genuinely declared. Seeds are enumerated
mechanically from what Step 4 established - the model is never asked to decide
*what* to ask about, only how to phrase a question about a seed it was handed.

The consequence is structural rather than instructional: a question about
`authenticate_user()` can only exist if `authenticate_user` was really parsed out
of a real file, so the model has nothing to invent.

Nothing here performs I/O, and nothing here calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.analysis.code_structure import FileStructure
from app.services.analysis.dependencies import ManifestReport
from app.services.analysis.security_scan import SecurityScanReport

# --- categories ---------------------------------------------------------------

PROJECT_UNDERSTANDING = "project_understanding"
ARCHITECTURE = "architecture"
CODE = "code"
TECHNOLOGY = "technology"
DATABASE = "database"
API = "api"
SECURITY = "security"
PERFORMANCE = "performance"
TESTING = "testing"
DEPLOYMENT = "deployment"
PROBLEM_SOLVING = "problem_solving"
PROJECT_DECISIONS = "project_decisions"

CATEGORIES: tuple[str, ...] = (
    PROJECT_UNDERSTANDING, ARCHITECTURE, CODE, TECHNOLOGY, DATABASE, API,
    SECURITY, PERFORMANCE, TESTING, DEPLOYMENT, PROBLEM_SOLVING, PROJECT_DECISIONS,
)

EASY, MEDIUM, HARD = "easy", "medium", "hard"
DIFFICULTIES: tuple[str, ...] = (EASY, MEDIUM, HARD)


@dataclass
class QuestionSeed:
    """One askable fact, with the evidence that makes it askable.

    `topic` and `angle` are what the model is given to phrase. `evidence` is
    produced here from real extraction output and is never model-authored, so
    it needs no trust check later - only the standard validation pass.
    """

    key: str                       # stable identity, used to match phrasing back
    category: str
    difficulty: str
    topic: str                     # the concrete thing being asked about
    angle: str                     # what the question should probe
    expected_topics: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    #: Signals a role filter can match against (e.g. "python", "api", "ml").
    tags: set[str] = field(default_factory=set)
    #: Higher wins when trimming to the requested question count.
    weight: int = 0
    #: Step 6. One of project_evidence | job_requirement | gap | architecture |
    #: scenario. Step 5 seeds leave this at the default.
    question_type: str = "project_evidence"
    #: Step 6. The job requirement this question comes from, when it comes from
    #: a job description rather than the repository. A seed must be grounded in
    #: one or the other - never in nothing.
    job_requirement: str | None = None

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)

    @property
    def is_grounded(self) -> bool:
        """A seed is askable when it rests on repository evidence OR on a stated
        job requirement.

        Gap and scenario questions legitimately have no repository evidence -
        that absence is the whole point of asking them - but they are still
        grounded, in the job description rather than the code.
        """
        return self.has_evidence or bool(self.job_requirement)


def _evidence(file: str, line: int | None = None, reason: str = "") -> dict[str, Any]:
    return {
        "file": file,
        "line_start": line,
        "line_end": line,
        "reason": reason,
    }


# --- language and framework tags ---------------------------------------------

_LANGUAGE_TAGS: dict[str, str] = {
    "python": "python", "javascript": "javascript", "typescript": "javascript",
    "go": "go", "rust": "rust", "java": "java", "cpp": "cpp", "c": "cpp",
    "csharp": "csharp", "ruby": "ruby", "php": "php", "sql": "sql",
}

#: Dependency names that indicate AI/ML work. Used by the role filter to decide
#: honestly whether a repository supports AI-oriented questions at all.
ML_PACKAGES: frozenset[str] = frozenset(
    {
        "torch", "tensorflow", "keras", "scikit-learn", "sklearn", "xgboost",
        "lightgbm", "transformers", "datasets", "sentence-transformers",
        "numpy", "pandas", "scipy", "opencv-python", "spacy", "nltk",
        "langchain", "llama-index", "openai", "anthropic", "ollama",
        "chromadb", "faiss-cpu", "faiss-gpu", "pinecone-client", "qdrant-client",
        "weaviate-client", "mlflow", "wandb", "onnxruntime", "diffusers",
    }
)

GENAI_PACKAGES: frozenset[str] = frozenset(
    {
        "langchain", "llama-index", "openai", "anthropic", "ollama", "chromadb",
        "faiss-cpu", "faiss-gpu", "pinecone-client", "qdrant-client",
        "weaviate-client", "transformers", "sentence-transformers", "diffusers",
    }
)


def _dependency_names(manifests: list[ManifestReport]) -> set[str]:
    return {
        dependency.name.lower()
        for report in manifests
        for dependency in report.dependencies
    }


# --- seed builders ------------------------------------------------------------


def _project_seeds(repository: dict[str, Any], readme_path: str | None) -> list[QuestionSeed]:
    """Questions about what the project is - always answerable by its author."""
    seeds: list[QuestionSeed] = []
    name = repository.get("full_name") or repository.get("name") or "this project"

    if readme_path:
        seeds.append(
            QuestionSeed(
                key="project:overview",
                category=PROJECT_UNDERSTANDING,
                difficulty=EASY,
                topic=f"the purpose of {name}",
                angle="what the project does and the problem it solves",
                expected_topics=["project purpose", "target users", "core functionality"],
                evidence=[_evidence(readme_path, None, "The README describes the project.")],
                weight=100,
            )
        )
        seeds.append(
            QuestionSeed(
                key="project:decisions",
                category=PROJECT_DECISIONS,
                difficulty=MEDIUM,
                topic=f"the main design decision behind {name}",
                angle="which decision was hardest, and what alternatives were rejected",
                expected_topics=["trade-offs", "alternatives considered", "rationale"],
                evidence=[_evidence(readme_path, None, "The README states the project's goals.")],
                weight=70,
            )
        )

    return seeds


def _architecture_seeds(
    architecture_summary: str,
    architecture_evidence: list[dict[str, Any]],
    domain_counts: dict[str, int],
) -> list[QuestionSeed]:
    """Questions about the structure Step 4 actually demonstrated."""
    seeds: list[QuestionSeed] = []

    if architecture_summary and architecture_evidence:
        seeds.append(
            QuestionSeed(
                key="arch:summary",
                category=ARCHITECTURE,
                difficulty=MEDIUM,
                topic="the overall architecture of the project",
                angle="why the pieces were separated this way rather than combined",
                expected_topics=["separation of concerns", "component boundaries", "data flow"],
                evidence=architecture_evidence[:3],
                tags={"architecture"},
                weight=95,
            )
        )
        seeds.append(
            QuestionSeed(
                key="arch:failure",
                category=PROBLEM_SOLVING,
                difficulty=HARD,
                topic="what happens when one component of this architecture fails",
                angle="failure modes, blast radius and recovery",
                expected_topics=["failure isolation", "graceful degradation", "retries", "monitoring"],
                evidence=architecture_evidence[:2],
                tags={"architecture"},
                weight=80,
            )
        )

    # A split frontend/backend is a concrete, evidenced architectural choice.
    if domain_counts.get("frontend") and domain_counts.get("backend"):
        seeds.append(
            QuestionSeed(
                key="arch:split",
                category=ARCHITECTURE,
                difficulty=HARD,
                topic="the separation between the frontend and the backend",
                angle="how the two communicate and what breaks if that contract changes",
                expected_topics=["API contract", "versioning", "CORS", "deployment coupling"],
                evidence=architecture_evidence[:2],
                tags={"architecture", "fullstack"},
                weight=85,
            )
        )

    return seeds


def _code_seeds(structures: list[FileStructure]) -> list[QuestionSeed]:
    """Questions about functions, classes and methods that genuinely exist.

    This is Feature 3. The name and line come from the parser, so a question can
    only mention `authenticate_user()` if that symbol was really extracted.
    """
    seeds: list[QuestionSeed] = []

    # Prefer symbols in files that also show interesting behaviour, then the
    # shallowest paths - both proxies for "central to the project".
    def file_rank(structure: FileStructure) -> tuple[int, int, str]:
        interest = -len(structure.signals) - len(structure.routes)
        return (interest, structure.path.count("/"), structure.path)

    for structure in sorted(structures, key=file_rank):
        language_tag = _LANGUAGE_TAGS.get(structure.language, structure.language)

        for symbol in (structure.classes + structure.functions + structure.methods)[:4]:
            kind = symbol.kind
            display = f"{symbol.name}()" if kind in ("function", "method") else symbol.name

            seeds.append(
                QuestionSeed(
                    key=f"code:{structure.path}:{symbol.name}:{symbol.line}",
                    category=CODE,
                    difficulty=MEDIUM,
                    topic=f"the {kind} `{display}` in `{structure.path}`",
                    angle=f"what it does, how it works, and why it is written that way",
                    expected_topics=["responsibility", "inputs and outputs", "edge cases", "error handling"],
                    evidence=[
                        _evidence(
                            structure.path,
                            symbol.line,
                            f"{kind} {symbol.name} is defined here.",
                        )
                    ],
                    tags={"code", language_tag},
                    weight=75,
                )
            )

        if len(seeds) >= 25:
            break

    return seeds


def _api_seeds(structures: list[FileStructure]) -> list[QuestionSeed]:
    """Questions about HTTP routes that were actually parsed out of the code."""
    seeds: list[QuestionSeed] = []

    for structure in structures:
        for route in structure.routes[:4]:
            seeds.append(
                QuestionSeed(
                    key=f"api:{structure.path}:{route.name}:{route.line}",
                    category=API,
                    difficulty=MEDIUM,
                    topic=f"the `{route.detail}` endpoint in `{structure.path}`",
                    angle="request handling, validation, status codes and error responses",
                    expected_topics=["input validation", "status codes", "error handling", "idempotency"],
                    evidence=[
                        _evidence(structure.path, route.line, f"Route {route.detail} is declared here.")
                    ],
                    tags={"api", "backend"},
                    weight=85,
                )
            )

    if seeds:
        first = seeds[0]
        seeds.append(
            QuestionSeed(
                key="api:versioning",
                category=API,
                difficulty=HARD,
                topic="the API surface of this project as a whole",
                angle="versioning, backwards compatibility and breaking changes",
                expected_topics=["API versioning", "deprecation", "contract stability"],
                evidence=first.evidence,
                tags={"api", "backend"},
                weight=60,
            )
        )

    return seeds[:8]


def _signal_seeds(structures: list[FileStructure]) -> list[QuestionSeed]:
    """Questions driven by behavioural signals found in the code.

    Each signal was recorded at a real line by the Step 4 extractor, so the
    question is anchored to code that demonstrably does the thing.
    """
    templates: dict[str, tuple[str, str, str, list[str], set[str]]] = {
        "database_query": (
            DATABASE, MEDIUM,
            "how database access is done in this project",
            ["query construction", "parameter binding", "transactions", "connection handling"],
            {"database", "backend"},
        ),
        "orm_model": (
            DATABASE, MEDIUM,
            "the data model defined in this project",
            ["schema design", "relationships", "migrations", "indexing"],
            {"database", "backend"},
        ),
        "authentication": (
            SECURITY, MEDIUM,
            "how authentication works in this project",
            ["credential handling", "token lifetime", "session management"],
            {"security", "backend"},
        ),
        "authorization": (
            SECURITY, HARD,
            "how authorization decisions are made in this project",
            ["permission model", "least privilege", "enforcement points"],
            {"security", "backend"},
        ),
        "external_api_call": (
            PERFORMANCE, HARD,
            "the outbound API calls this project makes",
            ["timeouts", "retries", "rate limits", "failure handling"],
            {"performance", "backend"},
        ),
        "caching": (
            PERFORMANCE, HARD,
            "the caching in this project",
            ["cache invalidation", "TTL", "cache keys", "staleness"],
            {"performance"},
        ),
        "async_operation": (
            PERFORMANCE, HARD,
            "the asynchronous code in this project",
            ["concurrency", "blocking calls", "race conditions", "back-pressure"],
            {"performance", "backend"},
        ),
        "subprocess": (
            SECURITY, HARD,
            "the subprocess or shell execution in this project",
            ["input sanitisation", "command injection", "least privilege"],
            {"security"},
        ),
        "file_io": (
            SECURITY, MEDIUM,
            "the file handling in this project",
            ["path validation", "traversal", "cleanup", "permissions"],
            {"security"},
        ),
        "env_config": (
            DEPLOYMENT, EASY,
            "how configuration is supplied to this project",
            ["environment variables", "secret management", "per-environment config"],
            {"deployment"},
        ),
        "logging": (
            DEPLOYMENT, MEDIUM,
            "the logging in this project",
            ["log levels", "structured logging", "sensitive data in logs"],
            {"deployment"},
        ),
    }

    seeds: list[QuestionSeed] = []
    seen: set[str] = set()

    for structure in structures:
        for signal, lines in structure.signals.items():
            template = templates.get(signal)
            if template is None or signal in seen or not lines:
                continue
            seen.add(signal)

            category, difficulty, topic, expected, tags = template
            seeds.append(
                QuestionSeed(
                    key=f"signal:{signal}",
                    category=category,
                    difficulty=difficulty,
                    topic=topic,
                    angle="how it is implemented here and what could go wrong",
                    expected_topics=expected,
                    evidence=[
                        _evidence(structure.path, lines[0], f"{signal.replace('_', ' ')} appears here.")
                    ],
                    tags=tags,
                    weight=70,
                )
            )

    return seeds


def _security_seeds(security: SecurityScanReport) -> list[QuestionSeed]:
    """Questions about confirmed and potential findings from the Step 4 scan.

    Excerpts are already redacted by the scanner, so no credential value can
    reach a question. Only the finding title and its location are used.
    """
    seeds: list[QuestionSeed] = []

    for hit in security.confirmed[:4]:
        seeds.append(
            QuestionSeed(
                key=f"sec:confirmed:{hit.rule}:{hit.file}:{hit.line}",
                category=SECURITY,
                difficulty=HARD,
                topic=f"the issue found in `{hit.file}` at line {hit.line}: {hit.title}",
                angle="why this is a risk, and how it should be fixed in this codebase",
                expected_topics=["root cause", "concrete fix", "prevention", "testing the fix"],
                # The scanner's excerpt is redacted; still, only the title is used.
                evidence=[_evidence(hit.file, hit.line, hit.title)],
                tags={"security"},
                weight=100,
            )
        )

    for hit in security.potential[:2]:
        seeds.append(
            QuestionSeed(
                key=f"sec:potential:{hit.rule}:{hit.file}:{hit.line}",
                category=SECURITY,
                difficulty=MEDIUM,
                topic=f"the pattern in `{hit.file}` at line {hit.line}: {hit.title}",
                angle="whether it is safe in this context, and what would make it unsafe",
                expected_topics=["context", "threat model", "when this becomes exploitable"],
                evidence=[_evidence(hit.file, hit.line, hit.title)],
                tags={"security"},
                weight=80,
            )
        )

    return seeds


def _defensive_security_seeds(
    technologies: list[str], structures: list[FileStructure]
) -> list[QuestionSeed]:
    """Fallback when the scan found nothing.

    Feature 5: with no findings, ask defensive questions grounded in the
    technologies that are genuinely present. No vulnerability is invented - the
    question is about what the candidate *would* do.
    """
    if not structures:
        return []

    anchor = structures[0]
    named = ", ".join(technologies[:4]) if technologies else "this stack"

    return [
        QuestionSeed(
            key="sec:defensive",
            category=SECURITY,
            difficulty=MEDIUM,
            topic=f"securing a project built with {named}",
            angle="which risks this particular stack introduces and how they are handled here",
            expected_topics=["input validation", "secret management", "dependency risk", "transport security"],
            evidence=[_evidence(anchor.path, None, "Representative source file from this project.")],
            tags={"security"},
            weight=55,
        )
    ]


def _technology_seeds(
    manifests: list[ManifestReport], technologies: list[str]
) -> list[QuestionSeed]:
    """Questions about dependencies the project genuinely declares."""
    seeds: list[QuestionSeed] = []

    for report in manifests[:3]:
        if not report.dependencies:
            continue

        headline = ", ".join(item.name for item in report.dependencies[:5])
        seeds.append(
            QuestionSeed(
                key=f"tech:{report.path}",
                category=TECHNOLOGY,
                difficulty=EASY,
                topic=f"the dependencies declared in `{report.path}` ({headline})",
                angle="why these were chosen and what they are used for here",
                expected_topics=["purpose of each dependency", "alternatives", "selection criteria"],
                evidence=[_evidence(report.path, None, f"{len(report.dependencies)} dependencies declared.")],
                tags={"technology"},
                weight=80,
            )
        )

        seeds.append(
            QuestionSeed(
                key=f"tech:tradeoff:{report.path}",
                category=PROJECT_DECISIONS,
                difficulty=HARD,
                topic=f"the dependency choices in `{report.path}`",
                angle="the cost of these dependencies and when you would remove one",
                expected_topics=["dependency risk", "maintenance burden", "build size", "supply chain"],
                evidence=[_evidence(report.path, None, "Dependency manifest.")],
                tags={"technology"},
                weight=55,
            )
        )

    if technologies:
        seeds.append(
            QuestionSeed(
                key="tech:primary",
                category=TECHNOLOGY,
                difficulty=EASY,
                topic=f"the main technology in this project ({technologies[0]})",
                angle="how it is used here and what it provides",
                expected_topics=[technologies[0], "core concepts", "why it fits this project"],
                evidence=[_evidence(manifests[0].path, None, f"{technologies[0]} is declared here.")]
                if manifests
                else [],
                tags={"technology"},
                weight=85,
            )
        )

    return [seed for seed in seeds if seed.has_evidence]


def _testing_seeds(
    testing_evidence: list[dict[str, Any]], structures: list[FileStructure]
) -> list[QuestionSeed]:
    """Questions about tests, honest about whether any were found."""
    if testing_evidence:
        return [
            QuestionSeed(
                key="testing:existing",
                category=TESTING,
                difficulty=MEDIUM,
                topic="the tests in this project",
                angle="what they cover, what they do not, and how you decide what to test",
                expected_topics=["test strategy", "coverage gaps", "mocking", "CI"],
                evidence=testing_evidence[:2],
                tags={"testing"},
                weight=75,
            )
        ]

    if not structures:
        return []

    # No test files were retrieved. Ask what the candidate *would* test, and say
    # plainly that this is based on their absence - not an accusation.
    return [
        QuestionSeed(
            key="testing:absent",
            category=TESTING,
            difficulty=MEDIUM,
            topic=f"testing `{structures[0].path}`",
            angle="no test files appear in the retrieved selection - how would you test this code",
            expected_topics=["unit vs integration", "what to test first", "test doubles"],
            evidence=[_evidence(structures[0].path, None, "No test files appear in the retrieved selection.")],
            tags={"testing"},
            weight=60,
        )
    ]


def _deployment_seeds(analyzed: dict[str, str]) -> list[QuestionSeed]:
    """Questions about deployment, only when infrastructure files were seen."""
    infra = [path for path, domain in analyzed.items() if domain == "infrastructure"]
    if not infra:
        return []

    return [
        QuestionSeed(
            key="deploy:infra",
            category=DEPLOYMENT,
            difficulty=MEDIUM,
            topic=f"how this project is built and deployed (`{infra[0]}`)",
            angle="the deployment process and what differs between environments",
            expected_topics=["build steps", "environments", "configuration", "rollback"],
            evidence=[_evidence(infra[0], None, "Infrastructure configuration for this project.")],
            tags={"deployment"},
            weight=70,
        ),
        QuestionSeed(
            key="deploy:scale",
            category=PROBLEM_SOLVING,
            difficulty=HARD,
            topic="running this project under significantly more load",
            angle="the first bottleneck, and how you would find it",
            expected_topics=["profiling", "horizontal scaling", "statelessness", "caching"],
            evidence=[_evidence(infra[0], None, "Deployment configuration.")],
            tags={"performance", "deployment"},
            weight=65,
        ),
    ]


def _performance_seeds(findings: list[dict[str, Any]]) -> list[QuestionSeed]:
    """Questions from Step 4 performance findings that carry evidence."""
    seeds: list[QuestionSeed] = []

    for index, finding in enumerate(findings[:3]):
        evidence = finding.get("evidence") or []
        if not evidence:
            continue
        seeds.append(
            QuestionSeed(
                key=f"perf:finding:{index}",
                category=PERFORMANCE,
                difficulty=HARD,
                topic=f"this observation about your code: {finding.get('finding', '')}",
                angle="whether you agree, and how you would measure and address it",
                expected_topics=["measurement", "profiling", "optimisation trade-offs"],
                evidence=evidence[:2],
                tags={"performance"},
                weight=75,
            )
        )

    return seeds


# --- entry point --------------------------------------------------------------


def build_seeds(
    *,
    repository: dict[str, Any],
    analysis: dict[str, Any],
    structures: list[FileStructure],
    manifests: list[ManifestReport],
    security: SecurityScanReport,
    analyzed: dict[str, str],
    domain_counts: dict[str, int],
    technologies: list[str],
    readme_path: str | None,
) -> list[QuestionSeed]:
    """Enumerate every askable, evidenced fact about this repository.

    Args:
        repository: Raw GitHub metadata.
        analysis: The validated Step 4 analysis, as a dict.
        structures: Extracted code structure (real symbols, real lines).
        manifests: Parsed dependency manifests.
        security: Mechanical security scan results.
        analyzed: path -> domain for files the model saw.
        domain_counts: Domain histogram.
        technologies: Technologies evidenced by dependencies.
        readme_path: Path of the README, if one was retrieved.

    Returns:
        Seeds sorted by weight, highest first. Every seed has evidence.
    """
    architecture = analysis.get("architecture") or {}
    performance = analysis.get("performance") or {}
    testing = analysis.get("testing") or {}

    seeds: list[QuestionSeed] = []
    seeds += _project_seeds(repository, readme_path)
    seeds += _architecture_seeds(
        architecture.get("summary", ""),
        architecture.get("evidence") or [],
        domain_counts,
    )
    seeds += _api_seeds(structures)
    seeds += _code_seeds(structures)
    seeds += _signal_seeds(structures)
    seeds += _technology_seeds(manifests, technologies)
    seeds += _testing_seeds(testing.get("evidence") or [], structures)
    seeds += _deployment_seeds(analyzed)
    seeds += _performance_seeds(performance.get("findings") or [])

    security_seeds = _security_seeds(security)
    if not security_seeds:
        security_seeds = _defensive_security_seeds(technologies, structures)
    seeds += security_seeds

    # A seed without evidence cannot become a repository-specific question.
    seeds = [seed for seed in seeds if seed.has_evidence]

    seeds.sort(key=lambda seed: (-seed.weight, seed.key))
    return seeds


def repository_tags(
    manifests: list[ManifestReport], structures: list[FileStructure]
) -> set[str]:
    """Capability tags this repository genuinely evidences.

    Used by the role filter to answer honestly when a candidate selects, say,
    "ML Engineer" for a repository with no ML in it.
    """
    tags: set[str] = set()
    names = _dependency_names(manifests)

    if names & ML_PACKAGES:
        tags.add("ml")
    if names & GENAI_PACKAGES:
        tags.add("genai")

    for structure in structures:
        language_tag = _LANGUAGE_TAGS.get(structure.language)
        if language_tag:
            tags.add(language_tag)
        if structure.routes:
            tags.update({"api", "backend"})
        for signal in structure.signals:
            if signal in ("database_query", "orm_model"):
                tags.add("database")
            if signal in ("authentication", "authorization"):
                tags.add("security")

    return tags
