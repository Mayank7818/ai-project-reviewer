"""Evaluate a candidate's answer.

Two independent judgements are combined:

    deterministic  claims.check_answer() verifies every technology the candidate
                   named against the repository. This cannot be fooled and does
                   not depend on the model remembering the repository.
    model          technical correctness, completeness, communication, and a
                   follow-up that builds on what was actually said.

The model receives only the question, the answer, the expected topics and the
evidence for that one question - never the repository (Feature 16). One model
call per answer.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.services.interview.claims import (
    ClaimReport,
    EvidenceVocabulary,
    check_answer,
)
from app.services.interview.prompts import (
    EVALUATION_SCHEMA,
    EVALUATION_SYSTEM,
    build_evaluation_prompt,
)
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)

#: An answer shorter than this is not a serious attempt. Scored deterministically
#: rather than spending a model call on it.
MIN_MEANINGFUL_ANSWER_CHARS = 12

EMPTY_ANSWER_FEEDBACK = (
    "No answer was given, so there is nothing to assess. In a real interview, "
    "saying what you do know - or asking a clarifying question - always beats "
    "silence."
)


def _evidence_lines(evidence: list[dict]) -> list[str]:
    """Render a question's evidence for the evaluation prompt."""
    lines: list[str] = []
    for item in evidence:
        location = item.get("file", "")
        start = item.get("line_start")
        if start:
            location = f"{location}:{start}"
        reason = item.get("reason") or ""
        lines.append(f"{location} — {reason}" if reason else location)
    return lines


class AnswerEvaluator:
    """Scores one answer against a question and its repository evidence."""

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def evaluate(
        self,
        *,
        question: dict,
        answer: str,
        vocabulary: EvidenceVocabulary,
    ) -> dict:
        """Evaluate one answer.

        Args:
            question: The question as generated, including evidence and topics.
            answer: The candidate's free text.
            vocabulary: What the repository demonstrably contains.

        Returns:
            A dict matching `AnswerEvaluation`.

        Raises:
            LLMUnavailableError, LLMModelNotFoundError,
            LLMInvalidResponseError: from the model.
        """
        # Claim checking runs regardless of answer length and never depends on
        # the model, so an unsupported claim is caught even if the model is
        # generous about it.
        claims = check_answer(answer, vocabulary)

        if len(answer.strip()) < MIN_MEANINGFUL_ANSWER_CHARS:
            return self._empty_answer(question, claims)

        payload = await self._llm.generate_json(
            build_evaluation_prompt(
                question=question["question"],
                answer=answer,
                expected_topics=question.get("expected_topics") or [],
                evidence_lines=_evidence_lines(question.get("evidence") or []),
                difficulty=question.get("difficulty", "medium"),
                category=question.get("category", "project_understanding"),
            ),
            schema=EVALUATION_SCHEMA,
            system=EVALUATION_SYSTEM,
        )

        return self._assemble(question, payload, claims)

    def _empty_answer(self, question: dict, claims: ClaimReport) -> dict:
        """Score a blank or near-blank answer without spending a model call."""
        return {
            "question_id": question["id"],
            "score": 0,
            "correct_points": [],
            "missing_points": list(question.get("expected_topics") or []),
            "incorrect_points": [],
            "feedback": EMPTY_ANSWER_FEEDBACK,
            "follow_up_question": "",
            "communication_score": 0,
            **self._claim_fields(claims),
        }

    def _assemble(self, question: dict, payload: dict, claims: ClaimReport) -> dict:
        """Merge model judgement with the deterministic claim check."""
        missing = list(payload.get("missing_points") or [])

        # An unverified claim is a genuine gap the model may not have noticed,
        # so surface it in the answer's missing points as well as its own field.
        for check in claims.unverified:
            note = (
                f"You mentioned {check.technology}, but the analysed files show "
                "no evidence of it."
            )
            if note not in missing:
                missing.append(note)

        return {
            "question_id": question["id"],
            "score": payload.get("score", 5),
            "correct_points": payload.get("correct_points") or [],
            "missing_points": missing,
            "incorrect_points": payload.get("incorrect_points") or [],
            "feedback": payload.get("feedback", ""),
            "follow_up_question": payload.get("follow_up_question", ""),
            "communication_score": payload.get("communication_score", 5),
            **self._claim_fields(claims),
        }

    @staticmethod
    def _claim_fields(claims: ClaimReport) -> dict:
        return {
            "verified_claims": [
                {
                    "technology": check.technology,
                    "verified": True,
                    "found_in": check.found_in,
                    "note": "",
                }
                for check in claims.verified
            ],
            "unverified_claims": [
                {
                    "technology": check.technology,
                    "verified": False,
                    "found_in": "",
                    "note": check.note,
                }
                for check in claims.unverified
            ],
        }
