"""Tests for file exclusion, prioritisation and the retrieval limits."""

from __future__ import annotations

import pytest

from app.services.github.file_filter import (
    TIER_CONFIG,
    TIER_DOC,
    TIER_ENTRYPOINT,
    TIER_MANIFEST,
    TIER_SOURCE,
    classify,
    is_secret_material,
    select_files,
    should_skip,
)


def blob(path: str, size: int = 100) -> dict:
    """Build a GitHub tree entry for a file."""
    return {"path": path, "type": "blob", "size": size}


# --- exclusion ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("node_modules/react/index.js", "ignored_directory"),
        ("frontend/node_modules/lodash/lodash.js", "ignored_directory"),
        ("dist/bundle.js", "ignored_directory"),
        ("build/output.js", "ignored_directory"),
        ("app/__pycache__/main.cpython-311.pyc", "ignored_directory"),
        ("venv/lib/site.py", "ignored_directory"),
        (".venv/lib/site.py", "ignored_directory"),
        (".git/config", "ignored_directory"),
        ("package-lock.json", "generated_file"),
        ("yarn.lock", "generated_file"),
        ("poetry.lock", "generated_file"),
        ("logo.png", "binary_or_media"),
        ("demo.mp4", "binary_or_media"),
        ("app.exe", "binary_or_media"),
        ("fonts/inter.woff2", "binary_or_media"),
        ("static/app.min.js", "minified_bundle"),
        ("bundle.js.map", "binary_or_media"),
    ],
)
def test_skips_noise(path: str, reason: str) -> None:
    assert should_skip(path, 100, 100_000) == reason


@pytest.mark.parametrize(
    "path",
    ["README.md", "package.json", "src/main.py", "Dockerfile", "app/config.yaml"],
)
def test_keeps_useful_files(path: str) -> None:
    assert should_skip(path, 100, 100_000) is None


def test_skips_files_over_the_size_limit() -> None:
    assert should_skip("src/huge.py", 200_000, 100_000) == "too_large"
    assert should_skip("src/small.py", 50_000, 100_000) is None


# --- secret material ----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "backend/.env",
        ".env.local",
        ".env.production",
        "keys/server.pem",
        "certs/private.key",
        "id_rsa",
        "config/secrets.yaml",
        ".npmrc",
    ],
)
def test_never_retrieves_secret_material(path: str) -> None:
    assert is_secret_material(path) is True
    assert should_skip(path, 100, 100_000) == "secret_material"


@pytest.mark.parametrize(
    "path", [".env.example", ".env.sample", "backend/.env.template"]
)
def test_env_templates_are_allowed(path: str) -> None:
    """Templates document configuration and contain placeholders, not secrets."""
    assert is_secret_material(path) is False
    assert should_skip(path, 100, 100_000) is None


# --- prioritisation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "tier"),
    [
        ("README.md", TIER_MANIFEST),
        ("package.json", TIER_MANIFEST),
        ("requirements.txt", TIER_MANIFEST),
        ("pyproject.toml", TIER_MANIFEST),
        ("Dockerfile", TIER_MANIFEST),
        ("Dockerfile.prod", TIER_MANIFEST),
        ("docker-compose.yml", TIER_MANIFEST),
        ("app/main.py", TIER_ENTRYPOINT),
        ("src/index.js", TIER_ENTRYPOINT),
        ("vite.config.js", TIER_CONFIG),
        ("app/settings.yaml", TIER_CONFIG),
        ("src/services/user_service.py", TIER_SOURCE),
        ("src/components/Button.tsx", TIER_SOURCE),
        ("docs/architecture.md", TIER_DOC),
    ],
)
def test_classification(path: str, tier: int) -> None:
    assert classify(path) == tier


def test_manifests_are_selected_before_source_when_budget_is_tight() -> None:
    entries = [
        blob("src/deep/nested/module.py"),
        blob("src/another.py"),
        blob("README.md"),
        blob("package.json"),
    ]

    selected, _ = select_files(
        entries,
        max_files=2,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )

    assert [item.path for item in selected] == ["README.md", "package.json"]


def test_shallower_files_win_within_the_same_tier() -> None:
    entries = [blob("a/b/c/d/service.py"), blob("service.py")]

    selected, _ = select_files(
        entries,
        max_files=2,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )

    assert selected[0].path == "service.py"


# --- limits -------------------------------------------------------------------


def test_file_count_limit_is_enforced() -> None:
    entries = [blob(f"src/module_{index}.py") for index in range(100)]

    selected, skipped = select_files(
        entries,
        max_files=10,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )

    assert len(selected) == 10
    assert skipped["file_count_limit"] == 90


def test_total_size_budget_is_enforced() -> None:
    entries = [blob(f"src/module_{index}.py", size=1_000) for index in range(20)]

    selected, skipped = select_files(
        entries,
        max_files=100,
        max_file_size_bytes=100_000,
        max_total_content_bytes=5_000,
    )

    assert sum(item.size_bytes for item in selected) <= 5_000
    assert len(selected) == 5
    assert skipped["total_size_limit"] == 15


def test_a_huge_repository_stays_bounded() -> None:
    """The whole point: a monorepo must not blow up the retrieval."""
    entries = (
        [blob(f"node_modules/pkg{index}/index.js") for index in range(50_000)]
        + [blob(f"src/module_{index}.py", size=2_000) for index in range(5_000)]
        + [blob("README.md"), blob("package.json")]
    )

    selected, skipped = select_files(
        entries,
        max_files=40,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )

    assert len(selected) <= 40
    assert skipped["ignored_directory"] == 50_000
    assert not any("node_modules" in item.path for item in selected)
    # The manifests still made the cut despite the noise.
    assert "README.md" in [item.path for item in selected]


def test_directories_and_submodules_are_ignored() -> None:
    entries = [
        {"path": "src", "type": "tree"},
        {"path": "libs/external", "type": "commit"},
        blob("src/main.py"),
    ]

    selected, _ = select_files(
        entries,
        max_files=40,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )

    assert [item.path for item in selected] == ["src/main.py"]


# --- unauthenticated safety net -----------------------------------------------


def test_unauthenticated_cap_keeps_one_analysis_inside_the_hourly_limit() -> None:
    """GitHub allows 60 unauthenticated requests/hour, and each analysis costs
    roughly `max_files + 4`. The lower cap must leave room for more than one run.
    """
    from app.core.config import Settings

    settings = Settings(github_token="")
    assert settings.effective_max_files == 15
    assert settings.effective_max_files + 4 < 60

    authenticated = Settings(github_token="fake-token-for-test")
    assert authenticated.effective_max_files == 40


# --- example/demo demotion ----------------------------------------------------


def test_real_source_outranks_manifests_inside_examples() -> None:
    """A library's own source explains it better than its example projects.

    Regression guard: pallets/click filled an entire 15-file budget with
    examples/*/README and examples/*/pyproject.toml, so the model never saw the
    library itself.
    """
    entries = [
        blob("examples/aliases/pyproject.toml"),
        blob("examples/colors/README"),
        blob("examples/inout/pyproject.toml"),
        blob("src/click/core.py"),
        blob("src/click/parser.py"),
        blob("README.md"),
    ]

    # Exactly three primary files exist, so a budget of three proves the point:
    # every slot goes to real content before any example is considered.
    selected, _ = select_files(
        entries,
        max_files=3,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )
    paths = [item.path for item in selected]

    assert paths[0] == "README.md"          # the root README still wins
    assert "src/click/core.py" in paths     # real source made the cut
    assert "src/click/parser.py" in paths
    assert not any(path.startswith("examples/") for path in paths)


def test_examples_are_demoted_not_excluded() -> None:
    """With budget to spare, example files are still worth including."""
    entries = [blob("src/main.py"), blob("examples/demo/app.py")]

    selected, _ = select_files(
        entries,
        max_files=10,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )
    paths = [item.path for item in selected]

    assert paths == ["src/main.py", "examples/demo/app.py"]


def test_category_label_is_unaffected_by_the_demotion() -> None:
    """Demotion changes ordering only - a manifest is still reported as one."""
    entries = [blob("examples/demo/package.json")]

    selected, _ = select_files(
        entries,
        max_files=10,
        max_file_size_bytes=100_000,
        max_total_content_bytes=600_000,
    )

    assert selected[0].category == "manifest"
