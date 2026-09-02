"""Response schema for the local model status endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LLMStatusResponse(BaseModel):
    """Whether the configured local model can serve a request."""

    ready: bool = Field(..., description="True when reachable AND the model is installed.")
    reachable: bool = Field(..., description="Is the Ollama server responding?")
    model_available: bool = Field(..., description="Is the configured model installed?")
    model: str = Field(..., description="Configured model name.")
    available_models: list[str] = Field(
        default_factory=list, description="Models currently installed in Ollama."
    )
    detail: str | None = Field(
        None, description="Actionable explanation when not ready."
    )

    model_config = {"protected_namespaces": ()}
