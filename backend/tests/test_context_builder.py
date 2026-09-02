"""Tests for prompt context building.

Pure functions over a retrieval result - no network, no model, no I/O.
"""

from __future__ import annotations

from app.services.analysis import compression
from app.services.analysis.context_builder import (
    MAX_STRUCTURE_PATHS,
    OMIT_BUDGET,
    OMIT_SECRET,
    OMIT_NO_ALLOWANCE,
    TRUNCATION_NOTE,
    build_context,
)
from app.services.github.service import RetrievalResult, RetrievedFile

METADATA = {
    "name": "sample",
    "full_name": "demo/sample",
    "owner": {"login": "demo"},
    "description": "A sample project.",
    "language": "Python",
    "default_branch": "main",
    "stargazers_count": 12,
    "forks_count": 3,
    "open_issues_count": 1,
    "license": {"spdx_id": "MIT"},
    "topics": ["demo"],
    "archived": False,
}


def file(path: str, content: str, category: str = "source") -> RetrievedFile:
    return RetrievedFile(
        path=path, size_bytes=len(content), category=category, content=content
    )


def result(
    files: list[RetrievedFile] | None = None,
    readme: str | None = "# Sample\n\nDoes a thing.",
    tree_paths: list[str] | None = None,
) -> RetrievalResult:
    paths = tree_paths if tree_paths is not None else ["README.md", "app/main.py"]
    return RetrievalResult(
        repository=METADATA,
        readme=readme,
        files=files or [],
        tree_paths=paths,
        tree_total_entries=len(paths),
        tree_truncated=False,
        skipped={},
        languages={"Python": 10_000, "HTML": 200},
    )


def build(res: RetrievalResult, total: int = 20_000, per_file: int = 2_500):
    return build_context(res, max_total_chars=total, max_chars_per_file=per_file)


def omitted_reasons(context) -> dict[str, str]:
    return {record.path: record.reason for record in context.omitted}


# --- content ------------------------------------------------------------------


def test_includes_metadata_structure_readme_and_files() -> None:
    context = build(result([file("app/main.py", "print('hi')", "entrypoint")]))

    assert "demo/sample" in context.text
    assert "A sample project." in context.text
    assert "MIT" in context.text
    assert "## FILE STRUCTURE" in context.text
    assert "## README" in context.text
    assert "Does a thing." in context.text
    assert "--- FILE: app/main.py [backend] ---" in context.text
    assert "print('hi')" in context.text
    assert context.readme_included is True
    assert list(context.analyzed) == ["app/main.py"]


def test_missing_readme_is_handled() -> None:
    context = build(result([], readme=None))

    assert context.readme_included is False
    assert "## README" not in context.text


def test_sent_files_records_exactly_what_the_model_saw() -> None:
    """`sent_files` is the ground truth evidence validation is checked against."""
    context = build(result([file("app/main.py", "print('hi')", "entrypoint")]))

    assert context.sent_files == {"app/main.py": "print('hi')"}


# --- Feature 10 prioritisation -----------------------------------------------


def test_files_are_ordered_by_prompt_priority() -> None:
    context = build(
        result(
            [
                file("tests/test_api.py", "def test_x(): pass", "source"),
                file("src/util.py", "util", "source"),
                file("package.json", "{}", "manifest"),
                file("app/main.py", "main", "entrypoint"),
                file("app/models/user.py", "class User: pass", "source"),
            ]
        )
    )

    order = list(context.analyzed)
    assert order[0] == "package.json"       # 2. dependency/config manifests
    assert order[1] == "app/main.py"        # 3. entry points
    assert order.index("app/models/user.py") < order.index("tests/test_api.py")
    assert order.index("tests/test_api.py") < order.index("src/util.py")


def test_domain_is_recorded_for_every_analysed_file() -> None:
    context = build(
        result(
            [
                file("frontend/src/App.jsx", "export default function App() {}", "source"),
                file("app/routes.py", "from fastapi import APIRouter", "source"),
                file("tests/test_x.py", "def test_x(): pass", "source"),
            ]
        )
    )

    assert context.analyzed["frontend/src/App.jsx"] == "frontend"
    assert context.analyzed["tests/test_x.py"] == "testing"
    assert context.domain_counts["testing"] == 1


# --- deterministic evidence ---------------------------------------------------


def test_dependencies_are_parsed_into_the_digest() -> None:
    context = build(
        result([file("package.json", '{"dependencies":{"react":"^19.0.0"}}', "manifest")])
    )

    assert "## DECLARED DEPENDENCIES" in context.text
    assert "react" in context.text
    assert "React" in context.declared_technologies
    assert context.manifests[0].ecosystem == "npm"


def test_code_structure_is_extracted_into_the_digest() -> None:
    source = "from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/ping')\ndef ping():\n    return {}\n"
    context = build(result([file("app/main.py", source, "entrypoint")]))

    assert "## EXTRACTED CODE STRUCTURE" in context.text
    assert "HTTP ROUTES FOUND" in context.text
    assert "GET /ping" in context.text


def test_security_scan_results_reach_the_digest() -> None:
    context = build(
        result([file("app/db.py", 'cur.execute(f"SELECT * FROM t WHERE id={x}")', "source")])
    )

    assert "## MECHANICAL SECURITY SCAN" in context.text
    assert "CONFIRMED" in context.text
    assert len(context.security.confirmed) >= 1


def test_clean_repository_reports_confirmed_none() -> None:
    context = build(result([file("app/main.py", "def add(a, b):\n    return a + b\n", "source")]))

    assert "CONFIRMED: none found." in context.text
    assert context.security.confirmed == []


# --- limits -------------------------------------------------------------------


def test_respects_the_total_character_budget() -> None:
    files = [file(f"src/module_{index}.py", "x" * 900) for index in range(50)]

    context = build(result(files), total=5_000)

    assert context.char_count <= 5_000
    assert len(context.omitted) > 0


def test_compresses_individual_files_and_marks_them() -> None:
    """A file too large for its allowance is extracted, not dumped.

    This one is a single 9,000-character line, so there is no line boundary to
    cut at - the degenerate case that still has to stay inside the budget.
    """
    context = build(result([file("src/big.py", "y" * 9_000)]), per_file=1_000)

    assert "src/big.py" in context.truncated
    assert compression.TRUNCATED_EXTRACT_NOTE in context.text
    assert "y" * 1_001 not in context.text


def test_truncates_a_long_readme_and_marks_it() -> None:
    context = build(result([], readme="z" * 9_000), per_file=1_000)

    assert context.readme_truncated is True
    assert TRUNCATION_NOTE in context.text


def test_structure_listing_is_capped() -> None:
    paths = [f"src/file_{index}.py" for index in range(MAX_STRUCTURE_PATHS + 60)]

    context = build(result([], tree_paths=paths))

    assert context.structure_truncated is True
    assert "listing truncated" in context.text


def test_omission_reason_is_recorded() -> None:
    """When the budget runs out, every dropped file says why."""
    files = [file(f"src/module_{index}.py", "x" * 4_000) for index in range(20)]

    context = build(
        result(files, readme=None, tree_paths=[]), total=3_000, per_file=5_000
    )

    reasons = set(omitted_reasons(context).values())
    assert context.omitted
    assert reasons <= {OMIT_BUDGET, OMIT_NO_ALLOWANCE}
    assert len(context.analyzed) + len(context.omitted) == len(files)


# --- security -----------------------------------------------------------------


def test_secret_files_never_reach_the_prompt() -> None:
    """Defence in depth: retrieval already excludes these, so must the prompt."""
    files = [
        file(".env", "SECRET_KEY=abc123xyz", "config"),
        file("backend/.env", "DB_PASSWORD=hunter2", "config"),
        file("certs/server.pem", "-----BEGIN PRIVATE KEY-----", "other"),
        file("app/main.py", "print('safe')", "entrypoint"),
    ]

    context = build(result(files))

    assert list(context.analyzed) == ["app/main.py"]
    assert "SECRET_KEY" not in context.text
    assert "hunter2" not in context.text
    assert "PRIVATE KEY" not in context.text
    assert omitted_reasons(context)[".env"] == OMIT_SECRET


def test_env_templates_are_still_allowed() -> None:
    context = build(result([file(".env.example", "API_KEY=your-key-here", "config")]))

    assert ".env.example" in context.analyzed


def test_hardcoded_secret_is_redacted_in_the_scan_output() -> None:
    """A secret that survived retrieval must not be reproduced in the prompt."""
    context = build(
        result([file("app/config.py", 'API_KEY = "a7Fk29Lm4Xq8Zt6Bv3Nc1Wp5"', "config")])
    )

    scan_section = context.text.split("## MECHANICAL SECURITY SCAN")[1].split("##")[0]
    assert "[REDACTED]" in scan_section
    assert "a7Fk29Lm4Xq8Zt6Bv3Nc1Wp5" not in scan_section


# --- resilience ---------------------------------------------------------------


def test_empty_repository_produces_a_usable_context() -> None:
    context = build(result([], readme=None, tree_paths=[]))

    assert "demo/sample" in context.text
    assert context.analyzed == {}
    assert context.char_count > 0


def test_domain_counts_describe_analysed_files_not_merely_retrieved() -> None:
    """The UI shows these beside the analysed-file list, so they must agree."""
    files = [
        file("app/main.py", "main", "entrypoint"),
        file("src/huge.py", "x" * 4_000, "source"),
    ]

    context = build(
        result(files, readme=None, tree_paths=[]), total=1_200, per_file=5_000
    )

    assert sum(context.domain_counts.values()) == len(context.analyzed)
    assert set(context.domain_counts) == set(context.analyzed.values())
