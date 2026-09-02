"""Prompts and the output schema for project analysis.

Kept separate from both the provider and the analysis service so the wording can
be iterated on without touching transport or orchestration code.

The schema below is passed to Ollama's `format` parameter, which constrains
decoding - the model cannot emit a non-conforming object. The same shape is then
re-validated with Pydantic before it leaves the API, because a *conforming*
object can still be nonsense (a score of 900, an empty summary).
"""

from __future__ import annotations

from typing import Any

#: Scores are on a 0-100 scale throughout the application.
SCORE_MIN, SCORE_MAX = 0, 100

#: Sentinel the model is told to use when the repository gives it nothing to go
#: on. Better an explicit "unknown" than a confident invention.
UNKNOWN = "Not enough evidence in the retrieved files."


def _score_property(description: str) -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": SCORE_MIN,
        "maximum": SCORE_MAX,
        "description": description,
    }


#: JSON Schema for the analysis object. Every field is required so the model
#: cannot quietly omit a section it found difficult.
ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_summary": {
            "type": "string",
            "description": "2-4 sentences on what this project actually does.",
        },
        "technologies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Languages, frameworks and tools evidenced by the files.",
        },
        "architecture": {
            "type": "string",
            "description": "How the code is organised, based on the paths shown.",
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "code_quality": {
            "type": "object",
            "properties": {
                "score": _score_property("Code quality, 0-100."),
                "reason": {"type": "string"},
            },
            "required": ["score", "reason"],
        },
        "documentation": {
            "type": "object",
            "properties": {
                "score": _score_property("Documentation quality, 0-100."),
                "reason": {"type": "string"},
            },
            "required": ["score", "reason"],
        },
        "security": {
            "type": "object",
            "properties": {
                "score": _score_property("Security posture, 0-100."),
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "issues"],
        },
        "overall_score": _score_property("Overall project score, 0-100."),
    },
    "required": [
        "project_summary",
        "technologies",
        "architecture",
        "strengths",
        "weaknesses",
        "code_quality",
        "documentation",
        "security",
        "overall_score",
    ],
}


SYSTEM_PROMPT = f"""\
You are a senior software engineer reviewing a public GitHub repository for a \
technical interview preparation tool.

You will be given a partial extract of a repository: its metadata, its file \
structure, and the contents of a limited selection of files. This extract is \
NOT the whole repository.

Rules you must follow:

1. Base every statement on the evidence provided. Do not guess, and do not \
describe features, files, tests, or tools that are not visible in the extract.
2. If the extract does not tell you something, say so explicitly using this \
exact wording: "{UNKNOWN}" Never invent a plausible-sounding answer to fill a gap.
3. Some file contents are truncated and marked as such. Do not draw conclusions \
about the parts you cannot see.
4. Absence of evidence is not evidence of absence. If you see no tests, say \
"no test files appear in the retrieved selection" - not "the project has no tests".
5. Scores are integers from {SCORE_MIN} to {SCORE_MAX}. Justify each one from \
what you actually observed. Be fair but not generous: a score above 80 requires \
clear supporting evidence.
6. If the extract gives you no evidence either way for a scored dimension, \
score it 50 and state that the evidence was insufficient. A low score must mean \
you saw something bad. It must never mean you simply saw nothing.
7. Keep the overall score consistent with the individual scores: it should sit \
close to their average, and must not be high while a component score is low.
8. For security, report only concrete, observable concerns. Note that \
credential-like values were redacted before you saw them, and files such as \
.env were never retrieved, so their absence tells you nothing.
9. Reply with a single JSON object and nothing else. No prose, no markdown \
fences, no commentary.
"""


def build_analysis_prompt(context: str) -> str:
    """Wrap the prepared repository context in the analysis instruction."""
    return (
        "Analyse the following repository extract and produce your review as a "
        "single JSON object.\n\n"
        "=== REPOSITORY EXTRACT START ===\n"
        f"{context}\n"
        "=== REPOSITORY EXTRACT END ===\n\n"
        "Remember: describe only what the extract supports. Where the extract "
        f'is insufficient, use exactly: "{UNKNOWN}"'
    )
