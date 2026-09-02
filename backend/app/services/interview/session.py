"""Interview session state and final scoring.

The session is a plain dataclass with no persistence concerns, so moving it to
PostgreSQL later means writing a mapper rather than rewriting the logic. All
state transitions live here; the API layer only calls them.

Final scoring is deterministic. Per-answer scores are model judgements, but how
they roll up into the seven headline numbers is arithmetic over the categories
that were actually asked - a dimension that was never tested is reported as not
assessed rather than given an invented score.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.interview import seeds as seed_module

IN_PROGRESS, COMPLETE = "in_progress", "complete"

#: Which question categories feed which headline score. A category may feed more
#: than one - a security question is both security and technical.
SCORE_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "technical": (
        seed_module.CODE, seed_module.API, seed_module.DATABASE,
        seed_module.TECHNOLOGY, seed_module.SECURITY, seed_module.PERFORMANCE,
        seed_module.TESTING,
    ),
    "project_knowledge": (
        seed_module.PROJECT_UNDERSTANDING, seed_module.PROJECT_DECISIONS,
        seed_module.CODE, seed_module.TECHNOLOGY,
    ),
    "architecture": (
        seed_module.ARCHITECTURE, seed_module.API, seed_module.DEPLOYMENT,
    ),
    "security": (seed_module.SECURITY,),
    "problem_solving": (
        seed_module.PROBLEM_SOLVING, seed_module.PERFORMANCE, seed_module.ARCHITECTURE,
    ),
}

#: Reported when no question in a dimension was asked. Neutral, not zero: a
#: dimension that was never tested must not read as a failure.
NOT_ASSESSED_SCORE = 50

#: Answers at or below this are worth revisiting.
WEAK_ANSWER_THRESHOLD = 5


@dataclass
class AnsweredRecord:
    """One completed question/answer/evaluation triple."""

    question: dict
    answer: str
    evaluation: dict
    answered_at: datetime


@dataclass
class InterviewSession:
    """One interview, from start to summary.

    Deliberately free of storage concerns: `store.py` holds instances, and a
    future database mapper would serialise these fields directly.
    """

    session_id: str
    repository: str
    target_role: str
    target_role_label: str
    difficulty: str
    questions: list[dict]
    role_notice: str | None = None
    #: Step 6. Present when this interview was generated against a job
    #: description: the match score, coverage and skill lists needed to compute
    #: readiness at the end. None for a plain Step 5 interview.
    job_context: dict | None = None
    history: list[AnsweredRecord] = field(default_factory=list)
    summary: dict | None = None
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None

    # --- state -------------------------------------------------------------

    @property
    def status(self) -> str:
        return COMPLETE if self.end_time is not None else IN_PROGRESS

    @property
    def answered_count(self) -> int:
        return len(self.history)

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    @property
    def answered_ids(self) -> set[str]:
        return {record.question["id"] for record in self.history}

    @property
    def current_question(self) -> dict | None:
        """The next unanswered question, or None when every one is done."""
        if self.status == COMPLETE:
            return None
        answered = self.answered_ids
        return next(
            (item for item in self.questions if item["id"] not in answered), None
        )

    @property
    def is_finished(self) -> bool:
        return self.current_question is None

    def find_question(self, question_id: str) -> dict | None:
        return next((item for item in self.questions if item["id"] == question_id), None)

    def record(self, question: dict, answer: str, evaluation: dict) -> None:
        """Store one answer. Re-answering a question is not permitted."""
        self.history.append(
            AnsweredRecord(
                question=question,
                answer=answer,
                evaluation=evaluation,
                answered_at=datetime.now(timezone.utc),
            )
        )

    def finish(self, summary: dict) -> None:
        self.summary = summary
        self.end_time = datetime.now(timezone.utc)

    # --- scoring -----------------------------------------------------------

    def compute_scores(self) -> dict[str, int]:
        """Roll per-answer scores up into the seven headline numbers.

        Each answer's 0-10 score becomes 0-100. A dimension is the mean over the
        questions whose category feeds it; a dimension with no questions is
        reported as `NOT_ASSESSED_SCORE` rather than 0.
        """
        if not self.history:
            return {
                name: NOT_ASSESSED_SCORE
                for name in ("overall", "technical", "project_knowledge",
                             "architecture", "security", "problem_solving",
                             "communication")
            }

        by_category: dict[str, list[int]] = {}
        communication: list[int] = []

        for record in self.history:
            category = record.question.get("category", "")
            score = int(record.evaluation.get("score", 0)) * 10
            by_category.setdefault(category, []).append(score)
            communication.append(int(record.evaluation.get("communication_score", 5)) * 10)

        def dimension(name: str) -> int:
            categories = SCORE_CATEGORY_MAP[name]
            values = [
                score
                for category in categories
                for score in by_category.get(category, [])
            ]
            return round(sum(values) / len(values)) if values else NOT_ASSESSED_SCORE

        scores = {name: dimension(name) for name in SCORE_CATEGORY_MAP}
        scores["communication"] = (
            round(sum(communication) / len(communication)) if communication else NOT_ASSESSED_SCORE
        )

        # Overall is the mean of the actual answers, not of the dimensions -
        # averaging dimensions would let one category dominate by feeding several.
        all_scores = [score for values in by_category.values() for score in values]
        scores["overall"] = round(sum(all_scores) / len(all_scores))

        return scores

    def assessed_dimensions(self) -> set[str]:
        """Which headline dimensions actually had a question behind them."""
        asked = {record.question.get("category", "") for record in self.history}
        return {
            name
            for name, categories in SCORE_CATEGORY_MAP.items()
            if asked & set(categories)
        }

    def weak_records(self) -> list[AnsweredRecord]:
        """Answers worth revisiting, weakest first."""
        weak = [
            record
            for record in self.history
            if int(record.evaluation.get("score", 0)) <= WEAK_ANSWER_THRESHOLD
        ]
        return sorted(weak, key=lambda record: record.evaluation.get("score", 0))

    def all_unverified_claims(self) -> list[dict]:
        """Every unsupported claim made across the interview, de-duplicated."""
        seen: set[str] = set()
        claims: list[dict] = []
        for record in self.history:
            for claim in record.evaluation.get("unverified_claims") or []:
                technology = claim.get("technology", "")
                if technology and technology not in seen:
                    seen.add(technology)
                    claims.append(claim)
        return claims


def new_session_id() -> str:
    """A URL-safe session identifier."""
    return uuid.uuid4().hex
