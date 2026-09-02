"""Validate evidence the model produced against what actually exists.

The model is asked to cite a file for every important claim. Nothing stops it
citing a file it never saw, or a line number beyond the end of that file - so
nothing is trusted until it is checked here, against the exact set of files that
were sent and their real line counts.

Three outcomes, in order of severity:

    unknown file  -> the whole evidence item is dropped
    bad line range-> the citation is kept, the line numbers are cleared
    valid         -> kept as-is

A finding left with no surviving evidence is itself dropped, because an
uncited claim is exactly what this step exists to prevent.

Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Cheap alias-matching for a model that shortens paths, e.g. citing "main.py"
#: when the file sent was "backend/app/main.py".
_MAX_SUFFIX_CANDIDATES = 1


@dataclass
class EvidenceIndex:
    """The ground truth an evidence citation is checked against."""

    #: path -> number of lines in the content that was actually sent.
    line_counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_files(cls, files: dict[str, str]) -> EvidenceIndex:
        return cls(
            line_counts={
                path: (content.count("\n") + 1 if content else 0)
                for path, content in files.items()
            }
        )

    @property
    def known_paths(self) -> set[str]:
        return set(self.line_counts)

    def resolve(self, cited: str) -> str | None:
        """Map a cited path onto a real one, or None if it cannot be resolved.

        Tolerates the two harmless ways a model gets a path slightly wrong -
        a leading `./` or `/`, and citing only the tail of the path - but never
        invents a match: an ambiguous suffix resolves to nothing.
        """
        if not cited:
            return None

        candidate = cited.strip().replace("\\", "/").lstrip("./").lstrip("/")
        if not candidate:
            return None

        if candidate in self.line_counts:
            return candidate

        # Exact tail match, e.g. "app/main.py" -> "backend/app/main.py".
        suffix_matches = [
            path
            for path in self.line_counts
            if path.endswith(f"/{candidate}") or path == candidate
        ]
        if len(suffix_matches) == _MAX_SUFFIX_CANDIDATES:
            return suffix_matches[0]

        return None


@dataclass
class ValidationStats:
    """What the validation pass changed. Surfaced so it is auditable."""

    evidence_kept: int = 0
    evidence_dropped_unknown_file: int = 0
    line_numbers_cleared: int = 0
    findings_dropped_without_evidence: int = 0

    @property
    def total_dropped(self) -> int:
        return self.evidence_dropped_unknown_file + self.findings_dropped_without_evidence


def validate_evidence_items(
    items: list[dict],
    index: EvidenceIndex,
    stats: ValidationStats,
) -> list[dict]:
    """Filter and correct a list of raw evidence dicts from the model.

    Args:
        items: Raw `{"file", "line_start", "line_end", "reason"}` dicts.
        index: Ground truth to check against.
        stats: Mutated in place with what was changed.

    Returns:
        Only the items that survived, with paths normalised and any
        unverifiable line numbers set to None rather than guessed.
    """
    validated: list[dict] = []

    for item in items:
        if not isinstance(item, dict):
            stats.evidence_dropped_unknown_file += 1
            continue

        resolved = index.resolve(str(item.get("file") or ""))
        if resolved is None:
            # The model cited something it was never shown. Drop it entirely -
            # a citation that cannot be checked is worse than no citation.
            stats.evidence_dropped_unknown_file += 1
            continue

        line_start = _coerce_line(item.get("line_start"))
        line_end = _coerce_line(item.get("line_end"))
        limit = index.line_counts.get(resolved, 0)

        if not _lines_are_plausible(line_start, line_end, limit):
            if line_start is not None or line_end is not None:
                stats.line_numbers_cleared += 1
            line_start = line_end = None

        validated.append(
            {
                "file": resolved,
                "line_start": line_start,
                "line_end": line_end,
                "reason": _clean_reason(item.get("reason")),
            }
        )
        stats.evidence_kept += 1

    return validated


def validate_findings(
    findings: list[dict],
    index: EvidenceIndex,
    stats: ValidationStats,
    *,
    require_evidence: bool = True,
) -> list[dict]:
    """Validate a list of `{finding, severity, evidence[]}` objects.

    Args:
        findings: Raw findings from the model.
        index: Ground truth.
        stats: Mutated in place.
        require_evidence: When True, a finding whose evidence all failed
            validation is dropped. This is the anti-hallucination guarantee:
            an important claim survives only if it cites a real file.
    """
    validated: list[dict] = []

    for finding in findings:
        if not isinstance(finding, dict):
            continue

        text = _clean_reason(finding.get("finding"))
        if not text:
            continue

        raw_evidence = finding.get("evidence")
        evidence = validate_evidence_items(
            raw_evidence if isinstance(raw_evidence, list) else [], index, stats
        )

        if require_evidence and not evidence:
            stats.findings_dropped_without_evidence += 1
            continue

        validated.append(
            {
                "finding": text,
                "severity": _normalise_severity(finding.get("severity")),
                "evidence": evidence,
            }
        )

    return validated


def validate_paths(paths: list, index: EvidenceIndex) -> list[str]:
    """Keep only the cited paths that resolve to files the model was shown."""
    resolved: list[str] = []
    for path in paths:
        match = index.resolve(str(path or ""))
        if match and match not in resolved:
            resolved.append(match)
    return resolved


# --- helpers ------------------------------------------------------------------

_VALID_SEVERITIES = {"low", "medium", "high"}


def _normalise_severity(value: object) -> str:
    """Map whatever the model wrote onto low/medium/high.

    Defaults to "medium" rather than "high": an unlabelled finding should not
    be escalated by accident.
    """
    text = str(value or "").strip().lower()
    if text in _VALID_SEVERITIES:
        return text
    if text in {"critical", "severe", "major"}:
        return "high"
    if text in {"minor", "info", "informational", "trivial"}:
        return "low"
    return "medium"


def _coerce_line(value: object) -> int | None:
    """Turn a line number into a positive int, or None if it is not one."""
    if value is None:
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _lines_are_plausible(start: int | None, end: int | None, limit: int) -> bool:
    """True only if the range genuinely exists in the file that was sent."""
    if start is None and end is None:
        return False
    if limit <= 0:
        return False
    if start is not None and start > limit:
        return False
    if end is not None and end > limit:
        return False
    if start is not None and end is not None and end < start:
        return False
    return True


def _clean_reason(value: object) -> str:
    return " ".join(str(value or "").split())[:400]
