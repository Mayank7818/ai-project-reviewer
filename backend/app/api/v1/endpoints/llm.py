"""Local model status endpoint.

Lets the UI tell the difference between "Ollama is not running" and "Ollama is
running but the configured model is not installed" *before* a user waits through
an analysis, since the fix differs in each case.

Reports names and booleans only - never a URL containing credentials, and never
any token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas.llm import LLMStatusResponse
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

router = APIRouter(tags=["llm"])


@router.get("/llm/status", response_model=LLMStatusResponse, summary="Local model status")
async def llm_status(
    provider: LLMProvider = Depends(get_llm_provider),
) -> LLMStatusResponse:
    """Report whether the local model is ready to run an analysis.

    Never raises: an unreachable Ollama is a normal condition for a local-first
    application, and is reported as data rather than as an error.
    """
    status = await provider.status()
    return LLMStatusResponse(
        ready=status.ready,
        reachable=status.reachable,
        model_available=status.model_available,
        model=status.model,
        available_models=status.available_models,
        detail=status.detail,
    )
