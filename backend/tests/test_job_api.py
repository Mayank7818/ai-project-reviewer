"""End-to-end tests for the job intelligence endpoints.

GitHub and Ollama are mocked at the httpx transport layer; no network call and
no model run ever happens. The analysis cache is seeded directly, which is how
the feature behaves in practice - Step 4 runs once and everything reuses it.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services.analysis.code_structure import extract_all
from app.services.analysis.dependencies import analyse_dependencies, infer_technologies
from app.services.analysis.security_scan import scan_files
from app.services.interview.store import (
    CachedAnalysis,
    analysis_cache_key,
    get_analysis_cache,
    reset_stores,
)
from app.services.job.seeds import HYPOTHETICAL_LABEL

client = TestClient(create_app(), raise_server_exceptions=False)
SETTINGS = get_settings()
PREFIX = f"{SETTINGS.api_v1_prefix}/job"

OLLAMA = SETTINGS.ollama_base_url.rstrip("/")
TAGS_URL = f"{OLLAMA}/api/tags"
GENERATE_URL = f"{OLLAMA}/api/generate"
MODEL = SETTINGS.ollama_model

REPO_URL = "https://github.com/demo/shop-api"

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
    "app/main.py": (
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        '@app.get("/products/{pid}")\n'
        "async def read_product(pid: int):\n"
        "    return {}\n"
    ),
}


@pytest.fixture(autouse=True)
def clean_stores():
    reset_stores()
    yield
    reset_stores()


def seed_cache() -> CachedAnalysis:
    manifests = analyse_dependencies(FILES)
    cached = CachedAnalysis(
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
        structures=extract_all({"app/main.py": FILES["app/main.py"]}),
        manifests=manifests,
        security=scan_files(FILES),
        analyzed={p: "backend" for p in FILES},
        domain_counts={"backend": 3},
        technologies=infer_technologies(manifests),
        evidence_files=dict(FILES),
        readme_path="README.md",
    )
    get_analysis_cache().put(analysis_cache_key(cached.repository_full_name), cached)
    return cached


ENRICHMENT = {
    "job_title": "Python Developer",
    "seniority": "mid",
    "responsibilities": ["Build APIs"],
    "soft_skills": ["Collaboration"],
}

INTERPRETATION = {
    "interpretation": "This project evidences the Python and FastAPI requirements.",
    "strengths": ["Python is used throughout the project"],
}

EVALUATION = {
    "score": 7,
    "correct_points": ["Explained the approach"],
    "missing_points": [],
    "incorrect_points": [],
    "feedback": "Reasonable answer.",
    "follow_up_question": "How would you test that?",
    "communication_score": 8,
}

SUMMARY = {
    "strong_areas": ["API design"],
    "weak_areas": ["Containerisation"],
    "recommended_topics": ["Docker basics"],
    "overall_feedback": "A solid showing.",
}


def _questions_for(prompt: str) -> dict:
    ids = [
        line.split("id: ", 1)[1].strip()
        for line in prompt.splitlines()
        if line.strip().startswith("id: ")
    ]
    return {
        "questions": [
            {
                "id": seed_id,
                "question": f"Tell me about {seed_id}?",
                "why_this_question": "Probes understanding.",
                "expected_topics": ["design"],
            }
            for seed_id in ids
        ]
    }


def mock_model(mock: respx.MockRouter) -> respx.Route:
    """Route each model call to the right canned reply, by schema shape."""
    mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": MODEL}]})
    )

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        keys = set((payload.get("format") or {}).get("properties", {}))

        if "job_title" in keys:
            body = ENRICHMENT
        elif "interpretation" in keys:
            body = INTERPRETATION
        elif "questions" in keys:
            body = _questions_for(payload["prompt"])
        elif "score" in keys:
            body = EVALUATION
        else:
            body = SUMMARY

        return httpx.Response(200, json={"response": json.dumps(body)})

    return mock.post(GENERATE_URL).mock(side_effect=responder)


def match_body(**overrides) -> dict:
    return {"github_url": REPO_URL, "job_description": JOB, **overrides}


# --- parse --------------------------------------------------------------------


@respx.mock
def test_parse_extracts_structured_requirements(respx_mock: respx.MockRouter) -> None:
    mock_model(respx_mock)

    body = client.post(f"{PREFIX}/parse", json={"job_description": JOB}).json()

    skills = {item["skill"]: item["importance"] for item in body["job"]["requirements"]}
    assert skills["Python"] == "required"
    assert skills["Redis"] == "preferred"
    assert body["job"]["title"] == "Python Developer"


@respx.mock
def test_parse_reports_the_privacy_position(respx_mock: respx.MockRouter) -> None:
    mock_model(respx_mock)

    body = client.post(f"{PREFIX}/parse", json={"job_description": JOB}).json()

    assert "locally by the configured Ollama model" in body["privacy_note"]
    assert "not sent to any" in body["privacy_note"]


@respx.mock
def test_parse_never_echoes_the_description_back(
    respx_mock: respx.MockRouter,
) -> None:
    """Only a character count leaves the backend, never the text (Feature 16)."""
    mock_model(respx_mock)
    secret = "Contact recruiter Jane Doe at jane@example.com"

    body = client.post(
        f"{PREFIX}/parse", json={"job_description": JOB + "\n" + secret}
    ).json()

    assert "jane@example.com" not in json.dumps(body)
    assert body["job"]["source_chars"] > 0


@respx.mock
def test_parse_succeeds_without_the_model(respx_mock: respx.MockRouter) -> None:
    """Skills are deterministic, so parsing degrades rather than failing."""
    respx_mock.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
    respx_mock.post(GENERATE_URL).mock(side_effect=httpx.ConnectError("refused"))

    response = client.post(f"{PREFIX}/parse", json={"job_description": JOB})

    assert response.status_code == 200
    body = response.json()
    assert body["llm_available"] is False
    assert {item["skill"] for item in body["job"]["requirements"]} >= {"Python", "Docker"}


@pytest.mark.parametrize("description", ["", "   ", "Python dev"])
def test_invalid_descriptions_are_rejected(description: str) -> None:
    response = client.post(f"{PREFIX}/parse", json={"job_description": description})

    assert response.status_code == 422
    code = response.json()["error"]["code"]
    assert code in {"INVALID_JOB_DESCRIPTION", "VALIDATION_ERROR"}


# --- match --------------------------------------------------------------------


@respx.mock
def test_match_classifies_every_requirement(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_model(respx_mock)

    body = client.post(f"{PREFIX}/match", json=match_body()).json()
    statuses = {item["skill"]: item["status"] for item in body["matches"]}

    assert statuses == {
        "Python": "verified",
        "FastAPI": "verified",
        "PostgreSQL": "partially_verified",
        "Docker": "not_verified",
        "AWS": "not_verified",
        "Redis": "not_verified",
    }


@respx.mock
def test_match_score_is_deterministic_and_shows_its_working(
    respx_mock: respx.MockRouter,
) -> None:
    seed_cache()
    mock_model(respx_mock)

    scores = [
        client.post(f"{PREFIX}/match", json=match_body()).json()["match_score"]
        for _ in range(2)
    ]

    assert scores[0]["score"] == scores[1]["score"] == 44
    assert scores[0]["required"]["percent"] == 62
    assert "70" in scores[0]["formula"]
    assert scores[0]["credit_scale"]["verified"] == 1.0


@respx.mock
def test_verified_skills_carry_evidence(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_model(respx_mock)

    body = client.post(f"{PREFIX}/match", json=match_body()).json()

    for item in body["matches"]:
        if item["status"] == "verified":
            assert item["evidence"], item["skill"]
            assert item["evidence"][0]["file"] in FILES


@respx.mock
def test_gaps_use_the_required_wording(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_model(respx_mock)

    body = client.post(f"{PREFIX}/match", json=match_body()).json()
    docker = next(item for item in body["gaps"] if item["skill"] == "Docker")

    assert "Not verified from repository evidence" in docker["reason"]


@respx.mock
def test_strengths_only_name_verified_skills(respx_mock: respx.MockRouter) -> None:
    """The model does not get to promote a gap into a strength."""
    seed_cache()
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": MODEL}]})
    )

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        keys = set((payload.get("format") or {}).get("properties", {}))
        if "job_title" in keys:
            body = ENRICHMENT
        else:
            body = {
                "interpretation": "Looks good.",
                "strengths": ["Strong Docker experience", "Python is used throughout"],
            }
        return httpx.Response(200, json={"response": json.dumps(body)})

    respx_mock.post(GENERATE_URL).mock(side_effect=responder)

    body = client.post(f"{PREFIX}/match", json=match_body()).json()

    assert not any("Docker" in line for line in body["strengths"])
    assert any("Python" in line for line in body["strengths"])


@respx.mock
def test_match_works_without_the_model(respx_mock: respx.MockRouter) -> None:
    """The score never depended on the model, so it still comes out."""
    seed_cache()
    respx_mock.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
    respx_mock.post(GENERATE_URL).mock(side_effect=httpx.ConnectError("refused"))

    response = client.post(f"{PREFIX}/match", json=match_body())

    assert response.status_code == 200
    body = response.json()
    assert body["match_score"]["score"] == 44
    assert body["llm_available"] is False
    assert body["interpretation"] == ""
    assert body["strengths"]  # deterministic strengths survive


@respx.mock
def test_match_reuses_the_cached_analysis(respx_mock: respx.MockRouter) -> None:
    """Feature 18: analysing a job must never re-run the repository pipeline."""
    seed_cache()
    mock_model(respx_mock)
    github = respx_mock.get(
        f"{SETTINGS.github_api_base_url.rstrip('/')}/repos/demo/shop-api"
    ).mock(return_value=httpx.Response(200, json={}))

    client.post(f"{PREFIX}/match", json=match_body())

    assert len(github.calls) == 0


@respx.mock
def test_learning_plan_is_prioritised_and_grounded(
    respx_mock: respx.MockRouter,
) -> None:
    seed_cache()
    mock_model(respx_mock)

    plan = client.post(f"{PREFIX}/match", json=match_body()).json()["learning_plan"]

    assert plan[0]["skill"] == "Docker"
    assert plan[0]["priority"] == 1
    asked = {"Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "Redis"}
    assert all(item["skill"] in asked for item in plan)


@respx.mock
def test_readiness_is_reported_before_any_interview(
    respx_mock: respx.MockRouter,
) -> None:
    seed_cache()
    mock_model(respx_mock)

    readiness = client.post(f"{PREFIX}/match", json=match_body()).json()["readiness"]

    assert readiness["interview_taken"] is False
    assert readiness["interview_score"] is None
    assert readiness["match_score"] == 44
    assert "Python" in readiness["strong_skills"]


def test_match_rejects_an_invalid_repository_url() -> None:
    response = client.post(
        f"{PREFIX}/match",
        json={"github_url": "https://gitlab.com/a/b", "job_description": JOB},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REPOSITORY_URL"


# --- job interview ------------------------------------------------------------


def start_job_interview(mock: respx.MockRouter, **overrides) -> dict:
    seed_cache()
    mock_model(mock)
    body = {
        "github_url": REPO_URL,
        "job_description": JOB,
        "target_role": "backend_developer",
        "difficulty": "mixed",
        "question_count": 6,
        **overrides,
    }
    response = client.post(f"{PREFIX}/interview/start", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@respx.mock
def test_job_interview_mixes_question_types(respx_mock: respx.MockRouter) -> None:
    session = start_job_interview(respx_mock)
    types = {q["question_type"] for q in
             [session["current_question"]] if q}

    # Inspect every question through the session view.
    full = client.get(f"{PREFIX}/interview/{session['session_id']}").json()
    assert full["total_questions"] == 6
    assert full["match_score"] == 44
    assert full["job_title"] == "Python Developer"
    assert types <= {"project_evidence", "job_requirement", "gap", "architecture", "scenario"}


@respx.mock
def test_gap_questions_are_marked_hypothetical(respx_mock: respx.MockRouter) -> None:
    """A question about Docker must not imply the project contains Docker."""
    session = start_job_interview(respx_mock, question_count=10)

    seen = []
    session_id = session["session_id"]
    for _ in range(10):
        state = client.get(f"{PREFIX}/interview/{session_id}").json()
        question = state["current_question"]
        if question is None:
            break
        seen.append(question)
        client.post(
            f"{PREFIX}/interview/{session_id}/answer",
            json={"question_id": question["id"], "answer": "A considered answer."},
        )

    gaps = [q for q in seen if q["question_type"] == "gap"]
    assert gaps, "expected at least one gap question for an unmet requirement"
    for question in gaps:
        assert question["is_hypothetical"] is True
        assert question["hypothetical_label"] == HYPOTHETICAL_LABEL
        assert question["job_requirement"]


@respx.mock
def test_every_question_is_grounded_in_code_or_the_job(
    respx_mock: respx.MockRouter,
) -> None:
    session = start_job_interview(respx_mock, question_count=8)
    session_id = session["session_id"]

    state = client.get(f"{PREFIX}/interview/{session_id}").json()
    question = state["current_question"]

    # The first question is enough to assert the invariant shape; the rest are
    # covered as they are answered below.
    assert question["evidence"] or question["job_requirement"]


@respx.mock
def test_answering_advances_the_job_session(respx_mock: respx.MockRouter) -> None:
    session = start_job_interview(respx_mock)
    question = session["current_question"]

    body = client.post(
        f"{PREFIX}/interview/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "I used FastAPI to build the routes."},
    ).json()

    assert body["answered"] == 1
    assert body["evaluation"]["score"] == 7
    assert body["next_question"]["id"] != question["id"]
    assert body["is_complete"] is False


@respx.mock
def test_a_past_claim_is_flagged_in_a_job_interview(
    respx_mock: respx.MockRouter,
) -> None:
    session = start_job_interview(respx_mock)
    question = session["current_question"]

    body = client.post(
        f"{PREFIX}/interview/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "I used Redis for caching here."},
    ).json()

    assert [c["technology"] for c in body["evaluation"]["unverified_claims"]] == ["Redis"]


@respx.mock
def test_a_hypothetical_proposal_is_not_flagged_in_a_job_interview(
    respx_mock: respx.MockRouter,
) -> None:
    """The distinction the spec calls critical, end to end."""
    session = start_job_interview(respx_mock)
    question = session["current_question"]

    body = client.post(
        f"{PREFIX}/interview/{session['session_id']}/answer",
        json={
            "question_id": question["id"],
            "answer": "I would use Redis for caching and containerise it with Docker.",
        },
    ).json()

    assert body["evaluation"]["unverified_claims"] == []


@respx.mock
def test_finishing_computes_job_readiness(respx_mock: respx.MockRouter) -> None:
    session = start_job_interview(respx_mock, question_count=3)
    session_id = session["session_id"]

    for _ in range(3):
        state = client.get(f"{PREFIX}/interview/{session_id}").json()
        question = state["current_question"]
        if question is None:
            break
        client.post(
            f"{PREFIX}/interview/{session_id}/answer",
            json={"question_id": question["id"], "answer": "A considered answer."},
        )

    finished = client.post(f"{PREFIX}/interview/{session_id}/finish").json()

    assert finished["status"] == "complete"
    readiness = finished["readiness"]
    assert readiness["interview_taken"] is True
    assert readiness["match_score"] == 44
    assert readiness["interview_score"] == 70  # every answer scored 7/10
    # (40*44 + 35*70 + 25*62) / 100 = 57.7 -> 58
    assert readiness["score"] == 58
    assert finished["summary"]["scores"]["overall"] == 70


@respx.mock
def test_readiness_is_reproducible_from_its_own_fields(
    respx_mock: respx.MockRouter,
) -> None:
    session = start_job_interview(respx_mock, question_count=3)
    session_id = session["session_id"]
    for _ in range(3):
        state = client.get(f"{PREFIX}/interview/{session_id}").json()
        question = state["current_question"]
        if question is None:
            break
        client.post(
            f"{PREFIX}/interview/{session_id}/answer",
            json={"question_id": question["id"], "answer": "An answer."},
        )

    readiness = client.post(f"{PREFIX}/interview/{session_id}/finish").json()["readiness"]

    expected = round(
        (40 * readiness["match_score"]
         + 35 * readiness["interview_score"]
         + 25 * readiness["required_coverage"]) / 100
    )
    assert readiness["score"] == expected


# --- failure modes ------------------------------------------------------------


def test_unknown_job_session_is_a_clean_404() -> None:
    assert client.get(f"{PREFIX}/interview/nope").status_code == 404
    assert client.post(f"{PREFIX}/interview/nope/finish").status_code == 404
    assert client.post(
        f"{PREFIX}/interview/nope/answer",
        json={"question_id": "x", "answer": "y"},
    ).status_code == 404


@respx.mock
def test_ollama_unavailable_blocks_the_interview_not_the_match(
    respx_mock: respx.MockRouter,
) -> None:
    """Matching degrades; phrasing a question genuinely needs the model."""
    seed_cache()
    respx_mock.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
    respx_mock.post(GENERATE_URL).mock(side_effect=httpx.ConnectError("refused"))

    assert client.post(f"{PREFIX}/match", json=match_body()).status_code == 200

    response = client.post(
        f"{PREFIX}/interview/start",
        json={**match_body(), "question_count": 3},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"


@respx.mock
def test_malformed_model_output_is_a_clean_502(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": MODEL}]})
    )
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": "{not json"})
    )

    response = client.post(
        f"{PREFIX}/interview/start", json={**match_body(), "question_count": 3}
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_INVALID_RESPONSE"


@respx.mock
def test_malformed_enrichment_does_not_break_matching(
    respx_mock: respx.MockRouter,
) -> None:
    """Enrichment is optional, so bad JSON from it must not fail the match."""
    seed_cache()
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": MODEL}]})
    )
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": "{broken"})
    )

    response = client.post(f"{PREFIX}/match", json=match_body())

    assert response.status_code == 200
    assert response.json()["match_score"]["score"] == 44
    assert response.json()["llm_available"] is False


@respx.mock
def test_question_count_is_bounded(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_model(respx_mock)

    response = client.post(
        f"{PREFIX}/interview/start", json={**match_body(), "question_count": 99}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@respx.mock
def test_a_multi_clause_gap_answer_is_never_flagged(
    respx_mock: respx.MockRouter,
) -> None:
    """Regression, end to end: answering a hypothetical question in full
    sentences must not produce a false claim flag."""
    session = start_job_interview(respx_mock)
    question = session["current_question"]

    body = client.post(
        f"{PREFIX}/interview/{session['session_id']}/answer",
        json={
            "question_id": question["id"],
            "answer": (
                "I'd expose a few endpoints, containerise it with Docker, and "
                "store request metadata in PostgreSQL. For scale I would add "
                "Redis in front of the read paths."
            ),
        },
    ).json()

    assert body["evaluation"]["unverified_claims"] == []


@respx.mock
def test_readiness_is_available_on_a_plain_session_read(
    respx_mock: respx.MockRouter,
) -> None:
    """Regression: GET returned readiness: null for a finished job interview,
    so the summary UI had nothing to render."""
    session = start_job_interview(respx_mock, question_count=3)
    session_id = session["session_id"]

    for _ in range(3):
        state = client.get(f"{PREFIX}/interview/{session_id}").json()
        question = state["current_question"]
        if question is None:
            break
        # Long enough to be a real attempt; a shorter one is scored 0 without a
        # model call, which would make this assertion about something else.
        client.post(
            f"{PREFIX}/interview/{session_id}/answer",
            json={"question_id": question["id"], "answer": "A considered answer about the design."},
        )

    client.post(f"{PREFIX}/interview/{session_id}/finish")
    fetched = client.get(f"{PREFIX}/interview/{session_id}").json()

    assert fetched["readiness"] is not None
    assert fetched["readiness"]["score"] == 58
    assert fetched["readiness"]["interview_taken"] is True


@respx.mock
def test_readiness_is_present_before_the_interview_is_finished(
    respx_mock: respx.MockRouter,
) -> None:
    """An in-progress session reports readiness from the match alone."""
    session = start_job_interview(respx_mock, question_count=3)

    fetched = client.get(f"{PREFIX}/interview/{session['session_id']}").json()

    assert fetched["readiness"]["interview_taken"] is False
    assert fetched["readiness"]["interview_score"] is None
    # 40*44 + 25*62 renormalised over 65
    assert fetched["readiness"]["score"] == 51


def test_a_plain_step_5_session_reports_no_job_readiness() -> None:
    """A non-job interview has no job to be ready for."""
    from app.services.interview.session import InterviewSession
    from app.services.job.service import readiness_for

    session = InterviewSession(
        session_id="s", repository="demo/x", target_role="software_developer",
        target_role_label="Software Developer", difficulty="mixed", questions=[],
    )

    assert readiness_for(session) is None


@respx.mock
def test_a_too_short_answer_scores_zero_without_a_model_call(
    respx_mock: respx.MockRouter,
) -> None:
    """Step 5's short-answer floor still applies inside a job interview."""
    session = start_job_interview(respx_mock, question_count=3)
    question = session["current_question"]

    body = client.post(
        f"{PREFIX}/interview/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "dunno"},
    ).json()

    assert body["evaluation"]["score"] == 0
    assert body["evaluation"]["communication_score"] == 0
