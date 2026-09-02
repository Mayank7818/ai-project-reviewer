"""Local LLM access layer.

Everything in the application talks to `LLMProvider` (an interface), never to
Ollama directly. That keeps the provider swappable: pointing at a different
local runtime later means adding one module here and changing one factory line,
with no changes anywhere else in the codebase.

    from app.services.llm import get_llm_provider
    provider = get_llm_provider()

Nothing here performs generation yet - see `ollama_provider.py`.
"""

from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

__all__ = ["LLMProvider", "get_llm_provider"]
