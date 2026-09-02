"""Project analysis.

Combines the GitHub retrieval layer with the local LLM layer:

    context_builder.py  retrieval -> bounded prompt context   no I/O
    service.py          orchestrates retrieve -> prompt -> validate

Only `service.py` is used by the API layer.
"""

from app.services.analysis.service import AnalysisService, get_analysis_service

__all__ = ["AnalysisService", "get_analysis_service"]
