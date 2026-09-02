"""Request and response models for the interview system.

Same two-gate approach as Step 4: the model's output is schema-constrained at
decode time, and these models clamp, trim and normalise before anything reaches
the browser. Evidence attached to a question comes from a seed rather than from
the model, so it is real by construction - it still passes through the Step 4
validator for consistency.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.analysis import Evidence
from app.services.interview.seeds import CATEGORIES, DIFFICULTIES

MAX_TEXT_CHARS = 2_000
MAX_ITEM_CHARS = 400
MAX_LIST_ITEMS = 10

ANSWER_MAX_CHARS = 8_000
MIN_QUESTIONS, MAX_QUESTIONS = 3, 20


def _clean_list(values: object, limit: int = MAX_LIST_ITEMS) -> list[str]:
    """Trim, de-duplicate and cap a list of strings."""
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


def _clamp(value: object, low: int, high: int, default: int) -> int:
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, int(round(number))))


# --- requests -----------------------------------------------------------------


class GenerateQuestionsRequest(BaseModel):
    """Body of `POST /api/v1/interview/generate`."""

    github_url: str = Field(..., min_length=1, max_length=300)
    target_role: str = Field(
        "software_developer", description="See GET /interview/options."
    )
    difficulty: str = Field(
        "mixed", description="easy | medium | hard | mixed"
    )
    question_count: int = Field(10, ge=MIN_QUESTIONS, le=MAX_QUESTIONS)


class StartInterviewRequest(GenerateQuestionsRequest):
    """Body of `POST /api/v1/interview/start`. Same inputs as generation."""


class SubmitAnswerRequest(BaseModel):
    """Body of `POST /api/v1/interview/{session_id}/answer`."""

    question_id: str = Field(..., min_length=1, max_length=300)
    answer: str = Field(..., max_length=ANSWER_MAX_CHARS)


# --- questions ----------------------------------------------------------------


class InterviewQuestion(BaseModel):
    """One question, grounded in repository evidence."""

    id: str
    category: str
    difficulty: str
    question: str = Field(..., max_length=MAX_TEXT_CHARS)
    why_this_question: str = Field("", max_length=MAX_TEXT_CHARS)
    expected_topics: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("category", mode="before")
    @classmethod
    def _valid_category(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in CATEGORIES else "project_understanding"

    @field_validator("difficulty", mode="before")
    @classmethod
    def _valid_difficulty(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in DIFFICULTIES else "medium"

    @field_validator("expected_topics", mode="before")
    @classmethod
    def _clean(cls, value: object) -> list[str]:
        return _clean_list(value, limit=6)

    @field_validator("question", "why_this_question", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> str:
        return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]


class GenerateQuestionsResponse(BaseModel):
    """Generated questions plus an honest account of coverage."""

    repository: str
    target_role: str
    target_role_label: str
    difficulty: str
    questions: list[InterviewQuestion]
    #: Set when the repository cannot support the chosen role.
    role_notice: str | None = None
    difficulty_counts: dict[str, int] = Field(default_factory=dict)
    category_counts: dict[str, int] = Field(default_factory=dict)
    evidence_dropped: int = 0
    seeds_available: int = Field(
        0, description="Evidenced topics found. Fewer than requested means the repository offered no more."
    )


# --- evaluation ---------------------------------------------------------------


class ClaimVerification(BaseModel):
    """A technology the candidate named, checked against the repository."""

    technology: str
    verified: bool
    found_in: str = ""
    note: str = ""


class AnswerEvaluation(BaseModel):
    """The result of evaluating one answer."""

    question_id: str
    score: int = Field(..., ge=0, le=10)
    correct_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    incorrect_points: list[str] = Field(default_factory=list)
    feedback: str = Field("", max_length=MAX_TEXT_CHARS)
    follow_up_question: str = Field("", max_length=MAX_TEXT_CHARS)
    communication_score: int = Field(5, ge=0, le=10)
    unverified_claims: list[ClaimVerification] = Field(
        default_factory=list,
        description="Technologies mentioned that the repository does not evidence.",
    )
    verified_claims: list[ClaimVerification] = Field(default_factory=list)

    @field_validator("score", "communication_score", mode="before")
    @classmethod
    def _clamp_score(cls, value: object) -> int:
        return _clamp(value, 0, 10, 5)

    @field_validator(
        "correct_points", "missing_points", "incorrect_points", mode="before"
    )
    @classmethod
    def _clean(cls, value: object) -> list[str]:
        return _clean_list(value)

    @field_validator("feedback", "follow_up_question", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> str:
        return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]


class SubmitAnswerResponse(BaseModel):
    """Evaluation plus what happens next in the session."""

    session_id: str
    evaluation: AnswerEvaluation
    answered: int
    total: int
    next_question: InterviewQuestion | None = None
    is_complete: bool = False


# --- session ------------------------------------------------------------------


class AnsweredQuestion(BaseModel):
    """A question that has been asked and answered."""

    question: InterviewQuestion
    answer: str
    evaluation: AnswerEvaluation
    answered_at: datetime


class ScoreBreakdown(BaseModel):
    """Final scores, each 0-100."""

    overall: int = Field(..., ge=0, le=100)
    technical: int = Field(..., ge=0, le=100)
    project_knowledge: int = Field(..., ge=0, le=100)
    architecture: int = Field(..., ge=0, le=100)
    security: int = Field(..., ge=0, le=100)
    problem_solving: int = Field(..., ge=0, le=100)
    communication: int = Field(..., ge=0, le=100)


class InterviewSummary(BaseModel):
    """The closing report."""

    scores: ScoreBreakdown
    strong_areas: list[str] = Field(default_factory=list)
    weak_areas: list[str] = Field(default_factory=list)
    recommended_topics: list[str] = Field(default_factory=list)
    questions_to_revisit: list[str] = Field(default_factory=list)
    overall_feedback: str = Field("", max_length=MAX_TEXT_CHARS)
    unverified_claims: list[ClaimVerification] = Field(default_factory=list)

    @field_validator(
        "strong_areas", "weak_areas", "recommended_topics", "questions_to_revisit",
        mode="before",
    )
    @classmethod
    def _clean(cls, value: object) -> list[str]:
        return _clean_list(value)

    @field_validator("overall_feedback", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> str:
        return " ".join(str(value or "").split())[:MAX_TEXT_CHARS]


class InterviewSessionResponse(BaseModel):
    """The full state of one interview session."""

    session_id: str
    repository: str
    target_role: str
    target_role_label: str
    difficulty: str
    role_notice: str | None = None
    status: str = Field(..., description="in_progress | complete")
    total_questions: int
    answered_count: int
    current_question: InterviewQuestion | None = None
    history: list[AnsweredQuestion] = Field(default_factory=list)
    summary: InterviewSummary | None = None
    start_time: datetime
    end_time: datetime | None = None


class RoleOption(BaseModel):
    key: str
    label: str


class InterviewOptionsResponse(BaseModel):
    """Choices the UI offers before an interview starts."""

    roles: list[RoleOption]
    difficulties: list[str]
    default_question_count: int
    min_questions: int = MIN_QUESTIONS
    max_questions: int = MAX_QUESTIONS
