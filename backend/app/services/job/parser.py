"""Parse a job description into structured requirements.

Skills are extracted **deterministically**. A regular expression either matched
"PostgreSQL" or it did not, and no model is asked to remember whether a job
mentioned Docker. The model is used only for the things pattern matching cannot
do well - reading the seniority out of prose, summarising responsibilities - and
even then it is best-effort: if the model is unavailable the parse still
succeeds with everything the deterministic layer found.

Importance (required / preferred / nice-to-have / responsibility) comes from the
document's own structure. Job descriptions are almost always sectioned, and the
section a skill sits under is a far better signal than anything a small model
would infer.

Privacy: the raw text is never logged. Only counts and canonical skill names
reach the log, and the description is sent nowhere except the locally configured
Ollama service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.exceptions import InvalidJobDescriptionError
from app.core.logging import get_logger
from app.services.job import vocabulary

logger = get_logger(__name__)

MIN_LENGTH = 40
MAX_LENGTH = 20_000

#: How much of the description is sent for model enrichment. Keeps the prompt
#: small (Feature 18) - the skills are already extracted by then.
MAX_ENRICHMENT_CHARS = 5_000

# --- importance ---------------------------------------------------------------

REQUIRED = "required"
PREFERRED = "preferred"
NICE_TO_HAVE = "nice_to_have"
RESPONSIBILITY = "responsibility"

IMPORTANCES: tuple[str, ...] = (REQUIRED, PREFERRED, NICE_TO_HAVE, RESPONSIBILITY)

#: Section headings, mapped to the importance everything beneath them inherits.
#: Ordered most specific first - "nice to have" must beat "have".
_SECTION_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(nice[\s-]?to[\s-]?have|bonus\s+points?|would\s+be\s+a\s+plus)\b", re.I), NICE_TO_HAVE),
    (re.compile(r"\b(preferred|desirable|good\s+to\s+have|plus(?:es)?|advantageous)\b", re.I), PREFERRED),
    (re.compile(r"\b(required|requirements?|must[\s-]?have|essential|minimum\s+qualifications?|"
                r"what\s+you.{0,3}ll\s+need|you\s+(?:will\s+)?have|qualifications?|skills?\s+(?:and|&)\s+experience)\b", re.I), REQUIRED),
    (re.compile(r"\b(responsibilit(?:y|ies)|what\s+you.{0,3}ll\s+do|the\s+role|day[\s-]to[\s-]day|"
                r"about\s+the\s+role|your\s+impact)\b", re.I), RESPONSIBILITY),
)

#: Inline markers, used when a bullet states its own importance.
_INLINE_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(nice[\s-]?to[\s-]?have|bonus|a\s+plus)\b", re.I), NICE_TO_HAVE),
    (re.compile(r"\b(preferred|desirable)\b", re.I), PREFERRED),
    (re.compile(r"\b(required|must\s+have|essential)\b", re.I), REQUIRED),
)

# --- seniority ----------------------------------------------------------------

SENIORITY_LEVELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(intern|internship|trainee)\b", re.I), "intern"),
    (re.compile(r"\b(principal|staff\s+engineer|distinguished)\b", re.I), "principal"),
    (re.compile(r"\b(lead|team\s+lead|tech\s+lead|head\s+of)\b", re.I), "lead"),
    (re.compile(r"\b(senior|sr\.?|experienced)\b", re.I), "senior"),
    (re.compile(r"\b(junior|jr\.?|entry[\s-]level|graduate|early[\s-]career)\b", re.I), "junior"),
    (re.compile(r"\b(mid[\s-]level|intermediate)\b", re.I), "mid"),
    (re.compile(r"\b(\d+)\+?\s*years?\b", re.I), ""),  # handled numerically below
)


@dataclass(frozen=True)
class JobRequirement:
    """One skill the job asks for, and how strongly."""

    skill: str
    category: str
    importance: str
    #: The line it was found on, trimmed. Lets the UI show where it came from.
    context: str = ""
    #: Requirements sharing a group are alternatives - "FastAPI or Flask" asks
    #: for either, not both. Scoring counts the group once, crediting the best
    #: match, so a candidate is never penalised for the option they did not take.
    alternative_group: str | None = None

    @property
    def is_scored(self) -> bool:
        """Whether this requirement can count towards a match score.

        Responsibilities describe the job, not a skill bar, and some skills
        cannot be evidenced by a repository at all.
        """
        skill = vocabulary.get(self.skill)
        return (
            self.importance in (REQUIRED, PREFERRED, NICE_TO_HAVE)
            and bool(skill)
            and skill.evidence_possible
        )


@dataclass
class ParsedJob:
    """A job description, structured."""

    title: str = ""
    seniority: str = ""
    company: str = ""
    requirements: list[JobRequirement] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    #: True when model enrichment ran; False when it was skipped or failed.
    enriched: bool = False
    #: Length only - the text itself is never retained in logs.
    source_chars: int = 0

    def by_importance(self, importance: str) -> list[JobRequirement]:
        return [item for item in self.requirements if item.importance == importance]

    def by_category(self, category: str) -> list[JobRequirement]:
        return [item for item in self.requirements if item.category == category]

    @property
    def scored_requirements(self) -> list[JobRequirement]:
        return [item for item in self.requirements if item.is_scored]


# --- deterministic extraction -------------------------------------------------


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split the description into `(importance, block)` pairs.

    A line is treated as a heading when it matches a section marker AND is short
    enough to be a heading rather than a sentence that happens to use the word.
    """
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current = ("", [])

    for line in lines:
        stripped = line.strip()
        heading = None

        # A heading is short, and often ends with a colon.
        if stripped and len(stripped) <= 80:
            for pattern, importance in _SECTION_MARKERS:
                if pattern.search(stripped):
                    # A bullet is content, not a heading, even if it uses the word.
                    if not re.match(r"^[-*•\d]", stripped) or stripped.endswith(":"):
                        heading = importance
                    break

        if heading:
            if current[1]:
                sections.append(current)
            current = (heading, [])
        else:
            current[1].append(line)

    if current[1]:
        sections.append(current)

    return [(importance, "\n".join(body)) for importance, body in sections]


def _detect_seniority(text: str) -> str:
    """Read the seniority level out of the text, or "" if it is not stated."""
    head = text[:600]  # titles and levels live at the top

    for pattern, level in SENIORITY_LEVELS:
        if not level:
            continue
        if pattern.search(head):
            return level

    # "5+ years of experience" implies senior; 2-3 implies mid.
    years = re.search(r"\b(\d+)\+?\s*years?\b", text, re.I)
    if years:
        count = int(years.group(1))
        if count >= 6:
            return "senior"
        if count >= 3:
            return "mid"
        return "junior"

    return ""


def _detect_title(text: str) -> str:
    """Guess the job title from the first meaningful line."""
    for line in text.splitlines():
        stripped = line.strip().strip("#").strip()
        if not stripped or len(stripped) > 90:
            continue
        # Skip lines that are obviously section headings or prose.
        if stripped.endswith(":") or stripped.lower().startswith(("about", "we are", "our ")):
            continue
        return stripped
    return ""


def extract_requirements(text: str) -> list[JobRequirement]:
    """Find every skill in the description, with the importance of its section.

    A skill mentioned in more than one place keeps its strongest importance:
    required beats preferred beats nice-to-have beats responsibility.
    """
    sections = _split_sections(text)
    has_requirement_section = any(
        importance in (REQUIRED, PREFERRED, NICE_TO_HAVE) for importance, _ in sections
    )

    # Skills outside any recognised section: if the document never states a
    # requirements section, the whole thing is the requirement. If it does, an
    # unsectioned mention is background rather than a bar to clear.
    default_importance = PREFERRED if has_requirement_section else REQUIRED

    strength = {REQUIRED: 3, PREFERRED: 2, NICE_TO_HAVE: 1, RESPONSIBILITY: 0}
    best: dict[str, JobRequirement] = {}

    for section_importance, block in sections:
        for line in block.splitlines():
            if not line.strip():
                continue

            importance = section_importance or default_importance
            # A bullet may override its section, e.g. "Docker (nice to have)".
            for pattern, inline in _INLINE_MARKERS:
                if pattern.search(line):
                    importance = inline
                    break

            found = vocabulary.find_skills(line)
            group = _alternative_group(line, found)

            for skill in found:
                requirement = JobRequirement(
                    skill=skill.name,
                    category=skill.category,
                    importance=importance,
                    context=" ".join(line.split())[:200],
                    alternative_group=(
                        group if group and skill.category == found[0].category else None
                    ),
                )
                existing = best.get(skill.name)
                if existing is None or strength[importance] > strength[existing.importance]:
                    best[skill.name] = requirement

    return sorted(best.values(), key=lambda item: (-strength[item.importance], item.skill))


_ALTERNATIVE = re.compile(r"\b(?:or|either)\b|/", re.I)


def _alternative_group(line: str, found: list) -> str | None:
    """Return a group id when a line offers a choice between equivalent skills.

    "FastAPI or Flask" asks for either. Without this, a candidate using FastAPI
    would lose credit for not also using Flask, which is not what the job said.

    Deliberately narrow: the skills must be in the same category (so "Python or
    AWS" is not treated as a choice) and the line must actually contain a
    disjunction.
    """
    same_category = {skill.category for skill in found}
    if len(found) < 2 or len(same_category) != 1:
        return None
    if not _ALTERNATIVE.search(line):
        return None
    return "|".join(sorted(skill.name for skill in found))


def _extract_responsibilities(text: str) -> list[str]:
    """Pull bullet lines from any responsibilities section."""
    lines: list[str] = []
    for importance, block in _split_sections(text):
        if importance != RESPONSIBILITY:
            continue
        for line in block.splitlines():
            stripped = line.strip().lstrip("-*•").strip()
            if len(stripped) > 12:
                lines.append(stripped[:200])
    return lines[:10]


def parse_deterministic(text: str) -> ParsedJob:
    """Parse without any model involvement. Always succeeds on valid input."""
    return ParsedJob(
        title=_detect_title(text),
        seniority=_detect_seniority(text),
        requirements=extract_requirements(text),
        responsibilities=_extract_responsibilities(text),
        soft_skills=[
            skill.name
            for skill in vocabulary.find_skills(text)
            if skill.category == vocabulary.SOFT_SKILL
        ],
        source_chars=len(text),
    )


def validate(text: str) -> str:
    """Check the description is usable, returning it trimmed.

    Raises:
        InvalidJobDescriptionError: empty, too short to parse, or absurdly long.
    """
    candidate = (text or "").strip()

    if not candidate:
        raise InvalidJobDescriptionError("A job description is required.")

    if len(candidate) < MIN_LENGTH:
        raise InvalidJobDescriptionError(
            "That job description is too short to analyse. Paste the full "
            "posting, including its requirements."
        )

    if len(candidate) > MAX_LENGTH:
        raise InvalidJobDescriptionError(
            f"That job description is unusually long (over {MAX_LENGTH:,} "
            "characters). Paste just the role and its requirements."
        )

    return candidate


def enrichment_excerpt(text: str) -> str:
    """The bounded slice of the description sent for model enrichment."""
    return text[:MAX_ENRICHMENT_CHARS]


def log_summary(parsed: ParsedJob) -> None:
    """Log what was found - never the description itself (Feature 16)."""
    logger.info(
        "Parsed job description (%d chars): %d requirements "
        "(%d required, %d preferred), seniority=%s, enriched=%s",
        parsed.source_chars,
        len(parsed.requirements),
        len(parsed.by_importance(REQUIRED)),
        len(parsed.by_importance(PREFERRED)),
        parsed.seniority or "unstated",
        parsed.enriched,
    )
