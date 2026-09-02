"""Orchestrates job parsing, matching and job-specific interviews.

Where the model is used, and where it is not:

    parse a description   deterministic; 1 optional enrichment call
    match against a repo   deterministic; 1 optional interpretation call
    match score            0 model calls - arithmetic
    job readiness          0 model calls - arithmetic
    generate questions     1 model call (phrasing only, reusing Step 5)
    evaluate an answer     1 model call (reusing Step 5)

Both optional calls are best-effort. If Ollama is stopped, parsing and matching
still return complete, correct results with `llm_available: false` - the numbers
never depended on the model. Only the interview endpoints require it, because a
question has to be phrased.

The Step 4 analysis is always taken from the cache. Nothing here re-runs the
repository pipeline or re-sends the repository to the model (Feature 18).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, InsufficientEvidenceError
from app.core.logging import get_logger
from app.services.analysis.service import AnalysisService, get_analysis_service
from app.services.interview import roles as role_module
from app.services.interview import seeds as base_seeds
from app.services.interview.generator import QuestionGenerator, select_seeds
from app.services.interview.service import (
    InterviewService,
    cache_outcome,
    get_interview_service,
)
from app.services.interview.session import InterviewSession, new_session_id
from app.services.interview.store import (
    CachedAnalysis,
    analysis_cache_key,
    get_analysis_cache,
    get_session_store,
)
from app.services.job import matcher as matcher_module
from app.services.job import prompts, scoring
from app.services.job import seeds as job_seeds
from app.services.job.matcher import JobProjectMatch, match_job
from app.services.job.parser import (
    ParsedJob,
    enrichment_excerpt,
    log_summary,
    parse_deterministic,
    validate,
)
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

logger = get_logger(__name__)


@dataclass
class MatchOutcome:
    """Everything a match response needs."""

    cached: CachedAnalysis
    job: ParsedJob
    match: JobProjectMatch
    score: scoring.MatchScore
    readiness: scoring.JobReadiness
    learning_plan: list[scoring.LearningItem]
    interpretation: str = ""
    strengths: list[str] = None  # type: ignore[assignment]
    llm_available: bool = True

    def __post_init__(self) -> None:
        if self.strengths is None:
            self.strengths = []


class JobService:
    """Job description intelligence, built on Steps 4 and 5."""

    def __init__(
        self,
        settings: Settings,
        analysis_service: AnalysisService,
        interview_service: InterviewService,
        llm_provider: LLMProvider,
    ) -> None:
        self._settings = settings
        self._analysis = analysis_service
        self._interviews = interview_service
        self._llm = llm_provider
        self._generator = QuestionGenerator(llm_provider)

    # --- parsing -------------------------------------------------------------

    async def parse(
        self, description: str, *, company: str = "", job_title: str = ""
    ) -> tuple[ParsedJob, bool]:
        """Parse a job description. Returns `(job, llm_available)`.

        The deterministic parse always runs and always succeeds on valid input.
        Model enrichment is attempted afterwards and silently skipped on any
        failure, because none of the extracted skills depend on it.
        """
        text = validate(description)
        job = parse_deterministic(text)
        job.company = company.strip()[:200]
        if job_title.strip():
            job.title = job_title.strip()[:200]

        llm_available = await self._enrich(job, text)
        log_summary(job)
        return job, llm_available

    async def _enrich(self, job: ParsedJob, text: str) -> bool:
        """Best-effort model enrichment. Never raises."""
        try:
            payload = await self._llm.generate_json(
                prompts.build_enrichment_prompt(enrichment_excerpt(text)),
                schema=prompts.ENRICHMENT_SCHEMA,
                system=prompts.ENRICHMENT_SYSTEM,
            )
        except AppError as exc:
            logger.info("Job enrichment skipped: %s", exc.code)
            return False
        except Exception:  # noqa: BLE001 - enrichment must never break a parse
            logger.warning("Job enrichment failed unexpectedly", exc_info=False)
            return False

        # The deterministic values win where they exist: a title read off the
        # first line is more reliable than one a small model paraphrased.
        if not job.title:
            job.title = str(payload.get("job_title") or "").strip()[:200]

        seniority = str(payload.get("seniority") or "").strip().lower()
        if not job.seniority and seniority and seniority != "unstated":
            job.seniority = seniority

        responsibilities = payload.get("responsibilities")
        if isinstance(responsibilities, list) and not job.responsibilities:
            job.responsibilities = [str(item)[:200] for item in responsibilities[:8]]

        soft = payload.get("soft_skills")
        if isinstance(soft, list):
            for item in soft:
                text_item = str(item).strip()[:80]
                if text_item and text_item not in job.soft_skills:
                    job.soft_skills.append(text_item)

        job.enriched = True
        return True

    # --- matching ------------------------------------------------------------

    async def match(
        self,
        github_url: str,
        description: str,
        *,
        company: str = "",
        job_title: str = "",
        interview_score: int | None = None,
    ) -> MatchOutcome:
        """Compare a job description against a repository's cached analysis."""
        # Parse first: the skills it names become the retrieval query, so a
        # repository analysed for the first time is biased toward the files this
        # job actually cares about.
        job, llm_available = await self.parse(
            description, company=company, job_title=job_title
        )
        cached = await self._cached_analysis(
            github_url, query_terms=[item.skill for item in job.requirements]
        )

        match = match_job(job, cached)
        score = scoring.compute_match_score(match)
        readiness = scoring.compute_readiness(match, score, interview_score)
        plan = scoring.build_learning_plan(match)

        outcome = MatchOutcome(
            cached=cached,
            job=job,
            match=match,
            score=score,
            readiness=readiness,
            learning_plan=plan,
            llm_available=llm_available,
        )

        # Deterministic strengths first: every verified skill, with its reason.
        outcome.strengths = [item.reason for item in match.verified]

        if llm_available:
            await self._interpret(outcome)

        logger.info(
            "Matched job against %s: score %d (required %d%%), %d verified, %d gaps",
            cached.repository_full_name,
            score.score,
            score.required.percent,
            len(match.verified),
            len(match.gaps),
        )
        return outcome

    async def _interpret(self, outcome: MatchOutcome) -> None:
        """Best-effort narrative. The numbers are already final."""
        try:
            payload = await self._llm.generate_json(
                prompts.build_interpretation_prompt(
                    job_title=outcome.job.title,
                    repository=outcome.cached.repository_full_name,
                    match_score=outcome.score.score,
                    required_coverage=outcome.score.required.percent,
                    verified=[item.skill for item in outcome.match.verified],
                    partial=[item.skill for item in outcome.match.partial],
                    gaps=[item.skill for item in outcome.match.gaps],
                ),
                schema=prompts.INTERPRETATION_SCHEMA,
                system=prompts.INTERPRETATION_SYSTEM,
            )
        except AppError as exc:
            logger.info("Match interpretation skipped: %s", exc.code)
            outcome.llm_available = False
            return
        except Exception:  # noqa: BLE001
            logger.warning("Match interpretation failed unexpectedly")
            outcome.llm_available = False
            return

        outcome.interpretation = " ".join(
            str(payload.get("interpretation") or "").split()
        )[:2000]

        # A strength may only name a verified skill. Anything else is dropped -
        # the model does not get to promote a gap into a strength.
        verified_names = {item.skill.lower() for item in outcome.match.verified}
        model_strengths = payload.get("strengths")
        if isinstance(model_strengths, list):
            for item in model_strengths:
                line = " ".join(str(item).split())[:200]
                if not line:
                    continue
                if any(name in line.lower() for name in verified_names):
                    if line not in outcome.strengths:
                        outcome.strengths.append(line)

    # --- job interview -------------------------------------------------------

    async def start_interview(
        self,
        github_url: str,
        description: str,
        *,
        target_role: str,
        difficulty: str,
        count: int,
        company: str = "",
        job_title: str = "",
    ) -> tuple[InterviewSession, MatchOutcome]:
        """Generate a job-aware interview and open a session for it."""
        outcome = await self.match(
            github_url, description, company=company, job_title=job_title
        )

        role = role_module.get_role(target_role)
        repository_tags = base_seeds.repository_tags(
            outcome.cached.manifests, outcome.cached.structures
        )
        fit = role_module.assess_fit(role, repository_tags)

        repository_seeds = base_seeds.build_seeds(
            repository=outcome.cached.repository,
            analysis=outcome.cached.analysis,
            structures=outcome.cached.structures,
            manifests=outcome.cached.manifests,
            security=outcome.cached.security,
            analyzed=outcome.cached.analyzed,
            domain_counts=outcome.cached.domain_counts,
            technologies=outcome.cached.technologies,
            readme_path=outcome.cached.readme_path,
        )

        architecture = (outcome.cached.analysis or {}).get("architecture") or {}
        available = job_seeds.build_job_seeds(
            outcome.job,
            outcome.match,
            repository_seeds,
            architecture.get("evidence") or [],
        )

        selected = select_seeds(
            available, count=count, difficulty=difficulty, role=role, fit=fit
        )
        if not selected:
            raise InsufficientEvidenceError(
                "No interview questions could be grounded in this repository or "
                "this job description."
            )

        questions, _dropped = await self._generator.phrase_and_assemble(
            selected, outcome.cached.evidence_files
        )

        if not questions:
            raise InsufficientEvidenceError(
                "No interview questions could be grounded in this repository or "
                "this job description."
            )

        session = InterviewSession(
            session_id=new_session_id(),
            repository=outcome.cached.repository_full_name,
            target_role=role.key,
            target_role_label=role.label,
            difficulty=difficulty,
            questions=questions,
            role_notice=fit.notice or None,
            job_context={
                "title": outcome.job.title,
                "match_score": outcome.score.score,
                "required_coverage": outcome.score.required.percent,
                "verified": [item.skill for item in outcome.match.verified],
                "gaps": [item.skill for item in outcome.match.gaps],
            },
        )
        get_session_store().put(session.session_id, session)
        logger.info(
            "Job interview %s started: %s, %d questions, match %d",
            session.session_id,
            session.repository,
            len(questions),
            outcome.score.score,
        )
        return session, outcome

    async def finish_interview(self, session_id: str) -> tuple[InterviewSession, dict]:
        """Finish a job interview and recompute readiness with its result."""
        session = await self._interviews.finish(session_id)
        return session, readiness_for(session) or {}

    # --- shared --------------------------------------------------------------

    async def _cached_analysis(
        self, github_url: str, *, query_terms: list[str] | None = None
    ) -> CachedAnalysis:
        """Reuse the Step 4 analysis, running one only if none is cached.

        The cache is always consulted first (Feature 18 of Step 6, and
        requirement 10 of Step 7): a repeated job match or interview against the
        same repository re-fetches nothing. `query_terms` therefore only
        influences a *first* retrieval, where it biases which files are pulled
        toward the skills this job actually asks about.
        """
        from app.services.github.url_parser import parse_repo_url

        ref = parse_repo_url(github_url)
        cache = get_analysis_cache()

        cached = cache.get(analysis_cache_key(ref.full_name))
        if cached is not None:
            logger.info("Reusing cached analysis for %s", ref.full_name)
            return cached

        logger.info(
            "No cached analysis for %s - running one now%s",
            ref.full_name,
            f" (biased toward {len(query_terms)} job terms)" if query_terms else "",
        )
        outcome = await self._analysis.analyze(github_url, query_terms=query_terms)
        return cache_outcome(outcome)


def readiness_for(session: InterviewSession) -> dict | None:
    """Compute job readiness from a session's stored job context.

    Recomputed from the stored numbers on every read rather than cached, so the
    value can never go stale relative to the interview it describes. Returns
    None for a plain Step 5 interview, which has no job to be ready for.
    """
    context = session.job_context or {}
    if not context:
        return None

    interview_score = (session.summary or {}).get("scores", {}).get("overall")
    match_score = context.get("match_score", 0)
    required = context.get("required_coverage", 0)

    if interview_score is None:
        # Renormalise across the terms we have rather than scoring an untaken
        # interview as a zero.
        total = scoring.READINESS_MATCH_WEIGHT + scoring.READINESS_REQUIRED_WEIGHT
        value = (
            scoring.READINESS_MATCH_WEIGHT * match_score
            + scoring.READINESS_REQUIRED_WEIGHT * required
        ) / total
    else:
        value = (
            scoring.READINESS_MATCH_WEIGHT * match_score
            + scoring.READINESS_INTERVIEW_WEIGHT * interview_score
            + scoring.READINESS_REQUIRED_WEIGHT * required
        ) / 100

    return {
        "score": max(0, min(100, round(value))),
        "match_score": match_score,
        "interview_score": interview_score,
        "required_coverage": required,
        "formula": scoring.READINESS_FORMULA,
        "strong_skills": context.get("verified", [])[:8],
        "needs_work": context.get("gaps", [])[:8],
        "interview_taken": interview_score is not None,
    }


def get_job_service() -> JobService:
    """FastAPI dependency provider for `JobService`."""
    settings = get_settings()
    return JobService(
        settings=settings,
        analysis_service=get_analysis_service(),
        interview_service=get_interview_service(),
        llm_provider=get_llm_provider(),
    )


def status_credit() -> dict[str, float]:
    """Exposed so the API can publish the scoring scale."""
    return dict(matcher_module.STATUS_CREDIT)
