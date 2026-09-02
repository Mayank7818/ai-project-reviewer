"""Provider selection.

One place decides which `LLMProvider` implementation the application uses, so
adding a second local runtime later is a change to this file alone.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.services.llm.base import LLMProvider
from app.services.llm.ollama_provider import OllamaProvider


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    """Return the configured LLM provider (cached for the process lifetime).

    Usable as a FastAPI dependency:  provider: LLMProvider = Depends(get_llm_provider)
    """
    return OllamaProvider(get_settings())
