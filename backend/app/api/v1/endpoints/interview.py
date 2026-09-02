"""Interview endpoints.

Thin by design: parse the request, call the service, map onto the response
schema. Typed `AppError`s from the layers below become the shared JSON error
shape via the handlers registered in `main.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.schemas.interview import (
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    AnsweredQuestion,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    InterviewOptionsResponse,
    InterviewSessionResponse,
    RoleOption,
    StartInterviewRequest,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
)
from app.services.interview.generator import MIXED
from app.services.interview.seeds import DIFFICULTIES
from app.services.interview.service import (
    InterviewService,
    get_interview_service,
    get_role_options,
)
from app.services.interview.session import InterviewSession

router = APIRouter(tags=["interview"])

DEFAULT_QUESTION_COUNT = 10

_ERROR_RESPONSES = {
    404: {"description": "Session not found or expired."},
    422: {"description": "Invalid URL, or the repository lacks usable evidence."},
    429: {"description": "GitHub API rate limit exceeded."},
    502: {"description": "GitHub unreachable, or the model returned invalid output."},
    503: {"description": "Ollama is not running, or the model is not installed."},
}


@router.get(
    "/options",
    response_model=InterviewOptionsResponse,
    summary="Roles and difficulties the UI can offer",
)
async def options() -> InterviewOptionsResponse:
    """Static choices for the interview setup form. No model, no network."""
    return InterviewOptionsResponse(
        roles=[RoleOption(**item) for item in get_role_options()],
        difficulties=[*DIFFICULTIES, MIXED],
        default_question_count=DEFAULT_QUESTION_COUNT,
        min_questions=MIN_QUESTIONS,
        max_questions=MAX_QUESTIONS,
    )


@router.post(
    "/generate",
    response_model=GenerateQuestionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate grounded interview questions without starting a session",
    responses=_ERROR_RESPONSES,
)
async def generate(
    payload: GenerateQuestionsRequest,
    service: InterviewService = Depends(get_interview_service),
) -> GenerateQuestionsResponse:
    """Produce questions grounded in the repository's real evidence.

    Every question cites a file that was actually analysed. Reuses the cached
    Step 4 analysis when one exists; otherwise runs one and caches it.
    """
    cached, generated = await service.generate(
        payload.github_url,
        target_role=payload.target_role,
        difficulty=payload.difficulty,
        count=payload.question_count,
    )

    return GenerateQuestionsResponse(
        repository=cached.repository_full_name,
        target_role=generated.role_fit.role.key,
        target_role_label=generated.role_fit.role.label,
        difficulty=payload.difficulty,
        questions=generated.questions,
        role_notice=generated.role_fit.notice or None,
        difficulty_counts=generated.difficulty_counts,
        category_counts=generated.category_counts,
        evidence_dropped=generated.evidence_dropped,
        seeds_available=generated.seeds_available,
    )


@router.post(
    "/start",
    response_model=InterviewSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start an interview session",
    responses=_ERROR_RESPONSES,
)
async def start(
    payload: StartInterviewRequest,
    service: InterviewService = Depends(get_interview_service),
) -> InterviewSessionResponse:
    """Generate questions and open a session, returning the first question."""
    session = await service.start(
        payload.github_url,
        target_role=payload.target_role,
        difficulty=payload.difficulty,
        count=payload.question_count,
    )
    return _to_session_response(session)


@router.post(
    "/{session_id}/answer",
    response_model=SubmitAnswerResponse,
    summary="Submit an answer and receive an evaluation",
    responses=_ERROR_RESPONSES,
)
async def answer(
    session_id: str,
    payload: SubmitAnswerRequest,
    service: InterviewService = Depends(get_interview_service),
) -> SubmitAnswerResponse:
    """Evaluate one answer against the question's repository evidence.

    Technologies the candidate names are verified against the repository
    deterministically, so an unsupported claim is caught whatever the model says.
    """
    session, evaluation = await service.submit_answer(
        session_id, question_id=payload.question_id, answer=payload.answer
    )

    return SubmitAnswerResponse(
        session_id=session.session_id,
        evaluation=evaluation,
        answered=session.answered_count,
        total=session.total_questions,
        next_question=session.current_question,
        is_complete=session.is_finished,
    )


@router.get(
    "/{session_id}",
    response_model=InterviewSessionResponse,
    summary="Fetch the current state of a session",
    responses=_ERROR_RESPONSES,
)
async def get_session(
    session_id: str,
    service: InterviewService = Depends(get_interview_service),
) -> InterviewSessionResponse:
    """Return the full session, including history and summary if finished."""
    return _to_session_response(service.get(session_id))


@router.post(
    "/{session_id}/finish",
    response_model=InterviewSessionResponse,
    summary="Finish an interview and produce the summary",
    responses=_ERROR_RESPONSES,
)
async def finish(
    session_id: str,
    service: InterviewService = Depends(get_interview_service),
) -> InterviewSessionResponse:
    """Close the session and generate its report.

    Can be called before every question is answered - the summary then reflects
    only what was actually asked, and says so.
    """
    return _to_session_response(await service.finish(session_id))


def _to_session_response(session: InterviewSession) -> InterviewSessionResponse:
    """Map the session dataclass onto the public schema."""
    return InterviewSessionResponse(
        session_id=session.session_id,
        repository=session.repository,
        target_role=session.target_role,
        target_role_label=session.target_role_label,
        difficulty=session.difficulty,
        role_notice=session.role_notice,
        status=session.status,
        total_questions=session.total_questions,
        answered_count=session.answered_count,
        current_question=session.current_question,
        history=[
            AnsweredQuestion(
                question=record.question,
                answer=record.answer,
                evaluation=record.evaluation,
                answered_at=record.answered_at,
            )
            for record in session.history
        ],
        summary=session.summary,
        start_time=session.start_time,
        end_time=session.end_time,
    )
