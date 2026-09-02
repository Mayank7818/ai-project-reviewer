"""Prompts and output schemas for the multi-stage analysis pipeline.

A 4B model handed one enormous "analyse everything" prompt does badly: it
skims, it pads, and it invents. Three narrow prompts each with a small schema
work far better, and each stage gets to build on facts established by the last.

    Stage 1  understand   what is this project, and how is it put together?
    Stage 2  findings     what is actually wrong or notable in the code?
    Stage 3  synthesise   scores, strengths, weaknesses, overall

Stages 1 and 2 both receive the deterministic evidence digest (classification,
structure, dependencies, security scan). Stage 3 receives the *outputs* of 1 and
2 rather than the raw repository, which keeps its prompt small and stops it
re-litigating what the earlier stages already established.

A single-stage schema is also provided for `ENABLE_MULTI_STAGE=false`, which
trades depth for one model call instead of three.
"""

from __future__ import annotations

from typing import Any

from app.core.untrusted import UNTRUSTED_DATA_RULE, fence

SCORE_MIN, SCORE_MAX = 0, 100

#: The exact wording the model must use when the evidence runs out. Checked for
#: in tests, and rendered distinctly in the UI.
UNKNOWN = "Not enough evidence in the retrieved files."


# --- reusable schema fragments ------------------------------------------------


def _score(description: str) -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": SCORE_MIN,
        "maximum": SCORE_MAX,
        "description": description,
    }


#: Ceilings on what the model may write.
#:
#: Constrained decoding enforces a JSON Schema exactly, which makes these hard
#: limits rather than requests - and on CPU that is the difference between an
#: answer that ends and one that does not. Every array here was unbounded until
#: it was measured: the findings stage generated 678 output tokens at 4.2 tok/s,
#: two and a half minutes of a user's life spent writing a list nobody asked to
#: be exhaustive.
#:
#: Three findings per area is not a quality ceiling in practice. A 4B model that
#: has genuinely found six distinct problems in seven files is usually padding.
MAX_FINDINGS = 3
MAX_EVIDENCE_PER_FINDING = 2
MAX_LIST_ITEMS = 4
MAX_SENTENCE = 220
MAX_PARAGRAPH = 600


def _evidence_array(description: str) -> dict[str, Any]:
    """An array of citations. Line numbers are optional by design.

    They are omitted from `required` on purpose: a model forced to supply a line
    number will invent one, and an invented number is worse than none.
    """
    return {
        "type": "array",
        "description": description,
        "maxItems": MAX_EVIDENCE_PER_FINDING,
        "items": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Exact path from the file list you were given.",
                },
                "line_start": {"type": "integer", "minimum": 1},
                "line_end": {"type": "integer", "minimum": 1},
                "reason": {
                    "type": "string",
                    "maxLength": MAX_SENTENCE,
                    "description": "What this file shows, in one sentence.",
                },
            },
            "required": ["file", "reason"],
        },
    }


def _findings_array(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "maxItems": MAX_FINDINGS,
        "items": {
            "type": "object",
            "properties": {
                "finding": {"type": "string", "maxLength": MAX_SENTENCE},
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "evidence": _evidence_array("Files that show this."),
            },
            "required": ["finding", "severity", "evidence"],
        },
    }


def _short_list(description: str) -> dict[str, Any]:
    """A bounded list of one-line strings."""
    return {
        "type": "array",
        "description": description,
        "maxItems": MAX_LIST_ITEMS,
        "items": {"type": "string", "maxLength": MAX_SENTENCE},
    }


# --- shared rules -------------------------------------------------------------

_GROUND_RULES = f"""\
You are a senior software engineer reviewing a public GitHub repository.

You are given a PARTIAL extract: repository metadata, a file inventory, facts
extracted mechanically from the code (imports, classes, functions, routes,
declared dependencies, security scan results), and the contents of a limited
selection of files.

Non-negotiable rules:

1. Every claim must come from the extract. Never describe a file, feature,
   framework, test or tool that is not visible in it.
2. When the extract does not answer something, write exactly: "{UNKNOWN}"
   Never fill a gap with a plausible guess.
3. Cite evidence using EXACT paths from the file list you were given. Never cite
   a path that is not in that list - citations are checked, and invented ones
   are discarded.
4. Only give line numbers you can actually see in the provided file contents or
   in the extracted structure. If you are not certain, omit them entirely.
5. Absence of evidence is not evidence of absence. Say "no test files appear in
   the retrieved selection", not "the project has no tests".
6. The security scan results are mechanical and already verified - treat them as
   fact. A missing best practice is NOT a vulnerability.
7. Reply with a single JSON object and nothing else. No prose, no markdown.

{UNTRUSTED_DATA_RULE}"""


# --- Stage 1: understanding ---------------------------------------------------

STAGE1_NAME = "understand"

STAGE1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_summary": {
            "type": "string",
            "maxLength": MAX_PARAGRAPH,
            "description": "2-4 sentences on what this project actually does.",
        },
        "technologies": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "maxLength": 40},
            "description": "Only technologies evidenced by dependencies, imports or file types.",
        },
        "architecture_summary": {
            "type": "string",
            "maxLength": MAX_PARAGRAPH,
            "description": "How the code is organised: layers, tiers, entry points.",
        },
        "architecture_evidence": _evidence_array(
            "Files that demonstrate the architecture you described."
        ),
    },
    "required": [
        "project_summary",
        "technologies",
        "architecture_summary",
        "architecture_evidence",
    ],
}

STAGE1_SYSTEM = f"""{_GROUND_RULES}

This is stage 1 of 3: UNDERSTANDING. Establish what the project is and how it is
structured. Do not judge quality, score anything, or list problems - later
stages do that.

For architecture, describe only what the file inventory and extracted routes
actually show. "React frontend with FastAPI backend" requires seeing both a
React dependency and FastAPI routes. If you cannot see both, do not claim both."""


def build_stage1_prompt(evidence_digest: str) -> str:
    return (
        "Identify what this project is and how it is architected.\n\n"
        f"{fence('REPOSITORY EXTRACT', evidence_digest)}\n\n"
        "Return JSON only. Cite exact paths from the file list."
    )


# --- Stage 2: findings --------------------------------------------------------

STAGE2_NAME = "findings"

STAGE2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code_quality_findings": _findings_array(
            "Maintainability, duplication, naming, complexity, error handling, "
            "separation of concerns, configuration management."
        ),
        "security_potential_risks": _findings_array(
            "Contextual security risks NOT already in the mechanical scan. "
            "Empty array if you see none - do not invent any."
        ),
        "security_no_evidence": _short_list(
            "Security aspects you looked for and could not assess."
        ),
        "performance_findings": _findings_array(
            "Repeated work, inefficient algorithms, excessive API calls, blocking "
            "operations, query problems, scalability bottlenecks."
        ),
        "documentation_findings": _findings_array(
            "What the README and docs do and do not cover."
        ),
        "testing_evidence": _evidence_array(
            "Test files visible in the extract. Empty array if there are none."
        ),
    },
    "required": [
        "code_quality_findings",
        "security_potential_risks",
        "security_no_evidence",
        "performance_findings",
        "documentation_findings",
        "testing_evidence",
    ],
}

STAGE2_SYSTEM = f"""{_GROUND_RULES}

This is stage 2 of 3: FINDINGS. Report concrete, specific observations. Do not
score anything - stage 3 does that.

Additional rules for this stage:

8. Every finding MUST cite at least one file. A finding you cannot cite is a
   finding you must not report.
9. Prefer few well-evidenced findings over many vague ones. An empty array is a
   perfectly good answer when you genuinely see nothing.
10. Do not restate the mechanical security scan results - they are already
    recorded. Report only additional contextual risks.
11. Never claim a dependency is vulnerable. You have no vulnerability data."""


def build_stage2_prompt(evidence_digest: str) -> str:
    return (
        "Report concrete findings about this repository's code quality, "
        "security, performance, documentation and testing.\n\n"
        f"{fence('REPOSITORY EXTRACT', evidence_digest)}\n\n"
        "Return JSON only. Every finding must cite an exact path from the file "
        "list. Use empty arrays where you see nothing."
    )


# --- Stage 3: synthesis -------------------------------------------------------

STAGE3_NAME = "synthesise"

STAGE3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code_quality_score": _score("Code quality, 0-100."),
        "code_quality_reason": {"type": "string", "maxLength": MAX_SENTENCE},
        "security_score": _score("Security posture, 0-100."),
        "performance_score": _score("Performance, 0-100."),
        "performance_reason": {"type": "string", "maxLength": MAX_SENTENCE},
        "documentation_score": _score("Documentation quality, 0-100."),
        "documentation_reason": {"type": "string", "maxLength": MAX_SENTENCE},
        "testing_score": _score("Testing, 0-100."),
        "testing_reason": {"type": "string", "maxLength": MAX_SENTENCE},
        "strengths": _short_list("What this project does well."),
        "weaknesses": _short_list("What holds this project back."),
        "overall_score": _score("Overall project score, 0-100."),
    },
    "required": [
        "code_quality_score", "code_quality_reason",
        "security_score",
        "performance_score", "performance_reason",
        "documentation_score", "documentation_reason",
        "testing_score", "testing_reason",
        "strengths", "weaknesses", "overall_score",
    ],
}

STAGE3_SYSTEM = f"""\
You are a senior software engineer completing a repository review.

Stages 1 and 2 have already established what the project is and what was found
in its code. You are given those results. Turn them into scores and a summary.

Scoring rules:

1. Scores are integers from {SCORE_MIN} to {SCORE_MAX}, justified by the findings you
   were given. Do not introduce new findings.
2. A score above 80 requires clear supporting evidence in those findings.
3. Where there was no evidence either way, score 50 and say the evidence was
   insufficient. A low score must mean something bad was observed - never that
   nothing was seen.
4. Confirmed security issues must pull the security score down in proportion to
   their severity. A high-severity confirmed issue means a security score below 40.
5. The overall score must sit close to the average of the component scores. It
   must never be high while a component score is low.
6. Strengths and weaknesses must restate what the findings show. Do not invent.
7. Reply with a single JSON object and nothing else.

{UNTRUSTED_DATA_RULE}"""


def build_stage3_prompt(understanding: str, findings: str) -> str:
    return (
        "Score this project and summarise its strengths and weaknesses, using "
        "only the established results below.\n\n"
        f"{fence('WHAT THE PROJECT IS', understanding)}\n\n"
        f"{fence('WHAT WAS FOUND', findings)}\n\n"
        "Return JSON only."
    )


# --- single-stage fallback ----------------------------------------------------

SINGLE_STAGE_NAME = "single"

SINGLE_STAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **STAGE1_SCHEMA["properties"],
        **STAGE2_SCHEMA["properties"],
        **STAGE3_SCHEMA["properties"],
    },
    "required": sorted(
        set(STAGE1_SCHEMA["required"])
        | set(STAGE2_SCHEMA["required"])
        | set(STAGE3_SCHEMA["required"])
    ),
}

SINGLE_STAGE_SYSTEM = f"""{_GROUND_RULES}

Produce a complete review in one object: what the project is, what you found,
and scores.

8. Every finding must cite at least one exact path from the file list.
9. Scores are integers {SCORE_MIN}-{SCORE_MAX}. Score 50 where evidence is
   insufficient; a low score must mean something bad was observed.
10. Keep the overall score close to the average of the component scores."""


def build_single_stage_prompt(evidence_digest: str) -> str:
    return (
        "Review this repository completely and return one JSON object.\n\n"
        f"{fence('REPOSITORY EXTRACT', evidence_digest)}\n\n"
        "Return JSON only. Cite exact paths. Use empty arrays where you see nothing."
    )


# --- Fast: one pass, deterministic facts withheld -----------------------------
#
# The default pipeline. It exists because of a measurement, not a preference.
#
# On gemma3:4b over two CPU cores, a deep run of psf/requests spent 673 of 677
# seconds inside the model - and 375 of those were *prompt* processing at
# 16.7 tok/s, because stages 1 and 2 each re-sent nearly the same 2,800-token
# context and stage 3 re-sent their summaries. The repository was read three
# times to answer one question.
#
# Fast mode sends it once. What it gives up is the model's chance to think in
# separate passes. What it does not give up is evidence: every citation is
# validated against the same index, by the same code, either way.
#
# It also declines to ask for anything already known. `technologies` is absent
# on purpose - Step 4 parses it from the manifests, which is fact, and having
# the model retype a list we already hold is output tokens spent on nothing.

FAST_NAME = "review"

#: Tighter than the deep caps. One pass has one budget, and three findings per
#: area across four areas is already twelve claims about seven files.
_FAST_FINDINGS = 2


def _fast_findings(description: str) -> dict[str, Any]:
    schema = _findings_array(description)
    schema["maxItems"] = _FAST_FINDINGS
    return schema


FAST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_summary": {
            "type": "string",
            "maxLength": MAX_PARAGRAPH,
            "description": "2-3 sentences on what this project actually does.",
        },
        "architecture_summary": {
            "type": "string",
            "maxLength": MAX_PARAGRAPH,
            "description": "How the code is organised: layers, entry points.",
        },
        "architecture_evidence": _evidence_array(
            "Files that demonstrate the architecture you described."
        ),
        "code_quality_findings": _fast_findings(
            "Maintainability, duplication, error handling, separation of concerns."
        ),
        "security_potential_risks": _fast_findings(
            "Contextual risks NOT already in the mechanical scan. Empty if none."
        ),
        "performance_findings": _fast_findings("Performance concerns you can see."),
        "documentation_findings": _fast_findings("Gaps in the documentation."),
        "testing_evidence": _evidence_array("Test files visible in the extract."),
        "code_quality_score": _score(f"Code quality, {SCORE_MIN}-{SCORE_MAX}."),
        "code_quality_reason": {"type": "string", "maxLength": MAX_SENTENCE},
        "security_score": _score("Security posture."),
        "performance_score": _score("Performance."),
        "performance_reason": {"type": "string", "maxLength": MAX_SENTENCE},
        "documentation_score": _score("Documentation quality."),
        "documentation_reason": {"type": "string", "maxLength": MAX_SENTENCE},
        "testing_score": _score("Testing."),
        "testing_reason": {"type": "string", "maxLength": MAX_SENTENCE},
        "strengths": _short_list("What this project does well."),
        "weaknesses": _short_list("What holds this project back."),
        "overall_score": _score("Overall project score."),
    },
    "required": [
        "project_summary",
        "architecture_summary",
        "code_quality_score",
        "security_score",
        "performance_score",
        "documentation_score",
        "testing_score",
        "overall_score",
        "strengths",
        "weaknesses",
    ],
}

FAST_SYSTEM = f"""{_GROUND_RULES}

Produce a complete review in one object: what the project is, what you found,
and scores.

8. Every finding must cite at least one exact path from the file list. A finding
   you cannot cite is one you should not report.
9. Scores are integers {SCORE_MIN}-{SCORE_MAX}. Score 50 where the evidence is
   insufficient to judge; a low score must mean something bad was observed, not
   that nothing was seen.
10. Keep the overall score close to the average of the component scores.
11. Report what you actually find. The schema already caps each area at two
    findings, so you do not need to hold back - a real, specific, cited finding
    is the most useful thing you can produce. What you should not do is pad:
    two sharp findings beat two sharp ones and two invented ones.
12. Do not list technologies. Those are already known from the project's own
    manifests, and repeating them here costs time without adding a fact."""


def build_fast_prompt(evidence_digest: str) -> str:
    return (
        "Review this repository and return one JSON object.\n\n"
        f"{fence('REPOSITORY EXTRACT', evidence_digest)}\n\n"
        "Return JSON only. Cite exact paths from the file list. Use empty arrays "
        "where you see nothing - an empty array is a valid answer."
    )
