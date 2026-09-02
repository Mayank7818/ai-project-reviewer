"""Tests for question seeds and role fitting.

The property these enforce: a seed only exists when the repository genuinely
evidences it, and every seed carries a real citation. Seeds are what make the
interview project-specific rather than generic, so an unevidenced seed is a bug.
"""

from __future__ import annotations

import pytest

from app.services.analysis.code_structure import extract_all
from app.services.analysis.dependencies import analyse_dependencies, infer_technologies
from app.services.analysis.security_scan import SecurityScanReport, scan_files
from app.services.interview import roles as role_module
from app.services.interview import seeds as seed_module

MAIN_PY = '''from fastapi import FastAPI
from app.auth import authenticate_user

app = FastAPI()


@app.get("/products/{pid}")
async def read_product(pid: int):
    return db.execute(f"SELECT * FROM products WHERE id={pid}")
'''

AUTH_PY = '''import jwt


class TokenService:
    def issue(self, user):
        return jwt.encode({"sub": user}, key)


def authenticate_user(username, password):
    return bcrypt.checkpw(password, lookup(username))
'''

FILES = {
    "README.md": "# Shop API\n\nA FastAPI storefront.",
    "requirements.txt": "fastapi==0.121\nsqlalchemy>=2\npyjwt\n",
    "app/main.py": MAIN_PY,
    "app/auth.py": AUTH_PY,
    "Dockerfile": "FROM python:3.12\n",
}

ANALYZED = {
    "README.md": "documentation",
    "requirements.txt": "configuration",
    "app/main.py": "backend",
    "app/auth.py": "security",
    "Dockerfile": "infrastructure",
}

ANALYSIS = {
    "architecture": {
        "summary": "A FastAPI backend.",
        "evidence": [
            {"file": "app/main.py", "line_start": 4, "line_end": 4, "reason": "App created."}
        ],
    },
    "performance": {"findings": []},
    "testing": {"evidence": []},
}


def build(**overrides) -> list[seed_module.QuestionSeed]:
    structures = extract_all({k: v for k, v in FILES.items() if k.endswith(".py")})
    manifests = analyse_dependencies(FILES)
    kwargs = {
        "repository": {"full_name": "demo/shop-api", "name": "shop-api"},
        "analysis": ANALYSIS,
        "structures": structures,
        "manifests": manifests,
        "security": scan_files(FILES),
        "analyzed": ANALYZED,
        "domain_counts": {"backend": 1, "security": 1},
        "technologies": infer_technologies(manifests),
        "readme_path": "README.md",
    }
    kwargs.update(overrides)
    return seed_module.build_seeds(**kwargs)


# --- the evidence guarantee ---------------------------------------------------


def test_every_seed_carries_evidence() -> None:
    """The core invariant. A seed without evidence must never exist."""
    for seed in build():
        assert seed.has_evidence, seed.key
        assert seed.evidence[0]["file"]


def test_every_seed_cites_a_real_analysed_file() -> None:
    known = set(ANALYZED)

    for seed in build():
        for item in seed.evidence:
            assert item["file"] in known, f"{seed.key} cited {item['file']}"


def test_seed_categories_and_difficulties_are_valid() -> None:
    for seed in build():
        assert seed.category in seed_module.CATEGORIES
        assert seed.difficulty in seed_module.DIFFICULTIES


# --- code-specific questions (Feature 3) --------------------------------------


def test_code_seeds_name_real_symbols_at_real_lines() -> None:
    seeds = [seed for seed in build() if seed.category == seed_module.CODE]

    assert seeds, "expected code seeds for a repository with functions"

    topics = " ".join(seed.topic for seed in seeds)
    assert "authenticate_user" in topics
    assert "TokenService" in topics

    # The cited line must really contain the declaration. Methods are recorded
    # qualified ("TokenService.issue"), so match on the final component.
    for seed in seeds:
        item = seed.evidence[0]
        source = FILES[item["file"]].splitlines()
        symbol = seed.key.split(":")[2].split(".")[-1]
        assert symbol in source[item["line_start"] - 1]


def test_no_seed_invents_a_symbol() -> None:
    """Every symbol named in a topic must appear in the source."""
    everything = "\n".join(FILES.values())

    for seed in build():
        if seed.category != seed_module.CODE:
            continue
        symbol = seed.key.split(":")[2].split(".")[-1]
        assert symbol in everything


# --- API questions ------------------------------------------------------------


def test_route_seeds_come_from_parsed_routes() -> None:
    seeds = [seed for seed in build() if seed.category == seed_module.API]

    topics = " ".join(seed.topic for seed in seeds)
    assert "GET /products/{pid}" in topics

    route_seed = next(seed for seed in seeds if "/products" in seed.topic)
    line = route_seed.evidence[0]["line_start"]
    assert "@app.get" in MAIN_PY.splitlines()[line - 1]


# --- security questions (Feature 5) -------------------------------------------


def test_confirmed_finding_becomes_a_security_question() -> None:
    seeds = [
        seed
        for seed in build()
        if seed.category == seed_module.SECURITY and seed.key.startswith("sec:confirmed")
    ]

    assert seeds
    seed = seeds[0]
    assert seed.difficulty == seed_module.HARD
    assert seed.evidence[0]["file"] == "app/main.py"
    # Line 9 holds the f-string SQL in MAIN_PY.
    assert "SELECT" in MAIN_PY.splitlines()[seed.evidence[0]["line_start"] - 1]


def test_clean_repository_gets_defensive_questions_not_invented_ones() -> None:
    """No findings must never become an invented vulnerability."""
    clean = {
        "README.md": "# Clean",
        "requirements.txt": "fastapi==0.121\n",
        "app/main.py": "def add(a, b):\n    return a + b\n",
    }
    structures = extract_all({"app/main.py": clean["app/main.py"]})
    manifests = analyse_dependencies(clean)

    seeds = seed_module.build_seeds(
        repository={"full_name": "demo/clean"},
        analysis=ANALYSIS,
        structures=structures,
        manifests=manifests,
        security=scan_files(clean),
        analyzed={"app/main.py": "backend"},
        domain_counts={"backend": 1},
        technologies=infer_technologies(manifests),
        readme_path="README.md",
    )

    security_seeds = [s for s in seeds if s.category == seed_module.SECURITY]
    assert security_seeds
    assert all(seed.key.startswith("sec:defensive") for seed in security_seeds)
    # The question asks what they *would* do - it asserts no vulnerability.
    assert "securing" in security_seeds[0].topic


def test_no_secret_value_reaches_a_seed() -> None:
    leaked = "a7Fk29Lm4Xq8Zt6Bv3Nc1Wp5"
    files = {"app/config.py": f'API_KEY = "{leaked}"\n'}
    structures = extract_all(files)

    seeds = seed_module.build_seeds(
        repository={"full_name": "demo/x"},
        analysis=ANALYSIS,
        structures=structures,
        manifests=[],
        security=scan_files(files),
        analyzed={"app/config.py": "configuration"},
        domain_counts={},
        technologies=[],
        readme_path=None,
    )

    blob = " ".join(
        seed.topic + seed.angle + " ".join(str(e) for e in seed.evidence)
        for seed in seeds
    )
    assert leaked not in blob


# --- testing questions --------------------------------------------------------


def test_absent_tests_produce_an_honest_question() -> None:
    seeds = [seed for seed in build() if seed.category == seed_module.TESTING]

    assert seeds
    assert "no test files appear" in seeds[0].angle


def test_present_tests_produce_a_coverage_question() -> None:
    analysis = {
        **ANALYSIS,
        "testing": {
            "evidence": [{"file": "app/main.py", "line_start": None, "reason": "Tests."}]
        },
    }

    seeds = [seed for seed in build(analysis=analysis) if seed.category == seed_module.TESTING]

    assert seeds[0].key == "testing:existing"


# --- repository capability tags -----------------------------------------------


def test_repository_tags_reflect_reality() -> None:
    structures = extract_all({k: v for k, v in FILES.items() if k.endswith(".py")})
    tags = seed_module.repository_tags(analyse_dependencies(FILES), structures)

    assert "python" in tags
    assert "api" in tags
    assert "security" in tags
    assert "ml" not in tags


def test_ml_repository_is_tagged_ml() -> None:
    files = {"requirements.txt": "torch==2.1\ntransformers\n"}
    tags = seed_module.repository_tags(analyse_dependencies(files), [])

    assert "ml" in tags
    assert "genai" in tags


# --- roles (Feature 11) -------------------------------------------------------


def test_unsupported_role_is_reported_honestly() -> None:
    """Selecting ML Engineer for a CRUD app must say so, not fake ML questions."""
    role = role_module.get_role("ml_engineer")
    fit = role_module.assess_fit(role, {"python", "api"})

    assert fit.supported is False
    assert "limited evidence of machine-learning" in fit.notice
    assert "transferable engineering questions" in fit.notice


def test_supported_role_has_no_notice() -> None:
    role = role_module.get_role("backend_developer")
    fit = role_module.assess_fit(role, {"backend", "api"})

    assert fit.supported is True
    assert fit.notice == ""


def test_role_without_requirements_always_fits() -> None:
    role = role_module.get_role("software_developer")

    assert role_module.assess_fit(role, set()).supported is True


def test_unknown_role_falls_back_to_software_developer() -> None:
    assert role_module.get_role("astronaut").key == role_module.SOFTWARE_DEVELOPER
    assert role_module.get_role(None).key == role_module.SOFTWARE_DEVELOPER


def test_role_shifts_ranking_without_adding_seeds() -> None:
    """A role can re-order evidenced seeds. It can never introduce one."""
    seeds = build()
    backend = role_module.get_role("backend_developer")
    fit = role_module.assess_fit(backend, {"backend", "api"})

    ranked = sorted(seeds, key=lambda s: -role_module.score_seed(s, backend, fit))

    assert {seed.key for seed in ranked} == {seed.key for seed in seeds}
    assert ranked[0].category in backend.priority_categories


def test_role_options_are_complete() -> None:
    options = role_module.role_options()

    assert len(options) == len(role_module.ROLES)
    assert {item["key"] for item in options} == set(role_module.ROLES)


# --- resilience ---------------------------------------------------------------


def test_empty_repository_yields_no_seeds_rather_than_generic_ones() -> None:
    seeds = seed_module.build_seeds(
        repository={},
        analysis={},
        structures=[],
        manifests=[],
        security=SecurityScanReport(),
        analyzed={},
        domain_counts={},
        technologies=[],
        readme_path=None,
    )

    assert seeds == []


@pytest.mark.parametrize("missing", ["structures", "manifests"])
def test_partial_evidence_still_produces_valid_seeds(missing: str) -> None:
    seeds = build(**{missing: []})

    assert all(seed.has_evidence for seed in seeds)
