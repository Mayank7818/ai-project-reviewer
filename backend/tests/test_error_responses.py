"""What an error response is allowed to contain.

Two rules, both about not saying more than the caller asked for: no internals
(paths, tracebacks, framework detail) and no echo of the submitted values, which
for this application can include a whole job description.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SETTINGS = get_settings()
client = TestClient(create_app(), raise_server_exceptions=False)

SECRET = "CONFIDENTIAL POSTING TEXT that should never come back"


def test_validation_errors_name_the_field_but_not_its_value() -> None:
    response = client.post(
        f"{SETTINGS.api_v1_prefix}/job/match",
        json={"github_url": "x" * 400, "job_description": SECRET * 20},
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert SECRET not in response.text

    for error in body["details"]["errors"]:
        assert set(error) <= {"loc", "msg", "type"}
        assert "input" not in error
        assert "ctx" not in error


def test_a_rejected_job_description_is_not_repeated_back() -> None:
    response = client.post(
        f"{SETTINGS.api_v1_prefix}/job/match",
        json={"github_url": "https://github.com/demo/sample", "job_description": "too short"},
    )

    assert response.status_code == 422
    assert SECRET not in response.text
    assert "too short" not in response.json()["error"]["details"].get("errors", "")


def test_error_bodies_never_carry_a_traceback_or_a_local_path() -> None:
    for payload in ({"github_url": "not-a-url"}, {"github_url": ""}):
        response = client.post(
            f"{SETTINGS.api_v1_prefix}/analyze-project", json=payload
        )

        assert response.status_code in (422, 404)
        text = response.text
        assert "Traceback" not in text
        assert "site-packages" not in text
        assert "\\\\Users\\\\" not in text
        assert "/app/services/" not in text


def test_every_error_uses_the_same_envelope() -> None:
    response = client.get(f"{SETTINGS.api_v1_prefix}/does-not-exist")

    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
