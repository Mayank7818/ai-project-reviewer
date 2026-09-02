"""What makes an analysis fast, expressed as things that must stay true.

Wall-clock is not asserted here — it depends on the machine, and a test that
fails on a slow laptop teaches nobody anything. What is asserted are the two
properties that produced the speed-up, measured on gemma3:4b over two CPU cores
against psf/requests:

  * **One model call, not three.** The deep pipeline spent 673 of 677 seconds
    inside the model, and 375 of those were *prompt* processing at 16.7 tok/s,
    because stages 1 and 2 each received nearly the same 2,800-token context.
  * **Bounded output.** Every array was unbounded, so the findings stage alone
    generated 678 output tokens at 4.2 tok/s. Constrained decoding enforces a
    JSON Schema exactly, which makes `maxItems` and `maxLength` real limits.

Both are cheap to break by accident and expensive to notice.
"""

from __future__ import annotations

import json

import respx

from app.core.config import Settings
from app.main import create_app
from app.services.analysis import stages
from app.services.analysis.service import AnalysisService, get_analysis_service
from app.services.github.service import GitHubService
from app.services.llm.factory import get_llm_provider

from tests.test_analyze_project import (
    ANALYZE,
    GENERATE_URL,
    REPO_URL,
    VALID_ANALYSIS,
    mock_everything,
)

from fastapi.testclient import TestClient

client = TestClient(create_app(), raise_server_exceptions=False)


def generate_calls(mock: respx.MockRouter) -> list[dict]:
    """Every payload posted to Ollama's generate endpoint."""
    return [
        json.loads(call.request.content)
        for call in mock.calls
        if str(call.request.url) == GENERATE_URL
    ]


# --- one call by default ------------------------------------------------------


@respx.mock
def test_the_default_pipeline_makes_exactly_one_model_call(
    respx_mock: respx.MockRouter,
) -> None:
    mock_everything(respx_mock)

    body = client.post(ANALYZE, json={"github_url": REPO_URL}).json()

    assert len(generate_calls(respx_mock)) == 1
    assert body["meta"]["stages_completed"] == [stages.FAST_NAME]


@respx.mock
def test_the_repository_context_is_sent_once_not_three_times(
    respx_mock: respx.MockRouter,
) -> None:
    """The measured cost of the deep pipeline was sending this twice over."""
    mock_everything(respx_mock)

    client.post(ANALYZE, json={"github_url": REPO_URL})

    prompts = [call["prompt"] for call in generate_calls(respx_mock)]
    carrying_the_extract = [p for p in prompts if "REPOSITORY EXTRACT" in p]
    assert len(carrying_the_extract) == 1


@respx.mock
def test_a_cached_analysis_makes_no_model_call_at_all(
    respx_mock: respx.MockRouter,
) -> None:
    mock_everything(respx_mock)

    client.post(ANALYZE, json={"github_url": REPO_URL})
    after_first = len(generate_calls(respx_mock))
    second = client.post(ANALYZE, json={"github_url": REPO_URL}).json()

    assert second["meta"]["cached"] is True
    assert len(generate_calls(respx_mock)) == after_first


# --- deep mode is still available ---------------------------------------------


def test_deep_mode_selects_the_three_pass_pipeline() -> None:
    config = Settings(_env_file=None, analysis_mode="deep")

    assert config.use_multi_stage is True


def test_fast_is_the_default() -> None:
    config = Settings(_env_file=None)

    assert config.analysis_mode == "fast"
    assert config.use_multi_stage is False


def test_an_explicit_legacy_flag_still_wins() -> None:
    """ENABLE_MULTI_STAGE predates ANALYSIS_MODE and still appears in .env files."""
    assert Settings(_env_file=None, enable_multi_stage=True).use_multi_stage is True
    assert Settings(_env_file=None, enable_multi_stage=False).use_multi_stage is False


@respx.mock
def test_deep_mode_still_runs_three_stages(respx_mock: respx.MockRouter) -> None:
    """The slow path must keep working - it is the quality option, not dead code."""
    mock_everything(respx_mock)

    # The service builds its own settings, so the provider is what to override.
    deep = Settings(_env_file=None, analysis_mode="deep")
    app = create_app()
    app.dependency_overrides[get_analysis_service] = lambda: AnalysisService(
        settings=deep,
        github_service=GitHubService(deep),
        llm_provider=get_llm_provider(),
    )
    deep_client = TestClient(app, raise_server_exceptions=False)

    body = deep_client.post(ANALYZE, json={"github_url": REPO_URL}).json()

    assert body["meta"]["stages_completed"] == ["understand", "findings", "synthesise"]
    assert len(generate_calls(respx_mock)) == 3


# --- output is bounded --------------------------------------------------------


def collect_unbounded(schema: object, path: str = "") -> list[str]:
    """Every array without a maxItems and every free string without a maxLength."""
    found: list[str] = []
    if not isinstance(schema, dict):
        return found

    if schema.get("type") == "array" and "maxItems" not in schema:
        found.append(f"{path or '<root>'} (array)")
    if (
        schema.get("type") == "string"
        and "maxLength" not in schema
        and "enum" not in schema
    ):
        found.append(f"{path or '<root>'} (string)")

    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for name, child in value.items():
                found += collect_unbounded(child, f"{path}.{name}")
        elif key == "items":
            found += collect_unbounded(value, f"{path}[]")
    return found


def test_no_schema_lets_the_model_write_without_limit() -> None:
    """The regression that made an analysis take eleven minutes.

    Under constrained decoding an unbounded array is an open invitation, and
    every output token is generated one at a time on CPU.
    """
    for name, schema in [
        ("fast", stages.FAST_SCHEMA),
        ("stage1", stages.STAGE1_SCHEMA),
        ("stage2", stages.STAGE2_SCHEMA),
        ("stage3", stages.STAGE3_SCHEMA),
        ("single", stages.SINGLE_STAGE_SCHEMA),
    ]:
        unbounded = collect_unbounded(schema)
        assert unbounded == [], f"{name} has unbounded fields: {unbounded}"


def test_the_fast_schema_is_tighter_than_the_deep_one() -> None:
    fast = stages.FAST_SCHEMA["properties"]["code_quality_findings"]["maxItems"]
    deep = stages.STAGE2_SCHEMA["properties"]["code_quality_findings"]["maxItems"]

    assert fast <= deep


def test_fast_mode_does_not_ask_for_what_is_already_known() -> None:
    """Technologies come from parsed manifests. Asking the model to retype them
    is output tokens spent re-deriving a fact already in hand."""
    assert "technologies" not in stages.FAST_SCHEMA["properties"]
    assert "technologies" in stages.STAGE1_SCHEMA["properties"]


@respx.mock
def test_technologies_still_reach_the_response_in_fast_mode(
    respx_mock: respx.MockRouter,
) -> None:
    """Withholding the question must not remove the answer."""
    mock_everything(respx_mock)

    body = client.post(ANALYZE, json={"github_url": REPO_URL}).json()

    # The mocked model still returns them, and the manifest-derived ones lead.
    assert body["analysis"]["technologies"]


# --- correctness is unchanged -------------------------------------------------


@respx.mock
def test_fast_mode_still_drops_invented_citations(
    respx_mock: respx.MockRouter,
) -> None:
    """Speed must not cost the anti-hallucination guarantee."""
    payload = {
        **VALID_ANALYSIS,
        "code_quality_findings": [
            {
                "finding": "Something is wrong in a file that was never sent.",
                "severity": "high",
                "evidence": [{"file": "src/invented.py", "reason": "as claimed"}],
            }
        ],
    }
    mock_everything(respx_mock)
    respx_mock.post(GENERATE_URL).mock(
        return_value=__import__("httpx").Response(
            200, json={"response": json.dumps(payload)}
        )
    )

    body = client.post(ANALYZE, json={"github_url": REPO_URL}).json()

    assert body["analysis"]["code_quality"]["findings"] == []
    assert body["meta"]["evidence_dropped"] >= 1


@respx.mock
def test_fast_mode_keeps_line_numbers_when_the_model_supplies_them(
    respx_mock: respx.MockRouter,
) -> None:
    payload = {
        **VALID_ANALYSIS,
        "architecture_evidence": [
            {
                "file": "app/main.py",
                "line_start": 1,
                "line_end": 3,
                "reason": "Declares the application.",
            }
        ],
    }
    mock_everything(respx_mock)
    respx_mock.post(GENERATE_URL).mock(
        return_value=__import__("httpx").Response(
            200, json={"response": json.dumps(payload)}
        )
    )

    evidence = client.post(ANALYZE, json={"github_url": REPO_URL}).json()["analysis"][
        "architecture"
    ]["evidence"]

    assert evidence[0]["line_start"] == 1
    assert evidence[0]["line_end"] == 3


# --- deterministic facts replace what the model used to be asked for ----------


def test_languages_are_credited_without_asking_the_model() -> None:
    """A Python library whose only dependency is pytest still evidences Python.

    The model used to supply this. It is a fact GitHub already reported, and
    re-deriving it cost output tokens - the slowest thing in the pipeline.
    """
    from app.services.analysis.context_builder import build_context

    from tests.test_context_compression import file, result

    retrieval = result([file("app/main.py", "print('hi')\n")])
    retrieval.languages = {"Python": 90_000, "HTML": 300}

    context = build_context(retrieval, max_total_chars=8_000, max_chars_per_file=2_500)

    assert "Python" in context.declared_technologies
    # 300 bytes of HTML is true and useless; it is below the share floor.
    assert "HTML" not in context.declared_technologies


def test_a_declared_dependency_still_leads_a_detected_language() -> None:
    from app.services.analysis.context_builder import build_context

    from tests.test_context_compression import file, result

    retrieval = result(
        [file("requirements.txt", "fastapi==0.115.0\n", "manifest")],
    )
    retrieval.languages = {"Python": 50_000}

    context = build_context(retrieval, max_total_chars=8_000, max_chars_per_file=2_500)

    technologies = context.declared_technologies
    assert "FastAPI" in technologies
    assert "Python" in technologies
    # Manifests are an explicit statement of intent, so they come first.
    assert technologies.index("FastAPI") < technologies.index("Python")


def test_a_language_is_not_listed_twice() -> None:
    from app.services.analysis.context_builder import build_context

    from tests.test_context_compression import file, result

    retrieval = result([file("app/main.py", "x = 1\n")])
    retrieval.languages = {"Python": 10_000}

    context = build_context(retrieval, max_total_chars=8_000, max_chars_per_file=2_500)

    assert context.declared_technologies.count("Python") == 1
