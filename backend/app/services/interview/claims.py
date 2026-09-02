"""Check candidate claims against repository evidence.

Feature 9, done deterministically rather than by asking a model to remember what
was in the repository. A candidate who says "I used Redis for caching" is checked
against declared dependencies, extracted imports, technologies and file paths.
If none of them mention Redis, the claim is flagged.

The wording matters and is deliberate. This never says the candidate lied. The
repository extract is partial by construction - Step 2 caps how many files are
retrieved - so an unverified claim may simply be about code the analysis never
saw. The phrase used throughout is:

    "Claim not verified from repository evidence."

Nothing here performs I/O, and nothing here calls a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.analysis.code_structure import FileStructure
from app.services.analysis.dependencies import ManifestReport

UNVERIFIED_NOTE = "Claim not verified from repository evidence."


PAST, HYPOTHETICAL = "past", "hypothetical"


@dataclass(frozen=True)
class ClaimCheck:
    """One technology the candidate named, and whether the repository shows it."""

    technology: str
    verified: bool
    #: Where it was found, when verified: "package.json", "import in app/x.py"…
    found_in: str = ""
    note: str = ""
    #: "past" - the candidate said they used it, which the repository can
    #: confirm or fail to confirm. "hypothetical" - they proposed using it,
    #: which the repository has nothing to say about either way.
    modality: str = PAST


@dataclass
class ClaimReport:
    """The result of checking one answer."""

    verified: list[ClaimCheck] = field(default_factory=list)
    unverified: list[ClaimCheck] = field(default_factory=list)
    #: Technologies the candidate *proposed* rather than claimed to have used.
    #: Never flagged: "I would use Redis for caching" is a design answer, and
    #: treating it as a false claim about the repository would be wrong.
    hypothetical: list[ClaimCheck] = field(default_factory=list)

    @property
    def has_unverified(self) -> bool:
        return bool(self.unverified)

    def as_notes(self) -> list[str]:
        """User-facing lines for the feedback panel."""
        return [
            f"{check.technology}: {UNVERIFIED_NOTE}" for check in self.unverified
        ]


#: Technologies worth checking, mapped to the tokens that identify them in a
#: manifest, an import, or a path. Deliberately a closed vocabulary: a claim is
#: only flagged when we are confident we know what to look for. An unrecognised
#: word is never flagged, because we cannot check it fairly.
CHECKABLE_TECHNOLOGIES: dict[str, tuple[str, ...]] = {
    # data stores
    "Redis": ("redis", "aioredis", "ioredis", "redis-py"),
    "PostgreSQL": ("postgres", "postgresql", "psycopg", "asyncpg", "pg", "pgx"),
    "MySQL": ("mysql", "pymysql", "mysqlclient", "mysql2"),
    "MongoDB": ("mongo", "mongodb", "pymongo", "mongoose", "motor"),
    "SQLite": ("sqlite", "sqlite3", "better-sqlite3"),
    "Elasticsearch": ("elasticsearch", "opensearch"),
    "Kafka": ("kafka", "confluent-kafka", "kafkajs", "aiokafka"),
    "RabbitMQ": ("rabbitmq", "pika", "amqplib", "amqp"),
    "Celery": ("celery",),
    # backend frameworks
    "FastAPI": ("fastapi",),
    "Flask": ("flask",),
    "Django": ("django",),
    "Express": ("express",),
    "NestJS": ("nestjs", "@nestjs/core"),
    "Spring": ("spring", "springframework"),
    "Gin": ("gin-gonic", "gin"),
    # frontend
    "React": ("react",),
    "Vue": ("vue",),
    "Angular": ("angular", "@angular/core"),
    "Svelte": ("svelte",),
    "Next.js": ("next",),
    "Tailwind CSS": ("tailwind", "tailwindcss"),
    # ORMs / data access
    "SQLAlchemy": ("sqlalchemy",),
    "Prisma": ("prisma",),
    "TypeORM": ("typeorm",),
    "Sequelize": ("sequelize",),
    "Alembic": ("alembic",),
    # infra
    "Docker": ("docker", "dockerfile", "docker-compose", "compose.yml"),
    "Kubernetes": ("kubernetes", "k8s", "helm"),
    "Terraform": ("terraform", ".tf"),
    "NGINX": ("nginx",),
    "AWS": ("boto3", "aws-sdk", "aws"),
    "GCP": ("google-cloud", "gcp"),
    "Azure": ("azure",),
    # auth / security
    "JWT": ("jwt", "jsonwebtoken", "pyjwt", "python-jose"),
    "OAuth": ("oauth", "authlib", "passport"),
    "bcrypt": ("bcrypt",),
    # AI / ML
    "PyTorch": ("torch", "pytorch"),
    "TensorFlow": ("tensorflow", "keras"),
    "scikit-learn": ("scikit-learn", "sklearn"),
    "Hugging Face Transformers": ("transformers", "huggingface"),
    "LangChain": ("langchain",),
    "OpenAI API": ("openai",),
    "Ollama": ("ollama",),
    "Pinecone": ("pinecone",),
    "ChromaDB": ("chromadb", "chroma"),
    "FAISS": ("faiss",),
    # testing
    "pytest": ("pytest",),
    "Jest": ("jest",),
    "Vitest": ("vitest",),
    "Cypress": ("cypress",),
    "Playwright": ("playwright",),
    # misc
    "GraphQL": ("graphql", "apollo"),
    "WebSockets": ("websocket", "websockets", "socket.io"),
    "gRPC": ("grpc", "grpcio"),
}


def _mention_pattern(technology: str) -> re.Pattern[str]:
    """Match the technology name as a whole word, case-insensitively.

    Built from the display name only. Matching on the internal tokens too would
    make "pg" match the word "page", which is exactly the kind of false accusation
    this module must avoid.
    """
    escaped = re.escape(technology)
    # Allow "Next.js" / "Next js" / "NextJS"-style spelling drift.
    flexible = escaped.replace(r"\ ", r"[\s-]?").replace(r"\.", r"\.?")

    # Acronyms are routinely pluralised ("JWTs", "APIs"). Ordinary names are
    # not given that latitude: allowing it would make "reacts" match "React".
    if technology.isupper() and len(technology) <= 5:
        flexible += "s?"

    return re.compile(rf"(?<![\w-]){flexible}(?![\w-])", re.IGNORECASE)


@dataclass
class EvidenceVocabulary:
    """Everything the repository demonstrably contains, lowercased for lookup."""

    dependency_names: set[str] = field(default_factory=set)
    import_names: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    technologies: set[str] = field(default_factory=set)

    @classmethod
    def build(
        cls,
        *,
        manifests: list[ManifestReport],
        structures: list[FileStructure],
        technologies: list[str],
        analyzed_paths: list[str],
    ) -> EvidenceVocabulary:
        return cls(
            dependency_names={
                dependency.name.lower()
                for report in manifests
                for dependency in report.dependencies
            },
            import_names={
                symbol.name.lower()
                for structure in structures
                for symbol in structure.imports
            },
            paths={path.lower() for path in analyzed_paths},
            technologies={name.lower() for name in technologies},
        )

    def locate(self, tokens: tuple[str, ...]) -> str:
        """Return where a technology's tokens appear, or "" if nowhere.

        Dependency and import matching allow prefix forms so `@nestjs/core`
        matches `nestjs` and `redis-py` matches `redis`, while path matching is
        substring-based because a filename like `docker-compose.yml` is itself
        the evidence.
        """
        for token in tokens:
            for name in self.dependency_names:
                if name == token or name.startswith(f"{token}-") or name.startswith(f"{token}/") or token in name.split("/"):
                    return f"declared dependency `{name}`"

        for token in tokens:
            for name in self.import_names:
                if name == token or name.startswith(f"{token}.") or name.startswith(f"{token}/"):
                    return f"import `{name}`"

        for token in tokens:
            if token in self.technologies:
                return "detected technology"

        for token in tokens:
            # Only distinctive tokens are matched against paths; a two-letter
            # token would match almost any filename.
            if len(token) >= 4:
                for path in self.paths:
                    if token in path:
                        return f"file `{path}`"

        return ""


#: Markers that turn a sentence into a proposal rather than a report. A job
#: interview asks "how *would* you containerise this?", and the honest answer
#: names technologies the repository does not contain. Flagging those as false
#: claims would punish the candidate for answering the question asked.
_HYPOTHETICAL_MARKERS = re.compile(
    r"\b("
    r"would|could|might|should|"
    r"i'?d\b|we'?d\b|"
    r"plan\s+to|planning\s+to|intend\s+to|going\s+to|"
    r"if\s+i\b|if\s+we\b|"
    r"plan\s+on|plan\s+is|"
    r"plan\b(?=\s+for)|"
    r"plan\s+for|"
    r"my\s+approach\s+would|the\s+approach\s+would|"
    r"one\s+option|another\s+option|an?\s+option\s+would|"
    r"you\s+(?:can|could)|i\s+can\s+see|"
    r"in\s+future|going\s+forward|next\s+step|"
    r"to\s+add\b|to\s+introduce\b|"
    r"i\s+will\b|we\s+will\b|"
    r"consider(?:ing)?\b|"
    r"suggest(?:ing)?\b|propose(?:d|s)?\b|recommend(?:ing)?\b"
    r")\b",
    re.IGNORECASE,
)

#: Markers that pin a sentence to what was actually built. Checked first,
#: because "I used Redis and I would add Kafka later" reports one and proposes
#: the other, and only the report is checkable.
_PAST_MARKERS = re.compile(
    r"\b("
    r"i\s+used|we\s+used|i\s+built|we\s+built|i\s+wrote|we\s+wrote|"
    r"i\s+implemented|we\s+implemented|i\s+added|we\s+added|"
    r"i\s+chose|we\s+chose|i\s+integrated|we\s+integrated|"
    r"it\s+uses|the\s+project\s+uses|this\s+uses|"
    r"is\s+built\s+(?:with|on)|are\s+stored\s+in|"
    r"i\s+have\s+used|we\s+have\s+used"
    r")\b",
    re.IGNORECASE,
)

#: Clause splitter. Modality belongs to the clause a technology sits in, not to
#: the whole answer: "I used Redis, and I would later move to Kafka" reports one
#: technology and proposes another, so the comma-joined clauses must be split.
_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?;])\s+"                       # sentence end
    r"|\n+"                                 # line break
    r"|,\s+(?=(?:and|but|then|while|although|though|however|whereas)\s)"
    ,
    re.IGNORECASE,
)


def _clauses(text: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]


#: A clause opening with a coordinating conjunction continues the previous one
#: and carries its modality. Without this, "I'd containerise it with Docker, and
#: store metadata in PostgreSQL" reports Docker as a proposal but PostgreSQL as a
#: past claim, because the second half states no modality of its own.
_CONTINUATION = re.compile(
    r"^(?:and|but|then|also|plus|while|although|though|however|whereas)\b", re.IGNORECASE
)


def detect_modality(clause: str, previous: str = PAST) -> str:
    """Decide whether a clause reports past work or proposes future work.

    Args:
        clause: The clause to judge.
        previous: The modality of the preceding clause, inherited when this one
            is a continuation that states none of its own.

    Past wins within a clause: "I used Redis and would scale it later" is a
    report with a proposal attached, so Redis stays checkable.
    """
    if _PAST_MARKERS.search(clause):
        return PAST
    if _HYPOTHETICAL_MARKERS.search(clause):
        return HYPOTHETICAL
    if _CONTINUATION.match(clause.strip()):
        return previous
    return PAST


def _clause_modalities(clauses: list[str]) -> list[str]:
    """Resolve every clause's modality, left to right, honouring continuations."""
    modalities: list[str] = []
    previous = PAST
    for clause in clauses:
        previous = detect_modality(clause, previous)
        modalities.append(previous)
    return modalities


def check_answer(answer: str, vocabulary: EvidenceVocabulary) -> ClaimReport:
    """Verify the technologies a candidate named against repository evidence.

    Only *past* claims are checkable. "I used Redis" asserts something about the
    repository, so the repository can fail to corroborate it. "I would use Redis
    for caching" asserts nothing about the repository - it answers a design
    question - and is recorded as hypothetical rather than flagged.

    Args:
        answer: The candidate's free-text answer.
        vocabulary: What the repository demonstrably contains.

    Returns:
        A `ClaimReport`. Technologies not in `CHECKABLE_TECHNOLOGIES` are ignored
        entirely - we only flag what we can check fairly.
    """
    report = ClaimReport()
    if not answer or not answer.strip():
        return report

    clauses = _clauses(answer)
    modalities = _clause_modalities(clauses)

    for technology, tokens in CHECKABLE_TECHNOLOGIES.items():
        pattern = _mention_pattern(technology)
        if not pattern.search(answer):
            continue

        # A technology reported anywhere as past work is treated as a past
        # claim, even if it is also proposed elsewhere in the answer.
        mentioned = [
            modalities[index]
            for index, clause in enumerate(clauses)
            if pattern.search(clause)
        ]
        modality = PAST if any(item == PAST for item in mentioned) else HYPOTHETICAL

        found_in = vocabulary.locate(tokens)

        if modality == HYPOTHETICAL and not found_in:
            report.hypothetical.append(
                ClaimCheck(
                    technology=technology,
                    verified=False,
                    modality=HYPOTHETICAL,
                    note=(
                        f"Proposed rather than claimed. {technology} does not "
                        "appear in the analysed files, which is expected for a "
                        "design answer."
                    ),
                )
            )
        elif found_in:
            report.verified.append(
                ClaimCheck(
                    technology=technology,
                    verified=True,
                    found_in=found_in,
                    modality=modality,
                )
            )
        else:
            report.unverified.append(
                ClaimCheck(
                    technology=technology,
                    verified=False,
                    modality=PAST,
                    note=(
                        f"{UNVERIFIED_NOTE} No dependency, import or file in the "
                        f"analysed selection mentions {technology}. Note the "
                        "analysis only sees a bounded subset of the repository."
                    ),
                )
            )

    return report
