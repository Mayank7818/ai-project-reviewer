"""Cached analyses and interview sessions.

Two stores, one shape - `app.core.cache.TTLStore`, which is also what the
repository retrieval cache uses. `TTLStore` is re-exported here so the many
existing imports of it keep working.

Why an analysis cache exists at all: Step 4 takes minutes on a local model.
Re-running it for every interview - or for a second look at the same repository
- would make the tool unusable, so the analysis is cached per repository and
every later flow reuses it (Feature 16).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.cache import TTLStore
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "CachedAnalysis",
    "TTLStore",
    "analysis_cache_key",
    "get_analysis_cache",
    "get_session_store",
    "reset_stores",
]


@dataclass
class CachedAnalysis:
    """Everything an interview needs from a completed Step 4 run.

    Holds the derived evidence rather than the whole response: seeds are built
    from these fields, and `evidence_files` is the ground truth that Step 4's
    evidence validator checks citations against.
    """

    repository_full_name: str
    repository: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    #: Step 4 products, reused verbatim.
    structures: list = field(default_factory=list)
    manifests: list = field(default_factory=list)
    security: object = None
    analyzed: dict = field(default_factory=dict)
    domain_counts: dict = field(default_factory=dict)
    technologies: list = field(default_factory=list)
    #: Ground truth for citation validation: every file that was mechanically
    #: analysed, not merely those whose text fitted the analysis prompt.
    evidence_files: dict = field(default_factory=dict)
    readme_path: str | None = None
    #: Step 7. The ranked repository map, cached so repeated job matches and
    #: interviews reuse it instead of re-fetching the tree.
    repository_map: object = None
    #: Step 9. The `AnalysisMeta` of the run that produced this, so a repeat
    #: request for the same repository can be answered from the cache with the
    #: same audit trail rather than re-running a multi-minute analysis.
    meta: object = None


#: Process-wide stores. Sized modestly: this is a local, single-user tool.
_analysis_cache: TTLStore[CachedAnalysis] = TTLStore(max_entries=20, ttl_seconds=3 * 60 * 60)
_session_store: TTLStore = TTLStore(max_entries=50, ttl_seconds=6 * 60 * 60)


def analysis_cache_key(repository_full_name: str) -> str:
    """The cache key for one repository's analysis.

    Keyed by more than the repository, because the same repository does not
    always produce the same analysis. A different model, a different pipeline
    mode or a different context budget is a different piece of work, and serving
    yesterday's fast result to someone who has just switched to deep mode would
    be quietly wrong - the expensive kind of wrong, because it looks like it
    worked.

    The branch is deliberately absent. Only the default branch is ever analysed,
    and which branch that is cannot be known until GitHub has been asked - so
    putting it in the key would mean fetching before every cache lookup, which
    is most of what the cache exists to avoid.
    """
    from app.core.config import get_settings

    settings = get_settings()
    return "|".join(
        [
            repository_full_name,
            settings.ollama_model,
            "deep" if settings.use_multi_stage else "fast",
            str(settings.max_llm_context_chars),
        ]
    )


def get_analysis_cache() -> TTLStore[CachedAnalysis]:
    """The per-repository analysis cache, keyed by `owner/repo`."""
    return _analysis_cache


def get_session_store() -> TTLStore:
    """The interview session store, keyed by session id."""
    return _session_store


def reset_stores() -> None:
    """Clear both stores. Used by tests to guarantee isolation."""
    global _analysis_cache, _session_store
    _analysis_cache = TTLStore(max_entries=20, ttl_seconds=3 * 60 * 60)
    _session_store = TTLStore(max_entries=50, ttl_seconds=6 * 60 * 60)
