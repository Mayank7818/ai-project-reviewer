"""Orchestrates the interview: generation, answering, and the closing summary.

Where the model is used, and where it is not:

    generate questions   1 model call per interview (phrasing only)
    evaluate an answer   1 model call per answer
    final summary        1 model call per interview
    claim verification   0 model calls - deterministic
    final scoring        0 model calls - arithmetic

The Step 4 analysis is fetched from the cache. If it is absent - a fresh backend,
or an expired entry - it is produced once and cached, and every subsequent
interview against that repository reuses it (Feature 16).
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.exceptions import InsufficientEvidenceError, SessionNotFoundError
from app.core.logging import get_logger
from app.services.analysis.service import AnalysisService, get_analysis_service
from app.services.interview import roles as role_module
from app.services.interview.claims import EvidenceVocabulary, check_answer
from app.services.interview.evaluator import AnswerEvaluator
from app.services.interview.generator import GeneratedQuestions, QuestionGenerator
from app.services.interview.prompts import (
    SUMMARY_SCHEMA,
    SUMMARY_SYSTEM,
    build_summary_prompt,
)
from app.services.interview.session import (
    InterviewSession,
    new_session_id,
)
from app.services.interview.store import (
    CachedAnalysis,
    analysis_cache_key,
    get_analysis_cache,
    get_session_store,
)
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

logger = get_logger(__name__)


class InterviewService:
    """The interview lifecycle, built on Step 4's evidence."""

    def __init__(
        self,
        settings: Settings,
        analysis_service: AnalysisService,
        llm_provider: LLMProvider,
    ) -> None:
        self._settings = settings
        self._analysis = analysis_service
        self._llm = llm_provider
        self._generator = QuestionGenerator(llm_provider)
        self._evaluator = AnswerEvaluator(llm_provider)

    # --- analysis reuse ------------------------------------------------------

    async def _cached_analysis(self, github_url: str) -> CachedAnalysis:
        """Return the Step 4 analysis for a repository, running it if needed.

        Keyed by the repository's full name plus the settings that change the
        answer, so the same project reached by a slightly different URL still
        hits the cache - and a project analysed under a different model or mode
        does not.
        """
        from app.services.github.url_parser import parse_repo_url

        ref = parse_repo_url(github_url)
        cache = get_analysis_cache()

        cached = cache.get(analysis_cache_key(ref.full_name))
        if cached is not None:
            logger.info("Reusing cached analysis for %s", ref.full_name)
            return cached

        logger.info("No cached analysis for %s - running one now", ref.full_name)
        outcome = await self._analysis.analyze(github_url)
        cached = cache_outcome(outcome)
        return cached

    # --- generation ----------------------------------------------------------

    async def generate(
        self, github_url: str, *, target_role: str, difficulty: str, count: int
    ) -> tuple[CachedAnalysis, GeneratedQuestions]:
        """Generate grounded questions without starting a session."""
        cached = await self._cached_analysis(github_url)
        generated = await self._generator.generate(
            cached, target_role=target_role, difficulty=difficulty, count=count
        )

        if not generated.questions:
            raise InsufficientEvidenceError(
                "No interview questions could be grounded in this repository. "
                "The analysed selection did not contain enough code, "
                "dependencies or documentation to ask about."
            )

        return cached, generated

    # --- session lifecycle ---------------------------------------------------

    async def start(
        self, github_url: str, *, target_role: str, difficulty: str, count: int
    ) -> InterviewSession:
        """Generate questions and open a session for them."""
        cached, generated = await self.generate(
            github_url, target_role=target_role, difficulty=difficulty, count=count
        )

        session = InterviewSession(
            session_id=new_session_id(),
            repository=cached.repository_full_name,
            target_role=generated.role_fit.role.key,
            target_role_label=generated.role_fit.role.label,
            difficulty=difficulty,
            questions=generated.questions,
            role_notice=generated.role_fit.notice or None,
        )

        get_session_store().put(session.session_id, session)
        logger.info(
            "Interview %s started: %s, %d questions",
            session.session_id,
            session.repository,
            session.total_questions,
        )
        return session

    def get(self, session_id: str) -> InterviewSession:
        """Fetch a session, or raise if it does not exist."""
        session = get_session_store().get(session_id)
        if session is None:
            raise SessionNotFoundError(
                "That interview session does not exist, or it has expired. "
                "Sessions are held in memory and are lost when the backend restarts."
            )
        return session

    async def submit_answer(
        self, session_id: str, *, question_id: str, answer: str
    ) -> tuple[InterviewSession, dict]:
        """Evaluate one answer and advance the session."""
        session = self.get(session_id)

        if session.status == "complete":
            raise SessionNotFoundError(
                "This interview is already finished. Start a new one to continue practising."
            )

        question = session.find_question(question_id)
        if question is None:
            raise SessionNotFoundError(
                "That question is not part of this interview session."
            )

        if question_id in session.answered_ids:
            raise SessionNotFoundError(
                "That question has already been answered in this session."
            )

        cached = get_analysis_cache().get(analysis_cache_key(session.repository))
        vocabulary = _vocabulary_for(cached)

        evaluation = await self._evaluator.evaluate(
            question=question, answer=answer, vocabulary=vocabulary
        )
        session.record(question, answer, evaluation)

        # Re-store so a future database-backed implementation sees the write.
        get_session_store().put(session.session_id, session)

        return session, evaluation

    async def finish(self, session_id: str) -> InterviewSession:
        """Close a session and produce its summary.

        Scores are computed deterministically first; the model only writes the
        narrative around numbers it cannot change.
        """
        session = self.get(session_id)

        if session.status == "complete":
            return session

        scores = session.compute_scores()
        summary = await self._build_summary(session, scores)
        session.finish(summary)
        get_session_store().put(session.session_id, session)

        logger.info(
            "Interview %s finished: %d answered, overall %d",
            session.session_id,
            session.answered_count,
            scores["overall"],
        )
        return session

    async def _build_summary(
        self, session: InterviewSession, scores: dict[str, int]
    ) -> dict:
        """Assemble the closing report."""
        assessed = session.assessed_dimensions()
        weak = session.weak_records()

        base = {
            "scores": scores,
            "questions_to_revisit": [
                record.question["question"] for record in weak[:5]
            ],
            "unverified_claims": session.all_unverified_claims(),
        }

        if not session.history:
            return {
                **base,
                "strong_areas": [],
                "weak_areas": [],
                "recommended_topics": [],
                "overall_feedback": (
                    "No questions were answered, so there is nothing to summarise."
                ),
            }

        rows = [
            f"- [{record.question.get('category', '')}/"
            f"{record.question.get('difficulty', '')}] "
            f"score {record.evaluation.get('score', 0)}/10. "
            f"Missing: {'; '.join(record.evaluation.get('missing_points') or []) or 'nothing noted'}"
            for record in session.history
        ]

        reported = {
            name: value
            for name, value in scores.items()
            if name in assessed or name in ("overall", "communication")
        }

        payload = await self._llm.generate_json(
            build_summary_prompt(rows, reported),
            schema=SUMMARY_SCHEMA,
            system=SUMMARY_SYSTEM,
        )

        # Dimensions nobody was asked about must not appear as strengths or
        # weaknesses - they were not tested.
        not_assessed = sorted(set(SCORE_DIMENSIONS) - assessed)
        weak_areas = list(payload.get("weak_areas") or [])
        for name in not_assessed:
            weak_areas.append(
                f"{name.replace('_', ' ').title()}: not assessed - no question "
                "in this area could be grounded in your repository."
            )

        return {
            **base,
            "strong_areas": payload.get("strong_areas") or [],
            "weak_areas": weak_areas,
            "recommended_topics": self._grounded_topics(
                payload.get("recommended_topics") or [], session
            ),
            "overall_feedback": payload.get("overall_feedback", ""),
        }

    @staticmethod
    def _grounded_topics(topics: list, session: InterviewSession) -> list[str]:
        """Drop study topics that name a technology nobody involved ever used.

        A small model will happily recommend "JWT expiry and revocation" after an
        interview about an HTTP client library that has no JWT anywhere. That is
        an invented technology claim, which Feature 14 forbids.

        The filter is deliberately narrow. A topic is dropped only when it names
        a technology from the checkable vocabulary that appears neither in the
        repository nor in anything the candidate said. Generic advice
        ("deployment strategies") names no technology and always survives, and a
        technology the candidate raised themselves stays too - discussing what
        they brought up is legitimate.
        """
        cleaned = [" ".join(str(topic).split()) for topic in topics if str(topic).strip()]
        if not cleaned:
            return []

        cached = get_analysis_cache().get(analysis_cache_key(session.repository))
        if cached is None:
            # Without the analysis we cannot check fairly, so we do not judge.
            return cleaned

        vocabulary = _vocabulary_for(cached)
        spoken = check_answer(
            " ".join(record.answer for record in session.history), vocabulary
        )
        raised_by_candidate = {
            check.technology for check in spoken.verified + spoken.unverified
        }

        kept: list[str] = []
        for topic in cleaned:
            report = check_answer(topic, vocabulary)
            invented = [
                check.technology
                for check in report.unverified
                if check.technology not in raised_by_candidate
            ]
            if invented:
                logger.info(
                    "Dropping recommended topic naming %s: not in the repository "
                    "and never raised in the interview",
                    ", ".join(invented),
                )
                continue
            kept.append(topic)

        return kept


#: Dimensions that can be reported as "not assessed".
SCORE_DIMENSIONS = ("technical", "project_knowledge", "architecture", "security", "problem_solving")


def _vocabulary_for(cached: CachedAnalysis | None) -> EvidenceVocabulary:
    """Build the claim-checking vocabulary, tolerating a lost cache entry.

    If the analysis has expired, an empty vocabulary would flag every technology
    the candidate names, which would be unfair. Returning an empty vocabulary
    with no manifests means nothing matches - so instead we return a vocabulary
    that verifies nothing and therefore flags nothing.
    """
    if cached is None:
        logger.info("Analysis cache miss during evaluation; claim checking disabled")
        return EvidenceVocabulary()

    return EvidenceVocabulary.build(
        manifests=cached.manifests,
        structures=cached.structures,
        technologies=cached.technologies,
        analyzed_paths=list(cached.analyzed),
    )


def cache_outcome(outcome) -> CachedAnalysis:
    """Store a completed Step 4 analysis for interview reuse.

    Called both by the analysis endpoint (so analysing then interviewing costs
    one analysis) and by the interview service when it has to run its own.
    """
    context = outcome.context
    repository = outcome.retrieval.repository
    full_name = repository.get("full_name") or repository.get("name") or "unknown"

    # Everything mechanically analysed is fair game for a question, so use the
    # wider set rather than only the files whose text fitted the prompt.
    domains = context.all_domains or context.analyzed
    readme_path = next(
        (path for path in domains if path.lower().startswith("readme")), None
    )

    cached = CachedAnalysis(
        repository_full_name=full_name,
        repository=repository,
        analysis=outcome.analysis.model_dump(),
        structures=context.structures,
        manifests=context.manifests,
        security=context.security,
        analyzed=dict(domains),
        domain_counts=dict(context.domain_counts),
        technologies=list(outcome.analysis.technologies),
        evidence_files=dict(context.evidence_files or context.sent_files),
        readme_path=readme_path,
        repository_map=outcome.retrieval.repository_map,
        meta=outcome.meta,
    )

    get_analysis_cache().put(analysis_cache_key(full_name), cached)
    logger.info("Cached analysis for %s", full_name)
    return cached


def get_interview_service() -> InterviewService:
    """FastAPI dependency provider for `InterviewService`."""
    return InterviewService(
        settings=get_settings(),
        analysis_service=get_analysis_service(),
        llm_provider=get_llm_provider(),
    )


def get_role_options() -> list[dict[str, str]]:
    """Roles for the UI dropdown."""
    return role_module.role_options()
