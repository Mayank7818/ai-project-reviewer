"""Tests for skill normalisation and job description parsing.

Everything here is deterministic - no model, no network.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import InvalidJobDescriptionError
from app.services.job import vocabulary
from app.services.job.parser import (
    NICE_TO_HAVE,
    PREFERRED,
    REQUIRED,
    RESPONSIBILITY,
    parse_deterministic,
    validate,
)

SAMPLE = """Senior Python Developer
Acme Corp

Responsibilities:
- Design and ship REST APIs
- Work with the frontend team

Required:
- 6+ years of Python
- FastAPI
- Postgres
- Docker containerisation

Preferred:
- Amazon Web Services, especially AWS Lambda
- Redis
- CI/CD pipelines

Nice to have:
- Kubernetes
"""


def parsed():
    return parse_deterministic(SAMPLE)


def skills(items) -> set[str]:
    return {item.skill for item in items}


# --- normalisation (Feature 2) ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("Postgres", "PostgreSQL"),
        ("PostgreSQL", "PostgreSQL"),
        ("postgres sql", "PostgreSQL"),
        ("psql", "PostgreSQL"),
        ("React.js", "React"),
        ("ReactJS", "React"),
        ("react", "React"),
        ("Amazon Web Services", "AWS"),
        ("AWS", "AWS"),
        ("k8s", "Kubernetes"),
        ("golang", "Go"),
        ("c sharp", "C#"),
        ("scikit learn", "scikit-learn"),
        ("continuous integration", "CI/CD"),
        ("tensor flow", "TensorFlow"),
    ],
)
def test_aliases_normalise_to_one_canonical_name(raw: str, canonical: str) -> None:
    assert vocabulary.normalise(raw) == canonical


def test_unknown_phrases_normalise_to_nothing() -> None:
    """Never invent a skill from a word we do not recognise."""
    assert vocabulary.normalise("synergy") is None
    assert vocabulary.normalise("") is None
    assert vocabulary.normalise("some-internal-tool") is None


def test_ci_cd_is_a_concept_not_a_language() -> None:
    """The example called out explicitly in Feature 2."""
    assert vocabulary.category_of("CI/CD") == vocabulary.CONCEPT
    assert vocabulary.category_of("Python") == vocabulary.LANGUAGE


@pytest.mark.parametrize(
    ("skill", "category"),
    [
        ("Python", vocabulary.LANGUAGE),
        ("FastAPI", vocabulary.FRAMEWORK),
        ("PostgreSQL", vocabulary.DATABASE),
        ("AWS", vocabulary.CLOUD),
        ("Docker", vocabulary.DEVOPS),
        ("PyTorch", vocabulary.AI_ML),
        ("pytest", vocabulary.TESTING),
        ("Microservices", vocabulary.CONCEPT),
        ("Teamwork", vocabulary.SOFT_SKILL),
    ],
)
def test_categories(skill: str, category: str) -> None:
    assert vocabulary.category_of(skill) == category


def test_specific_variants_declare_their_parent() -> None:
    assert vocabulary.get("AWS Lambda").parent == "AWS"
    assert vocabulary.get("Next.js").parent == "React"


def test_process_skills_are_marked_unevidenceable() -> None:
    """A repository cannot show that someone practises Agile."""
    assert vocabulary.get("Agile").evidence_possible is False
    assert vocabulary.get("Communication").evidence_possible is False
    assert vocabulary.get("Docker").evidence_possible is True


def test_find_skills_adds_the_parent_of_a_variant() -> None:
    found = {skill.name for skill in vocabulary.find_skills("Experience with AWS Lambda")}

    assert "AWS Lambda" in found
    assert "AWS" in found


def test_find_skills_returns_nothing_for_empty_text() -> None:
    assert vocabulary.find_skills("") == []


# --- importance (Feature 2) ---------------------------------------------------


def test_required_section_skills_are_required() -> None:
    assert skills(parsed().by_importance(REQUIRED)) == {
        "Python", "FastAPI", "PostgreSQL", "Docker",
    }


def test_preferred_section_skills_are_preferred() -> None:
    preferred = skills(parsed().by_importance(PREFERRED))

    assert {"AWS", "AWS Lambda", "Redis", "CI/CD"} <= preferred


def test_nice_to_have_is_distinguished_from_preferred() -> None:
    assert "Kubernetes" in skills(parsed().by_importance(NICE_TO_HAVE))


def test_responsibilities_are_distinguished_from_requirements() -> None:
    job = parsed()

    assert "REST APIs" in skills(job.by_importance(RESPONSIBILITY))
    # A responsibility is not a skill bar, so it never counts towards the score.
    assert all(not item.is_scored for item in job.by_importance(RESPONSIBILITY))


def test_inline_marker_overrides_its_section() -> None:
    job = parse_deterministic(
        "Role\n\nRequired:\n- Python\n- Docker (nice to have)\n"
    )

    by_skill = {item.skill: item.importance for item in job.requirements}
    assert by_skill["Python"] == REQUIRED
    assert by_skill["Docker"] == NICE_TO_HAVE


def test_a_skill_named_twice_keeps_its_strongest_importance() -> None:
    job = parse_deterministic(
        "Role\n\nPreferred:\n- Docker\n\nRequired:\n- Docker\n"
    )

    docker = next(item for item in job.requirements if item.skill == "Docker")
    assert docker.importance == REQUIRED


def test_unsectioned_description_treats_everything_as_required() -> None:
    job = parse_deterministic(
        "We are looking for someone who writes Python and has used Docker in production."
    )

    assert skills(job.by_importance(REQUIRED)) == {"Python", "Docker"}


# --- alternatives -------------------------------------------------------------


def test_or_alternatives_share_a_group() -> None:
    """"FastAPI or Flask" asks for either, not both."""
    job = parse_deterministic("Role\n\nRequired:\n- FastAPI or Flask\n")

    groups = {item.skill: item.alternative_group for item in job.requirements}
    assert groups["FastAPI"] == groups["Flask"] is not None


def test_unrelated_skills_on_one_line_are_not_alternatives() -> None:
    """"Python or AWS" is not a choice between equivalents."""
    job = parse_deterministic("Role\n\nRequired:\n- Python or AWS\n")

    assert all(item.alternative_group is None for item in job.requirements)


def test_a_plain_list_is_not_an_alternative_group() -> None:
    job = parse_deterministic("Role\n\nRequired:\n- FastAPI and Flask\n")

    assert all(item.alternative_group is None for item in job.requirements)


# --- metadata -----------------------------------------------------------------


def test_title_and_seniority_are_detected() -> None:
    job = parsed()

    assert job.title == "Senior Python Developer"
    assert job.seniority == "senior"


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("Junior Developer\n\nRequired:\n- Python\n", "junior"),
        ("Staff Engineer\n\nRequired:\n- Go\n", "principal"),
        ("Tech Lead\n\nRequired:\n- Java\n", "lead"),
        ("Developer\n\nRequired:\n- 8 years of Python\n", "senior"),
        ("Developer\n\nRequired:\n- 4 years of Python\n", "mid"),
    ],
)
def test_seniority_variants(text: str, level: str) -> None:
    assert parse_deterministic(text).seniority == level


def test_unstated_seniority_is_empty_not_guessed() -> None:
    assert parse_deterministic("Developer\n\nRequired:\n- Python\n").seniority == ""


def test_responsibilities_are_extracted() -> None:
    assert any("REST APIs" in line for line in parsed().responsibilities)


def test_soft_skills_are_collected_but_never_scored() -> None:
    job = parse_deterministic(
        "Role\n\nRequired:\n- Python\n- Strong communication and teamwork\n"
    )

    assert "Communication" in job.soft_skills
    communication = next(item for item in job.requirements if item.skill == "Communication")
    assert communication.is_scored is False


# --- validation ---------------------------------------------------------------


def test_empty_description_is_rejected() -> None:
    with pytest.raises(InvalidJobDescriptionError):
        validate("")
    with pytest.raises(InvalidJobDescriptionError):
        validate("   \n  ")


def test_too_short_description_is_rejected() -> None:
    with pytest.raises(InvalidJobDescriptionError):
        validate("Python dev")


def test_absurdly_long_description_is_rejected() -> None:
    with pytest.raises(InvalidJobDescriptionError):
        validate("Python " * 10_000)


def test_valid_description_is_returned_trimmed() -> None:
    text = "  Python Developer needed with FastAPI and Docker experience.  "

    assert validate(text) == text.strip()


def test_a_description_with_no_known_skills_still_parses() -> None:
    """No skills is a valid outcome, not an error."""
    job = parse_deterministic(
        "We need a thoughtful person to join our small team and help us grow."
    )

    assert job.requirements == [] or all(not item.is_scored for item in job.requirements)


def test_parsing_is_deterministic() -> None:
    first = parse_deterministic(SAMPLE)
    second = parse_deterministic(SAMPLE)

    assert [(i.skill, i.importance) for i in first.requirements] == [
        (i.skill, i.importance) for i in second.requirements
    ]
