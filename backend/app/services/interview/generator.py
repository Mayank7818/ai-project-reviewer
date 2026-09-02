"""Generate interview questions from repository evidence.

The pipeline, and why each step exists:

    1. seeds.build_seeds()      enumerate evidenced, askable facts   (no model)
    2. select()                 pick by role, difficulty mix, count  (no model)
    3. one model call           phrase a question per selected seed
    4. match back by id         phrasing is joined to its seed
    5. evidence validation      Step 4's validator, unchanged

Steps 1-2 decide *what* is asked, mechanically. The model only contributes step
3, and it is never shown the evidence - so it has nothing to alter and nothing
to invent. A phrasing that cannot be matched back to a seed is discarded, and a
seed the model skipped falls back to a plainly-worded question built from the
seed itself rather than being lost.

One model call per interview, not one per question (Feature 16).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.services.analysis.evidence import (
    EvidenceIndex,
    ValidationStats,
    validate_evidence_items,
)
from app.services.interview import roles as role_module
from app.services.interview import seeds as seed_module
from app.services.interview.prompts import (
    QUESTION_SCHEMA,
    QUESTION_SYSTEM,
    build_question_prompt,
)
from app.services.interview.store import CachedAnalysis
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)

MIXED = "mixed"

#: Feature 2's distribution. Applied when difficulty is "mixed".
DIFFICULTY_MIX: dict[str, float] = {
    seed_module.EASY: 0.30,
    seed_module.MEDIUM: 0.50,
    seed_module.HARD: 0.20,
}

#: No single category may exceed this share of a generated interview, so a
#: repository with forty functions does not produce forty code questions.
MAX_CATEGORY_SHARE = 0.34


@dataclass
class GeneratedQuestions:
    """Questions plus an honest account of how they were chosen."""

    questions: list[dict]
    role_fit: role_module.RoleFit
    seeds_available: int
    evidence_dropped: int

    @property
    def difficulty_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for question in self.questions:
            counts[question["difficulty"]] = counts.get(question["difficulty"], 0) + 1
        return counts

    @property
    def category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for question in self.questions:
            counts[question["category"]] = counts.get(question["category"], 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def target_distribution(count: int, difficulty: str) -> dict[str, int]:
    """How many questions of each difficulty to aim for.

    A specific difficulty means all questions at that level. "mixed" applies the
    30/50/20 split, rounding so the totals always add up to `count`.
    """
    if difficulty in seed_module.DIFFICULTIES:
        return {difficulty: count}

    easy = round(count * DIFFICULTY_MIX[seed_module.EASY])
    hard = round(count * DIFFICULTY_MIX[seed_module.HARD])
    medium = count - easy - hard

    # Rounding can push medium negative for very small counts; rebalance.
    if medium < 0:
        medium, hard = 0, count - easy
    return {
        seed_module.EASY: easy,
        seed_module.MEDIUM: medium,
        seed_module.HARD: hard,
    }


def select_seeds(
    available: list[seed_module.QuestionSeed],
    *,
    count: int,
    difficulty: str,
    role: role_module.Role,
    fit: role_module.RoleFit,
) -> list[seed_module.QuestionSeed]:
    """Choose which evidenced seeds become questions.

    Honours the difficulty distribution and the per-category share cap, then
    backfills from whatever remains if the repository could not supply enough of
    a given difficulty. Never invents a seed to hit a target - a small
    repository simply yields fewer questions.
    """
    if not available:
        return []

    ranked = sorted(
        available,
        key=lambda seed: (-role_module.score_seed(seed, role, fit), seed.key),
    )

    wanted = target_distribution(count, difficulty)
    category_cap = max(2, int(count * MAX_CATEGORY_SHARE))

    chosen: list[seed_module.QuestionSeed] = []
    per_category: dict[str, int] = {}

    def take(seed: seed_module.QuestionSeed) -> None:
        chosen.append(seed)
        per_category[seed.category] = per_category.get(seed.category, 0) + 1

    for level, quota in wanted.items():
        if quota <= 0:
            continue
        for seed in ranked:
            if len(chosen) >= count:
                break
            if seed in chosen or seed.difficulty != level:
                continue
            if per_category.get(seed.category, 0) >= category_cap:
                continue
            take(seed)
            if sum(1 for item in chosen if item.difficulty == level) >= quota:
                break

    # Backfill: the repository may not offer enough of some difficulty.
    if len(chosen) < count:
        for seed in ranked:
            if len(chosen) >= count:
                break
            if seed in chosen:
                continue
            if difficulty in seed_module.DIFFICULTIES and seed.difficulty != difficulty:
                # A specific difficulty was requested; do not silently mix.
                continue
            if per_category.get(seed.category, 0) >= category_cap:
                continue
            take(seed)

    # Last resort for a single-difficulty request the repository cannot fill:
    # relax the category cap rather than return almost nothing.
    if len(chosen) < count:
        for seed in ranked:
            if len(chosen) >= count:
                break
            if seed in chosen:
                continue
            if difficulty in seed_module.DIFFICULTIES and seed.difficulty != difficulty:
                continue
            take(seed)

    return chosen


def _fallback_question(seed: seed_module.QuestionSeed) -> str:
    """A plain question built from the seed when the model skipped it.

    Wooden but honest, and still entirely grounded in the seed's evidence.
    """
    return f"Walk me through {seed.topic}. Specifically: {seed.angle}?"


class QuestionGenerator:
    """Builds a grounded interview from a cached Step 4 analysis."""

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def generate(
        self,
        cached: CachedAnalysis,
        *,
        target_role: str,
        difficulty: str,
        count: int,
    ) -> GeneratedQuestions:
        """Produce grounded questions for one repository.

        Raises:
            LLMUnavailableError, LLMModelNotFoundError,
            LLMInvalidResponseError: from the model.
        """
        role = role_module.get_role(target_role)
        repository_tags = seed_module.repository_tags(cached.manifests, cached.structures)
        fit = role_module.assess_fit(role, repository_tags)

        available = seed_module.build_seeds(
            repository=cached.repository,
            analysis=cached.analysis,
            structures=cached.structures,
            manifests=cached.manifests,
            security=cached.security,
            analyzed=cached.analyzed,
            domain_counts=cached.domain_counts,
            technologies=cached.technologies,
            readme_path=cached.readme_path,
        )

        selected = select_seeds(
            available, count=count, difficulty=difficulty, role=role, fit=fit
        )

        logger.info(
            "Interview for %s: %d seeds available, %d selected (role=%s, supported=%s)",
            cached.repository_full_name,
            len(available),
            len(selected),
            role.key,
            fit.supported,
        )

        if not selected:
            return GeneratedQuestions(
                questions=[], role_fit=fit, seeds_available=0, evidence_dropped=0
            )

        phrasing = await self._phrase(selected)
        questions, dropped = self._assemble(selected, phrasing, cached.evidence_files)

        return GeneratedQuestions(
            questions=questions,
            role_fit=fit,
            seeds_available=len(available),
            evidence_dropped=dropped,
        )

    async def phrase_and_assemble(
        self,
        selected: list[seed_module.QuestionSeed],
        evidence_files: dict[str, str],
    ) -> tuple[list[dict], int]:
        """Phrase a pre-selected list of seeds and validate their citations.

        Public because Step 6 chooses its own seeds (job-driven ones alongside
        repository ones) but needs exactly this phrasing and validation
        behaviour. Splitting selection from phrasing keeps both layers honest
        without either duplicating the other.
        """
        if not selected:
            return [], 0
        phrasing = await self._phrase(selected)
        return self._assemble(selected, phrasing, evidence_files)

    async def _phrase(
        self, selected: list[seed_module.QuestionSeed]
    ) -> dict[str, dict]:
        """One model call to phrase every selected seed.

        Evidence is not sent - the model does not need it to write a question,
        and withholding it removes any chance of the citation being altered.
        """
        briefs = [
            {
                "id": seed.key,
                "difficulty": seed.difficulty,
                "topic": seed.topic,
                "angle": seed.angle,
            }
            for seed in selected
        ]

        payload = await self._llm.generate_json(
            build_question_prompt(briefs),
            schema=QUESTION_SCHEMA,
            system=QUESTION_SYSTEM,
        )

        phrasing: dict[str, dict] = {}
        for item in payload.get("questions") or []:
            if isinstance(item, dict) and item.get("id"):
                phrasing[str(item["id"])] = item

        missing = len(selected) - len(phrasing)
        if missing > 0:
            logger.info("Model skipped %d seed(s); using fallback phrasing", missing)

        return phrasing

    def _assemble(
        self,
        selected: list[seed_module.QuestionSeed],
        phrasing: dict[str, dict],
        sent_files: dict[str, str],
    ) -> tuple[list[dict], int]:
        """Join phrasing to seeds and validate every citation.

        Evidence comes from the seed, so validation should always pass - it runs
        anyway, because a silent divergence between the cached files and the
        seeds is exactly the kind of bug this project should catch loudly.
        """
        index = EvidenceIndex.from_files(sent_files)
        stats = ValidationStats()
        questions: list[dict] = []

        for seed in selected:
            written = phrasing.get(seed.key) or {}

            text = " ".join(str(written.get("question") or "").split())
            if not text:
                text = _fallback_question(seed)

            evidence = validate_evidence_items(seed.evidence, index, stats)
            if not evidence and not seed.job_requirement:
                # A repository-specific question without evidence must not exist.
                # A job-grounded question may have none - it is asking about
                # something the repository deliberately does not contain.
                logger.warning("Dropping question '%s': evidence did not validate", seed.key)
                continue

            model_topics = written.get("expected_topics")
            topics = (
                [str(item) for item in model_topics if str(item).strip()]
                if isinstance(model_topics, list) and model_topics
                else seed.expected_topics
            )

            questions.append(
                {
                    "id": seed.key,
                    "category": seed.category,
                    "difficulty": seed.difficulty,
                    "question": text,
                    "why_this_question": " ".join(
                        str(written.get("why_this_question") or "").split()
                    ),
                    "expected_topics": topics,
                    "evidence": evidence,
                    "question_type": seed.question_type,
                    "job_requirement": seed.job_requirement,
                }
            )

        dropped = stats.evidence_dropped_unknown_file + stats.findings_dropped_without_evidence
        return questions, dropped
