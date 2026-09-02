"""AI project analysis endpoint.

Thin by design: parse the request, call the service, map the outcome onto the
response schema. Errors are raised as typed `AppError`s by the layers below and
turned into the shared JSON error shape by the handlers in `main.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.logging import get_logger
from app.schemas.analysis import (
    AnalysisMeta,
    AnalyzeProjectRequest,
    AnalyzeProjectResponse,
    ProjectAnalysis,
    RepositorySummary,
)
from app.services.analysis import AnalysisService, get_analysis_service
from app.services.analysis.service import AnalysisOutcome
from app.services.interview.service import cache_outcome
from app.services.interview.store import (
    CachedAnalysis,
    analysis_cache_key,
    get_analysis_cache,
)

logger = get_logger(__name__)

router = APIRouter(tags=["analysis"])


@router.post(
    "/analyze-project",
    response_model=AnalyzeProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyse a public GitHub repository with the local model",
    responses={
        404: {"description": "Repository not found or not public."},
        422: {"description": "The URL is not a valid GitHub repository URL."},
        429: {"description": "GitHub API rate limit exceeded."},
        502: {"description": "GitHub unreachable, or the model returned invalid output."},
        503: {"description": "Ollama is not running, or the model is not installed."},
    },
)
async def analyze_project(
    payload: AnalyzeProjectRequest,
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalyzeProjectResponse:
    """Retrieve a repository and analyse its real contents with local Ollama.

    The analysis is produced entirely on this machine. Facts are established
    mechanically first (classification, extracted structure, declared
    dependencies, security scan); the model reasons over those facts, and every
    citation it makes is validated against the files it was actually shown.
    A run that cannot produce a valid analysis fails loudly rather than
    returning plausible filler.
    """
    # A repeat look at the same repository is answered from the cache. On a
    # local model an analysis costs minutes and about twenty GitHub requests, so
    # re-running one the user has already seen is the most expensive thing this
    # application could do by accident. `refresh` is the way to ask for it
    # anyway.
    if not payload.refresh:
        cached = _cached_response(payload.github_url)
        if cached is not None:
            return cached

    outcome = await service.analyze(payload.github_url)

    # Cache the evidence so a following interview reuses it instead of paying
    # for another multi-minute analysis (Step 5, Feature 16).
    cache_outcome(outcome)

    return _to_response(outcome)


def _cached_response(github_url: str) -> AnalyzeProjectResponse | None:
    """Rebuild a response from the analysis cache, or None if there is none.

    A malformed URL is left for the service to reject, so the caller still gets
    the precise validation error rather than a cache miss.
    """
    from app.core.exceptions import InvalidRepositoryUrlError
    from app.services.github.url_parser import parse_repo_url

    try:
        ref = parse_repo_url(github_url)
    except InvalidRepositoryUrlError:
        return None

    cached: CachedAnalysis | None = get_analysis_cache().get(
        analysis_cache_key(ref.full_name)
    )
    if cached is None or not cached.analysis or cached.meta is None:
        return None

    logger.info("Serving cached analysis for %s (no model call)", ref.full_name)

    # A copy rather than a mutation: the cached entry is shared with the
    # interview and job flows, and must not be marked by being read.
    meta = AnalysisMeta.model_validate(cached.meta).model_copy(update={"cached": True})
    return AnalyzeProjectResponse(
        repository=_repository_summary(cached.repository),
        analysis=ProjectAnalysis.model_validate(cached.analysis),
        meta=meta,
    )


def _repository_summary(raw: dict) -> RepositorySummary:
    """Map raw GitHub metadata onto the public repository shape."""
    license_info = raw.get("license") or {}
    return RepositorySummary(
        full_name=raw.get("full_name", ""),
        owner=(raw.get("owner") or {}).get("login", ""),
        description=raw.get("description"),
        html_url=raw.get("html_url", ""),
        primary_language=raw.get("language"),
        stars=raw.get("stargazers_count", 0),
        forks=raw.get("forks_count", 0),
        open_issues=raw.get("open_issues_count", 0),
        default_branch=raw.get("default_branch", "main"),
        license=license_info.get("spdx_id") or license_info.get("name"),
    )


def _to_response(outcome: AnalysisOutcome) -> AnalyzeProjectResponse:
    """Map the service outcome onto the public response schema."""
    return AnalyzeProjectResponse(
        repository=_repository_summary(outcome.retrieval.repository),
        analysis=outcome.analysis,
        meta=outcome.meta,
    )
