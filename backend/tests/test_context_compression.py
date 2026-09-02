"""Tests for Step 8 context compression.

Everything here is deterministic and offline: no network, no model, no I/O. The
same repository and the same budget must always produce the same prompt, so
every assertion below is an equality or a hard bound rather than a heuristic.

The guarantee under test throughout is that compression buys room *without*
weakening evidence: a citation into a shown line must still validate, and the
line numbers in the prompt must be the file's own.
"""

from __future__ import annotations

import re

from app.services.analysis import compression
from app.services.analysis.code_structure import extract_all
from app.services.analysis.context_builder import (
    OMIT_BUDGET,
    OMIT_NO_ALLOWANCE,
    build_context,
)
from app.services.analysis.evidence import (
    EvidenceIndex,
    ValidationStats,
    validate_evidence_items,
)
from app.services.github.service import RetrievalResult, RetrievedFile

METADATA = {
    "name": "sample",
    "full_name": "demo/sample",
    "owner": {"login": "demo"},
    "description": "A sample project.",
    "language": "Python",
    "default_branch": "main",
    "stargazers_count": 5,
    "forks_count": 1,
    "open_issues_count": 0,
    "license": {"spdx_id": "MIT"},
    "topics": [],
    "archived": False,
}


def file(path: str, content: str, category: str = "source") -> RetrievedFile:
    return RetrievedFile(
        path=path, size_bytes=len(content), category=category, content=content
    )


def result(files: list[RetrievedFile], readme: str | None = "# Sample") -> RetrievalResult:
    # The Git tree is sorted by path, whatever order the files were fetched in.
    paths = sorted(item.path for item in files)
    return RetrievalResult(
        repository=METADATA,
        readme=readme,
        files=files,
        tree_paths=paths,
        tree_total_entries=len(paths),
        tree_truncated=False,
        skipped={},
        languages={"Python": 10_000},
    )


def build(files: list[RetrievedFile], *, total=8_000, per_file=2_500, terms=None):
    return build_context(
        result(files),
        max_total_chars=total,
        max_chars_per_file=per_file,
        query_terms=terms,
    )


def big_module(name: str, functions: int = 30) -> str:
    """A file far too large to send whole, with parseable declarations."""
    parts = [f'"""Module {name}."""', "", "import os", ""]
    for index in range(functions):
        parts.append(f"def {name}_operation_{index}(payload):")
        parts.append(f'    """Handle operation {index}."""')
        parts.append("    value = payload.get('value')")
        parts.append("    result = value * 2")
        parts.append("    return {'result': result, 'padding': '" + "p" * 120 + "'}")
        parts.append("")
    return "\n".join(parts)


def structure_for(path: str, content: str):
    extracted = extract_all({path: content})
    return extracted[0] if extracted else None


# --- 1. the total limit is never exceeded -------------------------------------


def test_context_never_exceeds_the_configured_limit() -> None:
    """Whatever is thrown at it, the prompt fits. This is the Step 8 headline."""
    files = [file(f"app/service_{index}.py", big_module(f"svc{index}")) for index in range(25)]

    for limit in (2_000, 4_000, 8_000, 16_000):
        context = build(files, total=limit)
        assert context.char_count <= limit
        assert len(context.text) <= limit


def test_a_repository_of_huge_files_still_produces_a_usable_prompt() -> None:
    context = build([file("app/main.py", big_module("main", functions=200), "entrypoint")])

    assert context.char_count <= 8_000
    assert "app/main.py" in context.analyzed


# --- 2. snippets, not whole files ---------------------------------------------


def test_large_file_is_sent_as_extracts_rather_than_dropped() -> None:
    """The Step 8 problem statement: fifteen files retrieved, two in the prompt."""
    files = [
        file("requirements.txt", "fastapi==0.115.0\nhttpx==0.27.0\n", "manifest"),
        file("app/main.py", big_module("main"), "entrypoint"),
        file("app/routes.py", big_module("routes")),
        file("app/models.py", big_module("models")),
        file("app/db.py", big_module("db")),
        file("tests/test_main.py", big_module("test")),
    ]

    context = build(files)

    assert len(context.analyzed) >= 5
    assert context.snippets
    assert context.char_count <= 8_000


def test_small_file_is_sent_whole_and_not_marked_truncated() -> None:
    content = "def add(a, b):\n    return a + b\n"
    context = build([file("app/util.py", content)])

    assert "app/util.py" not in context.truncated
    assert "return a + b" in context.text


def test_extraction_prefers_declarations_over_the_file_head() -> None:
    content = big_module("payments")
    compressed = compression.extract_snippets(
        "app/payments.py", content, structure_for("app/payments.py", content), allowance=900
    )

    assert not compressed.whole
    assert compressed.snippets
    assert any("payments_operation_" in snippet.text for snippet in compressed.snippets)
    assert compressed.lines_shown < compressed.total_lines


def test_unparseable_file_falls_back_to_its_opening_lines() -> None:
    content = "\n".join(f"line {index} of an unparseable blob" for index in range(400))
    compressed = compression.extract_snippets(
        "data/notes.txt", content, None, allowance=900
    )

    assert compressed.snippets[0].start_line == 1
    assert compressed.snippets[0].reason == "opening lines"


# --- 3. line numbers survive compression --------------------------------------


def test_snippet_line_numbers_match_the_original_file() -> None:
    content = big_module("orders")
    lines = content.splitlines()
    compressed = compression.extract_snippets(
        "app/orders.py", content, structure_for("app/orders.py", content), allowance=1_200
    )

    for snippet in compressed.snippets:
        assert 1 <= snippet.start_line <= snippet.end_line <= len(lines)
        # The rendered text must be exactly the file's own lines at that range.
        expected = "\n".join(lines[snippet.start_line - 1 : snippet.end_line])
        assert snippet.text.split(f"\n{compression.TRUNCATED_EXTRACT_NOTE}")[0] in expected


def test_rendered_headers_state_the_original_line_range() -> None:
    content = big_module("billing")
    context = build([file("app/billing.py", content)], total=6_000)
    lines = content.splitlines()

    ranges = re.findall(r"\[lines (\d+)-(\d+)\]", context.text)
    assert ranges, "extracts must be labelled with their real line range"
    for start, end in ranges:
        assert 1 <= int(start) <= int(end) <= len(lines)

    # At least one extract starts below the file head, proving numbers are not
    # renumbered from 1 for every block.
    assert any(int(start) > 1 for start, _ in ranges)


def test_declaration_line_from_structure_is_inside_a_shown_range() -> None:
    """A cited declaration must fall inside a range the model actually saw."""
    content = big_module("catalog")
    structure = structure_for("app/catalog.py", content)
    compressed = compression.extract_snippets(
        "app/catalog.py", content, structure, allowance=1_500
    )

    shown = [(s.start_line, s.end_line) for s in compressed.snippets]
    first = structure.functions[0]
    assert any(start <= first.line <= end for start, end in shown)


# --- 4. high-priority files are always represented ----------------------------


def test_entrypoint_and_manifest_outrank_documentation() -> None:
    files = [
        file("docs/guide.md", "# Guide\n" + ("prose. " * 800), "docs"),
        file("app/main.py", big_module("main"), "entrypoint"),
        file("package.json", '{"name": "demo", "dependencies": {"react": "19.0.0"}}', "manifest"),
    ]

    context = build(files, total=4_000)

    assert "app/main.py" in context.analyzed
    assert "package.json" in context.analyzed


def test_high_band_file_receives_more_room_than_a_low_band_one() -> None:
    allowances = compression.allocate(
        [("app/main.py", "high"), ("docs/guide.md", "low")], 4_000
    )

    assert allowances["app/main.py"] > allowances["docs/guide.md"]


def test_allocation_never_promises_more_than_the_budget() -> None:
    bands = [(f"file_{index}.py", band) for index, band in enumerate(["high", "medium", "low"] * 8)]
    allowances = compression.allocate(bands, 5_000)

    assert sum(allowances.values()) <= 5_000
    assert all(value == 0 or value >= compression.MIN_USEFUL_ALLOWANCE for value in allowances.values())


# --- 5. manifests are preserved -----------------------------------------------


def test_manifest_dependencies_reach_the_prompt_even_under_pressure() -> None:
    files = [
        file("requirements.txt", "fastapi==0.115.0\nsqlalchemy==2.0.30\n", "manifest"),
        *[file(f"app/mod_{index}.py", big_module(f"m{index}")) for index in range(12)],
    ]

    context = build(files, total=3_500)

    assert "requirements.txt" in context.analyzed
    assert "sqlalchemy" in context.text


def test_readme_is_preserved_alongside_compressed_source() -> None:
    context = build_context(
        result(
            [file("app/main.py", big_module("main"), "entrypoint")],
            readme="# Sample\n\nA service that books appointments.",
        ),
        max_total_chars=5_000,
        max_chars_per_file=2_500,
    )

    assert "books appointments" in context.text


# --- 6. security evidence is preserved ----------------------------------------


def test_security_scan_findings_survive_compression() -> None:
    vulnerable = "\n".join(
        [
            "import subprocess",
            "",
            "def run(cmd):",
            "    subprocess.run(cmd, shell=True)",
            "",
            *[f"def filler_{index}():\n    return {index}" for index in range(200)],
        ]
    )
    files = [file("app/exec.py", vulnerable), *[file(f"app/pad_{i}.py", big_module(f"p{i}")) for i in range(8)]]

    context = build(files, total=4_000)

    hits = context.security.confirmed + context.security.potential
    assert hits, "the mechanical scan must still run on full files"
    assert any(item.file == "app/exec.py" for item in hits)
    assert any("shell" in item.title.lower() for item in hits)


def test_security_scan_reads_full_files_not_extracts() -> None:
    """The scan runs before compression, so a finding deep in a file is kept."""
    body = ["import subprocess", "", "def head():", "    return 1", ""]
    body += [f"def filler_{index}():\n    return {index}\n" for index in range(300)]
    body += ["def run_last(cmd):", "    subprocess.run(cmd, shell=True)"]
    content = "\n".join(body)
    deep_line = len(content.splitlines())

    context = build([file("app/security.py", content)], total=3_000)

    hits = context.security.confirmed + context.security.potential
    assert hits
    # The hit sits far below anything the extract could have shown.
    assert max(item.line for item in hits) > deep_line - 10
    assert "app/security.py" in context.truncated


# --- 7. query-aware selection -------------------------------------------------


def test_query_terms_pull_matching_declarations_into_the_extract() -> None:
    parts = ['"""Service."""', ""]
    for index in range(25):
        parts += [f"def helper_{index}(x):", f"    return x + {index}", ""]
    parts += ["def authenticate_user(token):", "    return verify(token)", ""]
    content = "\n".join(parts)

    without = compression.extract_snippets(
        "app/svc.py", content, structure_for("app/svc.py", content), allowance=600
    )
    with_terms = compression.extract_snippets(
        "app/svc.py",
        content,
        structure_for("app/svc.py", content),
        allowance=600,
        terms=("authenticate",),
    )

    assert "authenticate_user" not in "".join(s.text for s in without.snippets)
    assert "authenticate_user" in "".join(s.text for s in with_terms.snippets)


def test_query_terms_flow_through_build_context() -> None:
    """A job skill, or an interview question's subject, reaches the extractor."""
    padding = "q" * 200
    parts = ['"""Api."""', ""]
    for index in range(35):
        parts += [
            f"def helper_{index}(x):",
            f"    # padding so the file cannot be sent whole: {padding}",
            f"    return x + {index}",
            "",
        ]
    parts += ["def issue_jwt_token(user):", "    return sign(user)", ""]
    content = "\n".join(parts)
    files = [file("app/api.py", content)]

    plain = build(files, total=5_000)
    targeted = build(files, total=5_000, terms=["jwt"])

    # Compared against the file-contents section only: the code-structure digest
    # names every declaration either way, so it cannot show what was extracted.
    def excerpts(context) -> str:
        return context.text.split("## FILE CONTENTS", 1)[-1]

    assert "def issue_jwt_token" in excerpts(targeted)
    assert "def issue_jwt_token" not in excerpts(plain)


# --- 8. omitted files explain themselves --------------------------------------


def test_every_omitted_file_carries_a_reason() -> None:
    files = [file(f"app/mod_{index}.py", big_module(f"m{index}")) for index in range(30)]

    context = build(files, total=3_000)

    assert context.omitted
    for record in context.omitted:
        assert record.reason in {OMIT_BUDGET, OMIT_NO_ALLOWANCE}
        assert record.path


def test_analysed_and_omitted_together_account_for_every_file() -> None:
    files = [file(f"app/mod_{index}.py", big_module(f"m{index}")) for index in range(20)]

    context = build(files, total=4_000)

    assert len(context.analyzed) + len(context.omitted) == len(files)


def test_compression_ratio_is_reported_for_extracted_files() -> None:
    context = build([file("app/main.py", big_module("main"), "entrypoint")], total=5_000)

    shown, total = context.compression_ratio["app/main.py"]
    assert 0 < shown < total


# --- 9. deterministic ordering ------------------------------------------------


def test_same_input_produces_a_byte_identical_prompt() -> None:
    files = [file(f"app/mod_{index}.py", big_module(f"m{index}")) for index in range(10)]

    first = build(files, total=6_000)
    second = build(files, total=6_000)

    assert first.text == second.text
    assert list(first.analyzed) == list(second.analyzed)
    assert [s.start_line for s in first.snippets] == [s.start_line for s in second.snippets]


def test_retrieval_order_does_not_change_the_prompt() -> None:
    files = [file(f"app/mod_{index}.py", big_module(f"m{index}")) for index in range(8)]

    forward = build(files, total=6_000)
    backward = build(list(reversed(files)), total=6_000)

    assert forward.text == backward.text


# --- 10. no duplicated content ------------------------------------------------


def test_overlapping_declarations_are_merged_not_repeated() -> None:
    content = "\n".join(
        [
            "class Session:",
            '    """A session."""',
            "",
            "    def open(self):",
            "        return True",
            "",
            "    def close(self):",
            "        return False",
        ]
        + [f"# padding line {index}" for index in range(400)]
    )
    compressed = compression.extract_snippets(
        "app/session.py", content, structure_for("app/session.py", content), allowance=2_000
    )

    ranges = [(s.start_line, s.end_line) for s in compressed.snippets]
    for index, (start, end) in enumerate(ranges):
        for other_start, other_end in ranges[index + 1 :]:
            assert end < other_start or other_end < start, "ranges must not overlap"


def test_a_file_appears_once_in_the_prompt() -> None:
    files = [
        file("app/main.py", big_module("main"), "entrypoint"),
        file("app/routes.py", big_module("routes")),
    ]

    context = build(files, total=8_000)

    for path in context.analyzed:
        assert context.text.count(f"--- FILE: {path} [") == 1


def test_merge_collapses_adjacent_ranges() -> None:
    merged = compression._merge(
        [(1, 10, "class A", 0), (11, 20, "method A.b", 3), (60, 70, "def c", 1)]
    )

    # The merged block keeps the earlier reason and the stronger priority.
    assert merged == [(1, 20, "class A", 0), (60, 70, "def c", 1)]


# --- 11. evidence validation still works --------------------------------------


def cite(index, path, start, end):
    """Run one citation through the real validator and report what survived."""
    stats = ValidationStats()
    kept = validate_evidence_items(
        [{"file": path, "line_start": start, "line_end": end, "reason": "checked"}],
        index,
        stats,
    )
    return kept, stats


def test_citation_into_a_shown_snippet_validates() -> None:
    content = big_module("inventory")
    context = build([file("app/inventory.py", content, "entrypoint")], total=6_000)

    index = EvidenceIndex.from_files(context.evidence_files)
    snippet = context.snippets[0]
    kept, stats = cite(index, "app/inventory.py", snippet.start_line, snippet.end_line)

    assert kept and kept[0]["line_start"] == snippet.start_line
    assert stats.line_numbers_cleared == 0


def test_evidence_index_keeps_full_file_length_after_compression() -> None:
    """Indexing only the extract would reject valid citations further down."""
    content = big_module("shipping")
    context = build([file("app/shipping.py", content, "entrypoint")], total=6_000)

    index = EvidenceIndex.from_files(context.evidence_files)
    last_line = len(content.splitlines())
    kept, stats = cite(index, "app/shipping.py", last_line, last_line)

    assert kept and kept[0]["line_start"] == last_line
    assert stats.line_numbers_cleared == 0


def test_citation_beyond_the_file_is_still_rejected() -> None:
    content = big_module("returns")
    context = build([file("app/returns.py", content, "entrypoint")], total=6_000)

    index = EvidenceIndex.from_files(context.evidence_files)
    kept, stats = cite(index, "app/returns.py", 99_999, 100_000)

    assert kept and kept[0]["line_start"] is None
    assert stats.line_numbers_cleared == 1


def test_unknown_file_is_still_rejected() -> None:
    context = build([file("app/main.py", big_module("main"), "entrypoint")], total=6_000)
    index = EvidenceIndex.from_files(context.evidence_files)

    kept, stats = cite(index, "app/invented.py", 1, 5)

    assert kept == []
    assert stats.evidence_dropped_unknown_file == 1


def test_every_analysed_file_is_indexable_as_evidence() -> None:
    files = [file(f"app/mod_{index}.py", big_module(f"m{index}")) for index in range(6)]
    context = build(files, total=8_000)

    index = EvidenceIndex.from_files(context.evidence_files)
    for path in context.analyzed:
        assert index.resolve(path) == path


# --- 12. the prelude cannot crowd out the code --------------------------------


def test_source_extracts_survive_a_huge_readme_and_file_listing() -> None:
    """The regression that made Step 8 necessary in the first place.

    On psf/requests the metadata, the file listing, the facts digest and a
    2,500-character README consumed nearly the whole budget, and the model was
    left looking at a Makefile. Code is reserved first now.
    """
    files = [
        file("app/main.py", big_module("main"), "entrypoint"),
        file("app/routes.py", big_module("routes")),
        file("app/models.py", big_module("models")),
    ]
    retrieval = result(files, readme="# Project\n\n" + ("prose. " * 2_000))
    retrieval.tree_paths = [f"docs/page_{index}.md" for index in range(400)]
    retrieval.tree_total_entries = len(retrieval.tree_paths)

    context = build_context(
        retrieval, max_total_chars=8_000, max_chars_per_file=2_500
    )

    assert "## FILE CONTENTS" in context.text
    assert "app/main.py" in context.analyzed
    assert context.char_count <= 8_000


def test_readme_is_capped_so_it_cannot_take_the_whole_budget() -> None:
    retrieval = result(
        [file("app/main.py", big_module("main"), "entrypoint")],
        readme="# Project\n\n" + ("prose. " * 2_000),
    )

    context = build_context(
        retrieval, max_total_chars=8_000, max_chars_per_file=2_500
    )

    readme_block = context.text.split("## README", 1)[-1].split("## ", 1)[0]
    assert context.readme_truncated is True
    assert len(readme_block) <= int(8_000 * 0.12) + 200
    assert "app/main.py" in context.analyzed


def test_a_small_repository_still_sends_its_files_whole() -> None:
    """The reserve is a floor for code, not a ceiling on the prelude."""
    content = "def add(a, b):\n    return a + b\n"
    context = build([file("app/util.py", content)], total=8_000)

    assert "app/util.py" in context.analyzed
    assert "app/util.py" not in context.truncated
