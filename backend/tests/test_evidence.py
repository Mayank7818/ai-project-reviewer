"""Tests for evidence validation.

This is the anti-hallucination boundary. What it guarantees:

    * a citation to a file the model was never shown is discarded
    * a line range that does not exist in the file sent is cleared, not kept
    * an important finding with no surviving evidence is dropped entirely
"""

from __future__ import annotations

from app.services.analysis.evidence import (
    EvidenceIndex,
    ValidationStats,
    validate_evidence_items,
    validate_findings,
    validate_paths,
)

FILES = {
    "backend/app/main.py": "one\ntwo\nthree\nfour\nfive",   # 5 lines
    "frontend/src/App.jsx": "a\nb",                          # 2 lines
    "README.md": "# Title",                                  # 1 line
}


def index() -> EvidenceIndex:
    return EvidenceIndex.from_files(FILES)


def evidence(**overrides) -> dict:
    return {
        "file": "backend/app/main.py",
        "line_start": 2,
        "line_end": 3,
        "reason": "Declares the app.",
        **overrides,
    }


# --- the index ----------------------------------------------------------------


def test_index_records_real_line_counts() -> None:
    assert index().line_counts["backend/app/main.py"] == 5
    assert index().line_counts["README.md"] == 1


def test_exact_path_resolves() -> None:
    assert index().resolve("backend/app/main.py") == "backend/app/main.py"


def test_leading_slash_and_dot_are_tolerated() -> None:
    assert index().resolve("./backend/app/main.py") == "backend/app/main.py"
    assert index().resolve("/backend/app/main.py") == "backend/app/main.py"
    assert index().resolve("backend\\app\\main.py") == "backend/app/main.py"


def test_unique_suffix_resolves() -> None:
    """A model that shortens a path is being sloppy, not wrong."""
    assert index().resolve("app/main.py") == "backend/app/main.py"
    assert index().resolve("main.py") == "backend/app/main.py"


def test_ambiguous_suffix_resolves_to_nothing() -> None:
    """Never guess between two candidates."""
    ambiguous = EvidenceIndex.from_files({"a/config.py": "x", "b/config.py": "y"})

    assert ambiguous.resolve("config.py") is None


def test_unknown_path_resolves_to_nothing() -> None:
    assert index().resolve("does/not/exist.py") is None
    assert index().resolve("") is None


# --- evidence items -----------------------------------------------------------


def test_valid_evidence_is_kept_unchanged() -> None:
    stats = ValidationStats()

    result = validate_evidence_items([evidence()], index(), stats)

    assert result == [
        {
            "file": "backend/app/main.py",
            "line_start": 2,
            "line_end": 3,
            "reason": "Declares the app.",
        }
    ]
    assert stats.evidence_kept == 1


def test_citation_to_an_unsent_file_is_dropped() -> None:
    stats = ValidationStats()

    result = validate_evidence_items(
        [evidence(file="app/services/invented.py")], index(), stats
    )

    assert result == []
    assert stats.evidence_dropped_unknown_file == 1


def test_line_numbers_beyond_the_file_are_cleared_not_kept() -> None:
    stats = ValidationStats()

    result = validate_evidence_items(
        [evidence(line_start=900, line_end=950)], index(), stats
    )

    assert result[0]["line_start"] is None
    assert result[0]["line_end"] is None
    assert result[0]["file"] == "backend/app/main.py"  # the citation survives
    assert stats.line_numbers_cleared == 1


def test_inverted_range_is_cleared() -> None:
    stats = ValidationStats()

    result = validate_evidence_items(
        [evidence(line_start=4, line_end=2)], index(), stats
    )

    assert result[0]["line_start"] is None


def test_missing_line_numbers_are_fine() -> None:
    """Omitting lines is the correct behaviour when unsure - not an error."""
    stats = ValidationStats()

    result = validate_evidence_items(
        [{"file": "README.md", "reason": "The readme."}], index(), stats
    )

    assert result[0]["line_start"] is None
    assert stats.evidence_kept == 1
    assert stats.line_numbers_cleared == 0


def test_non_numeric_line_numbers_are_cleared() -> None:
    stats = ValidationStats()

    result = validate_evidence_items(
        [evidence(line_start="somewhere", line_end=None)], index(), stats
    )

    assert result[0]["line_start"] is None


def test_malformed_items_are_dropped() -> None:
    stats = ValidationStats()

    result = validate_evidence_items(["just a string", None, 42], index(), stats)

    assert result == []
    assert stats.evidence_dropped_unknown_file == 3


# --- findings -----------------------------------------------------------------


def test_finding_with_valid_evidence_survives() -> None:
    stats = ValidationStats()

    result = validate_findings(
        [{"finding": "No error handling", "severity": "high", "evidence": [evidence()]}],
        index(),
        stats,
    )

    assert len(result) == 1
    assert result[0]["severity"] == "high"


def test_finding_whose_evidence_all_fails_is_dropped() -> None:
    """An uncited claim is exactly what this module exists to prevent."""
    stats = ValidationStats()

    result = validate_findings(
        [
            {
                "finding": "The project has a race condition in the scheduler",
                "severity": "high",
                "evidence": [evidence(file="app/scheduler.py")],
            }
        ],
        index(),
        stats,
    )

    assert result == []
    assert stats.findings_dropped_without_evidence == 1


def test_finding_with_no_evidence_at_all_is_dropped() -> None:
    stats = ValidationStats()

    result = validate_findings(
        [{"finding": "Something vague", "severity": "medium", "evidence": []}],
        index(),
        stats,
    )

    assert result == []


def test_evidence_can_be_optional_when_requested() -> None:
    stats = ValidationStats()

    result = validate_findings(
        [{"finding": "General note", "severity": "low", "evidence": []}],
        index(),
        stats,
        require_evidence=False,
    )

    assert len(result) == 1


def test_empty_finding_text_is_dropped() -> None:
    stats = ValidationStats()

    result = validate_findings(
        [{"finding": "", "severity": "high", "evidence": [evidence()]}], index(), stats
    )

    assert result == []


# --- severity normalisation ---------------------------------------------------


def test_severity_synonyms_are_mapped() -> None:
    stats = ValidationStats()
    cases = {
        "critical": "high", "SEVERE": "high", "major": "high",
        "minor": "low", "info": "low",
        "high": "high", "medium": "medium", "low": "low",
    }

    for given, expected in cases.items():
        result = validate_findings(
            [{"finding": "x", "severity": given, "evidence": [evidence()]}],
            index(),
            stats,
        )
        assert result[0]["severity"] == expected, given


def test_unknown_severity_defaults_to_medium_not_high() -> None:
    """An unlabelled finding must not be escalated by accident."""
    stats = ValidationStats()

    result = validate_findings(
        [{"finding": "x", "severity": "catastrophic-ish", "evidence": [evidence()]}],
        index(),
        stats,
    )

    assert result[0]["severity"] == "medium"


# --- paths --------------------------------------------------------------------


def test_validate_paths_keeps_only_real_ones() -> None:
    result = validate_paths(
        ["README.md", "invented/file.py", "app/main.py", "README.md"], index()
    )

    assert result == ["README.md", "backend/app/main.py"]


def test_stats_total_counts_both_drop_kinds() -> None:
    stats = ValidationStats(
        evidence_dropped_unknown_file=2, findings_dropped_without_evidence=3
    )

    assert stats.total_dropped == 5
