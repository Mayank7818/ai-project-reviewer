"""Prompts for job intelligence.

Two small prompts, and both are *enrichment only*. Every number and every skill
in the output is produced deterministically before the model is asked anything,
so a model failure degrades the result rather than breaking it.

Prompt size is bounded on purpose (Feature 18): the enrichment prompt gets a
truncated slice of the description, and the interpretation prompt gets the
already-computed match summary - never the repository, and never the full
posting a second time.
"""

from __future__ import annotations

from typing import Any

from app.core.untrusted import UNTRUSTED_DATA_RULE, fence

# --- description enrichment ---------------------------------------------------

ENRICHMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "job_title": {
            "type": "string",
            "description": "The role title, e.g. 'Senior Backend Engineer'.",
        },
        "seniority": {
            "type": "string",
            "enum": ["intern", "junior", "mid", "senior", "lead", "principal", "unstated"],
        },
        "responsibilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-6 things the person will actually do, in their own words.",
        },
        "soft_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Non-technical qualities the posting asks for.",
        },
    },
    "required": ["job_title", "seniority", "responsibilities", "soft_skills"],
}

ENRICHMENT_SYSTEM = """\
You are reading a job posting and extracting a few facts from it.

Rules:

1. Report only what the posting says. If it does not state a seniority level,
   answer "unstated" rather than guessing from tone.
2. Responsibilities are what the person will DO, not the skills they need.
   Keep each to one short line.
3. Do not list technologies - those are extracted separately and more reliably.
4. Do not include company names, recruiter names or contact details in your
   output.
5. Reply with a single JSON object and nothing else.

""" + UNTRUSTED_DATA_RULE


def build_enrichment_prompt(excerpt: str) -> str:
    """Wrap a bounded slice of the posting for enrichment."""
    return (
        "Extract the role title, seniority and responsibilities from this job "
        "posting.\n\n"
        f"{fence('POSTING', excerpt)}\n\n"
        "Return JSON only."
    )


# --- match interpretation -----------------------------------------------------

INTERPRETATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "interpretation": {
            "type": "string",
            "description": "2-4 sentences on how well this project evidences this job.",
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short lines naming ONLY skills listed as verified.",
        },
    },
    "required": ["interpretation", "strengths"],
}

INTERPRETATION_SYSTEM = """\
You are explaining, to a candidate, how well their GitHub project evidences the
requirements of a specific job.

You are given the already-computed comparison. The statuses and the score are
facts - you are not re-deciding them, only putting them into words.

Rules:

1. Never claim the candidate has a skill that is not listed as verified. A skill
   listed as not verified means the repository does not show it, which is not
   the same as the candidate lacking it - say "not verified from repository
   evidence", never "you don't know it".
2. Only name skills that appear in the comparison you were given. Do not
   introduce technologies from your own knowledge.
3. Do not make a hiring judgement. Never say the candidate will or will not get
   the job, and never rate them as a person. Talk about what the repository
   evidences.
4. Be direct about gaps without being discouraging. Frame them as what the
   project does not yet show.
5. Strengths must be drawn only from skills marked verified.
6. Reply with a single JSON object and nothing else.

""" + UNTRUSTED_DATA_RULE


def build_interpretation_prompt(
    *,
    job_title: str,
    repository: str,
    match_score: int,
    required_coverage: int,
    verified: list[str],
    partial: list[str],
    gaps: list[str],
) -> str:
    """Render the already-computed comparison. The repository is never re-sent."""
    def render(label: str, items: list[str]) -> str:
        return f"{label}: {', '.join(items) if items else 'none'}"

    from app.core.untrusted import neutralise

    return (
        f"Job: {neutralise(job_title) or 'unspecified role'}\n"
        f"Repository: {repository}\n"
        f"Match score: {match_score}/100 (required coverage {required_coverage}%)\n\n"
        "=== COMPARISON (already established, do not change) ===\n"
        f"{render('Verified in the repository', verified)}\n"
        f"{render('Partially evidenced', partial)}\n"
        f"{render('Not verified from repository evidence', gaps)}\n\n"
        "Explain this to the candidate and list their strengths. Return JSON only."
    )
