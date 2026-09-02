"""Job-specific question seeds.

Extends Step 5's seed model rather than replacing it. A job interview is still
an interview about the candidate's repository - it just weights and supplements
the questions with what this particular job asks for.

Five types, per Feature 9:

    project_evidence  reused verbatim from Step 5, grounded in the code
    job_requirement   a skill the job wants AND the repository evidences
    gap               a skill the job wants that the repository does NOT show
    architecture      connects a job requirement to the project's structure
    scenario          how would you change this project to meet the requirement

Gap and scenario questions have no repository evidence by definition - that
absence is the reason for asking. They are still grounded: in the job
requirement rather than in the code, and the UI labels them
"Job requirement / hypothetical" so nobody mistakes them for claims about what
the project contains.

Nothing here performs I/O, and nothing here calls a model.
"""

from __future__ import annotations

from app.services.interview import seeds as base
from app.services.interview.seeds import QuestionSeed
from app.services.job import matcher
from app.services.job.matcher import JobProjectMatch, SkillMatch
from app.services.job.parser import REQUIRED, ParsedJob

# --- question types -----------------------------------------------------------

PROJECT_EVIDENCE = "project_evidence"
JOB_REQUIREMENT = "job_requirement"
GAP = "gap"
ARCHITECTURE = "architecture"
SCENARIO = "scenario"

QUESTION_TYPES: tuple[str, ...] = (
    PROJECT_EVIDENCE, JOB_REQUIREMENT, GAP, ARCHITECTURE, SCENARIO,
)

#: Label the UI shows on any question the repository cannot evidence.
HYPOTHETICAL_LABEL = "Job requirement / hypothetical"

#: Job seeds outrank generic repository seeds - the point of a job interview is
#: that it is about this job.
_REQUIRED_BONUS = 60
_PREFERRED_BONUS = 25


def _importance_bonus(match: SkillMatch) -> int:
    return _REQUIRED_BONUS if match.importance == REQUIRED else _PREFERRED_BONUS


def _category_for(match: SkillMatch) -> str:
    """Map a skill category onto a Step 5 interview category.

    Keeps the two systems speaking the same language, so scoring and the
    category cap in `select_seeds` keep working unchanged.
    """
    return {
        "language": base.CODE,
        "framework": base.TECHNOLOGY,
        "database": base.DATABASE,
        "cloud": base.DEPLOYMENT,
        "devops": base.DEPLOYMENT,
        "ai_ml": base.TECHNOLOGY,
        "testing": base.TESTING,
        "concept": base.ARCHITECTURE,
        "soft_skill": base.PROJECT_UNDERSTANDING,
    }.get(match.category, base.TECHNOLOGY)


# --- B. job requirement questions (skill present in both) ---------------------


def _requirement_seeds(match: JobProjectMatch) -> list[QuestionSeed]:
    """Ask about a skill the job wants and the repository actually shows.

    These are the strongest questions in a job interview: the requirement is
    real and the candidate has something concrete to talk about.
    """
    seeds: list[QuestionSeed] = []

    for item in match.matches:
        if item.status != matcher.VERIFIED or not item.evidence:
            continue

        seeds.append(
            QuestionSeed(
                key=f"job:req:{item.skill}",
                category=_category_for(item),
                difficulty=base.MEDIUM,
                topic=f"your use of {item.skill}, which this job lists as {item.importance.replace('_', ' ')}",
                angle=(
                    f"how {item.skill} is used in this project and what you learned "
                    "from using it here"
                ),
                expected_topics=[item.skill, "practical experience", "trade-offs", "limitations"],
                evidence=item.evidence[:2],
                tags={"job", item.category},
                weight=90 + _importance_bonus(item),
                question_type=JOB_REQUIREMENT,
                job_requirement=item.skill,
            )
        )

    return seeds


# --- C. gap questions (job wants it, repository does not show it) -------------


def _gap_seeds(match: JobProjectMatch) -> list[QuestionSeed]:
    """Ask about a required skill the repository does not evidence.

    Explicitly hypothetical. The question never asserts the project uses the
    technology - it asks what the candidate would do - and the answer is
    evaluated as a design answer, not as a claim about the code.
    """
    seeds: list[QuestionSeed] = []

    for item in match.matches:
        if not item.is_gap:
            continue

        seeds.append(
            QuestionSeed(
                key=f"job:gap:{item.skill}",
                category=_category_for(item),
                difficulty=base.HARD if item.importance == REQUIRED else base.MEDIUM,
                topic=(
                    f"{item.skill}, which this job lists as "
                    f"{item.importance.replace('_', ' ')} but which does not appear "
                    "in the analysed files"
                ),
                angle=(
                    f"how you would apply {item.skill} to this project - this is a "
                    "design question, not a claim that the project already uses it"
                ),
                expected_topics=[item.skill, "practical approach", "trade-offs", "what would change"],
                # No repository evidence by design; grounded in the requirement.
                tags={"job", "gap", item.category},
                weight=85 + _importance_bonus(item),
                question_type=GAP,
                job_requirement=item.skill,
            )
        )

    return seeds


# --- D. partial-evidence questions --------------------------------------------


def _partial_seeds(match: JobProjectMatch) -> list[QuestionSeed]:
    """Probe a skill the repository only partially evidences.

    The most informative case in practice: the README claims PostgreSQL but no
    code shows it, so the candidate is asked to fill in what the analysis could
    not see.
    """
    seeds: list[QuestionSeed] = []

    for item in match.matches:
        if item.status != matcher.PARTIALLY_VERIFIED:
            continue

        seeds.append(
            QuestionSeed(
                key=f"job:partial:{item.skill}",
                category=_category_for(item),
                difficulty=base.MEDIUM,
                topic=(
                    f"{item.skill} in this project - the analysis found only partial "
                    f"evidence for it ({item.reason})"
                ),
                angle=(
                    f"where {item.skill} actually appears in your project and how "
                    "deeply you have used it"
                ),
                expected_topics=[item.skill, "concrete usage", "depth of experience"],
                evidence=item.evidence[:2],
                tags={"job", "partial", item.category},
                weight=80 + _importance_bonus(item),
                question_type=JOB_REQUIREMENT,
                job_requirement=item.skill,
            )
        )

    return seeds


# --- E. architecture and scenario questions -----------------------------------


def _architecture_seeds(
    match: JobProjectMatch, architecture_evidence: list[dict]
) -> list[QuestionSeed]:
    """Connect a job requirement to the project's actual structure."""
    if not architecture_evidence:
        return []

    required_gaps = [
        item for item in match.matches if item.is_gap and item.importance == REQUIRED
    ]
    if not required_gaps:
        return []

    headline = ", ".join(item.skill for item in required_gaps[:3])

    return [
        QuestionSeed(
            key="job:arch:fit",
            category=base.ARCHITECTURE,
            difficulty=base.HARD,
            topic=(
                f"how this project's architecture would have to change to satisfy "
                f"the job's requirements ({headline})"
            ),
            angle="which components change, which stay, and what the risks are",
            expected_topics=["component boundaries", "migration path", "risk", "sequencing"],
            evidence=architecture_evidence[:2],
            tags={"job", "architecture"},
            weight=120,
            question_type=ARCHITECTURE,
            job_requirement=headline,
        )
    ]


def _scenario_seeds(match: JobProjectMatch) -> list[QuestionSeed]:
    """How would you change this project to meet a specific requirement.

    One scenario per interview, on the highest-priority gap - more than that
    turns the interview into a hypothetical exercise rather than a review of
    what the candidate built.
    """
    gaps = [
        item for item in match.matches if item.is_gap and item.importance == REQUIRED
    ]
    if not gaps:
        return []

    target = gaps[0]

    return [
        QuestionSeed(
            key=f"job:scenario:{target.skill}",
            category=base.PROBLEM_SOLVING,
            difficulty=base.HARD,
            topic=(
                f"a scenario: your team must ship this project using {target.skill} "
                "within two weeks"
            ),
            angle=(
                "the concrete steps you would take, what you would do first, and "
                "what you would deliberately leave out"
            ),
            expected_topics=["sequencing", "first milestone", "risk", "what to cut"],
            tags={"job", "scenario"},
            weight=100,
            question_type=SCENARIO,
            job_requirement=target.skill,
        )
    ]


# --- entry point --------------------------------------------------------------


def build_job_seeds(
    job: ParsedJob,
    match: JobProjectMatch,
    repository_seeds: list[QuestionSeed],
    architecture_evidence: list[dict] | None = None,
) -> list[QuestionSeed]:
    """Combine job-driven seeds with the repository seeds from Step 5.

    Args:
        job: The parsed job description.
        match: The skill-by-skill comparison against the repository.
        repository_seeds: Step 5's seeds, reused unchanged so a job interview is
            still anchored in the candidate's real code.
        architecture_evidence: Step 4's architecture citations, if any.

    Returns:
        Seeds sorted by weight, highest first. Job-driven seeds outrank generic
        repository seeds, so a job interview is about the job.
    """
    seeds: list[QuestionSeed] = []
    seeds += _architecture_seeds(match, architecture_evidence or [])
    seeds += _requirement_seeds(match)
    seeds += _gap_seeds(match)
    seeds += _partial_seeds(match)
    seeds += _scenario_seeds(match)

    # Step 5 seeds stay, so the interview still covers what the candidate built
    # even where the job says nothing about it.
    seeds += repository_seeds

    grounded = [seed for seed in seeds if seed.is_grounded]
    grounded.sort(key=lambda seed: (-seed.weight, seed.key))
    return grounded
