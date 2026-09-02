"""Ollama implementation of `LLMProvider`.

Ollama runs entirely on the local machine and exposes an HTTP API (default
http://localhost:11434), so no paid or cloud AI API is involved anywhere in this
project. The frontend never talks to Ollama - only this backend does.

Endpoints used:
    GET  /api/tags      which models are installed locally
    POST /api/generate  single-turn completion

Structured output is obtained with Ollama's `format` parameter: passing a JSON
Schema constrains decoding, so the model emits a conforming object by
construction rather than being asked nicely and hoped for.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import (
    LLMInvalidResponseError,
    LLMModelNotFoundError,
    LLMUnavailableError,
)
from app.core.logging import get_logger
from app.services.llm.base import LLMProvider, LLMStatus

logger = get_logger(__name__)

#: Short timeout for the cheap "is it up?" probe. The full generation timeout is
#: far longer, but a status check must never make the UI wait.
_STATUS_TIMEOUT_SECONDS = 5.0


class OllamaProvider(LLMProvider):
    """Talks to a locally running Ollama server over its HTTP API."""

    TAGS_ENDPOINT = "/api/tags"
    GENERATE_ENDPOINT = "/api/generate"

    def __init__(self, settings: Settings) -> None:
        # rstrip so a trailing slash in the env var cannot produce '//api/...'.
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model
        self._timeout = settings.ollama_timeout_seconds
        self._num_ctx = settings.ollama_num_ctx
        self._temperature = settings.ollama_temperature
        self._seed = settings.ollama_seed

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    # --- availability --------------------------------------------------------

    async def status(self) -> LLMStatus:
        """Check that Ollama responds and that the configured model is present.

        Never raises. Every failure mode becomes a described `LLMStatus`, since
        "Ollama is not running" is a normal thing for a local-first app to
        encounter and the user needs to be told which fix applies.
        """
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=_STATUS_TIMEOUT_SECONDS
            ) as client:
                response = await client.get(self.TAGS_ENDPOINT)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.info("Ollama not reachable at %s (%s)", self._base_url, type(exc).__name__)
            return LLMStatus(
                reachable=False,
                model_available=False,
                model=self._model,
                detail=(
                    f"No Ollama server responded at {self._base_url}. "
                    "Start it with `ollama serve`."
                ),
            )
        except ValueError:
            return LLMStatus(
                reachable=False,
                model_available=False,
                model=self._model,
                detail=f"The server at {self._base_url} is not an Ollama server.",
            )

        available = sorted(
            model.get("name", "")
            for model in payload.get("models", [])
            if model.get("name")
        )
        model_available = self._matches_installed(self._model, available)

        return LLMStatus(
            reachable=True,
            model_available=model_available,
            model=self._model,
            available_models=available,
            detail=(
                None
                if model_available
                else (
                    f"Model '{self._model}' is not installed. Pull it with "
                    f"`ollama pull {self._model}`, or set OLLAMA_MODEL to one of "
                    f"the installed models."
                )
            ),
        )

    @staticmethod
    def _matches_installed(model: str, available: list[str]) -> bool:
        """Match a configured name against installed model tags.

        Ollama reports fully-qualified tags ("gemma3:4b"). Users routinely
        configure the bare name ("gemma3"), which Ollama itself resolves to the
        `:latest` tag - so accept an exact match or a `name:` prefix match.
        """
        if not model:
            return False
        if model in available:
            return True
        return any(installed.split(":")[0] == model for installed in available)

    async def is_available(self) -> bool:
        return (await self.status()).ready

    # --- generation ----------------------------------------------------------

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Return the model's raw text completion."""
        payload = self._build_payload(prompt, system=system)
        response = await self._post_generate(payload)
        return response.get("response", "")

    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system: str | None = None,
    ) -> dict[str, Any]:
        """Return a parsed JSON object, decoding constrained to `schema`."""
        payload = self._build_payload(prompt, system=system)
        # Passing a JSON Schema (rather than the string "json") makes Ollama
        # constrain sampling to the schema's grammar.
        payload["format"] = schema

        response = await self._post_generate(payload)
        _log_generation_cost(response)
        raw = response.get("response", "")

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            # Log a bounded excerpt for debugging; never return it to the client.
            logger.warning("Ollama returned unparseable JSON: %r", raw[:400])
            raise LLMInvalidResponseError() from exc

        if not isinstance(parsed, dict):
            logger.warning("Ollama returned JSON of type %s, expected object", type(parsed).__name__)
            raise LLMInvalidResponseError()

        return parsed

    def _build_payload(self, prompt: str, *, system: str | None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": self._temperature,
            "num_ctx": self._num_ctx,
        }
        # A negative seed means "unset": Ollama picks one per request, so runs
        # differ even at temperature 0. Pin it to make a run reproducible.
        if self._seed >= 0:
            options["seed"] = self._seed

        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            payload["system"] = system
        return payload

    async def _post_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to /api/generate, mapping every failure to a typed error.

        No internal exception text or stack detail ever reaches the caller - the
        raised errors carry fixed, user-facing messages.
        """
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=httpx.Timeout(self._timeout)
            ) as client:
                response = await client.post(self.GENERATE_ENDPOINT, json=payload)
        except httpx.TimeoutException as exc:
            logger.warning("Ollama generation timed out after %ss", self._timeout)
            raise LLMUnavailableError(
                "The local model did not respond in time. A smaller model, or a "
                "higher OLLAMA_TIMEOUT_SECONDS, may help."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Ollama request failed (%s)", type(exc).__name__)
            raise LLMUnavailableError(
                f"Could not reach the Ollama server at {self._base_url}. "
                "Start it with `ollama serve`."
            ) from exc

        if response.status_code == 404:
            # Ollama uses 404 both for "no such model" and "no such route".
            body = response.text.lower()
            if "model" in body:
                logger.warning("Ollama reports model '%s' not found", self._model)
                raise LLMModelNotFoundError(
                    f"Model '{self._model}' is not installed in Ollama. Run "
                    f"`ollama pull {self._model}`, or set OLLAMA_MODEL to an "
                    "installed model."
                )
            raise LLMUnavailableError(
                "The Ollama server did not recognise the request. Check that "
                "OLLAMA_BASE_URL points at a supported Ollama version."
            )

        if response.status_code >= 400:
            logger.warning("Ollama returned status %s", response.status_code)
            raise LLMUnavailableError(
                f"The Ollama server returned an error ({response.status_code})."
            )

        try:
            return response.json()
        except ValueError as exc:
            logger.warning("Ollama returned a non-JSON body")
            raise LLMInvalidResponseError() from exc


def _log_generation_cost(response: dict[str, Any]) -> None:
    """Log what the model actually spent, using Ollama's own counters.

    Worth having permanently, not just while tuning: on CPU the two halves of a
    request cost very differently. Prompt tokens are processed in parallel and
    are comparatively cheap; output tokens are generated one at a time and are
    not. A request that looks slow because of a "big prompt" is usually slow
    because of a long answer, and these numbers say which.
    """
    prompt_tokens = response.get("prompt_eval_count") or 0
    output_tokens = response.get("eval_count") or 0
    prompt_ns = response.get("prompt_eval_duration") or 0
    output_ns = response.get("eval_duration") or 0
    load_ns = response.get("load_duration") or 0

    if not (prompt_tokens or output_tokens):
        return  # a mocked or older server that does not report counters

    def rate(tokens: int, nanoseconds: int) -> str:
        seconds = nanoseconds / 1e9
        return f"{tokens / seconds:.1f} tok/s" if seconds > 0 else "n/a"

    logger.info(
        "Ollama cost: prompt %d tok in %.1fs (%s), output %d tok in %.1fs (%s), load %.1fs",
        prompt_tokens,
        prompt_ns / 1e9,
        rate(prompt_tokens, prompt_ns),
        output_tokens,
        output_ns / 1e9,
        rate(output_tokens, output_ns),
        load_ns / 1e9,
    )
