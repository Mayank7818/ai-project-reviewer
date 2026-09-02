"""Project-specific interview intelligence.

Layered so the parts that must not hallucinate contain no model at all:

    seeds.py      evidence -> askable facts, each carrying its evidence   no I/O
    roles.py      target role -> which seeds to prefer                    no I/O
    claims.py     candidate answer -> verified / unverified technologies  no I/O
    session.py    session state and deterministic final scoring           no I/O
    prompts.py    the three small prompts and their schemas
    generator.py  seeds -> one model call for phrasing -> validation
    evaluator.py  one answer -> one model call + deterministic claim check
    store.py      in-memory analysis cache and session store
    service.py    orchestration

Only `service.py` is used by the API layer.
"""

from app.services.interview.service import InterviewService, get_interview_service

__all__ = ["InterviewService", "get_interview_service"]
