"""Job intelligence endpoints.

Thin by design: parse the request, call the service, map onto the response
schema. Answering a job interview question reuses the Step 5 interview service
unchanged - the session store is shared, so there is one implementation of
answer evaluation and session state, not two.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.schemas.interview import AnsweredQuestion, SubmitAnswerRequest
from app.schemas.job import (
    PRIVACY_NOTE,
    CoverageModel,
    JobDescriptionModel,
    JobInterviewQuestion,
    JobInterviewSessionResponse,
    JobProjectMatchResponse,
    JobReadinessModel,
    JobRequirementModel,
    LearningItemModel,
    MatchJobRequest,
    MatchScoreModel,
    ParseJobRequest,
    ParseJobResponse,
    SkillGapModel,
    SkillMatchModel,
    StartJobInterviewRequest,
    SubmitJobAnswerResponse,
)
from app.services.interview.service import InterviewService, get_interview_service
from app.services.interview.session import InterviewSession
from app.services.job import seeds as job_seeds
from app.services.job.matcher import JobProjectMatch, SkillMatch
from app.services.job.parser import ParsedJob
from app.services.job.scoring import CoverageBand, JobReadiness, MatchScore
from app.services.job.service import JobService, get_job_service, readiness_for

router = APIRouter(tags=["job"])

_ERRORS = {
    404: {"description": "Session not found or expired."},
    422: {"description": "Invalid URL, unusable job description, or no evidence."},
    429: {"description": "GitHub API rate limit exceeded."},
    502: {"description": "GitHub unreachable, or the model returned invalid output."},
    503: {"description": "Ollama is not running, or the model is not installed."},
}


# --- mapping helpers ----------------------------------------------------------


def _job_model(job: ParsedJob) -> JobDescriptionModel:
    return JobDescriptionModel(
        title=job.title,
        seniority=job.seniority,
        company=job.company,
        requirements=[
            JobRequirementModel(
                skill=item.skill,
                category=item.category,
                importance=item.importance,
                context=item.context,
                alternative_group=item.alternative_group,
                counts_towards_score=item.is_scored,
            )
            for item in job.requirements
        ],
        responsibilities=job.responsibilities,
        soft_skills=job.soft_skills,
        enriched=job.enriched,
        source_chars=job.source_chars,
    )


def _skill_model(item: SkillMatch) -> SkillMatchModel:
    return SkillMatchModel(
        skill=item.skill,
        category=item.category,
        importance=item.importance,
        status=item.status,
        evidence=item.evidence,
        reason=item.reason,
        strength=item.strength,
        credit=item.credit,
    )


def _coverage(band: CoverageBand) -> CoverageModel:
    return CoverageModel(
        label=band.label,
        groups=band.groups,
        credit=round(band.credit, 3),
        percent=band.percent,
    )


def _score_model(score: MatchScore) -> MatchScoreModel:
    return MatchScoreModel(
        score=score.score,
        required=_coverage(score.required),
        optional=_coverage(score.optional),
        formula=score.formula,
        counted_groups=score.counted_groups,
        excluded_requirements=score.excluded_requirements,
    )


def _readiness_model(readiness: JobReadiness) -> JobReadinessModel:
    return JobReadinessModel(
        score=readiness.score,
        match_score=readiness.match_score,
        interview_score=readiness.interview_score,
        required_coverage=readiness.required_coverage,
        formula=readiness.formula,
        strong_skills=readiness.strong_skills,
        needs_work=readiness.needs_work,
        interview_taken=readiness.interview_taken,
    )


def _gaps(match: JobProjectMatch) -> list[SkillGapModel]:
    return [
        SkillGapModel(
            skill=item.skill,
            importance=item.importance,
            status=item.status,
            reason=item.reason,
        )
        for item in match.gaps
    ]


def _question_model(question: dict | None) -> JobInterviewQuestion | None:
    """Map a stored question onto the job-aware schema.

    A question with no repository evidence is, by construction, one asking about
    something the project does not contain - so it is labelled hypothetical
    rather than presented as a claim about the code.
    """
    if question is None:
        return None

    hypothetical = not question.get("evidence") and bool(question.get("job_requirement"))

    return JobInterviewQuestion(
        id=question["id"],
        question_type=question.get("question_type", "project_evidence"),
        category=question.get("category", ""),
        difficulty=question.get("difficulty", "medium"),
        question=question.get("question", ""),
        why_this_question=question.get("why_this_question", ""),
        expected_topics=question.get("expected_topics") or [],
        evidence=question.get("evidence") or [],
        job_requirement=question.get("job_requirement"),
        is_hypothetical=hypothetical,
        hypothetical_label=job_seeds.HYPOTHETICAL_LABEL if hypothetical else "",
    )


def _session_response(
    session: InterviewSession, readiness: JobReadinessModel | None = None
) -> JobInterviewSessionResponse:
    context = session.job_context or {}

    # Recomputed on every read, so a session fetched after it finished still
    # reports readiness rather than a null.
    if readiness is None:
        computed = readiness_for(session)
        if computed:
            readiness = JobReadinessModel(**computed)
    return JobInterviewSessionResponse(
        session_id=session.session_id,
        repository=session.repository,
        target_role=session.target_role,
        target_role_label=session.target_role_label,
        difficulty=session.difficulty,
        role_notice=session.role_notice,
        status=session.status,
        total_questions=session.total_questions,
        answered_count=session.answered_count,
        current_question=_question_model(session.current_question),
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
        readiness=readiness,
        job_title=context.get("title", ""),
        match_score=context.get("match_score", 0),
        start_time=session.start_time.isoformat(),
        end_time=session.end_time.isoformat() if session.end_time else None,
    )


# --- endpoints ----------------------------------------------------------------


@router.post(
    "/parse",
    response_model=ParseJobResponse,
    summary="Parse a job description into structured requirements",
    responses=_ERRORS,
)
async def parse_job(
    payload: ParseJobRequest,
    service: JobService = Depends(get_job_service),
) -> ParseJobResponse:
    """Extract skills, seniority and responsibilities from a posting.

    Skills are extracted deterministically, so this succeeds even when Ollama is
    unavailable - `llm_available` reports whether the optional enrichment ran.
    The description is never logged and never leaves this machine.
    """
    job, llm_available = await service.parse(
        payload.job_description,
        company=payload.company,
        job_title=payload.job_title,
    )
    return ParseJobResponse(
        job=_job_model(job),
        llm_available=llm_available,
        privacy_note=PRIVACY_NOTE,
    )


@router.post(
    "/match",
    response_model=JobProjectMatchResponse,
    summary="Compare a job description against a repository",
    responses=_ERRORS,
)
async def match_job_endpoint(
    payload: MatchJobRequest,
    service: JobService = Depends(get_job_service),
) -> JobProjectMatchResponse:
    """Judge every requirement against the repository's cached Step 4 evidence.

    A skill is credited only when the repository shows it. The score is
    arithmetic over those judgements and is reproducible; the model contributes
    narrative only.
    """
    outcome = await service.match(
        payload.github_url,
        payload.job_description,
        company=payload.company,
        job_title=payload.job_title,
    )

    return JobProjectMatchResponse(
        repository=outcome.cached.repository_full_name,
        job=_job_model(outcome.job),
        match_score=_score_model(outcome.score),
        readiness=_readiness_model(outcome.readiness),
        matches=[_skill_model(item) for item in outcome.match.matches],
        unscored=[_skill_model(item) for item in outcome.match.unscored],
        strengths=outcome.strengths,
        gaps=_gaps(outcome.match),
        learning_plan=[
            LearningItemModel(
                priority=item.priority,
                skill=item.skill,
                reason=item.reason,
                status=item.status,
            )
            for item in outcome.learning_plan
        ],
        interpretation=outcome.interpretation,
        llm_available=outcome.llm_available,
        privacy_note=PRIVACY_NOTE,
    )


@router.post(
    "/interview/generate",
    response_model=JobInterviewSessionResponse,
    summary="Generate job-specific questions without starting a session",
    responses=_ERRORS,
)
async def generate_job_interview(
    payload: StartJobInterviewRequest,
    service: JobService = Depends(get_job_service),
) -> JobInterviewSessionResponse:
    """Produce the questions and return them as an unsaved session view.

    Useful for previewing what would be asked. `POST /interview/start` does the
    same work and persists the session.
    """
    session, outcome = await service.start_interview(
        payload.github_url,
        payload.job_description,
        target_role=payload.target_role,
        difficulty=payload.difficulty,
        count=payload.question_count,
        company=payload.company,
        job_title=payload.job_title,
    )
    return _session_response(session, _readiness_model(outcome.readiness))


@router.post(
    "/interview/start",
    response_model=JobInterviewSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a job-specific interview",
    responses=_ERRORS,
)
async def start_job_interview(
    payload: StartJobInterviewRequest,
    service: JobService = Depends(get_job_service),
) -> JobInterviewSessionResponse:
    """Open a session whose questions are driven by this job and this repository."""
    session, outcome = await service.start_interview(
        payload.github_url,
        payload.job_description,
        target_role=payload.target_role,
        difficulty=payload.difficulty,
        count=payload.question_count,
        company=payload.company,
        job_title=payload.job_title,
    )
    return _session_response(session, _readiness_model(outcome.readiness))


@router.post(
    "/interview/{session_id}/answer",
    response_model=SubmitJobAnswerResponse,
    summary="Submit an answer to a job interview question",
    responses=_ERRORS,
)
async def answer_job_question(
    session_id: str,
    payload: SubmitAnswerRequest,
    service: InterviewService = Depends(get_interview_service),
) -> SubmitJobAnswerResponse:
    """Evaluate one answer, reusing the Step 5 evaluator unchanged.

    Claim verification distinguishes what the candidate says they built from
    what they propose building: "I used Redis" is checked against the
    repository, "I would use Redis" is a design answer and is not flagged.
    """
    session, evaluation = await service.submit_answer(
        session_id, question_id=payload.question_id, answer=payload.answer
    )

    return SubmitJobAnswerResponse(
        session_id=session.session_id,
        evaluation=evaluation,
        answered=session.answered_count,
        total=session.total_questions,
        next_question=_question_model(session.current_question),
        is_complete=session.is_finished,
    )


@router.get(
    "/interview/{session_id}",
    response_model=JobInterviewSessionResponse,
    summary="Fetch a job interview session",
    responses=_ERRORS,
)
async def get_job_session(
    session_id: str,
    service: InterviewService = Depends(get_interview_service),
) -> JobInterviewSessionResponse:
    """Return the session, including history and summary once finished."""
    return _session_response(service.get(session_id))


@router.post(
    "/interview/{session_id}/finish",
    response_model=JobInterviewSessionResponse,
    summary="Finish a job interview and compute readiness",
    responses=_ERRORS,
)
async def finish_job_interview(
    session_id: str,
    service: JobService = Depends(get_job_service),
) -> JobInterviewSessionResponse:
    """Close the session and combine the job match with interview performance."""
    session, readiness = await service.finish_interview(session_id)
    return _session_response(session, JobReadinessModel(**readiness))
