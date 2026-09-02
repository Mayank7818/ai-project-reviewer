"""The provider-agnostic LLM interface.

Defined as an abstract base class so any future local runtime can be dropped in
by implementing these methods. Callers depend on this contract only, which is
what makes the model and the provider swappable from configuration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMStatus:
    """A report on whether the provider can actually serve a request.

    Deliberately a value object rather than a bare bool: the frontend needs to
    distinguish "the server is down" from "the server is up but the model is not
    pulled", because the fix is different in each case.
    """

    reachable: bool
    model_available: bool
    model: str
    available_models: list[str] = field(default_factory=list)
    detail: str | None = None

    @property
    def ready(self) -> bool:
        return self.reachable and self.model_available


class LLMProvider(ABC):
    """Minimal contract every LLM backend must satisfy."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model this provider is currently configured to use."""

    @abstractmethod
    async def status(self) -> LLMStatus:
        """Report reachability and model availability.

        Must never raise: connectivity problems are an expected condition, and
        the caller decides how to degrade.
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """Convenience wrapper over `status()`. Must never raise."""

    @abstractmethod
    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Return the model's raw completion for `prompt`.

        Raises:
            LLMUnavailableError: the provider could not be reached.
            LLMModelNotFoundError: the configured model is not installed.
        """

    @abstractmethod
    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system: str | None = None,
    ) -> dict[str, Any]:
        """Return a parsed JSON object constrained to `schema`.

        Implementations should use whatever native structured-output support the
        runtime offers (Ollama constrains decoding to a JSON Schema) so that a
        parseable object is produced by construction rather than by luck.

        Raises:
            LLMUnavailableError, LLMModelNotFoundError,
            LLMInvalidResponseError: the reply could not be parsed as JSON.
        """
