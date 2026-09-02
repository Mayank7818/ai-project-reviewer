"""Target job roles, and how they shape question selection.

A role biases *which* evidenced seeds get asked - it never introduces topics the
repository cannot support. Selecting "ML Engineer" for a CRUD app does not
conjure machine-learning questions; it produces an honest notice and falls back
to transferable engineering questions drawn from the same evidence.

Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.interview import seeds as seed_module

SOFTWARE_DEVELOPER = "software_developer"
CPP_DEVELOPER = "cpp_developer"
PYTHON_DEVELOPER = "python_developer"
BACKEND_DEVELOPER = "backend_developer"
FULLSTACK_DEVELOPER = "fullstack_developer"
AI_DEVELOPER = "ai_developer"
AI_ENGINEER = "ai_engineer"
ML_ENGINEER = "ml_engineer"
GENAI_ENGINEER = "genai_engineer"


@dataclass(frozen=True)
class Role:
    """A target role: what it emphasises, and what evidence it expects."""

    key: str
    label: str
    #: Categories this role cares about most, in order.
    priority_categories: tuple[str, ...] = ()
    #: Seed tags that earn a bonus for this role.
    preferred_tags: frozenset[str] = frozenset()
    #: Tags the repository must show for the role to be well supported.
    #: Empty means any repository supports it.
    required_tags: frozenset[str] = frozenset()
    #: Shown when `required_tags` are missing from the repository.
    insufficient_evidence_note: str = ""


_S = seed_module

ROLES: dict[str, Role] = {
    SOFTWARE_DEVELOPER: Role(
        key=SOFTWARE_DEVELOPER,
        label="Software Developer",
        priority_categories=(_S.CODE, _S.PROJECT_UNDERSTANDING, _S.ARCHITECTURE, _S.TESTING),
        preferred_tags=frozenset({"code", "architecture", "testing"}),
    ),
    CPP_DEVELOPER: Role(
        key=CPP_DEVELOPER,
        label="C++ Developer",
        priority_categories=(_S.CODE, _S.PERFORMANCE, _S.PROBLEM_SOLVING, _S.ARCHITECTURE),
        preferred_tags=frozenset({"cpp", "code", "performance"}),
        required_tags=frozenset({"cpp"}),
        insufficient_evidence_note=(
            "Your repository currently provides limited evidence of C++ code."
        ),
    ),
    PYTHON_DEVELOPER: Role(
        key=PYTHON_DEVELOPER,
        label="Python Developer",
        priority_categories=(_S.CODE, _S.TECHNOLOGY, _S.TESTING, _S.ARCHITECTURE),
        preferred_tags=frozenset({"python", "code", "testing"}),
        required_tags=frozenset({"python"}),
        insufficient_evidence_note=(
            "Your repository currently provides limited evidence of Python code."
        ),
    ),
    BACKEND_DEVELOPER: Role(
        key=BACKEND_DEVELOPER,
        label="Backend Developer",
        priority_categories=(_S.API, _S.DATABASE, _S.SECURITY, _S.ARCHITECTURE, _S.PERFORMANCE),
        preferred_tags=frozenset({"api", "backend", "database", "security"}),
        required_tags=frozenset({"backend"}),
        insufficient_evidence_note=(
            "Your repository currently provides limited evidence of backend or API code."
        ),
    ),
    FULLSTACK_DEVELOPER: Role(
        key=FULLSTACK_DEVELOPER,
        label="Full Stack Developer",
        priority_categories=(_S.ARCHITECTURE, _S.API, _S.CODE, _S.DEPLOYMENT),
        preferred_tags=frozenset({"fullstack", "api", "architecture", "javascript"}),
    ),
    AI_DEVELOPER: Role(
        key=AI_DEVELOPER,
        label="AI Developer",
        priority_categories=(_S.CODE, _S.TECHNOLOGY, _S.ARCHITECTURE, _S.PERFORMANCE),
        preferred_tags=frozenset({"ml", "genai", "python", "technology"}),
        required_tags=frozenset({"ml", "genai"}),
        insufficient_evidence_note=(
            "Your repository currently provides limited evidence of AI/ML implementation."
        ),
    ),
    AI_ENGINEER: Role(
        key=AI_ENGINEER,
        label="AI Engineer",
        priority_categories=(_S.ARCHITECTURE, _S.PERFORMANCE, _S.DEPLOYMENT, _S.CODE),
        preferred_tags=frozenset({"ml", "genai", "performance", "deployment"}),
        required_tags=frozenset({"ml", "genai"}),
        insufficient_evidence_note=(
            "Your repository currently provides limited evidence of AI/ML implementation."
        ),
    ),
    ML_ENGINEER: Role(
        key=ML_ENGINEER,
        label="ML Engineer",
        priority_categories=(_S.TECHNOLOGY, _S.PERFORMANCE, _S.DATABASE, _S.DEPLOYMENT),
        preferred_tags=frozenset({"ml", "performance", "database", "deployment"}),
        required_tags=frozenset({"ml"}),
        insufficient_evidence_note=(
            "Your repository currently provides limited evidence of machine-learning work."
        ),
    ),
    GENAI_ENGINEER: Role(
        key=GENAI_ENGINEER,
        label="GenAI Engineer",
        priority_categories=(_S.TECHNOLOGY, _S.ARCHITECTURE, _S.PERFORMANCE, _S.SECURITY),
        preferred_tags=frozenset({"genai", "technology", "architecture"}),
        required_tags=frozenset({"genai"}),
        insufficient_evidence_note=(
            "Your repository currently provides limited evidence of generative-AI work."
        ),
    ),
}

DEFAULT_ROLE = SOFTWARE_DEVELOPER


@dataclass
class RoleFit:
    """Whether a repository actually supports the chosen role."""

    role: Role
    supported: bool
    notice: str = ""
    matched_tags: set[str] = field(default_factory=set)


def get_role(key: str | None) -> Role:
    """Look up a role, falling back to Software Developer."""
    return ROLES.get((key or "").strip().lower(), ROLES[DEFAULT_ROLE])


def assess_fit(role: Role, repository_tags: set[str]) -> RoleFit:
    """Decide honestly whether the repository can support this role.

    `required_tags` is satisfied by ANY match, not all: an ML repository need
    only show one of the ML signals to justify ML questions.
    """
    if not role.required_tags:
        return RoleFit(role=role, supported=True, matched_tags=set(repository_tags))

    matched = set(role.required_tags) & repository_tags
    if matched:
        return RoleFit(role=role, supported=True, matched_tags=matched)

    return RoleFit(
        role=role,
        supported=False,
        notice=(
            f"{role.insufficient_evidence_note} The questions below are "
            "transferable engineering questions drawn from the evidence your "
            "repository does provide."
        ),
    )


def score_seed(seed: seed_module.QuestionSeed, role: Role, fit: RoleFit) -> int:
    """Rank a seed for this role. Higher sorts first.

    Starts from the seed's intrinsic weight (how much it explains the project)
    and adds role-specific bonuses. A role can only re-order evidenced seeds; it
    can never add one.
    """
    score = seed.weight

    if seed.category in role.priority_categories:
        # Earlier in the tuple means the role cares more.
        position = role.priority_categories.index(seed.category)
        score += 40 - (position * 8)

    if seed.tags & role.preferred_tags:
        score += 15

    # When the role is unsupported, its preferences are noise - fall back to
    # whatever best explains the project instead.
    if not fit.supported:
        score = seed.weight + (10 if seed.category in (_S.CODE, _S.ARCHITECTURE) else 0)

    return score


def role_options() -> list[dict[str, str]]:
    """Roles for the UI dropdown, in a sensible presentation order."""
    order = (
        SOFTWARE_DEVELOPER, PYTHON_DEVELOPER, CPP_DEVELOPER, BACKEND_DEVELOPER,
        FULLSTACK_DEVELOPER, AI_DEVELOPER, AI_ENGINEER, ML_ENGINEER, GENAI_ENGINEER,
    )
    return [{"key": key, "label": ROLES[key].label} for key in order]
