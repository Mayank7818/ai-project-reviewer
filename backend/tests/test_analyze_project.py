"""End-to-end tests for `POST /api/v1/analyze-project` and `/api/v1/llm/status`.

Both GitHub and Ollama are mocked at the httpx transport layer, so the suite
never touches the network and never runs a model.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

client = TestClient(create_app(), raise_server_exceptions=False)
SETTINGS = get_settings()

ANALYZE = f"{SETTINGS.api_v1_prefix}/analyze-project"
LLM_STATUS = f"{SETTINGS.api_v1_prefix}/llm/status"

GITHUB = SETTINGS.github_api_base_url.rstrip("/")
OLLAMA = SETTINGS.ollama_base_url.rstrip("/")
MODEL = SETTINGS.ollama_model

OWNER, REPO = "octocat", "demo-project"
REPO_API = f"{GITHUB}/repos/{OWNER}/{REPO}"
REPO_URL = f"https://github.com/{OWNER}/{REPO}"

TAGS_URL = f"{OLLAMA}/api/tags"
GENERATE_URL = f"{OLLAMA}/api/generate"


# --- fixtures -----------------------------------------------------------------


def encoded(text: str) -> dict:
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


METADATA = {
    "name": REPO,
    "full_name": f"{OWNER}/{REPO}",
    "owner": {"login": OWNER},
    "description": "A demo project.",
    "default_branch": "main",
    "stargazers_count": 1234,
    "forks_count": 56,
    "open_issues_count": 7,
    "language": "Python",
    "topics": ["fastapi"],
    "license": {"spdx_id": "MIT"},
    "html_url": REPO_URL,
    "size": 900,
}

TREE = {
    "truncated": False,
    "tree": [
        {"path": "README.md", "type": "blob", "size": 400},
        {"path": "app/main.py", "type": "blob", "size": 600},
        {"path": ".env", "type": "blob", "size": 120},
    ],
}

FILE_BODIES = {
    "README.md": "# Demo Project\n\nA small FastAPI demo.",
    "app/main.py": "from fastapi import FastAPI\n\napp = FastAPI()\n",
}

#: One object covering every stage's fields. The service reads only the keys a
#: given stage needs, so the same mock serves all three calls.
VALID_ANALYSIS = {
    # stage 1
    "project_summary": "A minimal FastAPI service exposing a single application object.",
    "technologies": ["Python", "FastAPI"],
    "architecture_summary": "A single-module application with no separate layers.",
    "architecture_evidence": [
        {"file": "app/main.py", "reason": "Declares the FastAPI application."}
    ],
    # stage 2
    "code_quality_findings": [
        {
            "finding": "No error handling around the application entry point.",
            "severity": "low",
            "evidence": [{"file": "app/main.py", "reason": "No try/except present."}],
        }
    ],
    "security_potential_risks": [],
    "security_no_evidence": ["No authentication code is present to assess."],
    "performance_findings": [],
    "documentation_findings": [
        {
            "finding": "The README does not describe how to run the service.",
            "severity": "medium",
            "evidence": [{"file": "README.md", "reason": "No run instructions."}],
        }
    ],
    "testing_evidence": [],
    # stage 3
    "code_quality_score": 70,
    "code_quality_reason": "Readable but minimal.",
    "security_score": 50,
    "performance_score": 55,
    "performance_reason": "Nothing notable observed.",
    "documentation_score": 60,
    "documentation_reason": "A short README is present.",
    "testing_score": 50,
    "testing_reason": "No test files appear in the retrieved selection.",
    "strengths": ["Very small and easy to follow"],
    "weaknesses": ["No test files appear in the retrieved selection"],
    "overall_score": 62,
}


def mock_github(mock: respx.MockRouter) -> None:
    mock.get(REPO_API).mock(return_value=httpx.Response(200, json=METADATA))
    mock.get(f"{REPO_API}/readme").mock(
        return_value=httpx.Response(200, json=encoded(FILE_BODIES["README.md"]))
    )
    mock.get(f"{REPO_API}/languages").mock(
        return_value=httpx.Response(200, json={"Python": 10_000})
    )
    mock.get(f"{REPO_API}/git/trees/main").mock(
        return_value=httpx.Response(200, json=TREE)
    )
    for path, body in FILE_BODIES.items():
        mock.get(f"{REPO_API}/contents/{path}").mock(
            return_value=httpx.Response(200, json=encoded(body))
        )


def mock_ollama_ready(mock: respx.MockRouter) -> None:
    mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": MODEL}]})
    )


def mock_ollama_reply(mock: respx.MockRouter, payload: dict | str):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": body})
    )


def mock_everything(mock: respx.MockRouter) -> None:
    mock_github(mock)
    mock_ollama_ready(mock)
    mock_ollama_reply(mock, VALID_ANALYSIS)


# --- llm status endpoint ------------------------------------------------------


@respx.mock
def test_llm_status_reports_ready(respx_mock: respx.MockRouter) -> None:
    mock_ollama_ready(respx_mock)

    body = client.get(LLM_STATUS).json()

    assert body["ready"] is True
    assert body["reachable"] is True
    assert body["model_available"] is True
    assert body["model"] == MODEL
    assert body["detail"] is None


@respx.mock
def test_llm_status_reports_server_down_without_erroring(
    respx_mock: respx.MockRouter,
) -> None:
    """An unreachable Ollama is data, not a 500."""
    respx_mock.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))

    response = client.get(LLM_STATUS)

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["reachable"] is False
    assert "ollama serve" in body["detail"]


@respx.mock
def test_llm_status_reports_missing_model(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": "some-other:7b"}]})
    )

    body = client.get(LLM_STATUS).json()

    assert body["reachable"] is True
    assert body["model_available"] is False
    assert body["available_models"] == ["some-other:7b"]
    assert "ollama pull" in body["detail"]


# --- successful analysis ------------------------------------------------------


@respx.mock
def test_returns_structured_analysis(respx_mock: respx.MockRouter) -> None:
    mock_everything(respx_mock)

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 200
    analysis = response.json()["analysis"]
    assert analysis["project_summary"].startswith("A minimal FastAPI service")
    assert analysis["technologies"] == ["Python", "FastAPI"]
    assert analysis["architecture"]
    assert analysis["strengths"] == ["Very small and easy to follow"]
    assert analysis["code_quality"]["score"] == 70
    assert analysis["documentation"]["score"] == 60
    assert analysis["security"]["score"] == 50
    assert analysis["overall_score"] == 62


@respx.mock
def test_returns_repository_identity(respx_mock: respx.MockRouter) -> None:
    mock_everything(respx_mock)

    repository = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["repository"]

    assert repository["full_name"] == f"{OWNER}/{REPO}"
    assert repository["owner"] == OWNER
    assert repository["stars"] == 1234
    assert repository["license"] == "MIT"


@respx.mock
def test_meta_reports_what_the_model_actually_saw(
    respx_mock: respx.MockRouter,
) -> None:
    mock_everything(respx_mock)

    meta = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["meta"]

    assert meta["model"] == MODEL
    analysed = {record["path"]: record["domain"] for record in meta["files_analyzed"]}
    assert analysed["app/main.py"] == "backend"
    assert meta["readme_included"] is True
    assert meta["context_chars"] > 0
    assert meta["duration_seconds"] >= 0
    # One pass by default. The deep three-pass pipeline is still available and
    # is covered in test_analysis_performance.py.
    assert meta["stages_completed"] == ["review"]


@respx.mock
def test_meta_reports_the_compression_that_was_applied(
    respx_mock: respx.MockRouter,
) -> None:
    """Step 8: the caller can see exactly which extracts the model was shown."""
    mock_everything(respx_mock)

    meta = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["meta"]

    assert meta["context_limit"] > 0
    assert meta["context_chars"] <= meta["context_limit"]

    for snippet in meta["snippets"]:
        assert snippet["path"] in {r["path"] for r in meta["files_analyzed"]}
        assert 1 <= snippet["line_start"] <= snippet["line_end"]
        assert snippet["reason"]

    for record in meta["files_analyzed"]:
        if record["truncated"]:
            assert 0 < record["lines_shown"] < record["lines_total"]


@respx.mock
def test_prompt_contains_real_repository_content(
    respx_mock: respx.MockRouter,
) -> None:
    """The model must analyse the retrieved files, not a template."""
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    route = mock_ollama_reply(respx_mock, VALID_ANALYSIS)

    client.post(ANALYZE, json={"github_url": REPO_URL})

    # Stage 1 receives the repository extract; stage 3 deliberately does not.
    first = json.loads(route.calls[0].request.content)
    assert "from fastapi import FastAPI" in first["prompt"]
    assert f"{OWNER}/{REPO}" in first["prompt"]
    # Structured output is constrained by schema, not merely requested.
    assert first["format"]["required"]
    assert first["options"]["temperature"] == 0.0
    # Three stages ran.
    # One model call by default: the deep pipeline sent this same context three
    # times, and on CPU prompt processing was the larger half of the cost.
    assert len(route.calls) == 1


@respx.mock
def test_env_contents_are_never_sent_to_the_model(
    respx_mock: respx.MockRouter,
) -> None:
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    route = mock_ollama_reply(respx_mock, VALID_ANALYSIS)

    client.post(ANALYZE, json={"github_url": REPO_URL})

    prompts = [json.loads(call.request.content)["prompt"] for call in route.calls]
    assert all("--- FILE: .env" not in prompt for prompt in prompts)
    assert all("DB_PASSWORD" not in prompt for prompt in prompts)


# --- output validation --------------------------------------------------------


@respx.mock
def test_out_of_range_scores_are_clamped(respx_mock: respx.MockRouter) -> None:
    """A schema-conforming reply can still be nonsense - clamp, do not crash."""
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(
        respx_mock,
        {**VALID_ANALYSIS, "overall_score": 900, "code_quality_score": -5},
    )

    analysis = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["analysis"]

    assert analysis["overall_score"] == 100
    assert analysis["code_quality"]["score"] == 0


@respx.mock
def test_string_scores_are_coerced(respx_mock: respx.MockRouter) -> None:
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(respx_mock, {**VALID_ANALYSIS, "overall_score": "85%"})

    analysis = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["analysis"]

    assert analysis["overall_score"] == 85


@respx.mock
def test_duplicate_technologies_are_removed(respx_mock: respx.MockRouter) -> None:
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(
        respx_mock,
        {**VALID_ANALYSIS, "technologies": ["Python", "python", "  PYTHON  ", "FastAPI"]},
    )

    analysis = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["analysis"]

    assert analysis["technologies"] == ["Python", "FastAPI"]


@respx.mock
def test_invalid_json_from_the_model_is_handled_safely(
    respx_mock: respx.MockRouter,
) -> None:
    """Unparseable output must produce a clean 502, never a crash."""
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(respx_mock, "Sure! Here is the analysis: {broken")

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "LLM_INVALID_RESPONSE"
    # Raw model output must not be echoed back to the client.
    assert "broken" not in error["message"]


@respx.mock
def test_missing_required_fields_are_retried_then_rejected(
    respx_mock: respx.MockRouter,
) -> None:
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    route = mock_ollama_reply(respx_mock, {"project_summary": "only this field"})

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_INVALID_RESPONSE"
    # Stage 1 was retried, then the run was abandoned - later stages never ran.
    assert len(route.calls) == SETTINGS.ollama_max_attempts


@respx.mock
def test_a_valid_retry_after_a_bad_first_reply_succeeds(
    respx_mock: respx.MockRouter,
) -> None:
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    good = httpx.Response(200, json={"response": json.dumps(VALID_ANALYSIS)})
    respx_mock.post(GENERATE_URL).mock(
        side_effect=[
            httpx.Response(200, json={"response": "{not json"}),  # stage 1, retried
            good,                                                 # stage 1 retry
            httpx.Response(200, json={"response": json.dumps(VALID_ANALYSIS)}),  # stage 2
            httpx.Response(200, json={"response": json.dumps(VALID_ANALYSIS)}),  # stage 3
        ]
    )

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 200
    assert response.json()["analysis"]["overall_score"] == 62


# --- model availability failures ----------------------------------------------


@respx.mock
def test_ollama_down_fails_before_touching_github(
    respx_mock: respx.MockRouter,
) -> None:
    """Do not make the user wait through a full retrieval to learn Ollama is off."""
    respx_mock.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
    github_route = respx_mock.get(REPO_API).mock(
        return_value=httpx.Response(200, json=METADATA)
    )

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"
    assert len(github_route.calls) == 0


@respx.mock
def test_model_not_installed_lists_what_is_installed(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json={"models": [{"name": "other-model:7b"}]})
    )

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "LLM_MODEL_NOT_FOUND"
    assert error["details"]["installed_models"] == "other-model:7b"


@respx.mock
def test_generation_timeout_is_reported(respx_mock: respx.MockRouter) -> None:
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    respx_mock.post(GENERATE_URL).mock(side_effect=httpx.ReadTimeout("slow"))

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"


# --- GitHub failures still surface correctly ----------------------------------


@pytest.mark.parametrize(
    "url", ["not-a-url", "https://gitlab.com/owner/repo", "https://github.com/owner"]
)
def test_rejects_invalid_urls(url: str) -> None:
    response = client.post(ANALYZE, json={"github_url": url})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REPOSITORY_URL"


@respx.mock
def test_repository_not_found(respx_mock: respx.MockRouter) -> None:
    mock_ollama_ready(respx_mock)
    respx_mock.get(REPO_API).mock(return_value=httpx.Response(404))

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPOSITORY_NOT_FOUND"


@respx.mock
def test_github_rate_limit_surfaces_through_the_pipeline(
    respx_mock: respx.MockRouter,
) -> None:
    mock_ollama_ready(respx_mock)
    respx_mock.get(REPO_API).mock(
        return_value=httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0"},
            json={"message": "API rate limit exceeded"},
        )
    )

    response = client.post(ANALYZE, json={"github_url": REPO_URL})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "GITHUB_RATE_LIMIT"


def test_rejects_missing_field() -> None:
    response = client.post(ANALYZE, json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- the no-evidence rule -----------------------------------------------------


@respx.mock
def test_absence_of_evidence_does_not_produce_a_damning_score(
    respx_mock: respx.MockRouter,
) -> None:
    """A low score must mean something bad was seen, not that nothing was.

    Small models routinely return 0 for "no test files found", which reads as a
    damning verdict when the honest answer is "we could not tell".
    """
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(
        respx_mock,
        {
            **VALID_ANALYSIS,
            "testing_evidence": [],
            "testing_score": 0,
            "performance_findings": [],
            "performance_score": 5,
        },
    )

    analysis = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["analysis"]

    assert analysis["testing"]["score"] == 50
    assert analysis["performance"]["score"] == 50


@respx.mock
def test_a_harsh_score_survives_when_findings_back_it_up(
    respx_mock: respx.MockRouter,
) -> None:
    """The floor applies only to the genuinely empty case."""
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(respx_mock, {**VALID_ANALYSIS, "code_quality_score": 20})

    analysis = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["analysis"]

    # VALID_ANALYSIS carries a code-quality finding, so 20 stands.
    assert analysis["code_quality"]["score"] == 20


# --- evidence validation end to end -------------------------------------------


@respx.mock
def test_citations_to_unsent_files_never_reach_the_client(
    respx_mock: respx.MockRouter,
) -> None:
    """The anti-hallucination guarantee, verified through the whole pipeline."""
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(
        respx_mock,
        {
            **VALID_ANALYSIS,
            "code_quality_findings": [
                {
                    "finding": "Race condition in the scheduler",
                    "severity": "high",
                    "evidence": [
                        {"file": "app/scheduler.py", "reason": "Invented file."}
                    ],
                },
                {
                    "finding": "A real observation",
                    "severity": "low",
                    "evidence": [{"file": "app/main.py", "reason": "Real file."}],
                },
            ],
        },
    )

    body = client.post(ANALYZE, json={"github_url": REPO_URL}).json()
    findings = body["analysis"]["code_quality"]["findings"]

    assert [item["finding"] for item in findings] == ["A real observation"]
    assert body["meta"]["evidence_dropped"] >= 1
    assert "scheduler" not in json.dumps(body)


@respx.mock
def test_impossible_line_numbers_are_cleared_not_returned(
    respx_mock: respx.MockRouter,
) -> None:
    mock_github(respx_mock)
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(
        respx_mock,
        {
            **VALID_ANALYSIS,
            "architecture_evidence": [
                {
                    "file": "app/main.py",
                    "line_start": 9000,
                    "line_end": 9100,
                    "reason": "Out of range.",
                }
            ],
        },
    )

    body = client.post(ANALYZE, json={"github_url": REPO_URL}).json()
    evidence = body["analysis"]["architecture"]["evidence"][0]

    assert evidence["file"] == "app/main.py"
    assert evidence["line_start"] is None
    assert body["meta"]["line_numbers_cleared"] >= 1


@respx.mock
def test_confirmed_security_issues_come_from_the_mechanical_scan(
    respx_mock: respx.MockRouter,
) -> None:
    """Confirmed issues are pattern matches on real lines, not model output."""
    respx_mock.get(REPO_API).mock(return_value=httpx.Response(200, json=METADATA))
    respx_mock.get(f"{REPO_API}/readme").mock(
        return_value=httpx.Response(200, json=encoded("# Demo"))
    )
    respx_mock.get(f"{REPO_API}/languages").mock(
        return_value=httpx.Response(200, json={"Python": 1})
    )
    respx_mock.get(f"{REPO_API}/git/trees/main").mock(
        return_value=httpx.Response(
            200,
            json={
                "truncated": False,
                "tree": [{"path": "app/db.py", "type": "blob", "size": 60}],
            },
        )
    )
    respx_mock.get(f"{REPO_API}/contents/app/db.py").mock(
        return_value=httpx.Response(
            200, json=encoded('cur.execute(f"SELECT * FROM t WHERE id={x}")\n')
        )
    )
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(respx_mock, {**VALID_ANALYSIS, "security_potential_risks": []})

    security = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["analysis"]["security"]

    assert len(security["confirmed_issues"]) == 1
    issue = security["confirmed_issues"][0]
    assert issue["severity"] == "high"
    assert issue["evidence"][0]["file"] == "app/db.py"
    assert issue["evidence"][0]["line_start"] == 1  # a real line
    # Backwards-compatible flat list is still populated.
    assert security["issues"]


@respx.mock
def test_technologies_include_declared_dependencies(
    respx_mock: respx.MockRouter,
) -> None:
    """Manifest evidence outranks the model's recall."""
    respx_mock.get(REPO_API).mock(return_value=httpx.Response(200, json=METADATA))
    respx_mock.get(f"{REPO_API}/readme").mock(
        return_value=httpx.Response(200, json=encoded("# Demo"))
    )
    respx_mock.get(f"{REPO_API}/languages").mock(
        return_value=httpx.Response(200, json={"Python": 1})
    )
    respx_mock.get(f"{REPO_API}/git/trees/main").mock(
        return_value=httpx.Response(
            200,
            json={
                "truncated": False,
                "tree": [{"path": "package.json", "type": "blob", "size": 60}],
            },
        )
    )
    respx_mock.get(f"{REPO_API}/contents/package.json").mock(
        return_value=httpx.Response(
            200, json=encoded('{"dependencies": {"react": "^19.0.0"}}')
        )
    )
    mock_ollama_ready(respx_mock)
    mock_ollama_reply(respx_mock, {**VALID_ANALYSIS, "technologies": ["Guesswork"]})

    technologies = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["analysis"]["technologies"]

    assert technologies[0] == "React"  # from the manifest, listed first
