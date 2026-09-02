"""End-to-end tests for `POST /api/v1/analyze-repository`.

Every GitHub call is mocked at the httpx transport layer with respx, so the
suite never touches the network, never consumes rate limit, and runs the same
way offline and in CI.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

client = TestClient(create_app(), raise_server_exceptions=False)
SETTINGS = get_settings()
ENDPOINT = f"{SETTINGS.api_v1_prefix}/analyze-repository"
API = SETTINGS.github_api_base_url.rstrip("/")

OWNER, REPO = "octocat", "demo-project"
REPO_API = f"{API}/repos/{OWNER}/{REPO}"
REPO_URL = f"https://github.com/{OWNER}/{REPO}"


# --- fixtures -----------------------------------------------------------------


def encoded(text: str) -> dict:
    """Build a GitHub contents-API payload for `text`."""
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
    "topics": ["fastapi", "demo"],
    "license": {"spdx_id": "MIT", "name": "MIT License"},
    "html_url": REPO_URL,
    "fork": False,
    "archived": False,
    "size": 900,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-06-01T00:00:00Z",
    "pushed_at": "2024-06-02T00:00:00Z",
}

TREE = {
    "truncated": False,
    "tree": [
        {"path": "README.md", "type": "blob", "size": 400},
        {"path": "requirements.txt", "type": "blob", "size": 80},
        {"path": "app", "type": "tree"},
        {"path": "app/main.py", "type": "blob", "size": 600},
        {"path": "app/settings.py", "type": "blob", "size": 300},
        # Everything below must be filtered out.
        {"path": ".env", "type": "blob", "size": 120},
        {"path": "node_modules/react/index.js", "type": "blob", "size": 5_000},
        {"path": "package-lock.json", "type": "blob", "size": 900_000},
        {"path": "assets/logo.png", "type": "blob", "size": 20_000},
        {"path": "dist/bundle.min.js", "type": "blob", "size": 300_000},
        {"path": "huge_dataset.py", "type": "blob", "size": 5_000_000},
    ],
}

FILE_BODIES = {
    "README.md": "# Demo Project\n\nA small FastAPI demo.",
    "requirements.txt": "fastapi==0.121.2\n",
    "app/main.py": "from fastapi import FastAPI\n\napp = FastAPI()\n",
    "app/settings.py": 'API_KEY = os.getenv("API_KEY")\n',
}


def mock_successful_repository(mock: respx.MockRouter) -> None:
    """Register the full happy-path set of GitHub responses."""
    mock.get(REPO_API).mock(return_value=httpx.Response(200, json=METADATA))
    mock.get(f"{REPO_API}/readme").mock(
        return_value=httpx.Response(200, json=encoded(FILE_BODIES["README.md"]))
    )
    mock.get(f"{REPO_API}/languages").mock(
        return_value=httpx.Response(200, json={"Python": 10_000, "HTML": 500})
    )
    mock.get(f"{REPO_API}/git/trees/main").mock(
        return_value=httpx.Response(200, json=TREE)
    )

    for path, body in FILE_BODIES.items():
        mock.get(f"{REPO_API}/contents/{path}").mock(
            return_value=httpx.Response(200, json=encoded(body))
        )


# --- happy path ---------------------------------------------------------------


@respx.mock
def test_returns_structured_metadata(respx_mock: respx.MockRouter) -> None:
    mock_successful_repository(respx_mock)

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 200
    repository = response.json()["repository"]
    assert repository["name"] == REPO
    assert repository["owner"] == OWNER
    assert repository["description"] == "A demo project."
    assert repository["default_branch"] == "main"
    assert repository["stars"] == 1234
    assert repository["forks"] == 56
    assert repository["open_issues"] == 7
    assert repository["primary_language"] == "Python"
    assert repository["html_url"] == REPO_URL
    assert repository["license"] == "MIT"
    assert repository["languages"] == {"Python": 10_000, "HTML": 500}


@respx.mock
def test_returns_readme_and_structure(respx_mock: respx.MockRouter) -> None:
    mock_successful_repository(respx_mock)

    body = client.post(ENDPOINT, json={"github_url": REPO_URL}).json()

    assert "Demo Project" in body["readme"]
    # Structure carries paths only - never file content.
    assert "app/main.py" in body["structure"]["paths"]
    assert body["structure"]["truncated"] is False
    assert not any("node_modules" in path for path in body["structure"]["paths"])


@respx.mock
def test_filters_noise_out_of_retrieved_files(respx_mock: respx.MockRouter) -> None:
    mock_successful_repository(respx_mock)

    body = client.post(ENDPOINT, json={"github_url": REPO_URL}).json()
    paths = [item["path"] for item in body["files"]]

    assert set(paths) == set(FILE_BODIES)
    for excluded in (
        ".env",
        "node_modules/react/index.js",
        "package-lock.json",
        "assets/logo.png",
        "dist/bundle.min.js",
        "huge_dataset.py",
    ):
        assert excluded not in paths

    skipped = body["retrieval"]["skipped"]
    assert skipped["secret_material"] == 1
    assert skipped["binary_or_media"] == 1
    assert skipped["generated_file"] == 1
    assert skipped["too_large"] == 1


@respx.mock
def test_no_ai_analysis_is_produced(respx_mock: respx.MockRouter) -> None:
    """Step 2 retrieves only - it must never invent an analysis."""
    mock_successful_repository(respx_mock)

    body = client.post(ENDPOINT, json={"github_url": REPO_URL}).json()

    assert body["analysis"] is None


@respx.mock
def test_retrieval_summary_reports_limits_not_secrets(
    respx_mock: respx.MockRouter,
) -> None:
    mock_successful_repository(respx_mock)

    retrieval = client.post(ENDPOINT, json={"github_url": REPO_URL}).json()["retrieval"]

    assert retrieval["files_retrieved"] == len(FILE_BODIES)
    assert retrieval["limits"]["max_files"] == SETTINGS.effective_max_files
    assert isinstance(retrieval["authenticated"], bool)
    # The token itself must never appear anywhere in the response.
    assert "github_token" not in str(retrieval).lower()


@respx.mock
def test_file_categories_are_reported(respx_mock: respx.MockRouter) -> None:
    mock_successful_repository(respx_mock)

    body = client.post(ENDPOINT, json={"github_url": REPO_URL}).json()
    categories = {item["path"]: item["category"] for item in body["files"]}

    assert categories["README.md"] == "manifest"
    assert categories["requirements.txt"] == "manifest"
    assert categories["app/main.py"] == "entrypoint"


# --- input validation ---------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["not-a-url", "https://gitlab.com/owner/repo", "https://github.com/only-owner"],
)
def test_rejects_invalid_urls(url: str) -> None:
    response = client.post(ENDPOINT, json={"github_url": url})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REPOSITORY_URL"


def test_rejects_missing_field() -> None:
    response = client.post(ENDPOINT, json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- upstream failures --------------------------------------------------------


@respx.mock
def test_repository_not_found(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(REPO_API).mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPOSITORY_NOT_FOUND"


@respx.mock
def test_rate_limit_is_reported_clearly(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(REPO_API).mock(
        return_value=httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"},
            json={"message": "API rate limit exceeded"},
        )
    )

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "GITHUB_RATE_LIMIT"
    assert "GITHUB_TOKEN" in error["message"]
    assert "resets_at" in error["details"]


@respx.mock
def test_forbidden_without_rate_limit_is_not_found(
    respx_mock: respx.MockRouter,
) -> None:
    """A 403 that is not a rate limit means the repo is not publicly readable."""
    respx_mock.get(REPO_API).mock(
        return_value=httpx.Response(
            403, headers={"x-ratelimit-remaining": "42"}, json={"message": "Forbidden"}
        )
    )

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPOSITORY_NOT_FOUND"


@respx.mock
def test_invalid_token_is_reported(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(REPO_API).mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GITHUB_AUTH_ERROR"


@respx.mock
def test_network_failure_is_handled(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(REPO_API).mock(side_effect=httpx.ConnectError("no route to host"))

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "EXTERNAL_SERVICE_ERROR"
    # Internal detail must not leak to the client.
    assert "no route to host" not in error["message"]


@respx.mock
def test_timeout_is_handled(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(REPO_API).mock(side_effect=httpx.ReadTimeout("timed out"))

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    assert "time" in response.json()["error"]["message"].lower()


@respx.mock
def test_server_error_is_handled(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(REPO_API).mock(return_value=httpx.Response(500))

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "EXTERNAL_SERVICE_ERROR"


# --- resilience ---------------------------------------------------------------


@respx.mock
def test_repository_without_readme(respx_mock: respx.MockRouter) -> None:
    """A missing README is a normal condition, not an error."""
    mock_successful_repository(respx_mock)
    respx_mock.get(f"{REPO_API}/readme").mock(return_value=httpx.Response(404))

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 200
    assert response.json()["readme"] is None


@respx.mock
def test_empty_repository(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(REPO_API).mock(return_value=httpx.Response(200, json=METADATA))
    respx_mock.get(f"{REPO_API}/readme").mock(return_value=httpx.Response(404))
    respx_mock.get(f"{REPO_API}/languages").mock(
        return_value=httpx.Response(200, json={})
    )
    respx_mock.get(f"{REPO_API}/git/trees/main").mock(
        return_value=httpx.Response(404)
    )

    response = client.post(ENDPOINT, json={"github_url": REPO_URL})

    assert response.status_code == 200
    body = response.json()
    assert body["files"] == []
    assert body["structure"]["total_entries"] == 0


@respx.mock
def test_secrets_in_file_content_are_redacted(respx_mock: respx.MockRouter) -> None:
    """A key hardcoded in an ordinary source file must not reach the client."""
    leaked = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    mock_successful_repository(respx_mock)
    respx_mock.get(f"{REPO_API}/contents/app/settings.py").mock(
        return_value=httpx.Response(200, json=encoded(f'TOKEN = "{leaked}"\n'))
    )

    body = client.post(ENDPOINT, json={"github_url": REPO_URL}).json()
    settings_file = next(
        item for item in body["files"] if item["path"] == "app/settings.py"
    )

    assert leaked not in settings_file["content"]
    assert leaked not in response_text(body)
    assert settings_file["redacted"] is True


def response_text(body: dict) -> str:
    """Flatten the whole response so leak assertions cannot miss a field."""
    return str(body)


@respx.mock
def test_a_summary_request_omits_file_content(respx_mock: respx.MockRouter) -> None:
    """The UI asks what was retrieved while it waits; it does not need the text."""
    mock_successful_repository(respx_mock)

    response = client.post(
        ENDPOINT, json={"github_url": REPO_URL, "include_content": False}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["files"], "the file list itself must still be reported"
    assert all(item["content"] == "" for item in body["files"])
    assert all(item["path"] for item in body["files"])
    assert all(item["size_bytes"] >= 0 for item in body["files"])


@respx.mock
def test_content_is_included_by_default(respx_mock: respx.MockRouter) -> None:
    mock_successful_repository(respx_mock)

    body = client.post(ENDPOINT, json={"github_url": REPO_URL}).json()

    assert any(item["content"] for item in body["files"])
