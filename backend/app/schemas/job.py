"""Request and response models for job intelligence.

Enums are used for every closed set - importance, status, question type - so an
invalid value is a validation error rather than a string that quietly flows into
the UI.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.schemas.analysis import Evidence
from app.schemas.interview import (
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    AnsweredQuestion,
    InterviewSummary,
)
from app.services.job import parser as parser_module
from app.services.job import scoring as scoring_module
from app.services.job.matcher import STATUS_CREDIT

MAX_TEXT_CHARS = 2_000
MAX_ITEM_CHARS = 400
MAX_LIST_ITEMS = 20


def _clean_list(values: object, limit: int = MAX_LIST_ITEMS) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = " ".join(str(value).split())[:MAX_ITEM_CHARS]
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


# --- enums --------------------------------------------------------------------


class SkillImportance(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    NICE_TO_HAVE = "nice_to_have"
    RESPONSIBILITY = "responsibility"


class SkillStatus(str, Enum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    NOT_VERIFIED = "not_verified"
    CONTRADICTED = "contradicted"


class SkillCategory(str, Enum):
    LANGUAGE = "language"
    FRAMEWORK = "framework"
    DATABASE = "database"
    CLOUD = "cloud"
    DEVOPS = "devops"
    AI_ML = "ai_ml"
    TESTING = "testing"
    CONCEPT = "concept"
    SOFT_SKILL = "soft_skill"


class JobQuestionType(str, Enum):
    PROJECT_EVIDENCE = "project_evidence"
    JOB_REQUIREMENT = "job_requirement"
    GAP = "gap"
    ARCHITECTURE = "architecture"
    SCENARIO = "scenario"


# --- requests -----------------------------------------------------------------


class ParseJobRequest(BaseModel):
    """Body of `POST /api/v1/job/parse`."""

    job_description: str = Field(
        ...,
        min_length=1,
        max_length=parser_module.MAX_LENGTH,
        description="The job posting text. Processed locally; never logged.",
    )
    company: str = Field("", max_length=200, description="Optional.")
    job_title: str = Field("", max_length=200, description="Optional.")


class MatchJobRequest(ParseJobRequest):
    """Body of `POST /api/v1/job/match`."""

    github_url: str = Field(..., min_length=1, max_length=300)
    target_role: str = Field("software_developer")


class StartJobInterviewRequest(MatchJobRequest):
    """Body of `POST /api/v1/job/interview/start`."""

    difficulty: str = Field("mixed", description="easy | medium | hard | mixed")
    question_count: int = Field(10, ge=MIN_QUESTIONS, le=MAX_QUESTIONS)


# --- parsed job ---------------------------------------------------------------


class JobRequirementModel(BaseModel):
    """One skill the job asks for."""

    skill: str
    category: SkillCategory
    importance: SkillImportance
    context: str = Field("", max_length=MAX_ITEM_CHARS)
    alternative_group: str | None = Field(
        None, description="Requirements sharing a group are alternatives."
    )
    counts_towards_score: bool = Field(
        True, description="False for responsibilities and skills code cannot evidence."
    )


class JobDescriptionModel(BaseModel):
    """A parsed job description."""

    title: str = ""
    seniority: str = ""
    company: str = ""
    requirements: list[JobRequirementModel] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    enriched: bool = Field(
        False, description="True when model enrichment ran successfully."
    )
    #: Length only. The description itself is never echoed back or logged.
    source_chars: int = 0

    @field_validator("responsibilities", "soft_skills", mode="before")
    @classmethod
    def _clean(cls, value: object) -> list[str]:
        return _clean_list(value)


# --- match --------------------------------------------------------------------


class SkillMatchModel(BaseModel):
    """One requirement, judged against the repository."""

    skill: str
    category: SkillCategory
    importance: SkillImportance
    status: SkillStatus
    evidence: list[Evidence] = Field(default_factory=list)
    reason: str = Field("", max_length=MAX_ITEM_CHARS)
    strength: str = Field("none", description="strong | moderate | weak | none")
    credit: float = Field(0.0, description="What this contributes to the score.")


class SkillGapModel(BaseModel):
    """A requirement the repository does not evidence."""

    skill: str
    importance: SkillImportance
    status: SkillStatus
    reason: str = Field("", max_length=MAX_ITEM_CHARS)


class CoverageModel(BaseModel):
    """Coverage for one importance band."""

    label: str
    groups: int
    credit: float
    percent: int


class MatchScoreModel(BaseModel):
    """The deterministic match score, with the working shown."""

    score: int = Field(..., ge=0, le=100)
    required: CoverageModel
    optional: CoverageModel
    formula: str = Field(..., description="How the score was computed.")
    counted_groups: int = 0
    excluded_requirements: int = Field(
        0, description="Requirements deliberately not scored."
    )
    credit_scale: dict[str, float] = Field(
        default_factory=lambda: dict(STATUS_CREDIT),
        description="Credit each status contributes.",
    )


class LearningItemModel(BaseModel):
    """One prioritised preparation step."""

    priority: int
    skill: str
    reason: str = Field("", max_length=MAX_ITEM_CHARS)
    status: SkillStatus


class JobReadinessModel(BaseModel):
    """Job readiness, and the inputs it was computed from."""

    score: int = Field(..., ge=0, le=100)
    match_score: int = Field(..., ge=0, le=100)
    interview_score: int | None = None
    required_coverage: int = Field(..., ge=0, le=100)
    formula: str
    strong_skills: list[str] = Field(default_factory=list)
    needs_work: list[str] = Field(default_factory=list)
    interview_taken: bool = False


class JobProjectMatchResponse(BaseModel):
    """Full response for `POST /api/v1/job/match`."""

    repository: str
    job: JobDescriptionModel
    match_score: MatchScoreModel
    readiness: JobReadinessModel
    matches: list[SkillMatchModel] = Field(default_factory=list)
    unscored: list[SkillMatchModel] = Field(
        default_factory=list,
        description="Responsibilities and skills a repository cannot evidence.",
    )
    strengths: list[str] = Field(
        default_factory=list, description="Why this project matches, with evidence."
    )
    gaps: list[SkillGapModel] = Field(default_factory=list)
    learning_plan: list[LearningItemModel] = Field(default_factory=list)
    interpretation: str = Field(
        "", max_length=MAX_TEXT_CHARS,
        description="Narrative from the local model. Empty if it was unavailable.",
    )
    llm_available: bool = Field(
        True, description="False when the match was produced without the model."
    )
    privacy_note: str = Field(
        "", description="How the job description was processed."
    )


class ParseJobResponse(BaseModel):
    """Full response for `POST /api/v1/job/parse`."""

    job: JobDescriptionModel
    llm_available: bool = True
    privacy_note: str = ""


# --- job interview ------------------------------------------------------------


class JobInterviewQuestion(BaseModel):
    """One job-aware interview question."""

    id: str
    question_type: JobQuestionType
    category: str
    difficulty: str
    question: str = Field(..., max_length=MAX_TEXT_CHARS)
    why_this_question: str = Field("", max_length=MAX_TEXT_CHARS)
    expected_topics: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    job_requirement: str | None = Field(
        None, description="The requirement this question comes from, if any."
    )
    is_hypothetical: bool = Field(
        False,
        description="True when the repository does not evidence the subject - "
        "the question asks what the candidate would do, not what they did.",
    )
    hypothetical_label: str = ""


class JobInterviewSessionResponse(BaseModel):
    """The full state of one job interview session."""

    session_id: str
    repository: str
    target_role: str
    target_role_label: str
    difficulty: str
    role_notice: str | None = None
    status: str
    total_questions: int
    answered_count: int
    current_question: JobInterviewQuestion | None = None
    history: list[AnsweredQuestion] = Field(default_factory=list)
    summary: InterviewSummary | None = None
    #: Present once the interview is finished.
    readiness: JobReadinessModel | None = None
    job_title: str = ""
    match_score: int = Field(0, ge=0, le=100)
    start_time: str
    end_time: str | None = None


class SubmitJobAnswerResponse(BaseModel):
    """Evaluation plus what happens next."""

    session_id: str
    evaluation: dict
    answered: int
    total: int
    next_question: JobInterviewQuestion | None = None
    is_complete: bool = False


#: Shown in the UI. Accurate for this architecture: the description reaches the
#: locally configured Ollama service and nothing else.
PRIVACY_NOTE = (
    "Your job description is processed locally by the configured Ollama model. "
    "It is not stored on disk, not written to logs, and not sent to any "
    "third-party service."
)

MATCH_FORMULA = scoring_module.MATCH_FORMULA
READINESS_FORMULA = scoring_module.READINESS_FORMULA
