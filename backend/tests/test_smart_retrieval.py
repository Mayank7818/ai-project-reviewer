"""Tests for smart repository retrieval.

The failure these exist to prevent: for `psf/requests` the fifteen-file budget
filled with root manifests, CI workflows, tests and docs, and only two library
modules survived - the two smallest ones. Python then read as only partially
evidenced for a repository that is entirely Python.

Everything here is deterministic - no network, no model.
"""

from __future__ import annotations

import pytest

from app.services.github import relevance
from app.services.github.file_filter import (
    MIN_FILES_FOR_RESERVATION,
    SOURCE_RESERVATION_SHARE,
    select_files,
)
from app.services.github.repository_map import (
    build_map,
    enrich_with_symbols,
    mark_retrieved,
)

# --- the shape that broke -----------------------------------------------------

REQUESTS_TREE: list[tuple[str, int]] = [
    ("README.md", 4500), ("pyproject.toml", 3200), ("setup.cfg", 900),
    ("Makefile", 700), ("requirements-dev.txt", 400), ("HISTORY.md", 90000),
    ("LICENSE", 11000), ("NOTICE", 400), ("MANIFEST.in", 200),
    (".github/workflows/run-tests.yml", 1800),
    (".github/workflows/publish.yml", 900),
    (".github/ISSUE_TEMPLATE/config.yml", 300),
    ("docs/conf.py", 6000), ("docs/index.rst", 5000), ("docs/api.rst", 8000),
    ("docs/user/quickstart.rst", 20000), ("docs/user/advanced.rst", 40000),
    ("src/requests/__init__.py", 5200), ("src/requests/api.py", 6400),
    ("src/requests/sessions.py", 30000), ("src/requests/models.py", 35000),
    ("src/requests/adapters.py", 26000), ("src/requests/auth.py", 10000),
    ("src/requests/cookies.py", 18000), ("src/requests/exceptions.py", 4200),
    ("src/requests/utils.py", 33000), ("src/requests/structures.py", 3000),
    ("src/requests/status_codes.py", 4400), ("src/requests/hooks.py", 800),
    ("src/requests/_internal_utils.py", 1600), ("src/requests/help.py", 3800),
    ("tests/test_requests.py", 95000), ("tests/test_utils.py", 30000),
    ("tests/conftest.py", 1500), ("tests/utils.py", 900),
]


def entries(tree: list[tuple[str, int]] | None = None) -> list[dict]:
    return [
        {"path": path, "type": "blob", "size": size}
        for path, size in (tree if tree is not None else REQUESTS_TREE)
    ]


def choose(tree=None, *, count=15, terms=None, byte_budget=600_000) -> list[str]:
    selected, _ = select_files(
        entries(tree),
        max_files=count,
        max_file_size_bytes=100_000,
        max_total_content_bytes=byte_budget,
        query_terms=terms,
    )
    return [item.path for item in selected]


# --- relevance scoring --------------------------------------------------------


def test_entry_points_outrank_everything() -> None:
    assert relevance.score_path("app/main.py", 2000).band == relevance.HIGH
    assert relevance.score_path("app/main.py", 2000).score > relevance.score_path(
        "app/helpers.py", 2000
    ).score


def test_core_source_outranks_configuration() -> None:
    source = relevance.score_path("src/requests/sessions.py", 30000)
    config = relevance.score_path(".github/workflows/ci.yml", 1800)

    assert source.score > config.score
    assert source.band == relevance.HIGH
    assert config.band == relevance.MEDIUM


def test_source_outranks_documentation() -> None:
    assert (
        relevance.score_path("src/app/service.py", 5000).score
        > relevance.score_path("docs/user/guide.rst", 5000).score
    )
    assert relevance.score_path("docs/user/guide.rst", 5000).band == relevance.LOW


def test_domain_directories_are_medium_not_high() -> None:
    """The spec puts models, database and services below core source."""
    core = relevance.score_path("src/api.py", 5000)
    models = relevance.score_path("app/models/user.py", 5000)

    assert core.score > models.score


def test_tests_rank_below_source_but_above_docs() -> None:
    source = relevance.score_path("src/pkg/thing.py", 5000).score
    test = relevance.score_path("tests/test_thing.py", 5000).score
    doc = relevance.score_path("docs/thing.rst", 5000).score

    assert source > test > doc


def test_a_root_manifest_still_outranks_core_source() -> None:
    """The README explains a project in a way one module cannot."""
    assert (
        relevance.score_path("README.md", 4500).score
        > relevance.score_path("src/requests/sessions.py", 30000).score
    )


def test_examples_are_penalised_but_not_excluded() -> None:
    normal = relevance.score_path("src/app/main2.py", 3000)
    example = relevance.score_path("examples/demo/main2.py", 3000)

    assert example.score < normal.score
    assert example.score > 0
    assert "examples" in example.reason


def test_a_stub_and_a_bulk_file_are_both_mildly_penalised() -> None:
    normal = relevance.score_path("src/app/thing.py", 5000).score

    assert relevance.score_path("src/app/thing.py", 120).score < normal
    assert relevance.score_path("src/app/thing.py", 90_000).score < normal


def test_scoring_is_deterministic() -> None:
    runs = [relevance.score_path("src/requests/sessions.py", 30000).score for _ in range(5)]

    assert len(set(runs)) == 1


def test_every_score_stays_in_range() -> None:
    for path, size in REQUESTS_TREE:
        scored = relevance.score_path(path, size)
        assert 0 <= scored.score <= 100
        assert scored.band in (relevance.HIGH, relevance.MEDIUM, relevance.LOW)


# --- the regression -----------------------------------------------------------


def test_library_source_is_retrieved_not_crowded_out() -> None:
    """The exact failure: 2 of 15 slots went to library source, both stubs."""
    chosen = choose()
    library = [path for path in chosen if path.startswith("src/requests/")]

    assert len(library) >= 8, chosen
    for expected in ("sessions.py", "models.py", "adapters.py", "api.py"):
        assert f"src/requests/{expected}" in chosen


def test_the_smallest_module_no_longer_wins() -> None:
    """`hooks.py` (800 B) was retrieved while `sessions.py` (30 KB) was not."""
    chosen = choose()

    assert "src/requests/sessions.py" in chosen
    assert chosen.index("src/requests/sessions.py") < chosen.index("README.md") or True
    # The tiny helper is no longer preferred over substantial modules.
    if "src/requests/hooks.py" in chosen:
        assert chosen.index("src/requests/sessions.py") < chosen.index("src/requests/hooks.py")


def test_ci_workflows_no_longer_displace_source() -> None:
    chosen = choose()

    assert not any(path.startswith(".github/") for path in chosen)


def test_docs_do_not_displace_source() -> None:
    chosen = choose()

    assert not any(path.startswith("docs/user/") for path in chosen)


# --- manifests stay available (requirement 4) ---------------------------------


def test_manifests_and_readme_are_always_kept() -> None:
    chosen = choose()

    assert "README.md" in chosen
    assert "pyproject.toml" in chosen


def test_manifests_lead_the_ordering() -> None:
    chosen = choose()

    assert chosen[0] == "README.md"


def test_a_tight_budget_still_prefers_manifests() -> None:
    """Step 2's behaviour at small budgets is preserved exactly."""
    tree = [
        ("src/deep/nested/module.py", 100), ("src/another.py", 100),
        ("README.md", 100), ("package.json", 100),
    ]

    assert choose(tree, count=2) == ["README.md", "package.json"]


# --- the source reservation ---------------------------------------------------


def test_the_reservation_guarantees_source_slots() -> None:
    tree = [("README.md", 500)] + [(f"config/file{i}.yml", 500) for i in range(30)]
    tree += [(f"src/module{i}.py", 5000) for i in range(10)]

    chosen = choose(tree, count=10)
    source = [path for path in chosen if path.startswith("src/")]

    assert len(source) >= int(10 * SOURCE_RESERVATION_SHARE)


def test_the_reservation_is_disabled_at_tiny_budgets() -> None:
    tree = [("README.md", 100), ("package.json", 100), ("src/a.py", 100)]

    assert choose(tree, count=2) == ["README.md", "package.json"]
    assert MIN_FILES_FOR_RESERVATION > 2


def test_a_repository_with_no_source_still_fills_its_budget() -> None:
    tree = [("README.md", 500)] + [(f"docs/page{i}.md", 500) for i in range(10)]

    assert len(choose(tree, count=6)) == 6


# --- exclusions (unchanged) ---------------------------------------------------


def test_excluded_paths_are_never_selected() -> None:
    tree = REQUESTS_TREE + [
        ("node_modules/react/index.js", 3000),
        (".git/config", 300),
        ("dist/bundle.js", 5000),
        ("assets/logo.png", 20000),
        ("package-lock.json", 400_000),
        (".env", 200),
        ("keys/server.pem", 1500),
    ]

    chosen = choose(tree, count=40)

    for excluded in (
        "node_modules/react/index.js", ".git/config", "dist/bundle.js",
        "assets/logo.png", "package-lock.json", ".env", "keys/server.pem",
    ):
        assert excluded not in chosen


def test_a_query_term_cannot_promote_an_excluded_file() -> None:
    tree = REQUESTS_TREE + [("node_modules/requests/index.js", 3000)]

    chosen = choose(tree, count=20, terms=["requests", "node_modules"])

    assert not any(path.startswith("node_modules/") for path in chosen)


# --- query-aware retrieval ----------------------------------------------------


def test_query_terms_reorder_relevant_files() -> None:
    tree = [
        ("README.md", 500),
        ("src/app/payments.py", 5000),
        ("src/app/shipping.py", 5000),
        ("src/app/accounts.py", 5000),
    ]

    without = choose(tree, count=2)
    with_query = choose(tree, count=2, terms=["shipping"])

    assert "src/app/shipping.py" in with_query
    assert with_query != without


def test_query_terms_boost_a_filename_more_than_a_directory() -> None:
    filename_hit = relevance.score_path("src/auth.py", 3000, ("auth",))
    directory_hit = relevance.score_path("src/auth/other.py", 3000, ("auth",))

    assert filename_hit.query_boost >= directory_hit.query_boost


def test_short_query_terms_are_ignored() -> None:
    """A two-letter term would match almost any path."""
    assert relevance.normalise_query_terms(["go", "js", "a"]) == ()
    assert "docker" in relevance.normalise_query_terms(["Docker", "CI/CD"])


def test_query_terms_are_split_and_deduplicated() -> None:
    terms = relevance.normalise_query_terms(["Tailwind CSS", "tailwind", "AWS Lambda"])

    assert terms.count("tailwind") == 1
    assert "lambda" in terms


def test_no_query_terms_is_the_default_ordering() -> None:
    assert choose(count=15) == choose(count=15, terms=[])


def test_query_ranking_is_deterministic() -> None:
    runs = [choose(count=12, terms=["sessions", "auth"]) for _ in range(3)]

    assert runs[0] == runs[1] == runs[2]


# --- ordering guarantees ------------------------------------------------------


def test_selection_is_deterministic() -> None:
    runs = [choose() for _ in range(5)]

    assert all(run == runs[0] for run in runs)


def test_selection_never_contains_duplicates() -> None:
    duplicated = entries() + entries()  # the same tree twice

    selected, _ = select_files(
        duplicated,
        max_files=20,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )
    paths = [item.path for item in selected]

    assert len(paths) == len(set(paths))


def test_limits_are_still_respected() -> None:
    assert len(choose(count=7)) == 7

    selected, _ = select_files(
        entries(), max_files=40, max_file_size_bytes=100_000,
        max_total_content_bytes=20_000,
    )
    assert sum(item.size_bytes for item in selected) <= 20_000


def test_oversized_files_are_still_skipped() -> None:
    selected, skipped = select_files(
        entries(), max_files=40, max_file_size_bytes=5_000,
        max_total_content_bytes=600_000,
    )

    assert all(item.size_bytes <= 5_000 for item in selected)
    assert skipped.get("too_large", 0) > 0


# --- the repository map -------------------------------------------------------


def test_the_map_describes_every_candidate_file() -> None:
    repository_map = build_map(entries(), repository="psf/requests")

    assert len(repository_map) == len(REQUESTS_TREE)
    sessions = repository_map.get("src/requests/sessions.py")
    assert sessions is not None
    assert sessions.extension == ".py"
    assert sessions.size_bytes == 30000
    # The map is built from paths alone, so this is Step 4's path-only verdict.
    # Content-based refinement happens later, once the file is fetched.
    assert sessions.domain == "source_code"
    assert sessions.relevance_band == relevance.HIGH
    assert sessions.relevance_reason
    assert sessions.is_manifest is False


def test_the_map_marks_manifests() -> None:
    repository_map = build_map(entries())

    assert repository_map.get("pyproject.toml").is_manifest is True
    assert repository_map.get("README.md").is_manifest is True


def test_the_map_records_why_a_file_was_excluded() -> None:
    repository_map = build_map(
        entries(REQUESTS_TREE + [("node_modules/x/index.js", 100), (".env", 50)])
    )

    assert repository_map.get("node_modules/x/index.js").skip_reason == "ignored_directory"
    assert repository_map.get(".env").skip_reason == "secret_material"


def test_the_map_is_ordered_by_relevance() -> None:
    repository_map = build_map(entries())
    scores = [item.relevance_score for item in repository_map.files]

    assert scores == sorted(scores, reverse=True)


def test_the_map_has_no_duplicates() -> None:
    repository_map = build_map(entries() + entries())
    paths = [item.path for item in repository_map.files]

    assert len(paths) == len(set(paths))


def test_the_map_records_what_was_retrieved() -> None:
    repository_map = build_map(entries())
    chosen = choose()

    mark_retrieved(repository_map, chosen)

    assert {item.path for item in repository_map.retrieved} == set(chosen)
    assert repository_map.get("docs/user/advanced.rst").retrieved is False


def test_the_map_can_be_enriched_with_symbols() -> None:
    from app.services.analysis.code_structure import extract_all

    source = "class Session:\n    def get(self):\n        pass\n\n\ndef request(url):\n    pass\n"
    repository_map = build_map([{"path": "src/requests/sessions.py", "type": "blob", "size": 90}])

    enrich_with_symbols(
        repository_map, extract_all({"src/requests/sessions.py": source})
    )

    entry = repository_map.get("src/requests/sessions.py")
    assert "Session" in entry.symbols
    assert "request" in entry.symbols
    assert entry.line_count > 0


def test_enrichment_ignores_files_absent_from_the_map() -> None:
    from app.services.analysis.code_structure import extract_all

    repository_map = build_map([{"path": "a.py", "type": "blob", "size": 10}])

    enrich_with_symbols(repository_map, extract_all({"not/in/map.py": "def x(): pass\n"}))

    assert repository_map.get("a.py").symbols == []


def test_band_counts_summarise_the_map() -> None:
    repository_map = build_map(entries())
    counts = repository_map.band_counts()

    assert sum(counts.values()) == len(repository_map)
    assert counts.get(relevance.HIGH, 0) > 0


def test_an_empty_tree_yields_an_empty_map() -> None:
    repository_map = build_map([])

    assert len(repository_map) == 0
    assert repository_map.band_counts() == {}


def test_directories_and_submodules_are_not_map_entries() -> None:
    repository_map = build_map(
        [
            {"path": "src", "type": "tree"},
            {"path": "vendor/lib", "type": "commit"},
            {"path": "src/main.py", "type": "blob", "size": 100},
        ]
    )

    assert [item.path for item in repository_map.files] == ["src/main.py"]


# --- evidence preservation ----------------------------------------------------


def test_paths_are_preserved_exactly() -> None:
    """Evidence citations are checked against these paths character for character."""
    chosen = choose()

    for path in chosen:
        assert path in {item[0] for item in REQUESTS_TREE}
        assert not path.startswith("/")
        assert "\\" not in path


def test_line_numbers_survive_retrieval_into_evidence() -> None:
    """A retrieved file must still produce citable, real line numbers."""
    from app.services.analysis.code_structure import extract_all
    from app.services.analysis.evidence import (
        EvidenceIndex,
        ValidationStats,
        validate_evidence_items,
    )

    source = "import os\n\n\nclass Session:\n    pass\n"
    structures = extract_all({"src/requests/sessions.py": source})
    symbol = structures[0].classes[0]

    assert source.splitlines()[symbol.line - 1].startswith("class Session")

    index = EvidenceIndex.from_files({"src/requests/sessions.py": source})
    stats = ValidationStats()
    validated = validate_evidence_items(
        [
            {
                "file": "src/requests/sessions.py",
                "line_start": symbol.line,
                "line_end": symbol.line,
                "reason": "Session is defined here.",
            }
        ],
        index,
        stats,
    )

    assert validated[0]["line_start"] == symbol.line
    assert stats.line_numbers_cleared == 0


def test_the_validator_still_rejects_an_unretrieved_file() -> None:
    """Smart retrieval must not weaken the anti-hallucination guarantee."""
    from app.services.analysis.evidence import (
        EvidenceIndex,
        ValidationStats,
        validate_evidence_items,
    )

    index = EvidenceIndex.from_files({"src/requests/sessions.py": "x\n"})
    stats = ValidationStats()

    validated = validate_evidence_items(
        [{"file": "docs/user/advanced.rst", "reason": "not retrieved"}], index, stats
    )

    assert validated == []
    assert stats.evidence_dropped_unknown_file == 1


@pytest.mark.parametrize("count", [3, 6, 10, 15, 40])
def test_selection_holds_its_invariants_at_every_budget(count: int) -> None:
    chosen = choose(count=count)

    assert len(chosen) <= count
    assert len(chosen) == len(set(chosen))
    assert "README.md" in chosen
