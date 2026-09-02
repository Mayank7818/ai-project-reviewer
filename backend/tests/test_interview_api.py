"""End-to-end tests for the interview endpoints and session lifecycle.

Both GitHub and Ollama are mocked at the httpx transport layer, so no network
call and no model run ever happens.

The analysis cache is seeded directly in most tests, which is exactly how the
feature behaves in practice: Step 4 runs once, and interviews reuse it.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services.analysis.code_structure import extract_all
from app.services.analysis.dependencies import analyse_dependencies, infer_technologies
from app.services.analysis.security_scan import scan_files
from app.services.interview.session import (
    AnsweredRecord,
    InterviewSession,
    NOT_ASSESSED_SCORE,
)
from app.services.interview.store import (
    CachedAnalysis,
    analysis_cache_key,
    get_analysis_cache,
    reset_stores,
)

client = TestClient(create_app(), raise_server_exceptions=False)
SETTINGS = get_settings()
PREFIX = f"{SETTINGS.api_v1_prefix}/interview"

OLLAMA = SETTINGS.ollama_base_url.rstrip("/")
TAGS_URL = f"{OLLAMA}/api/tags"
GENERATE_URL = f"{OLLAMA}/api/generate"
MODEL = SETTINGS.ollama_model

GITHUB = SETTINGS.github_api_base_url.rstrip("/")
REPO_URL = "https://github.com/demo/shop-api"
REPO_API = f"{GITHUB}/repos/demo/shop-api"

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


@pytest.fixture(autouse=True)
def clean_stores():
    """Every test starts with empty stores."""
    reset_stores()
    yield
    reset_stores()


def seed_cache() -> CachedAnalysis:
    """Put a completed Step 4 analysis in the cache, as /analyze-project would."""
    structures = extract_all({k: v for k, v in FILES.items() if k.endswith(".py")})
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
        structures=structures,
        manifests=manifests,
        security=scan_files(FILES),
        analyzed=ANALYZED,
        domain_counts={"backend": 1, "security": 1},
        technologies=infer_technologies(manifests),
        evidence_files=dict(FILES),
        readme_path="README.md",
    )
    get_analysis_cache().put(analysis_cache_key(cached.repository_full_name), cached)
    return cached


def mock_ollama_ready(mock: respx.MockRouter) -> None:
    mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": MODEL}]})
    )


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
                "why_this_question": "Probes their understanding.",
                "expected_topics": ["design", "trade-offs"],
            }
            for seed_id in ids
        ]
    }


EVALUATION = {
    "score": 7,
    "correct_points": ["Explained the request flow clearly"],
    "missing_points": ["Did not mention error handling"],
    "incorrect_points": [],
    "feedback": "Solid answer with one gap.",
    "follow_up_question": "How would you handle a failure there?",
    "communication_score": 8,
}

SUMMARY = {
    "strong_areas": ["API design"],
    "weak_areas": ["Error handling"],
    "recommended_topics": ["Retry strategies"],
    "overall_feedback": "A good showing overall.",
}


def mock_model(mock: respx.MockRouter) -> respx.Route:
    """Route each model call to the right canned reply, by schema shape."""
    mock_ollama_ready(mock)

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        schema_keys = set((payload.get("format") or {}).get("properties", {}))

        if "questions" in schema_keys:
            body = _questions_for(payload["prompt"])
        elif "score" in schema_keys:
            body = EVALUATION
        else:
            body = SUMMARY

        return httpx.Response(200, json={"response": json.dumps(body)})

    return mock.post(GENERATE_URL).mock(side_effect=responder)


def start_interview(mock: respx.MockRouter, **overrides) -> dict:
    seed_cache()
    mock_model(mock)
    body = {
        "github_url": REPO_URL,
        "target_role": "backend_developer",
        "difficulty": "mixed",
        "question_count": 5,
        **overrides,
    }
    response = client.post(f"{PREFIX}/start", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- options ------------------------------------------------------------------


def test_options_lists_roles_and_difficulties() -> None:
    body = client.get(f"{PREFIX}/options").json()

    keys = {role["key"] for role in body["roles"]}
    assert "software_developer" in keys
    assert "ml_engineer" in keys
    assert "genai_engineer" in keys
    assert set(body["difficulties"]) == {"easy", "medium", "hard", "mixed"}
    assert body["min_questions"] <= body["default_question_count"] <= body["max_questions"]


# --- generation ---------------------------------------------------------------


@respx.mock
def test_generate_returns_grounded_questions(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_model(respx_mock)

    response = client.post(
        f"{PREFIX}/generate",
        json={"github_url": REPO_URL, "target_role": "backend_developer",
              "difficulty": "mixed", "question_count": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["repository"] == "demo/shop-api"
    assert len(body["questions"]) == 10

    for question in body["questions"]:
        assert question["evidence"], question["id"]
        assert question["evidence"][0]["file"] in ANALYZED


@respx.mock
def test_generate_honours_the_difficulty_mix(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_model(respx_mock)

    body = client.post(
        f"{PREFIX}/generate",
        json={"github_url": REPO_URL, "difficulty": "mixed", "question_count": 10},
    ).json()

    assert body["difficulty_counts"] == {"easy": 3, "medium": 5, "hard": 2}


@respx.mock
def test_generate_can_request_a_single_difficulty(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_model(respx_mock)

    body = client.post(
        f"{PREFIX}/generate",
        json={"github_url": REPO_URL, "difficulty": "hard", "question_count": 4},
    ).json()

    assert set(body["difficulty_counts"]) == {"hard"}


@respx.mock
def test_generate_spans_multiple_categories(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_model(respx_mock)

    body = client.post(
        f"{PREFIX}/generate",
        json={"github_url": REPO_URL, "difficulty": "mixed", "question_count": 10},
    ).json()

    assert len(body["category_counts"]) >= 4


@respx.mock
def test_unsupported_role_returns_an_honest_notice(
    respx_mock: respx.MockRouter,
) -> None:
    seed_cache()
    mock_model(respx_mock)

    body = client.post(
        f"{PREFIX}/generate",
        json={"github_url": REPO_URL, "target_role": "ml_engineer", "question_count": 5},
    ).json()

    assert "limited evidence of machine-learning" in body["role_notice"]
    assert len(body["questions"]) == 5  # transferable questions still generated


@respx.mock
def test_generate_reuses_the_cached_analysis(respx_mock: respx.MockRouter) -> None:
    """Feature 16: an interview must never re-run the Step 4 pipeline."""
    seed_cache()
    mock_model(respx_mock)
    github = respx_mock.get(REPO_API).mock(return_value=httpx.Response(200, json={}))

    client.post(f"{PREFIX}/generate", json={"github_url": REPO_URL, "question_count": 5})

    assert len(github.calls) == 0


@respx.mock
def test_repository_without_evidence_is_refused_not_faked(
    respx_mock: respx.MockRouter,
) -> None:
    """Generic questions are the one thing this product must never produce."""
    empty = CachedAnalysis(repository_full_name="demo/shop-api", security=scan_files({}))
    get_analysis_cache().put(analysis_cache_key("demo/shop-api"), empty)
    mock_model(respx_mock)

    response = client.post(
        f"{PREFIX}/generate", json={"github_url": REPO_URL, "question_count": 5}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_EVIDENCE"


# --- session lifecycle --------------------------------------------------------


@respx.mock
def test_start_opens_a_session_with_a_first_question(
    respx_mock: respx.MockRouter,
) -> None:
    body = start_interview(respx_mock)

    assert body["status"] == "in_progress"
    assert body["total_questions"] == 5
    assert body["answered_count"] == 0
    assert body["current_question"]["evidence"]
    assert body["session_id"]


@respx.mock
def test_answering_advances_the_session(respx_mock: respx.MockRouter) -> None:
    session = start_interview(respx_mock)
    question_id = session["current_question"]["id"]

    response = client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question_id, "answer": "It uses FastAPI routing to dispatch requests."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answered"] == 1
    assert body["total"] == 5
    assert body["evaluation"]["score"] == 7
    assert body["evaluation"]["follow_up_question"]
    assert body["next_question"]["id"] != question_id
    assert body["is_complete"] is False


@respx.mock
def test_full_interview_to_completion(respx_mock: respx.MockRouter) -> None:
    session = start_interview(respx_mock)
    session_id = session["session_id"]

    for _ in range(5):
        state = client.get(f"{PREFIX}/{session_id}").json()
        question = state["current_question"]
        assert question is not None
        client.post(
            f"{PREFIX}/{session_id}/answer",
            json={"question_id": question["id"], "answer": "A considered answer about FastAPI."},
        )

    final = client.get(f"{PREFIX}/{session_id}").json()
    assert final["answered_count"] == 5
    assert final["current_question"] is None
    assert len(final["history"]) == 5

    finished = client.post(f"{PREFIX}/{session_id}/finish").json()
    assert finished["status"] == "complete"
    assert finished["end_time"]
    assert finished["summary"]["scores"]["overall"] == 70  # 7/10 -> 70
    assert finished["summary"]["strong_areas"] == ["API design"]


@respx.mock
def test_answering_the_same_question_twice_is_rejected(
    respx_mock: respx.MockRouter,
) -> None:
    session = start_interview(respx_mock)
    question_id = session["current_question"]["id"]
    payload = {"question_id": question_id, "answer": "An answer about the API."}

    client.post(f"{PREFIX}/{session['session_id']}/answer", json=payload)
    response = client.post(f"{PREFIX}/{session['session_id']}/answer", json=payload)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


@respx.mock
def test_unknown_question_id_is_rejected(respx_mock: respx.MockRouter) -> None:
    session = start_interview(respx_mock)

    response = client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": "invented:question", "answer": "Something."},
    )

    assert response.status_code == 404


def test_unknown_session_id_is_a_clean_404() -> None:
    response = client.get(f"{PREFIX}/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_answering_an_unknown_session_is_a_clean_404() -> None:
    response = client.post(
        f"{PREFIX}/nope/answer", json={"question_id": "x", "answer": "y"}
    )

    assert response.status_code == 404


@respx.mock
def test_finishing_early_summarises_only_what_was_asked(
    respx_mock: respx.MockRouter,
) -> None:
    session = start_interview(respx_mock)
    question = session["current_question"]
    client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "A reasonable answer."},
    )

    finished = client.post(f"{PREFIX}/{session['session_id']}/finish").json()

    assert finished["status"] == "complete"
    # Dimensions with no question are reported as not assessed, not as failures.
    assert any("not assessed" in item for item in finished["summary"]["weak_areas"])


@respx.mock
def test_finishing_twice_is_idempotent(respx_mock: respx.MockRouter) -> None:
    session = start_interview(respx_mock)

    first = client.post(f"{PREFIX}/{session['session_id']}/finish").json()
    second = client.post(f"{PREFIX}/{session['session_id']}/finish").json()

    assert first["end_time"] == second["end_time"]


# --- claim verification through the API ---------------------------------------


@respx.mock
def test_unsupported_claim_is_surfaced_in_the_evaluation(
    respx_mock: respx.MockRouter,
) -> None:
    session = start_interview(respx_mock)
    question = session["current_question"]

    body = client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={
            "question_id": question["id"],
            "answer": "I used Redis for caching and Kafka for events.",
        },
    ).json()

    flagged = {item["technology"] for item in body["evaluation"]["unverified_claims"]}
    assert flagged == {"Redis", "Kafka"}
    assert any("Redis" in point for point in body["evaluation"]["missing_points"])


@respx.mock
def test_supported_claim_is_verified(respx_mock: respx.MockRouter) -> None:
    session = start_interview(respx_mock)
    question = session["current_question"]

    body = client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "I used FastAPI with SQLAlchemy."},
    ).json()

    verified = {item["technology"] for item in body["evaluation"]["verified_claims"]}
    assert {"FastAPI", "SQLAlchemy"} <= verified
    assert body["evaluation"]["unverified_claims"] == []


@respx.mock
def test_unverified_claims_reach_the_final_summary(
    respx_mock: respx.MockRouter,
) -> None:
    session = start_interview(respx_mock)
    question = session["current_question"]
    client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "I used Redis heavily here."},
    )

    finished = client.post(f"{PREFIX}/{session['session_id']}/finish").json()

    assert finished["summary"]["unverified_claims"][0]["technology"] == "Redis"


# --- answer evaluation edge cases ---------------------------------------------


@respx.mock
def test_blank_answer_scores_zero_without_a_model_call(
    respx_mock: respx.MockRouter,
) -> None:
    session = start_interview(respx_mock)
    question = session["current_question"]
    route = respx_mock.post(GENERATE_URL)
    before = len(route.calls)

    body = client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "   "},
    ).json()

    assert body["evaluation"]["score"] == 0
    assert body["evaluation"]["communication_score"] == 0
    assert len(route.calls) == before  # no model call was spent


@respx.mock
def test_out_of_range_model_score_is_clamped(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_ollama_ready(respx_mock)

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        keys = set((payload.get("format") or {}).get("properties", {}))
        if "questions" in keys:
            body = _questions_for(payload["prompt"])
        elif "score" in keys:
            body = {**EVALUATION, "score": 99, "communication_score": -4}
        else:
            body = SUMMARY
        return httpx.Response(200, json={"response": json.dumps(body)})

    respx_mock.post(GENERATE_URL).mock(side_effect=responder)

    session = client.post(
        f"{PREFIX}/start",
        json={"github_url": REPO_URL, "question_count": 3},
    ).json()
    question = session["current_question"]

    body = client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "A full and detailed answer."},
    ).json()

    assert body["evaluation"]["score"] == 10
    assert body["evaluation"]["communication_score"] == 0


@respx.mock
def test_malformed_model_json_is_a_clean_502(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    mock_ollama_ready(respx_mock)
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": "{not json at all"})
    )

    response = client.post(
        f"{PREFIX}/start", json={"github_url": REPO_URL, "question_count": 3}
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_INVALID_RESPONSE"


@respx.mock
def test_ollama_unavailable_is_a_clean_503(respx_mock: respx.MockRouter) -> None:
    seed_cache()
    respx_mock.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
    respx_mock.post(GENERATE_URL).mock(side_effect=httpx.ConnectError("refused"))

    response = client.post(
        f"{PREFIX}/start", json={"github_url": REPO_URL, "question_count": 3}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"


# --- input validation ---------------------------------------------------------


@pytest.mark.parametrize("url", ["not-a-url", "https://gitlab.com/a/b"])
def test_invalid_repository_url_is_rejected(url: str) -> None:
    response = client.post(f"{PREFIX}/generate", json={"github_url": url})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REPOSITORY_URL"


@pytest.mark.parametrize("count", [0, 2, 50])
def test_question_count_is_bounded(count: int) -> None:
    response = client.post(
        f"{PREFIX}/generate", json={"github_url": REPO_URL, "question_count": count}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- scoring (Feature 13) -----------------------------------------------------


def make_session(records: list[tuple[str, int, int]]) -> InterviewSession:
    """Build a session with canned (category, score, communication) results."""
    session = InterviewSession(
        session_id="s", repository="demo/x", target_role="software_developer",
        target_role_label="Software Developer", difficulty="mixed", questions=[],
    )
    for index, (category, score, communication) in enumerate(records):
        session.history.append(
            AnsweredRecord(
                question={"id": f"q{index}", "category": category, "question": "?"},
                answer="a",
                evaluation={"score": score, "communication_score": communication},
                answered_at=datetime.now(timezone.utc),
            )
        )
    return session


def test_scores_convert_from_ten_to_one_hundred() -> None:
    scores = make_session([("code", 7, 8), ("api", 9, 6)]).compute_scores()

    assert scores["overall"] == 80  # mean of 70 and 90
    assert scores["communication"] == 70  # mean of 80 and 60


def test_untested_dimension_is_neutral_not_zero() -> None:
    """A dimension nobody was asked about must not read as a failure."""
    scores = make_session([("code", 8, 8)]).compute_scores()

    assert scores["security"] == NOT_ASSESSED_SCORE
    assert scores["technical"] == 80


def test_a_session_with_no_answers_scores_neutrally() -> None:
    scores = make_session([]).compute_scores()

    assert set(scores.values()) == {NOT_ASSESSED_SCORE}


def test_weak_answers_are_identified_for_revisiting() -> None:
    session = make_session([("code", 9, 8), ("api", 3, 5), ("security", 5, 5)])

    weak = session.weak_records()

    assert [record.evaluation["score"] for record in weak] == [3, 5]


def test_assessed_dimensions_reflect_the_questions_asked() -> None:
    session = make_session([("security", 6, 6)])

    assessed = session.assessed_dimensions()

    assert "security" in assessed
    assert "architecture" not in assessed


# --- analysis caching ---------------------------------------------------------


@respx.mock
def test_analyze_project_populates_the_interview_cache(
    respx_mock: respx.MockRouter,
) -> None:
    """Analysing then interviewing must cost exactly one analysis."""
    metadata = {
        "name": "shop-api", "full_name": "demo/shop-api",
        "owner": {"login": "demo"}, "default_branch": "main",
        "html_url": REPO_URL, "language": "Python",
    }
    respx_mock.get(REPO_API).mock(return_value=httpx.Response(200, json=metadata))
    respx_mock.get(f"{REPO_API}/readme").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(b"# Shop API").decode(),
            },
        )
    )
    respx_mock.get(f"{REPO_API}/languages").mock(
        return_value=httpx.Response(200, json={"Python": 1000})
    )
    respx_mock.get(f"{REPO_API}/git/trees/main").mock(
        return_value=httpx.Response(
            200,
            json={
                "truncated": False,
                "tree": [{"path": "app/main.py", "type": "blob", "size": 100}],
            },
        )
    )
    respx_mock.get(f"{REPO_API}/contents/app/main.py").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(FILES["app/main.py"].encode()).decode(),
            },
        )
    )
    mock_ollama_ready(respx_mock)
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "response": json.dumps(
                    {
                        "project_summary": "A demo.",
                        "technologies": ["FastAPI"],
                        "architecture_summary": "One module.",
                        "architecture_evidence": [
                            {"file": "app/main.py", "reason": "App."}
                        ],
                        "code_quality_findings": [],
                        "security_potential_risks": [],
                        "security_no_evidence": [],
                        "performance_findings": [],
                        "documentation_findings": [],
                        "testing_evidence": [],
                        "code_quality_score": 70, "code_quality_reason": "ok",
                        "security_score": 60,
                        "performance_score": 60, "performance_reason": "ok",
                        "documentation_score": 60, "documentation_reason": "ok",
                        "testing_score": 50, "testing_reason": "none",
                        "strengths": [], "weaknesses": [], "overall_score": 62,
                    }
                )
            },
        )
    )

    key = analysis_cache_key("demo/shop-api")
    assert get_analysis_cache().get(key) is None

    response = client.post(
        f"{SETTINGS.api_v1_prefix}/analyze-project", json={"github_url": REPO_URL}
    )

    assert response.status_code == 200
    cached = get_analysis_cache().get(key)
    assert cached is not None
    assert "app/main.py" in cached.evidence_files


@respx.mock
def test_evidence_ground_truth_covers_every_analysed_file(
    respx_mock: respx.MockRouter,
) -> None:
    """Regression: a file may be mechanically analysed without its text fitting
    the analysis prompt. Citations derived from that file are still valid, so the
    evidence index must cover the wider set - not only what the model was shown.

    psf/requests hit this: 15 files were retrieved and parsed, 2 fitted the
    prompt, and every question was discarded as unverifiable.
    """
    cached = seed_cache()
    # Mimic the analysis prompt having room for only one file.
    cached.evidence_files = dict(FILES)
    get_analysis_cache().put(analysis_cache_key(cached.repository_full_name), cached)
    mock_model(respx_mock)

    body = client.post(
        f"{PREFIX}/generate", json={"github_url": REPO_URL, "question_count": 8}
    ).json()

    cited = {item["file"] for q in body["questions"] for item in q["evidence"]}
    assert len(body["questions"]) == 8
    assert cited <= set(FILES)
    # Files beyond a single prompt-sized one are still citable.
    assert len(cited) >= 2


# --- recommended topics must stay grounded (Feature 14) -----------------------


@respx.mock
def test_invented_study_topics_are_dropped(respx_mock: respx.MockRouter) -> None:
    """Regression: a 4B model recommended "JWT expiry and revocation" after an
    interview about an HTTP client library with no JWT anywhere - not in the
    repository, not in any answer. That is an invented technology claim.
    """
    seed_cache()
    mock_ollama_ready(respx_mock)

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        keys = set((payload.get("format") or {}).get("properties", {}))
        if "questions" in keys:
            body = _questions_for(payload["prompt"])
        elif "score" in keys:
            body = EVALUATION
        else:
            body = {
                **SUMMARY,
                "recommended_topics": [
                    "Kubernetes operator patterns",          # nowhere near this repo
                    "Deployment strategies across environments",  # generic, keep
                    "SQLAlchemy session scoping",            # a real dependency, keep
                ],
            }
        return httpx.Response(200, json={"response": json.dumps(body)})

    respx_mock.post(GENERATE_URL).mock(side_effect=responder)

    session = client.post(
        f"{PREFIX}/start", json={"github_url": REPO_URL, "question_count": 3}
    ).json()
    question = session["current_question"]
    client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "A considered answer about the API."},
    )

    topics = client.post(f"{PREFIX}/{session['session_id']}/finish").json()[
        "summary"
    ]["recommended_topics"]

    assert "Kubernetes operator patterns" not in topics
    assert "Deployment strategies across environments" in topics
    assert "SQLAlchemy session scoping" in topics


@respx.mock
def test_a_topic_the_candidate_raised_is_kept(respx_mock: respx.MockRouter) -> None:
    """Advising on something the candidate themselves brought up is legitimate,
    even when the repository does not evidence it."""
    seed_cache()
    mock_ollama_ready(respx_mock)

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        keys = set((payload.get("format") or {}).get("properties", {}))
        if "questions" in keys:
            body = _questions_for(payload["prompt"])
        elif "score" in keys:
            body = EVALUATION
        else:
            body = {**SUMMARY, "recommended_topics": ["Redis cache invalidation"]}
        return httpx.Response(200, json={"response": json.dumps(body)})

    respx_mock.post(GENERATE_URL).mock(side_effect=responder)

    session = client.post(
        f"{PREFIX}/start", json={"github_url": REPO_URL, "question_count": 3}
    ).json()
    question = session["current_question"]
    client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "I used Redis to cache responses."},
    )

    summary = client.post(f"{PREFIX}/{session['session_id']}/finish").json()["summary"]

    assert "Redis cache invalidation" in summary["recommended_topics"]
    # It is still reported as unverified against the repository.
    assert summary["unverified_claims"][0]["technology"] == "Redis"


@respx.mock
def test_generic_advice_naming_no_technology_always_survives(
    respx_mock: respx.MockRouter,
) -> None:
    seed_cache()
    mock_ollama_ready(respx_mock)

    def responder(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        keys = set((payload.get("format") or {}).get("properties", {}))
        if "questions" in keys:
            body = _questions_for(payload["prompt"])
        elif "score" in keys:
            body = EVALUATION
        else:
            body = {
                **SUMMARY,
                "recommended_topics": [
                    "Writing clearer commit messages",
                    "Structuring error handling consistently",
                ],
            }
        return httpx.Response(200, json={"response": json.dumps(body)})

    respx_mock.post(GENERATE_URL).mock(side_effect=responder)

    session = client.post(
        f"{PREFIX}/start", json={"github_url": REPO_URL, "question_count": 3}
    ).json()
    question = session["current_question"]
    client.post(
        f"{PREFIX}/{session['session_id']}/answer",
        json={"question_id": question["id"], "answer": "An answer."},
    )

    topics = client.post(f"{PREFIX}/{session['session_id']}/finish").json()[
        "summary"
    ]["recommended_topics"]

    assert len(topics) == 2
