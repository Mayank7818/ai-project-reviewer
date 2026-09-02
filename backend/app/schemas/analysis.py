"""Request and response models for AI project analysis.

Two gates protect the output. Decoding is constrained by a JSON Schema, so the
model cannot emit a malformed object; these models are the second gate, because
a *conforming* object can still be unusable - a score of 900, an empty summary,
a hundred duplicate technologies.

Evidence citations are validated separately in `services/analysis/evidence.py`
against the files that were actually sent, before they ever reach these models.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

SCORE_MIN, SCORE_MAX = 0, 100

#: Defensive caps, so a runaway model cannot bloat the response.
MAX_LIST_ITEMS = 15
MAX_FINDINGS = 12
MAX_EVIDENCE_PER_FINDING = 6
MAX_ITEM_CHARS = 400
MAX_TEXT_CHARS = 2_000

SEVERITIES = ("low", "medium", "high")


def _clean_list(values: list[str], limit: int = MAX_LIST_ITEMS) -> list[str]:
    """Trim, de-duplicate (case-insensitively) and cap a list of strings."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = " ".join(str(value).split())[:MAX_ITEM_CHARS]
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _coerce_score(value: object) -> int:
    """Turn whatever the model produced into an integer inside 0-100.

    Small local models occasionally emit "85", 8.5, or 850. Clamping is more
    useful than rejecting an entire analysis over one malformed number.
    """
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return SCORE_MIN
    return max(SCORE_MIN, min(SCORE_MAX, int(round(number))))


# --- request ------------------------------------------------------------------


class AnalyzeProjectRequest(BaseModel):
    """Body of `POST /api/v1/analyze-project`."""

    github_url: str = Field(
        ...,
        min_length=1,
        max_length=300,
        description="Public GitHub repository URL.",
        examples=["https://github.com/tiangolo/fastapi"],
    )
    refresh: bool = Field(
        False,
        description=(
            "Re-run the analysis even if a recent one is cached for this "
            "repository. Costs another few minutes of local inference."
        ),
    )


# --- evidence -----------------------------------------------------------------


class Evidence(BaseModel):
    """A citation to a real file, and optionally a real line range.

    `line_start`/`line_end` are null whenever exact lines could not be
    established. They are never invented: a range that does not exist in the
    file actually sent is cleared during validation.
    """

    file: str = Field(..., description="Repository-relative path that was analysed.")
    line_start: int | None = Field(None, ge=1)
    line_end: int | None = Field(None, ge=1)
    reason: str = Field("", max_length=MAX_ITEM_CHARS)


class Finding(BaseModel):
    """One observation, its severity, and the evidence supporting it."""

    finding: str = Field(..., max_length=MAX_ITEM_CHARS)
    severity: str = Field("medium")
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in SEVERITIES else "medium"

    @field_validator("evidence", mode="before")
    @classmethod
    def _cap(cls, value: object) -> list:
        return value[:MAX_EVIDENCE_PER_FINDING] if isinstance(value, list) else []


class SecurityIssue(Finding):
    """A security finding. Same shape as `Finding`, named for clarity."""


# --- sections -----------------------------------------------------------------


class ArchitectureSection(BaseModel):
    """How the project is put together, and what says so."""

    summary: str = Field("", max_length=MAX_TEXT_CHARS)
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> str:
        return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]

    @field_validator("evidence", mode="before")
    @classmethod
    def _cap(cls, value: object) -> list:
        return value[:MAX_LIST_ITEMS] if isinstance(value, list) else []


class ScoredFindings(BaseModel):
    """A 0-100 score, the reasoning, and the findings behind it.

    Used for code quality, performance and documentation. `reason` is retained
    from the Step 3 schema so existing consumers keep working.
    """

    score: int = Field(..., ge=SCORE_MIN, le=SCORE_MAX)
    reason: str = Field("", max_length=MAX_TEXT_CHARS)
    findings: list[Finding] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _clamp(cls, value: object) -> int:
        return _coerce_score(value)

    @field_validator("reason", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> str:
        return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]

    @field_validator("findings", mode="before")
    @classmethod
    def _cap(cls, value: object) -> list:
        return value[:MAX_FINDINGS] if isinstance(value, list) else []


class SecuritySection(BaseModel):
    """Security split by how much the evidence actually supports.

    The three buckets are the point of the section: a missing best practice
    belongs in `no_evidence`, never in `confirmed_issues`.
    """

    score: int = Field(..., ge=SCORE_MIN, le=SCORE_MAX)
    confirmed_issues: list[SecurityIssue] = Field(
        default_factory=list,
        description="Observed in the code, with a file and usually a line.",
    )
    potential_risks: list[SecurityIssue] = Field(
        default_factory=list,
        description="Risky shapes whose severity depends on context.",
    )
    no_evidence: list[str] = Field(
        default_factory=list,
        description="Checked for and not found. Absence, stated explicitly.",
    )
    #: Retained from the Step 3 schema so existing clients keep working.
    issues: list[str] = Field(
        default_factory=list,
        description="Flat titles of confirmed issues (backwards compatibility).",
    )

    @field_validator("score", mode="before")
    @classmethod
    def _clamp(cls, value: object) -> int:
        return _coerce_score(value)

    @field_validator("confirmed_issues", "potential_risks", mode="before")
    @classmethod
    def _cap(cls, value: object) -> list:
        return value[:MAX_FINDINGS] if isinstance(value, list) else []

    @field_validator("no_evidence", "issues", mode="before")
    @classmethod
    def _clean(cls, value: object) -> list[str]:
        return _clean_list(value if isinstance(value, list) else [])


class TestingSection(BaseModel):
    """What the retrieved files show about testing."""

    score: int = Field(..., ge=SCORE_MIN, le=SCORE_MAX)
    reason: str = Field("", max_length=MAX_TEXT_CHARS)
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _clamp(cls, value: object) -> int:
        return _coerce_score(value)

    @field_validator("reason", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> str:
        return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]

    @field_validator("evidence", mode="before")
    @classmethod
    def _cap(cls, value: object) -> list:
        return value[:MAX_LIST_ITEMS] if isinstance(value, list) else []


class DependencySummary(BaseModel):
    """Dependencies declared by one manifest.

    Derived entirely from parsing the manifest, never from the model. Per
    Feature 7 this makes no claim about whether any version is vulnerable.
    """

    file: str
    ecosystem: str
    count: int = 0
    names: list[str] = Field(default_factory=list)


# --- top level ----------------------------------------------------------------


class ProjectAnalysis(BaseModel):
    """The validated analysis object."""

    project_summary: str = Field("", max_length=MAX_TEXT_CHARS)
    technologies: list[str] = Field(default_factory=list)
    architecture: ArchitectureSection
    code_quality: ScoredFindings
    security: SecuritySection
    performance: ScoredFindings
    documentation: ScoredFindings
    testing: TestingSection
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    overall_score: int = Field(..., ge=SCORE_MIN, le=SCORE_MAX)

    @field_validator("overall_score", mode="before")
    @classmethod
    def _clamp(cls, value: object) -> int:
        return _coerce_score(value)

    @field_validator("technologies", "strengths", "weaknesses", mode="before")
    @classmethod
    def _clean(cls, value: object) -> list[str]:
        return _clean_list(value if isinstance(value, list) else [])

    @field_validator("project_summary", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> str:
        return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]


class FileRecord(BaseModel):
    """One file the model was shown, and how it was classified."""

    path: str
    domain: str = Field(..., description="documentation | frontend | backend | …")
    truncated: bool = False
    lines_shown: int = Field(
        0, description="Lines sent to the model. 0 means the file was sent whole."
    )
    lines_total: int = Field(0, description="Lines in the original file.")


class ContextSnippet(BaseModel):
    """One extract sent to the model, at its true position in the file.

    `line_start`/`line_end` are the file's own line numbers, never renumbered,
    so a citation landing inside this range refers to the real code.
    """

    path: str
    line_start: int
    line_end: int
    reason: str = Field(..., description="Why this range was selected, e.g. 'class Session'.")
    chars: int = 0


class OmittedFile(BaseModel):
    """A retrieved file that did not make it into the prompt, and why."""

    path: str
    reason: str


class AnalysisMeta(BaseModel):
    """How the analysis was produced.

    Returned so the result is auditable rather than a black box: which model
    ran, which stages completed, how much of the repository it actually saw,
    what was left out, and how much of its own output failed validation.
    """

    model: str = Field(..., description="Ollama model that produced the analysis.")
    stages_completed: list[str] = Field(default_factory=list)
    files_analyzed: list[FileRecord] = Field(default_factory=list)
    files_truncated: list[str] = Field(default_factory=list)
    files_omitted: list[OmittedFile] = Field(default_factory=list)
    snippets: list[ContextSnippet] = Field(
        default_factory=list,
        description="Extracts selected for the prompt, with original line ranges.",
    )
    domain_counts: dict[str, int] = Field(default_factory=dict)
    dependencies: list[DependencySummary] = Field(default_factory=list)
    readme_included: bool = False
    context_chars: int = 0
    context_limit: int = Field(0, description="Configured ceiling the context fits inside.")
    duration_seconds: float = 0.0
    cached: bool = Field(
        False,
        description=(
            "True when this analysis was served from the cache rather than "
            "re-run. `duration_seconds` then describes the original run."
        ),
    )
    evidence_dropped: int = Field(
        0, description="Citations discarded because they referenced unsent files."
    )
    line_numbers_cleared: int = Field(
        0, description="Line ranges cleared because they did not exist."
    )

    model_config = {"protected_namespaces": ()}


class RepositorySummary(BaseModel):
    """Just enough repository identity to render alongside the analysis."""

    full_name: str
    owner: str
    description: str | None = None
    html_url: str
    primary_language: str | None = None
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    default_branch: str = "main"
    license: str | None = None


class AnalyzeProjectResponse(BaseModel):
    """Full response for `POST /api/v1/analyze-project`."""

    repository: RepositorySummary
    analysis: ProjectAnalysis
    meta: AnalysisMeta
