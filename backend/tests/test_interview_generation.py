"""Tests for question selection, generation and claim verification.

The model is mocked throughout - no real LLM call is ever made.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.analysis.code_structure import extract_all
from app.services.analysis.dependencies import analyse_dependencies, infer_technologies
from app.services.analysis.security_scan import scan_files
from app.services.interview import roles as role_module
from app.services.interview import seeds as seed_module
from app.services.interview.claims import (
    UNVERIFIED_NOTE,
    EvidenceVocabulary,
    check_answer,
)
from app.services.interview.generator import (
    QuestionGenerator,
    select_seeds,
    target_distribution,
)
from app.services.interview.store import CachedAnalysis

FILES = {
    "README.md": "# Shop API\n\nA FastAPI storefront.",
    "requirements.txt": "fastapi==0.121\nsqlalchemy>=2\npyjwt\n",
    "app/main.py": (
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        '@app.get("/products/{pid}")\n'
        "async def read_product(pid: int):\n"
        '    return db.execute(f"SELECT * FROM products WHERE id={pid}")\n'
    ),
    "app/auth.py": (
        "import jwt\n\n\n"
        "def authenticate_user(username, password):\n"
        "    return check(username, password)\n"
    ),
    "Dockerfile": "FROM python:3.12\n",
}

ANALYZED = {
    "README.md": "documentation",
    "requirements.txt": "configuration",
    "app/main.py": "backend",
    "app/auth.py": "security",
    "Dockerfile": "infrastructure",
}


def run(coro):
    return asyncio.run(coro)


def cached_analysis() -> CachedAnalysis:
    structures = extract_all({k: v for k, v in FILES.items() if k.endswith(".py")})
    manifests = analyse_dependencies(FILES)
    return CachedAnalysis(
        repository_full_name="demo/shop-api",
        repository={"full_name": "demo/shop-api", "name": "shop-api"},
        analysis={
            "architecture": {
                "summary": "A FastAPI backend.",
                "evidence": [
                    {"file": "app/main.py", "line_start": 3, "line_end": 3, "reason": "App."}
                ],
            },
            "performance": {"findings": []},
            "testing": {"evidence": []},
        },
        structures=structures,
        manifests=manifests,
        security=scan_files(FILES),
        analyzed=ANALYZED,
        domain_counts={"backend": 1, "security": 1},
        technologies=infer_technologies(manifests),
        evidence_files=dict(FILES),
        readme_path="README.md",
    )


def build_seeds():
    cached = cached_analysis()
    return seed_module.build_seeds(
        repository=cached.repository,
        analysis=cached.analysis,
        structures=cached.structures,
        manifests=cached.manifests,
        security=cached.security,
        analyzed=cached.analyzed,
        domain_counts=cached.domain_counts,
        technologies=cached.technologies,
        readme_path=cached.readme_path,
    )


class FakeLLM:
    """A stand-in provider that echoes back a phrasing for every seed."""

    model_name = "fake-model"

    def __init__(self, *, skip: set[str] | None = None, payload: dict | None = None):
        self._skip = skip or set()
        self._payload = payload
        self.calls: list[dict] = []

    async def status(self):  # pragma: no cover - unused here
        raise NotImplementedError

    async def is_available(self) -> bool:  # pragma: no cover - unused here
        return True

    async def generate(self, prompt, *, system=None):  # pragma: no cover
        raise NotImplementedError

    async def generate_json(self, prompt, *, schema, system=None):
        self.calls.append({"prompt": prompt, "schema": schema, "system": system})
        if self._payload is not None:
            return self._payload

        ids = [
            line.split("id: ", 1)[1].strip()
            for line in prompt.splitlines()
            if line.strip().startswith("id: ")
        ]
        return {
            "questions": [
                {
                    "id": seed_id,
                    "question": f"Phrased question for {seed_id}?",
                    "why_this_question": "Probes their understanding.",
                    "expected_topics": ["topic one", "topic two"],
                }
                for seed_id in ids
                if seed_id not in self._skip
            ]
        }


# --- difficulty distribution (Feature 2) --------------------------------------


def test_mixed_distribution_is_30_50_20() -> None:
    assert target_distribution(10, "mixed") == {"easy": 3, "medium": 5, "hard": 2}


def test_distribution_always_sums_to_the_requested_count() -> None:
    for count in range(3, 21):
        assert sum(target_distribution(count, "mixed").values()) == count


def test_specific_difficulty_requests_only_that_level() -> None:
    assert target_distribution(8, "hard") == {"hard": 8}


def test_selection_honours_the_mixed_distribution() -> None:
    role = role_module.get_role("software_developer")
    fit = role_module.assess_fit(role, {"python"})

    chosen = select_seeds(build_seeds(), count=10, difficulty="mixed", role=role, fit=fit)

    counts: dict[str, int] = {}
    for seed in chosen:
        counts[seed.difficulty] = counts.get(seed.difficulty, 0) + 1

    assert len(chosen) == 10
    assert counts == {"easy": 3, "medium": 5, "hard": 2}


def test_selection_never_exceeds_the_requested_count() -> None:
    role = role_module.get_role("software_developer")
    fit = role_module.assess_fit(role, set())

    assert len(select_seeds(build_seeds(), count=5, difficulty="mixed", role=role, fit=fit)) == 5


def test_asking_for_more_than_the_repository_offers_returns_what_exists() -> None:
    """A small repository yields fewer questions rather than invented ones."""
    role = role_module.get_role("software_developer")
    fit = role_module.assess_fit(role, set())
    seeds = build_seeds()

    chosen = select_seeds(seeds, count=100, difficulty="mixed", role=role, fit=fit)

    assert len(chosen) == len(seeds)


def test_no_category_dominates_the_interview() -> None:
    role = role_module.get_role("software_developer")
    fit = role_module.assess_fit(role, set())

    chosen = select_seeds(build_seeds(), count=10, difficulty="mixed", role=role, fit=fit)

    counts: dict[str, int] = {}
    for seed in chosen:
        counts[seed.category] = counts.get(seed.category, 0) + 1

    assert max(counts.values()) <= max(2, int(10 * 0.34)) + 1


def test_role_changes_which_seeds_are_selected() -> None:
    seeds = build_seeds()
    backend = role_module.get_role("backend_developer")
    generic = role_module.get_role("software_developer")

    backend_choice = select_seeds(
        seeds, count=6, difficulty="mixed", role=backend,
        fit=role_module.assess_fit(backend, {"backend", "api"}),
    )
    generic_choice = select_seeds(
        seeds, count=6, difficulty="mixed", role=generic,
        fit=role_module.assess_fit(generic, set()),
    )

    assert {s.key for s in backend_choice} != {s.key for s in generic_choice}


# --- generation ---------------------------------------------------------------


def test_generation_produces_grounded_questions() -> None:
    llm = FakeLLM()
    generator = QuestionGenerator(llm)

    result = run(
        generator.generate(
            cached_analysis(), target_role="backend_developer", difficulty="mixed", count=8
        )
    )

    assert len(result.questions) == 8
    for question in result.questions:
        assert question["evidence"], question["id"]
        assert question["evidence"][0]["file"] in ANALYZED
        assert question["question"]
        assert question["category"] in seed_module.CATEGORIES


def test_generation_makes_exactly_one_model_call() -> None:
    """Feature 16: one call per interview, not one per question."""
    llm = FakeLLM()

    run(QuestionGenerator(llm).generate(
        cached_analysis(), target_role="software_developer", difficulty="mixed", count=10
    ))

    assert len(llm.calls) == 1


def test_the_model_is_never_shown_the_evidence() -> None:
    """Withholding it removes any chance of a citation being altered."""
    llm = FakeLLM()

    run(QuestionGenerator(llm).generate(
        cached_analysis(), target_role="software_developer", difficulty="mixed", count=6
    ))

    prompt = llm.calls[0]["prompt"]
    assert "line_start" not in prompt
    assert "evidence" not in prompt.lower()


def test_a_seed_the_model_skipped_still_becomes_a_question() -> None:
    """A skipped seed falls back to plain phrasing rather than being lost."""
    seeds = build_seeds()
    skip = {seeds[0].key}
    llm = FakeLLM(skip=skip)

    result = run(QuestionGenerator(llm).generate(
        cached_analysis(), target_role="software_developer", difficulty="mixed", count=10
    ))

    texts = {question["id"]: question["question"] for question in result.questions}
    if seeds[0].key in texts:
        assert texts[seeds[0].key].startswith("Walk me through")


def test_phrasing_for_an_unknown_id_is_ignored() -> None:
    """A hallucinated id cannot smuggle an extra question in."""
    llm = FakeLLM(
        payload={
            "questions": [
                {
                    "id": "totally:invented:seed",
                    "question": "A question about a file that does not exist?",
                    "why_this_question": "x",
                    "expected_topics": [],
                }
            ]
        }
    )

    result = run(QuestionGenerator(llm).generate(
        cached_analysis(), target_role="software_developer", difficulty="mixed", count=5
    ))

    assert all(question["id"] != "totally:invented:seed" for question in result.questions)
    # The real seeds still produce questions, via fallback phrasing.
    assert len(result.questions) == 5


def test_unsupported_role_reports_a_notice_and_still_generates() -> None:
    llm = FakeLLM()

    result = run(QuestionGenerator(llm).generate(
        cached_analysis(), target_role="ml_engineer", difficulty="mixed", count=6
    ))

    assert result.role_fit.supported is False
    assert "limited evidence of machine-learning" in result.role_fit.notice
    assert len(result.questions) == 6  # transferable questions, still grounded


def test_empty_repository_generates_nothing() -> None:
    llm = FakeLLM()
    empty = CachedAnalysis(repository_full_name="demo/empty")
    empty.security = scan_files({})

    result = run(QuestionGenerator(llm).generate(
        empty, target_role="software_developer", difficulty="mixed", count=5
    ))

    assert result.questions == []
    assert len(llm.calls) == 0  # no point calling the model with nothing to ask


# --- claim verification (Feature 9) -------------------------------------------


def vocabulary() -> EvidenceVocabulary:
    cached = cached_analysis()
    return EvidenceVocabulary.build(
        manifests=cached.manifests,
        structures=cached.structures,
        technologies=cached.technologies,
        analyzed_paths=list(cached.analyzed),
    )


def test_supported_claims_are_verified() -> None:
    report = check_answer("I built it with FastAPI and SQLAlchemy.", vocabulary())

    assert {check.technology for check in report.verified} == {"FastAPI", "SQLAlchemy"}
    assert report.unverified == []


def test_unsupported_claim_is_flagged_without_accusation() -> None:
    report = check_answer("I used Redis for caching.", vocabulary())

    assert [check.technology for check in report.unverified] == ["Redis"]
    note = report.unverified[0].note
    assert UNVERIFIED_NOTE in note
    assert "bounded subset" in note  # the wording stays fair
    for word in ("lie", "lying", "false", "wrong"):
        assert word not in note.lower()


def test_docker_is_verified_from_a_file_path() -> None:
    report = check_answer("It is containerised with Docker.", vocabulary())

    assert report.verified[0].technology == "Docker"
    assert "dockerfile" in report.verified[0].found_in


def test_unknown_technologies_are_never_flagged() -> None:
    """We only flag what we can check fairly."""
    report = check_answer("I used my own custom in-house caching layer.", vocabulary())

    assert report.unverified == []


def test_substring_false_positives_are_avoided() -> None:
    """'pg' must not match 'page', 'gin' must not match 'imagine'."""
    report = check_answer("I paginate results and imagine future features.", vocabulary())

    assert report.unverified == []
    assert report.verified == []


def test_empty_answer_produces_no_claims() -> None:
    assert check_answer("", vocabulary()).unverified == []
    assert check_answer("   ", vocabulary()).verified == []


@pytest.mark.parametrize(
    "answer", ["I used JWT tokens.", "Authentication uses jwt.", "We issue JWTs."]
)
def test_jwt_is_verified_from_the_pyjwt_dependency(answer: str) -> None:
    report = check_answer(answer, vocabulary())

    assert any(check.technology == "JWT" for check in report.verified)


def test_plurals_do_not_create_false_positives() -> None:
    """Acronyms may be pluralised; ordinary names may not, or 'reacts' matches 'React'."""
    report = check_answer("The service reacts to events and expresses intent.", vocabulary())

    assert not any(check.technology in ("React", "Express") for check in report.unverified)
    assert not any(check.technology in ("React", "Express") for check in report.verified)
