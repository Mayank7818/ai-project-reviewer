"""Repository retrieval endpoint.

Thin by design: parse the request, call the service, map the result onto the
response schema. All error handling is delegated - the service raises typed
`AppError`s and the handlers registered in `main.py` turn them into the shared
JSON error shape.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.config import Settings, get_settings
from app.schemas.repository import (
    AnalyzeRepositoryRequest,
    AnalyzeRepositoryResponse,
    RepositoryFile,
    RepositoryInfo,
    RepositoryStructure,
    RetrievalSummary,
)
from app.services.github import GitHubService, get_github_service
from app.services.github.service import RetrievalResult

router = APIRouter(tags=["repository"])


@router.post(
    "/analyze-repository",
    response_model=AnalyzeRepositoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a public GitHub repository",
    responses={
        404: {"description": "Repository not found or not public."},
        422: {"description": "The URL is not a valid GitHub repository URL."},
        429: {"description": "GitHub API rate limit exceeded."},
        502: {"description": "GitHub is unreachable or returned an error."},
    },
)
async def analyze_repository(
    payload: AnalyzeRepositoryRequest,
    service: GitHubService = Depends(get_github_service),
    settings: Settings = Depends(get_settings),
) -> AnalyzeRepositoryResponse:
    """Fetch metadata, README, structure and a bounded selection of files.

    Retrieval only. No AI analysis is performed and no result is invented - the
    `analysis` field is always null at this stage.
    """
    result = await service.retrieve(payload.github_url)
    return _to_response(result, settings, include_content=payload.include_content)


def _to_response(
    result: RetrievalResult, settings: Settings, *, include_content: bool = True
) -> AnalyzeRepositoryResponse:
    """Map the service result onto the public response schema.

    Field-by-field on purpose: GitHub's raw payload is never forwarded, so the
    contract stays stable and nothing unintended is exposed.
    """
    raw = result.repository
    owner = (raw.get("owner") or {}).get("login", "")
    license_info = raw.get("license") or {}

    repository = RepositoryInfo(
        name=raw.get("name", ""),
        full_name=raw.get("full_name", ""),
        owner=owner,
        description=raw.get("description"),
        default_branch=raw.get("default_branch", "main"),
        stars=raw.get("stargazers_count", 0),
        forks=raw.get("forks_count", 0),
        open_issues=raw.get("open_issues_count", 0),
        primary_language=raw.get("language"),
        languages=result.languages,
        topics=raw.get("topics") or [],
        license=license_info.get("spdx_id") or license_info.get("name"),
        html_url=raw.get("html_url", ""),
        is_fork=bool(raw.get("fork")),
        is_archived=bool(raw.get("archived")),
        size_kb=raw.get("size", 0),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        pushed_at=raw.get("pushed_at"),
    )

    # A caller that only wants to know *what* was retrieved gets exactly that.
    # The UI asks for a summary while it waits for the analysis, and shipping a
    # whole repository's text to the browser to render a file count would be
    # both wasteful and more of the repository than the browser needs to hold.
    files = [
        RepositoryFile(
            path=item.path,
            size_bytes=item.size_bytes,
            category=item.category,
            content=item.content if include_content else "",
            truncated=item.truncated,
            redacted=item.redacted,
        )
        for item in result.files
    ]

    structure = RepositoryStructure(
        total_entries=result.tree_total_entries,
        returned_entries=len(result.tree_paths),
        truncated=result.tree_truncated,
        paths=result.tree_paths,
    )

    retrieval = RetrievalSummary(
        files_retrieved=len(files),
        total_content_bytes=sum(len(item.content) for item in files),
        skipped=result.skipped,
        limits={
            "max_files": settings.effective_max_files,
            "max_file_size_bytes": settings.max_file_size_bytes,
            "max_total_content_bytes": settings.max_total_content_bytes,
            "max_tree_entries_returned": settings.max_tree_entries_returned,
        },
        # Reports *whether* a token was used, never the token itself.
        authenticated=bool(settings.github_token),
    )

    return AnalyzeRepositoryResponse(
        repository=repository,
        readme=result.readme,
        structure=structure,
        files=files,
        retrieval=retrieval,
        analysis=None,
    )
