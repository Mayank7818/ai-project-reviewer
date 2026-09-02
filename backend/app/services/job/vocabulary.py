"""Canonical skill vocabulary and normalisation.

Job descriptions name the same thing a dozen ways. "Postgres", "PostgreSQL" and
"Postgres SQL" are one skill; "React.js", "ReactJS" and "React" are one skill;
"Amazon Web Services" is AWS. Matching those to repository evidence only works
if both sides are normalised to the same canonical name first.

Two things this module deliberately gets right:

* **CI/CD is a concept, not a language.** Every skill carries a category, so a
  practice, an architectural idea and a programming language are never conflated
  in the output or the scoring.
* **Some skills are unverifiable from a repository.** "Agile", "mentoring" or
  "stakeholder communication" cannot be evidenced by code, so they are marked
  `evidence_possible=False` and excluded from the match score entirely rather
  than counted as gaps the candidate cannot close with a commit.

Detection tokens are reused from Step 5's `claims.CHECKABLE_TECHNOLOGIES`
wherever that module already knows how to spot a technology in a manifest or an
import, so the two layers cannot drift apart.

Nothing here performs I/O, and nothing here calls a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.interview.claims import CHECKABLE_TECHNOLOGIES

# --- categories ---------------------------------------------------------------

LANGUAGE = "language"
FRAMEWORK = "framework"
DATABASE = "database"
CLOUD = "cloud"
DEVOPS = "devops"
AI_ML = "ai_ml"
TESTING = "testing"
CONCEPT = "concept"
SOFT_SKILL = "soft_skill"

CATEGORIES: tuple[str, ...] = (
    LANGUAGE, FRAMEWORK, DATABASE, CLOUD, DEVOPS,
    AI_ML, TESTING, CONCEPT, SOFT_SKILL,
)


@dataclass(frozen=True)
class Skill:
    """One canonical skill, with everything needed to find and judge it."""

    name: str
    category: str
    #: Spellings that appear in job descriptions. Matched case-insensitively as
    #: whole phrases; the canonical name is always matched too.
    aliases: tuple[str, ...] = ()
    #: A broader skill this one is a specific part of. "AWS Lambda" -> "AWS".
    #: Drives partial matching: parent evidence without the specific variant is
    #: partial credit, never full.
    parent: str | None = None
    #: False for skills a repository cannot evidence (process, communication).
    #: Excluded from the match score rather than counted as an unclosable gap.
    evidence_possible: bool = True
    #: Extra detection tokens beyond whatever `claims` already knows.
    extra_tokens: tuple[str, ...] = ()
    #: Skills that are realistic alternatives to this one. Strong evidence of a
    #: peer plus none of this skill is what "contradicted" means.
    exclusive_with: tuple[str, ...] = ()


def _s(name: str, category: str, **kwargs) -> Skill:
    return Skill(name=name, category=category, **kwargs)


# --- the registry -------------------------------------------------------------
# Ordered longest-name-first at match time, so "AWS Lambda" wins over "AWS".

SKILLS: tuple[Skill, ...] = (
    # --- languages ------------------------------------------------------------
    _s("Python", LANGUAGE, aliases=("python3", "py"), extra_tokens=(".py",)),
    _s("JavaScript", LANGUAGE, aliases=("js", "ecmascript", "es6"), extra_tokens=(".js",)),
    _s("TypeScript", LANGUAGE, aliases=("ts",), extra_tokens=(".ts", ".tsx")),
    _s("C++", LANGUAGE, aliases=("cpp", "c plus plus"), extra_tokens=(".cpp", ".hpp")),
    _s("C#", LANGUAGE, aliases=("csharp", "c sharp", ".net"), extra_tokens=(".cs",)),
    _s("Go", LANGUAGE, aliases=("golang",), extra_tokens=(".go", "go.mod")),
    _s("Rust", LANGUAGE, extra_tokens=(".rs", "cargo.toml")),
    _s("Java", LANGUAGE, extra_tokens=(".java", "pom.xml")),
    _s("Kotlin", LANGUAGE, extra_tokens=(".kt",)),
    _s("Ruby", LANGUAGE, extra_tokens=(".rb", "gemfile")),
    _s("PHP", LANGUAGE, extra_tokens=(".php",)),
    _s("Swift", LANGUAGE, extra_tokens=(".swift",)),
    _s("SQL", LANGUAGE, extra_tokens=(".sql",)),
    _s("Bash", LANGUAGE, aliases=("shell scripting", "shell"), extra_tokens=(".sh",)),

    # --- backend frameworks ---------------------------------------------------
    _s("FastAPI", FRAMEWORK, aliases=("fast api",)),
    _s("Flask", FRAMEWORK),
    _s("Django", FRAMEWORK),
    _s("Express", FRAMEWORK, aliases=("express.js", "expressjs")),
    _s("NestJS", FRAMEWORK, aliases=("nest.js", "nest")),
    _s("Spring", FRAMEWORK, aliases=("spring boot", "springboot")),
    _s("Gin", FRAMEWORK),
    _s("Celery", FRAMEWORK),

    # --- frontend -------------------------------------------------------------
    _s("React", FRAMEWORK, aliases=("react.js", "reactjs"),
       exclusive_with=("Vue", "Angular", "Svelte")),
    _s("Vue", FRAMEWORK, aliases=("vue.js", "vuejs"),
       exclusive_with=("React", "Angular", "Svelte")),
    _s("Angular", FRAMEWORK, aliases=("angular.js", "angularjs"),
       exclusive_with=("React", "Vue", "Svelte")),
    _s("Svelte", FRAMEWORK, aliases=("sveltekit",),
       exclusive_with=("React", "Vue", "Angular")),
    _s("Next.js", FRAMEWORK, aliases=("nextjs", "next js"), parent="React"),
    _s("Tailwind CSS", FRAMEWORK, aliases=("tailwind", "tailwindcss")),
    _s("Vite", FRAMEWORK),
    _s("Webpack", FRAMEWORK),

    # --- databases ------------------------------------------------------------
    _s("PostgreSQL", DATABASE, aliases=("postgres", "postgres sql", "psql")),
    _s("MySQL", DATABASE, aliases=("maria db", "mariadb")),
    _s("MongoDB", DATABASE, aliases=("mongo",)),
    _s("SQLite", DATABASE),
    _s("Redis", DATABASE),
    _s("Elasticsearch", DATABASE, aliases=("elastic search", "opensearch")),
    _s("SQLAlchemy", FRAMEWORK, aliases=("sql alchemy",)),
    _s("Prisma", FRAMEWORK),
    _s("Alembic", FRAMEWORK),

    # --- cloud ----------------------------------------------------------------
    _s("AWS", CLOUD, aliases=("amazon web services", "amazon aws")),
    _s("AWS Lambda", CLOUD, aliases=("lambda functions",), parent="AWS"),
    _s("AWS S3", CLOUD, aliases=("s3",), parent="AWS"),
    _s("AWS EC2", CLOUD, aliases=("ec2",), parent="AWS"),
    _s("GCP", CLOUD, aliases=("google cloud", "google cloud platform")),
    _s("Azure", CLOUD, aliases=("microsoft azure",)),

    # --- devops ---------------------------------------------------------------
    _s("Docker", DEVOPS, aliases=("containerisation", "containerization", "containers")),
    _s("Kubernetes", DEVOPS, aliases=("k8s",)),
    _s("Terraform", DEVOPS, aliases=("infrastructure as code", "iac")),
    _s("NGINX", DEVOPS),
    _s("GitHub Actions", DEVOPS, aliases=("github action",)),
    _s("Jenkins", DEVOPS),
    _s("Git", DEVOPS, aliases=("version control",)),

    # --- AI / ML --------------------------------------------------------------
    _s("PyTorch", AI_ML, aliases=("torch",)),
    _s("TensorFlow", AI_ML, aliases=("tensor flow", "keras")),
    _s("scikit-learn", AI_ML, aliases=("sklearn", "scikit learn")),
    _s("Hugging Face Transformers", AI_ML, aliases=("hugging face", "huggingface", "transformers")),
    _s("LangChain", AI_ML, aliases=("lang chain",)),
    _s("OpenAI API", AI_ML, aliases=("openai", "gpt api")),
    _s("Ollama", AI_ML),
    _s("Pinecone", AI_ML),
    _s("ChromaDB", AI_ML, aliases=("chroma",)),
    _s("FAISS", AI_ML),
    _s("NumPy", AI_ML, aliases=("numpy",)),
    _s("pandas", AI_ML),
    _s("RAG", AI_ML, aliases=("retrieval augmented generation", "retrieval-augmented generation")),
    _s("LLM", AI_ML, aliases=("large language model", "large language models")),
    _s("MLOps", CONCEPT, aliases=("ml ops",)),

    # --- testing --------------------------------------------------------------
    _s("pytest", TESTING),
    _s("Jest", TESTING),
    _s("Vitest", TESTING),
    _s("Cypress", TESTING),
    _s("Playwright", TESTING),
    _s("Unit testing", TESTING, aliases=("unit tests",)),
    _s("Integration testing", TESTING, aliases=("integration tests",)),

    # --- protocols / interfaces ----------------------------------------------
    _s("REST APIs", CONCEPT, aliases=("rest", "restful", "rest api", "restful api", "restful apis")),
    _s("GraphQL", CONCEPT, aliases=("graph ql",)),
    _s("gRPC", CONCEPT),
    _s("WebSockets", CONCEPT, aliases=("websocket", "web sockets")),
    _s("JWT", CONCEPT, aliases=("json web token", "json web tokens")),
    _s("OAuth", CONCEPT, aliases=("oauth2", "oauth 2.0")),

    # --- concepts and practices ----------------------------------------------
    # A practice is never a language. Keeping these categorised as concepts is
    # what stops "CI/CD" being reported as a programming skill.
    _s("CI/CD", CONCEPT, aliases=("ci cd", "continuous integration", "continuous delivery",
                                  "continuous deployment", "ci/cd pipelines")),
    _s("Microservices", CONCEPT, aliases=("micro services", "microservice architecture")),
    _s("Distributed systems", CONCEPT),
    _s("System design", CONCEPT, aliases=("systems design",)),
    _s("Scalability", CONCEPT),
    _s("Performance optimisation", CONCEPT, aliases=("performance optimization", "performance tuning")),
    _s("Security best practices", CONCEPT, aliases=("application security", "secure coding")),
    _s("Code review", CONCEPT, aliases=("code reviews",)),
    _s("Agile", CONCEPT, aliases=("scrum", "kanban", "agile methodologies"), evidence_possible=False),
    _s("Monitoring", CONCEPT, aliases=("observability", "logging and monitoring")),
    _s("Message queues", CONCEPT, aliases=("message queue", "kafka", "rabbitmq", "pub/sub")),
    _s("Caching", CONCEPT, aliases=("cache", "caching strategies")),

    # --- soft skills ----------------------------------------------------------
    # None of these can be evidenced by a repository, so none of them affect the
    # match score. Reported for completeness only.
    _s("Communication", SOFT_SKILL, aliases=("communication skills",), evidence_possible=False),
    _s("Teamwork", SOFT_SKILL, aliases=("collaboration", "team player"), evidence_possible=False),
    _s("Problem solving", SOFT_SKILL, aliases=("problem-solving",), evidence_possible=False),
    _s("Mentoring", SOFT_SKILL, aliases=("mentorship", "coaching"), evidence_possible=False),
    _s("Ownership", SOFT_SKILL, aliases=("self-starter", "autonomy"), evidence_possible=False),
    _s("Stakeholder management", SOFT_SKILL, aliases=("stakeholder communication",), evidence_possible=False),
)

BY_NAME: dict[str, Skill] = {skill.name: skill for skill in SKILLS}


#: Canonical language name -> the id `code_structure.detect_language` returns.
#: A language is evidenced by source files existing in it, which Step 4 already
#: established; without this a Python repository would score Python as absent.
LANGUAGE_IDS: dict[str, str] = {
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "Go": "go",
    "Rust": "rust",
    "Java": "java",
    "Kotlin": "kotlin",
    "Ruby": "ruby",
    "PHP": "php",
    "C#": "csharp",
    "C++": "cpp",
    "Swift": "swift",
    "SQL": "sql",
    "Bash": "shell",
}


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Whole-phrase, case-insensitive match tolerant of spacing and punctuation.

    Word boundaries are asserted with lookarounds rather than `\\b` because many
    skill names end in a non-word character - "C++", "C#", "CI/CD" - where `\\b`
    behaves in exactly the wrong way.
    """
    escaped = re.escape(phrase)
    # "node.js" / "node js" / "nodejs"; "ci/cd" / "ci cd"
    flexible = escaped.replace(r"\ ", r"[\s\-_/]?").replace(r"\.", r"\.?")
    return re.compile(rf"(?<![A-Za-z0-9]){flexible}(?![A-Za-z0-9])", re.IGNORECASE)


#: name-or-alias -> canonical skill, longest phrase first so "AWS Lambda" is
#: matched before "AWS" and "Next.js" before "Next".
_MATCHERS: list[tuple[re.Pattern[str], Skill]] = sorted(
    (
        (_phrase_pattern(phrase), skill)
        for skill in SKILLS
        for phrase in (skill.name, *skill.aliases)
    ),
    key=lambda pair: -len(pair[0].pattern),
)


def normalise(text: str) -> str | None:
    """Map a single skill spelling onto its canonical name.

    Args:
        text: One skill as written, e.g. "postgres" or "React.js".

    Returns:
        The canonical name, or None if the phrase is not a known skill.
    """
    candidate = (text or "").strip()
    if not candidate:
        return None

    for pattern, skill in _MATCHERS:
        if pattern.fullmatch(candidate):
            return skill.name

    # Fall back to a contained match for phrases like "strong Python experience".
    for pattern, skill in _MATCHERS:
        if pattern.search(candidate):
            return skill.name

    return None


def find_skills(text: str) -> list[Skill]:
    """Find every canonical skill mentioned anywhere in a block of text.

    Longest-first matching means a mention of "AWS Lambda" yields the specific
    skill; the parent "AWS" is added too, because a job asking for Lambda is
    implicitly asking for AWS.
    """
    if not text:
        return []

    found: dict[str, Skill] = {}
    for pattern, skill in _MATCHERS:
        if skill.name in found:
            continue
        if pattern.search(text):
            found[skill.name] = skill

    # A specific variant implies its parent.
    for skill in list(found.values()):
        if skill.parent and skill.parent not in found and skill.parent in BY_NAME:
            found[skill.parent] = BY_NAME[skill.parent]

    return sorted(found.values(), key=lambda item: item.name)


def detection_tokens(skill: Skill) -> tuple[str, ...]:
    """Tokens that identify this skill in a manifest, an import or a path.

    Reuses Step 5's detection vocabulary wherever it already knows the
    technology, so the job layer and the claim checker cannot disagree about
    what counts as evidence of, say, Redis.
    """
    shared = CHECKABLE_TECHNOLOGIES.get(skill.name, ())
    own = tuple(alias.lower() for alias in (skill.name, *skill.aliases))
    return tuple(dict.fromkeys(shared + own + skill.extra_tokens))


def get(name: str) -> Skill | None:
    return BY_NAME.get(name)


def category_of(name: str) -> str:
    skill = BY_NAME.get(name)
    return skill.category if skill else CONCEPT
