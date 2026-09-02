"""Request and response models for repository retrieval.

These models are the API contract. Building the response by explicitly mapping
fields (rather than forwarding GitHub's payload) means the frontend receives a
stable, documented shape and nothing unexpected can leak through when GitHub
changes its own response.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AnalyzeRepositoryRequest(BaseModel):
    """Body of `POST /api/v1/analyze-repository`."""

    github_url: str = Field(
        ...,
        min_length=1,
        max_length=300,
        description="Public GitHub repository URL.",
        examples=["https://github.com/tiangolo/fastapi"],
    )
    include_content: bool = Field(
        True,
        description=(
            "Include each file's text. Set false for a summary - paths, sizes "
            "and categories only - when the caller just needs to know what was "
            "retrieved."
        ),
    )


class RepositoryInfo(BaseModel):
    """Curated repository metadata - an explicit subset of GitHub's payload."""

    name: str
    full_name: str
    owner: str
    description: str | None = None
    default_branch: str
    stars: int
    forks: int
    open_issues: int
    primary_language: str | None = None
    languages: dict[str, int] = Field(
        default_factory=dict, description="Bytes of code per language."
    )
    topics: list[str] = Field(default_factory=list)
    license: str | None = None
    html_url: str
    is_fork: bool = False
    is_archived: bool = False
    size_kb: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    pushed_at: datetime | None = None


class RepositoryFile(BaseModel):
    """One retrieved file, after truncation and secret redaction."""

    path: str
    size_bytes: int
    category: str = Field(
        ..., description="manifest | entrypoint | config | source | docs | other"
    )
    content: str
    truncated: bool = Field(
        False, description="True if content was cut at the per-file size limit."
    )
    redacted: bool = Field(
        False, description="True if credential-shaped values were masked."
    )


class RepositoryStructure(BaseModel):
    """Paths only - never content - so the client can render the file tree."""

    total_entries: int = Field(..., description="Entries in the full Git tree.")
    returned_entries: int = Field(..., description="Paths included below.")
    truncated: bool = Field(
        False, description="True if GitHub itself truncated the tree listing."
    )
    paths: list[str] = Field(default_factory=list)


class RetrievalSummary(BaseModel):
    """An honest account of what was fetched, what was skipped, and the limits.

    Exposed so the retrieval is auditable rather than a black box - the user can
    see that nothing was silently dropped.
    """

    files_retrieved: int
    total_content_bytes: int
    skipped: dict[str, int] = Field(
        default_factory=dict,
        description="Skip reason -> file count (e.g. binary_or_media, too_large).",
    )
    limits: dict[str, int] = Field(
        default_factory=dict, description="Limits in force for this request."
    )
    authenticated: bool = Field(
        ..., description="Whether a server-side GITHUB_TOKEN was used."
    )


class AnalyzeRepositoryResponse(BaseModel):
    """Full response for `POST /api/v1/analyze-repository`.

    Contains retrieved data only. No AI analysis, score or interview question is
    produced at this step, and none is fabricated.
    """

    repository: RepositoryInfo
    readme: str | None = Field(
        None, description="Decoded README text, or null if the repo has none."
    )
    structure: RepositoryStructure
    files: list[RepositoryFile]
    retrieval: RetrievalSummary
    analysis: None = Field(
        None,
        description="Always null until the analysis step is implemented.",
    )
