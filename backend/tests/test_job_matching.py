"""Tests for skill matching, deterministic scoring and claim modality.

Feature 21's fixture is encoded here as the canonical expectation, so the
match result is pinned rather than merely "reasonable".
"""

from __future__ import annotations

import pytest

from app.services.analysis.code_structure import extract_all
from app.services.analysis.dependencies import analyse_dependencies, infer_technologies
from app.services.interview.claims import (
    HYPOTHETICAL,
    PAST,
    EvidenceVocabulary,
    check_answer,
    detect_modality,
)
from app.services.interview.store import CachedAnalysis
from app.services.job import scoring
from app.services.job.matcher import (
    CONTRADICTED,
    NOT_VERIFIED,
    PARTIALLY_VERIFIED,
    VERIFIED,
    match_job,
)
from app.services.job.parser import parse_deterministic

# --- Feature 21's fixture -----------------------------------------------------

JOB = """Python Developer

Required:
Python
FastAPI
PostgreSQL
Docker

Preferred:
AWS
Redis
"""

FILES = {
    "README.md": "# Shop API\n\nA FastAPI storefront backed by PostgreSQL.",
    "requirements.txt": "fastapi==0.121\nsqlalchemy>=2\npyjwt\n",
    "app/main.py": "from fastapi import FastAPI\n\napp = FastAPI()\n",
}


def cached(files: dict[str, str] | None = None, **overrides) -> CachedAnalysis:
    content = files if files is not None else FILES
    manifests = analyse_dependencies(content)
    python_files = {k: v for k, v in content.items() if k.endswith(".py")}
    defaults = dict(
        repository_full_name="demo/shop-api",
        structures=extract_all(python_files),
        manifests=manifests,
        technologies=infer_technologies(manifests),
        analyzed={path: "backend" for path in content},
        evidence_files=dict(content),
        readme_path="README.md" if "README.md" in content else None,
    )
    defaults.update(overrides)
    return CachedAnalysis(**defaults)


def statuses(job_text: str = JOB, **kwargs) -> dict[str, str]:
    match = match_job(parse_deterministic(job_text), cached(**kwargs))
    return {item.skill: item.status for item in match.matches}


# --- the pinned fixture -------------------------------------------------------


def test_feature_21_fixture_matches_exactly() -> None:
    assert statuses() == {
        "Python": VERIFIED,
        "FastAPI": VERIFIED,
        "PostgreSQL": PARTIALLY_VERIFIED,
        "Docker": NOT_VERIFIED,
        "AWS": NOT_VERIFIED,
        "Redis": NOT_VERIFIED,
    }


def test_matching_is_deterministic() -> None:
    runs = [statuses() for _ in range(3)]

    assert runs[0] == runs[1] == runs[2]


def test_score_is_reproducible() -> None:
    scores = []
    for _ in range(3):
        match = match_job(parse_deterministic(JOB), cached())
        scores.append(scoring.compute_match_score(match).score)

    assert len(set(scores)) == 1


# --- evidence strength --------------------------------------------------------


def test_a_declared_dependency_is_strong_evidence() -> None:
    match = match_job(parse_deterministic(JOB), cached())
    fastapi = next(item for item in match.matches if item.skill == "FastAPI")

    assert fastapi.status == VERIFIED
    assert fastapi.strength == "strong"
    assert fastapi.evidence[0]["file"] == "requirements.txt"


def test_a_language_is_evidenced_by_its_source_files() -> None:
    """Regression: Python scored as absent in a repository full of .py files."""
    match = match_job(parse_deterministic(JOB), cached())
    python = next(item for item in match.matches if item.skill == "Python")

    assert python.status == VERIFIED
    assert python.evidence[0]["file"].endswith(".py")


def test_a_readme_mention_alone_is_only_partial() -> None:
    """Prose is weak evidence: the README says PostgreSQL, no code confirms it."""
    match = match_job(parse_deterministic(JOB), cached())
    postgres = next(item for item in match.matches if item.skill == "PostgreSQL")

    assert postgres.status == PARTIALLY_VERIFIED
    assert postgres.strength == "weak"
    assert "README" in postgres.evidence[0]["file"]


def test_nothing_anywhere_is_not_verified_with_the_right_wording() -> None:
    match = match_job(parse_deterministic(JOB), cached())
    docker = next(item for item in match.matches if item.skill == "Docker")

    assert docker.status == NOT_VERIFIED
    assert "Not verified from repository evidence" in docker.reason
    # Never an accusation about the candidate.
    assert "you don't" not in docker.reason.lower()
    assert "lack" not in docker.reason.lower()


# --- partial by parent (Feature 7) --------------------------------------------


def test_parent_evidence_gives_partial_credit_not_full() -> None:
    """AWS SDK present, Lambda absent: partial, never verified."""
    files = {**FILES, "requirements.txt": "boto3==1.34\n"}
    result = statuses("Role\n\nRequired:\nAWS Lambda\n", files=files)

    assert result["AWS"] == VERIFIED
    assert result["AWS Lambda"] == PARTIALLY_VERIFIED


def test_partial_reason_explains_itself() -> None:
    files = {**FILES, "requirements.txt": "boto3==1.34\n"}
    match = match_job(parse_deterministic("Role\n\nRequired:\nAWS Lambda\n"), cached(files=files))
    lambda_match = next(item for item in match.matches if item.skill == "AWS Lambda")

    assert "AWS is evidenced" in lambda_match.reason
    assert "AWS Lambda" in lambda_match.reason


# --- contradiction ------------------------------------------------------------


def test_a_strongly_evidenced_alternative_is_a_contradiction() -> None:
    """A React codebase against a job demanding Angular."""
    files = {
        "package.json": '{"dependencies": {"react": "^19.0.0"}}',
        "README.md": "# App",
    }
    result = statuses("Role\n\nRequired:\nAngular\n", files=files)

    assert result["Angular"] == CONTRADICTED


def test_contradiction_is_not_used_for_a_mere_absence() -> None:
    """No Docker is a gap, not a contradiction - nothing opposes it."""
    assert statuses()["Docker"] == NOT_VERIFIED


# --- scoring (Feature 4) ------------------------------------------------------


def test_required_weighs_more_than_preferred() -> None:
    match = match_job(parse_deterministic(JOB), cached())
    score = scoring.compute_match_score(match)

    # required: Python 1 + FastAPI 1 + Postgres 0.5 + Docker 0 = 2.5/4 = 62.5%
    assert score.required.percent == 62
    assert score.optional.percent == 0
    # 70 * 0.625 + 30 * 0 = 43.75 -> 44
    assert score.score == 44


def test_a_job_with_only_required_skills_scores_on_them_alone() -> None:
    match = match_job(parse_deterministic("Role\n\nRequired:\nPython\nFastAPI\n"), cached())
    score = scoring.compute_match_score(match)

    assert score.optional.groups == 0
    assert score.score == 100


def test_alternatives_are_credited_once_at_their_best() -> None:
    """FastAPI present, Flask absent: the group is satisfied."""
    match = match_job(parse_deterministic("Role\n\nRequired:\n- FastAPI or Flask\n"), cached())
    score = scoring.compute_match_score(match)

    assert score.required.groups == 1
    assert score.score == 100


def test_unscoreable_requirements_are_excluded_not_failed() -> None:
    """Agile cannot be evidenced by code, so it must not drag the score down."""
    with_agile = match_job(
        parse_deterministic("Role\n\nRequired:\nPython\nFastAPI\nAgile methodologies\n"),
        cached(),
    )
    without = match_job(
        parse_deterministic("Role\n\nRequired:\nPython\nFastAPI\n"), cached()
    )

    assert scoring.compute_match_score(with_agile).score == scoring.compute_match_score(without).score
    assert scoring.compute_match_score(with_agile).excluded_requirements >= 1


def test_a_job_with_nothing_scoreable_is_neutral_not_zero() -> None:
    match = match_job(
        parse_deterministic("Role\n\nRequired:\n- Strong communication and teamwork\n"),
        cached(),
    )

    assert scoring.compute_match_score(match).score == 50


def test_the_formula_is_published() -> None:
    score = scoring.compute_match_score(match_job(parse_deterministic(JOB), cached()))

    assert "70" in score.formula and "30" in score.formula


# --- readiness (Feature 11) ---------------------------------------------------


def test_readiness_without_an_interview_uses_only_what_exists() -> None:
    match = match_job(parse_deterministic(JOB), cached())
    score = scoring.compute_match_score(match)

    readiness = scoring.compute_readiness(match, score)

    assert readiness.interview_taken is False
    assert readiness.interview_score is None
    # 40*44 + 25*62 renormalised over 65 = 50.9 -> 51
    assert readiness.score == 51


def test_readiness_with_an_interview_includes_it() -> None:
    match = match_job(parse_deterministic(JOB), cached())
    score = scoring.compute_match_score(match)

    readiness = scoring.compute_readiness(match, score, interview_score=80)

    assert readiness.interview_taken is True
    # (40*44 + 35*80 + 25*62) / 100 = 61.1 -> 61
    assert readiness.score == 61


def test_readiness_reports_strengths_and_gaps() -> None:
    match = match_job(parse_deterministic(JOB), cached())
    readiness = scoring.compute_readiness(match, scoring.compute_match_score(match))

    assert "Python" in readiness.strong_skills
    assert "Docker" in readiness.needs_work


# --- learning plan (Feature 13) -----------------------------------------------


def test_learning_plan_prioritises_required_gaps() -> None:
    match = match_job(parse_deterministic(JOB), cached())
    plan = scoring.build_learning_plan(match)

    assert plan[0].skill == "Docker"
    assert plan[0].priority == 1
    assert "not verified" in plan[0].reason.lower()


def test_learning_plan_only_names_skills_the_job_asked_for() -> None:
    match = match_job(parse_deterministic(JOB), cached())
    plan = scoring.build_learning_plan(match)

    asked = {item.skill for item in match.matches}
    assert all(item.skill in asked for item in plan)


def test_learning_plan_is_empty_when_everything_is_verified() -> None:
    match = match_job(parse_deterministic("Role\n\nRequired:\nPython\nFastAPI\n"), cached())

    assert scoring.build_learning_plan(match) == []


# --- claim modality (Feature 10) ----------------------------------------------


def vocabulary_for() -> EvidenceVocabulary:
    entry = cached()
    return EvidenceVocabulary.build(
        manifests=entry.manifests,
        structures=entry.structures,
        technologies=entry.technologies,
        analyzed_paths=list(entry.analyzed),
    )


@pytest.mark.parametrize(
    "clause",
    ["I used Redis", "We built it with Kafka", "The project uses MongoDB",
     "I implemented caching with Redis"],
)
def test_past_clauses_are_detected(clause: str) -> None:
    assert detect_modality(clause) == PAST


@pytest.mark.parametrize(
    "clause",
    ["I would use Redis", "I'd containerise it with Docker",
     "My plan is to use Kubernetes", "We could introduce Kafka",
     "One option would be Redis", "To add caching I would introduce Redis"],
)
def test_hypothetical_clauses_are_detected(clause: str) -> None:
    assert detect_modality(clause) == HYPOTHETICAL


def test_a_past_claim_about_an_absent_technology_is_flagged() -> None:
    report = check_answer("I used Redis.", vocabulary_for())

    assert [check.technology for check in report.unverified] == ["Redis"]
    assert report.hypothetical == []


def test_a_hypothetical_proposal_is_not_flagged() -> None:
    """Feature 21's second case, and the distinction the spec calls critical."""
    report = check_answer("I would use Redis for caching.", vocabulary_for())

    assert report.unverified == []
    assert [check.technology for check in report.hypothetical] == ["Redis"]


def test_a_gap_answer_naming_many_absent_technologies_is_not_punished() -> None:
    report = check_answer(
        "I would containerise the app with Docker and deploy it to AWS Lambda.",
        vocabulary_for(),
    )

    assert report.unverified == []
    assert {check.technology for check in report.hypothetical} == {"Docker", "AWS"}


def test_one_answer_can_report_and_propose_at_once() -> None:
    report = check_answer(
        "I used Redis, and I would later move to Kafka.", vocabulary_for()
    )

    assert [check.technology for check in report.unverified] == ["Redis"]
    assert [check.technology for check in report.hypothetical] == ["Kafka"]


def test_a_verified_technology_stays_verified_however_it_is_phrased() -> None:
    for answer in ["I used FastAPI.", "I would extend the FastAPI routes."]:
        report = check_answer(answer, vocabulary_for())
        assert [check.technology for check in report.verified] == ["FastAPI"]
        assert report.unverified == []


# --- continuation clauses (regression) ----------------------------------------


def test_a_continuation_clause_inherits_the_proposal_modality() -> None:
    """Regression, found in live testing.

    "I'd containerise it with Docker, and store metadata in PostgreSQL" is one
    proposal. The half after the comma states no modality of its own, so before
    the fix it defaulted to a past claim and PostgreSQL was wrongly flagged -
    punishing the candidate for answering a hypothetical question.
    """
    report = check_answer(
        "I'd expose a few endpoints, containerise it with Docker, and store "
        "request metadata in PostgreSQL.",
        vocabulary_for(),
    )

    assert report.unverified == []
    assert {check.technology for check in report.hypothetical} == {"Docker", "PostgreSQL"}


def test_a_continuation_clause_inherits_the_past_modality_too() -> None:
    """Inheritance runs both ways: a continuation of a report is still a report."""
    report = check_answer("I used Redis, and added MongoDB too.", vocabulary_for())

    assert {check.technology for check in report.unverified} == {"Redis", "MongoDB"}
    assert report.hypothetical == []


def test_an_explicit_marker_beats_inheritance() -> None:
    report = check_answer(
        "I used Redis, and I would later move to Kafka.", vocabulary_for()
    )

    assert [check.technology for check in report.unverified] == ["Redis"]
    assert [check.technology for check in report.hypothetical] == ["Kafka"]


def test_a_new_sentence_does_not_inherit() -> None:
    """Only a conjunction-led continuation inherits; a fresh sentence does not."""
    report = check_answer(
        "I would use Docker. I used Redis for caching.", vocabulary_for()
    )

    assert [check.technology for check in report.unverified] == ["Redis"]
    assert [check.technology for check in report.hypothetical] == ["Docker"]
