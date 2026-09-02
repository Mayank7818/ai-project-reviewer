"""Tests for dependency manifest parsing."""

from __future__ import annotations

from app.services.analysis.dependencies import (
    MAX_DEPENDENCIES_PER_FILE,
    analyse_dependencies,
    infer_technologies,
    is_manifest,
    parse_manifest,
)


def names(report) -> set[str]:
    return {item.name for item in report.dependencies}


# --- recognition --------------------------------------------------------------


def test_recognises_manifests() -> None:
    for path in [
        "package.json", "requirements.txt", "pyproject.toml", "go.mod",
        "Cargo.toml", "pom.xml", "build.gradle", "backend/requirements.txt",
    ]:
        assert is_manifest(path), path


def test_ignores_non_manifests() -> None:
    for path in ["README.md", "app/main.py", "config.yaml"]:
        assert not is_manifest(path)
        assert parse_manifest(path, "") is None


# --- npm ----------------------------------------------------------------------


def test_package_json_splits_runtime_and_dev() -> None:
    report = parse_manifest(
        "package.json",
        '{"dependencies": {"react": "^19.0.0", "express": "^4.18.0"},'
        ' "devDependencies": {"vite": "^7.0.0"}}',
    )

    assert report.ecosystem == "npm"
    assert names(report) == {"react", "express", "vite"}
    assert set(report.runtime_names) == {"react", "express"}
    assert next(d for d in report.dependencies if d.name == "vite").dev is True


def test_malformed_package_json_is_reported_not_raised() -> None:
    report = parse_manifest("package.json", "{not json")

    assert report.parse_error is not None
    assert report.dependencies == []


# --- pypi ---------------------------------------------------------------------


def test_requirements_txt() -> None:
    report = parse_manifest(
        "requirements.txt",
        "fastapi==0.121.2\n"
        "sqlalchemy>=2.0\n"
        "uvicorn[standard]==0.39.0\n"
        "\n"
        "# a comment\n"
        "-r other-requirements.txt\n"
        "git+https://github.com/x/y.git\n",
    )

    assert names(report) == {"fastapi", "sqlalchemy", "uvicorn"}
    assert next(d for d in report.dependencies if d.name == "fastapi").version == "==0.121.2"


def test_pyproject_pep621() -> None:
    report = parse_manifest(
        "pyproject.toml",
        '[project]\nname = "demo"\ndependencies = [\n  "fastapi>=0.100",\n  "httpx",\n]\n',
    )

    assert names(report) == {"fastapi", "httpx"}


def test_pyproject_poetry() -> None:
    report = parse_manifest(
        "pyproject.toml",
        '[tool.poetry.dependencies]\npython = "^3.11"\nfastapi = "^0.110"\nredis = "^5.0"\n\n[tool.poetry.dev-dependencies]\n',
    )

    # `python` is the interpreter constraint, not a dependency.
    assert names(report) == {"fastapi", "redis"}


# --- other ecosystems ---------------------------------------------------------


def test_go_mod() -> None:
    report = parse_manifest(
        "go.mod",
        "module example.com/app\n\ngo 1.22\n\nrequire (\n"
        "\tgithub.com/gin-gonic/gin v1.9.1\n"
        "\tgithub.com/stretchr/testify v1.8.4 // indirect\n)\n",
    )

    assert names(report) == {"github.com/gin-gonic/gin", "github.com/stretchr/testify"}


def test_cargo_toml() -> None:
    report = parse_manifest(
        "Cargo.toml",
        '[dependencies]\nserde = "1.0"\ntokio = { version = "1" }\n\n[dev-dependencies]\ncriterion = "0.5"\n',
    )

    assert names(report) == {"serde", "tokio", "criterion"}
    assert next(d for d in report.dependencies if d.name == "criterion").dev is True


def test_pom_xml() -> None:
    report = parse_manifest(
        "pom.xml",
        "<dependencies><dependency>"
        "<groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-web</artifactId>"
        "<version>3.2.0</version>"
        "</dependency></dependencies>",
    )

    assert "org.springframework.boot:spring-boot-starter-web" in names(report)


def test_build_gradle() -> None:
    report = parse_manifest(
        "build.gradle",
        "dependencies {\n"
        "    implementation 'org.springframework.boot:spring-boot-starter:3.2.0'\n"
        "    testImplementation 'junit:junit:4.13'\n}\n",
    )

    assert "org.springframework.boot:spring-boot-starter" in names(report)
    assert next(d for d in report.dependencies if d.name == "junit:junit").dev is True


# --- aggregation --------------------------------------------------------------


def test_analyse_dependencies_finds_every_manifest() -> None:
    reports = analyse_dependencies(
        {
            "package.json": '{"dependencies": {"react": "^19"}}',
            "backend/requirements.txt": "fastapi==0.121.2\n",
            "README.md": "# not a manifest",
        }
    )

    assert {report.path for report in reports} == {"package.json", "backend/requirements.txt"}


def test_technologies_are_inferred_from_evidence_not_recall() -> None:
    reports = analyse_dependencies(
        {
            "package.json": '{"dependencies": {"react": "^19", "tailwindcss": "^4"}}',
            "requirements.txt": "fastapi==0.121\nsqlalchemy>=2\npsycopg2-binary\n",
        }
    )

    technologies = infer_technologies(reports)

    assert "React" in technologies
    assert "Tailwind CSS" in technologies
    assert "FastAPI" in technologies
    assert "SQLAlchemy" in technologies
    assert "PostgreSQL" in technologies


def test_unrecognised_packages_produce_no_technology_claim() -> None:
    """Never invent a technology from a name we do not recognise."""
    reports = analyse_dependencies({"requirements.txt": "some-private-lib==1.0\n"})

    assert infer_technologies(reports) == []


def test_dependency_count_is_capped() -> None:
    body = "\n".join(f"package-{index}==1.0" for index in range(200))

    report = parse_manifest("requirements.txt", body)

    assert len(report.dependencies) == MAX_DEPENDENCIES_PER_FILE


def test_empty_manifest_is_safe() -> None:
    report = parse_manifest("requirements.txt", "")

    assert report.dependencies == []
    assert report.parse_error is None
