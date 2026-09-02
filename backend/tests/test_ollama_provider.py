"""Tests for the Ollama provider.

Every Ollama call is mocked at the httpx transport layer with respx, so the
suite never starts a model, never needs Ollama installed, and runs identically
in CI. No test here consumes a GPU second.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from app.core.config import Settings
from app.core.exceptions import (
    LLMInvalidResponseError,
    LLMModelNotFoundError,
    LLMUnavailableError,
)
from app.services.llm.ollama_provider import OllamaProvider

BASE_URL = "http://localhost:11434"
MODEL = "gemma3:4b"

TAGS_URL = f"{BASE_URL}/api/tags"
GENERATE_URL = f"{BASE_URL}/api/generate"

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def provider(**overrides) -> OllamaProvider:
    values = {
        "ollama_base_url": BASE_URL,
        "ollama_model": MODEL,
        "ollama_timeout_seconds": 5,
    }
    values.update(overrides)
    return OllamaProvider(Settings(**values))


def run(coro):
    """Drive a coroutine without pulling in an async test plugin."""
    return asyncio.run(coro)


def tags_payload(*names: str) -> dict:
    return {"models": [{"name": name} for name in names]}


# --- health check -------------------------------------------------------------


@respx.mock
def test_status_ready_when_server_up_and_model_installed(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json=tags_payload("gemma3:4b", "llama3.1:8b"))
    )

    status = run(provider().status())

    assert status.reachable is True
    assert status.model_available is True
    assert status.ready is True
    assert status.model == MODEL
    assert status.available_models == ["gemma3:4b", "llama3.1:8b"]
    assert status.detail is None


@respx.mock
def test_status_reports_unreachable_server(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(TAGS_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    status = run(provider().status())

    assert status.reachable is False
    assert status.ready is False
    assert "ollama serve" in status.detail
    # Internal exception text must not leak into the user-facing detail.
    assert "connection refused" not in status.detail


@respx.mock
def test_status_never_raises_on_timeout(respx_mock: respx.MockRouter) -> None:
    """Connectivity problems are a normal condition, not an exception."""
    respx_mock.get(TAGS_URL).mock(side_effect=httpx.ReadTimeout("slow"))

    status = run(provider().status())

    assert status.reachable is False


@respx.mock
def test_status_rejects_a_non_ollama_server(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(TAGS_URL).mock(return_value=httpx.Response(200, text="<html>hi</html>"))

    status = run(provider().status())

    assert status.reachable is False
    assert "not an Ollama server" in status.detail


# --- model availability -------------------------------------------------------


@respx.mock
def test_status_reports_missing_model_with_actionable_detail(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json=tags_payload("llama3.1:8b"))
    )

    status = run(provider().status())

    assert status.reachable is True
    assert status.model_available is False
    assert status.ready is False
    assert f"ollama pull {MODEL}" in status.detail
    # The user is told what they *do* have, not just what they lack.
    assert status.available_models == ["llama3.1:8b"]


@respx.mock
def test_bare_model_name_matches_a_tagged_install(
    respx_mock: respx.MockRouter,
) -> None:
    """`OLLAMA_MODEL=gemma3` should match an installed `gemma3:4b`."""
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json=tags_payload("gemma3:4b"))
    )

    status = run(provider(ollama_model="gemma3").status())

    assert status.model_available is True


@respx.mock
def test_empty_model_config_is_not_available(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json=tags_payload("gemma3:4b"))
    )

    assert run(provider(ollama_model="").status()).model_available is False


@respx.mock
def test_is_available_wraps_status(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json=tags_payload(MODEL))
    )

    assert run(provider().is_available()) is True


# --- successful generation ----------------------------------------------------


@respx.mock
def test_generate_json_returns_parsed_object(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": '{"answer": "42"}'})
    )

    result = run(provider().generate_json("q", schema=SCHEMA))

    assert result == {"answer": "42"}


@respx.mock
def test_generate_json_sends_schema_and_deterministic_options(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": '{"answer": "ok"}'})
    )

    run(provider().generate_json("q", schema=SCHEMA, system="be terse"))

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == MODEL
    assert sent["stream"] is False
    assert sent["system"] == "be terse"
    # Constrained decoding: the schema itself is sent, not the string "json".
    assert sent["format"] == SCHEMA
    assert sent["options"]["temperature"] == 0.0
    assert sent["options"]["num_ctx"] == 8192


@respx.mock
def test_generate_returns_raw_text(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": "plain text"})
    )

    assert run(provider().generate("q")) == "plain text"


@respx.mock
def test_trailing_slash_in_base_url_is_handled(respx_mock: respx.MockRouter) -> None:
    """`OLLAMA_BASE_URL=http://localhost:11434/` must not produce `//api/tags`."""
    respx_mock.get(TAGS_URL).mock(
        return_value=httpx.Response(200, json=tags_payload(MODEL))
    )

    status = run(provider(ollama_base_url=f"{BASE_URL}/").status())

    assert status.reachable is True


# --- invalid model output -----------------------------------------------------


@respx.mock
def test_unparseable_json_raises_invalid_response(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": "Sure! Here you go: {oops"})
    )

    with pytest.raises(LLMInvalidResponseError):
        run(provider().generate_json("q", schema=SCHEMA))


@respx.mock
def test_json_array_instead_of_object_is_rejected(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, json={"response": "[1, 2, 3]"})
    )

    with pytest.raises(LLMInvalidResponseError):
        run(provider().generate_json("q", schema=SCHEMA))


@respx.mock
def test_non_json_http_body_is_rejected(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(200, text="not json at all")
    )

    with pytest.raises(LLMInvalidResponseError):
        run(provider().generate_json("q", schema=SCHEMA))


# --- upstream failures --------------------------------------------------------


@respx.mock
def test_model_not_found_raises_actionable_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(GENERATE_URL).mock(
        return_value=httpx.Response(404, json={"error": f'model "{MODEL}" not found'})
    )

    with pytest.raises(LLMModelNotFoundError) as exc_info:
        run(provider().generate_json("q", schema=SCHEMA))

    assert f"ollama pull {MODEL}" in str(exc_info.value)


@respx.mock
def test_unknown_route_404_is_not_a_missing_model(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(GENERATE_URL).mock(return_value=httpx.Response(404, text="404 page not found"))

    with pytest.raises(LLMUnavailableError):
        run(provider().generate_json("q", schema=SCHEMA))


@respx.mock
def test_connection_error_raises_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GENERATE_URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(LLMUnavailableError) as exc_info:
        run(provider().generate_json("q", schema=SCHEMA))

    message = str(exc_info.value)
    assert "ollama serve" in message
    assert "refused" not in message  # no internal detail leaks


@respx.mock
def test_timeout_raises_unavailable_with_a_hint(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GENERATE_URL).mock(side_effect=httpx.ReadTimeout("too slow"))

    with pytest.raises(LLMUnavailableError) as exc_info:
        run(provider().generate_json("q", schema=SCHEMA))

    assert "did not respond in time" in str(exc_info.value)


@respx.mock
def test_server_error_raises_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GENERATE_URL).mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(LLMUnavailableError):
        run(provider().generate_json("q", schema=SCHEMA))
