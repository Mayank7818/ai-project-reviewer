"""Repeating yourself is the expensive mistake in this application.

One analysis costs about twenty GitHub requests out of an hourly sixty, and
minutes of local inference. A user who analyses a repository, matches a job
against it and then interviews about it touches the same repository three times,
so these tests pin down that the second and third times are nearly free.
"""

from __future__ import annotations

import asyncio

import respx
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.github.service import GitHubService, get_retrieval_cache
from app.core.config import get_settings

from tests.test_analyze_project import (  # reuse the established fixture set
    ANALYZE,
    MODEL,
    REPO_URL,
    mock_everything,
)

client = TestClient(create_app())


# --- retrieval cache ----------------------------------------------------------


@respx.mock
def test_second_retrieval_makes_no_github_requests(
    respx_mock: respx.MockRouter,
) -> None:
    mock_everything(respx_mock)
    service = GitHubService(get_settings())

    first = asyncio.run(service.retrieve(REPO_URL))
    calls_after_first = respx_mock.calls.call_count

    second = asyncio.run(service.retrieve(REPO_URL))

    assert respx_mock.calls.call_count == calls_after_first
    assert [f.path for f in second.files] == [f.path for f in first.files]


@respx.mock
def test_a_query_biased_retrieval_is_cached_separately(
    respx_mock: respx.MockRouter,
) -> None:
    """Terms change which files are selected, so they must change the key."""
    mock_everything(respx_mock)
    service = GitHubService(get_settings())

    asyncio.run(service.retrieve(REPO_URL))
    calls_after_first = respx_mock.calls.call_count

    asyncio.run(service.retrieve(REPO_URL, query_terms=["postgresql"]))

    assert respx_mock.calls.call_count > calls_after_first


@respx.mock
def test_clearing_the_cache_forces_a_fresh_retrieval(
    respx_mock: respx.MockRouter,
) -> None:
    mock_everything(respx_mock)
    service = GitHubService(get_settings())

    asyncio.run(service.retrieve(REPO_URL))
    get_retrieval_cache().clear()
    calls_before = respx_mock.calls.call_count

    asyncio.run(service.retrieve(REPO_URL))

    assert respx_mock.calls.call_count > calls_before


# --- analysis cache -----------------------------------------------------------


@respx.mock
def test_repeat_analysis_is_served_from_the_cache(
    respx_mock: respx.MockRouter,
) -> None:
    mock_everything(respx_mock)

    first = client.post(ANALYZE, json={"github_url": REPO_URL}).json()
    calls_after_first = respx_mock.calls.call_count

    second = client.post(ANALYZE, json={"github_url": REPO_URL}).json()

    assert first["meta"]["cached"] is False
    assert second["meta"]["cached"] is True
    # No GitHub requests, and - the expensive half - no model requests either.
    assert respx_mock.calls.call_count == calls_after_first
    assert second["repository"]["full_name"] == first["repository"]["full_name"]
    assert second["analysis"]["overall_score"] == first["analysis"]["overall_score"]


@respx.mock
def test_the_cached_response_keeps_its_audit_trail(
    respx_mock: respx.MockRouter,
) -> None:
    """A cached result is still the real one: same evidence, same model name."""
    mock_everything(respx_mock)

    first = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["meta"]
    second = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["meta"]

    assert second["model"] == MODEL
    assert second["files_analyzed"] == first["files_analyzed"]
    assert second["context_chars"] == first["context_chars"]
    assert second["duration_seconds"] == first["duration_seconds"]


@respx.mock
def test_refresh_re_runs_the_analysis(respx_mock: respx.MockRouter) -> None:
    mock_everything(respx_mock)

    client.post(ANALYZE, json={"github_url": REPO_URL})
    calls_after_first = respx_mock.calls.call_count

    refreshed = client.post(
        ANALYZE, json={"github_url": REPO_URL, "refresh": True}
    ).json()

    assert refreshed["meta"]["cached"] is False
    assert respx_mock.calls.call_count > calls_after_first


@respx.mock
def test_an_invalid_url_is_still_rejected_rather_than_missing_the_cache(
    respx_mock: respx.MockRouter,
) -> None:
    mock_everything(respx_mock)

    response = client.post(ANALYZE, json={"github_url": "not-a-url"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REPOSITORY_URL"


# --- the key covers what changes the answer -----------------------------------


def test_the_cache_key_separates_settings_that_change_the_analysis(
    monkeypatch,
) -> None:
    """Serving a fast result to someone who switched to deep mode would be the
    expensive kind of wrong: it looks like it worked."""
    from app.core.config import Settings, get_settings
    from app.services.interview.store import analysis_cache_key

    def use(**overrides):
        config = Settings(_env_file=None, **overrides)
        monkeypatch.setattr(
            "app.core.config.get_settings", lambda: config, raising=False
        )
        return analysis_cache_key("psf/requests")

    fast = use()
    deep = use(analysis_mode="deep")
    other_model = use(ollama_model="llama3:8b")
    bigger_context = use(max_llm_context_chars=16_000)

    assert len({fast, deep, other_model, bigger_context}) == 4
    assert all(key.startswith("psf/requests|") for key in (fast, deep, other_model))
    get_settings.cache_clear()


def test_the_same_settings_produce_the_same_key() -> None:
    from app.services.interview.store import analysis_cache_key

    assert analysis_cache_key("psf/requests") == analysis_cache_key("psf/requests")
    assert analysis_cache_key("psf/requests") != analysis_cache_key("other/repo")
