"""Deterministic scoring for job match and job readiness.

Both numbers are arithmetic over facts, not a model's opinion. The same
repository and the same job description always produce the same score, and the
formulas are documented here and in the README so a user can check the maths.

--------------------------------------------------------------------------------
MATCH SCORE (0-100)
--------------------------------------------------------------------------------

    match = 70 x required_coverage + 30 x optional_coverage

    coverage = sum(credit) / count      over the requirement *groups* in that band

    credit:   verified            1.0
              partially_verified  0.5
              not_verified        0.0
              contradicted        0.0

Three details that matter:

* **Groups, not skills.** "FastAPI or Flask" is one group scoring the best of
  its members, so a candidate is never penalised for the option they did not
  take.
* **Unscoreable requirements are excluded**, not failed. Responsibilities and
  skills a repository cannot evidence (Agile, communication) are reported but
  never counted, because no amount of committing would close them.
* **An empty band redistributes.** A job with no preferred skills is scored
  entirely on its required ones, rather than losing 30 points it can never earn.

--------------------------------------------------------------------------------
JOB READINESS (0-100)
--------------------------------------------------------------------------------

    readiness = 40 x match + 35 x interview + 25 x required_coverage

Match and required coverage both appear because they answer different questions:
the match score blends required and preferred, while required coverage alone
says whether the hard bar is cleared. Before any interview is taken the
interview term is dropped and the remaining weights are renormalised, so a match
score alone still yields an honest readiness number.

Nothing here performs I/O, and nothing here calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.job.matcher import JobProjectMatch, SkillMatch
from app.services.job.parser import NICE_TO_HAVE, PREFERRED, REQUIRED

# --- documented weights -------------------------------------------------------

REQUIRED_WEIGHT = 70
OPTIONAL_WEIGHT = 30

READINESS_MATCH_WEIGHT = 40
READINESS_INTERVIEW_WEIGHT = 35
READINESS_REQUIRED_WEIGHT = 25

#: Preferred and nice-to-have share the optional band. A nice-to-have is worth
#: less than a preferred within it, so a job's own emphasis is preserved.
OPTIONAL_BAND: dict[str, float] = {PREFERRED: 1.0, NICE_TO_HAVE: 0.6}

MATCH_FORMULA = (
    f"{REQUIRED_WEIGHT} x required_coverage + {OPTIONAL_WEIGHT} x optional_coverage; "
    "coverage = sum(credit)/count over requirement groups; "
    "credit: verified 1.0, partial 0.5, not verified 0.0"
)

READINESS_FORMULA = (
    f"{READINESS_MATCH_WEIGHT} x match + {READINESS_INTERVIEW_WEIGHT} x interview "
    f"+ {READINESS_REQUIRED_WEIGHT} x required_coverage "
    "(interview term dropped and weights renormalised before an interview is taken)"
)


@dataclass
class CoverageBand:
    """Coverage for one importance band."""

    label: str
    groups: int = 0
    credit: float = 0.0

    @property
    def coverage(self) -> float:
        """0.0-1.0. An empty band is treated as fully covered by convention;
        callers drop empty bands from the weighting instead of scoring them."""
        return (self.credit / self.groups) if self.groups else 1.0

    @property
    def percent(self) -> int:
        return round(self.coverage * 100)


@dataclass
class MatchScore:
    """The match score, with the working shown."""

    score: int
    required: CoverageBand
    optional: CoverageBand
    formula: str = MATCH_FORMULA
    #: Skills counted, so the user can see what the number is made of.
    counted_groups: int = 0
    excluded_requirements: int = 0


def _collapse_groups(matches: list[SkillMatch]) -> list[list[SkillMatch]]:
    """Collapse alternative requirements into single scoring units.

    Requirements sharing an `alternative_group` become one unit; everything else
    is a unit of its own.
    """
    groups: dict[str, list[SkillMatch]] = {}
    singles: list[list[SkillMatch]] = []

    for match in matches:
        if match.alternative_group:
            groups.setdefault(match.alternative_group, []).append(match)
        else:
            singles.append([match])

    return singles + list(groups.values())


def _band_for(match: SkillMatch) -> str | None:
    if match.importance == REQUIRED:
        return REQUIRED
    if match.importance in OPTIONAL_BAND:
        return "optional"
    return None


def compute_match_score(match: JobProjectMatch) -> MatchScore:
    """Score how well this repository evidences this job's requirements."""
    required = CoverageBand(label="required")
    optional = CoverageBand(label="optional")

    for group in _collapse_groups(match.matches):
        # An alternative group is satisfied by its best member.
        best = max(group, key=lambda item: item.credit)
        band = _band_for(best)

        if band == REQUIRED:
            required.groups += 1
            required.credit += best.credit
        elif band == "optional":
            optional.groups += 1
            # A nice-to-have contributes less than a preferred within the band.
            optional.credit += best.credit * OPTIONAL_BAND.get(best.importance, 1.0)

    # An absent band must not cost points that could never be earned, so the
    # remaining band takes the whole weight.
    if required.groups and optional.groups:
        score = REQUIRED_WEIGHT * required.coverage + OPTIONAL_WEIGHT * optional.coverage
    elif required.groups:
        score = 100 * required.coverage
    elif optional.groups:
        score = 100 * optional.coverage
    else:
        # Nothing scoreable was found. Zero would imply a bad match; there is
        # simply nothing to judge, so the neutral midpoint is honest.
        score = 50.0

    return MatchScore(
        score=max(0, min(100, round(score))),
        required=required,
        optional=optional,
        counted_groups=required.groups + optional.groups,
        excluded_requirements=len(match.unscored),
    )


@dataclass
class JobReadiness:
    """The combined readiness number, with its inputs exposed."""

    score: int
    match_score: int
    interview_score: int | None
    required_coverage: int
    formula: str = READINESS_FORMULA
    strong_skills: list[str] = field(default_factory=list)
    needs_work: list[str] = field(default_factory=list)
    interview_taken: bool = False


def compute_readiness(
    match: JobProjectMatch,
    match_score: MatchScore,
    interview_score: int | None = None,
) -> JobReadiness:
    """Combine the job match with interview performance.

    Args:
        match: The skill-by-skill comparison.
        match_score: Its deterministic score.
        interview_score: The interview's overall 0-100, or None if no interview
            has been taken yet.

    Returns:
        A `JobReadiness` whose `score` is reproducible from its own fields.
    """
    required_coverage = match_score.required.percent

    if interview_score is None:
        # Renormalise across the two terms we actually have, rather than
        # treating an untaken interview as a zero.
        total = READINESS_MATCH_WEIGHT + READINESS_REQUIRED_WEIGHT
        score = (
            READINESS_MATCH_WEIGHT * match_score.score
            + READINESS_REQUIRED_WEIGHT * required_coverage
        ) / total
    else:
        score = (
            READINESS_MATCH_WEIGHT * match_score.score
            + READINESS_INTERVIEW_WEIGHT * interview_score
            + READINESS_REQUIRED_WEIGHT * required_coverage
        ) / 100

    strong = [item.skill for item in match.matches if item.status == "verified"]
    needs_work = [
        item.skill
        for item in match.matches
        if item.is_gap and item.importance == REQUIRED
    ] or [item.skill for item in match.matches if item.is_gap]

    return JobReadiness(
        score=max(0, min(100, round(score))),
        match_score=match_score.score,
        interview_score=interview_score,
        required_coverage=required_coverage,
        strong_skills=strong[:8],
        needs_work=needs_work[:8],
        interview_taken=interview_score is not None,
    )


# --- learning plan ------------------------------------------------------------


@dataclass
class LearningItem:
    """One prioritised preparation step, grounded in the match."""

    priority: int
    skill: str
    reason: str
    status: str


def build_learning_plan(match: JobProjectMatch, limit: int = 5) -> list[LearningItem]:
    """Rank what to study, grounded entirely in job and repository evidence.

    Ordering: required gaps first, then required partials, then preferred gaps.
    No technology is ever recommended that the job did not ask for, which is
    what keeps this from becoming generic advice.
    """
    def rank(item: SkillMatch) -> tuple[int, str]:
        if item.importance == REQUIRED and item.is_gap:
            return (0, item.skill)
        if item.importance == REQUIRED and item.status == "partially_verified":
            return (1, item.skill)
        if item.is_gap:
            return (2, item.skill)
        if item.status == "partially_verified":
            return (3, item.skill)
        return (9, item.skill)

    candidates = [item for item in match.matches if rank(item)[0] < 9]
    candidates.sort(key=rank)

    plan: list[LearningItem] = []
    for position, item in enumerate(candidates[:limit], start=1):
        if item.is_gap:
            reason = (
                f"{item.importance.replace('_', ' ').title()} by the job and not "
                "verified from repository evidence."
            )
        else:
            reason = (
                f"{item.importance.replace('_', ' ').title()} by the job and the "
                "repository evidence is only partial."
            )
        plan.append(
            LearningItem(
                priority=position,
                skill=item.skill,
                reason=reason,
                status=item.status,
            )
        )

    return plan
