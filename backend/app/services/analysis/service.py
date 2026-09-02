"""Orchestrates the multi-stage analysis pipeline.

    URL -> GitHub retrieval (Step 2, reused unchanged)
        -> deterministic analysis (classify, extract, parse, scan)
        -> stage 1: understanding
        -> stage 2: findings
        -> stage 3: synthesis
        -> evidence validation against the files actually sent
        -> Pydantic validation
        -> response

Two things are worth noting about the ordering.

First, the model check runs before GitHub: if Ollama is stopped you find out in
under a second rather than after a full retrieval, and no GitHub rate limit is
spent.

Second, the deterministic pass runs before any model call. Everything that can
be established mechanically - dependencies, declarations, routes, security
patterns - is established as fact, and the model reasons over those facts rather
than re-deriving them from raw text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    LLMInvalidResponseError,
    LLMModelNotFoundError,
    LLMUnavailableError,
)
from app.core.logging import get_logger
from app.schemas.analysis import (
    AnalysisMeta,
    ContextSnippet,
    DependencySummary,
    FileRecord,
    OmittedFile,
    ProjectAnalysis,
)
from app.services.analysis import stages
from app.services.analysis.context_builder import (
    BuiltContext,
    build_context,
    summarise_for_synthesis,
)
from app.services.analysis.evidence import (
    EvidenceIndex,
    ValidationStats,
    validate_evidence_items,
    validate_findings,
)
from app.services.analysis.security_scan import SecurityScanReport
from app.services.github.repository_map import enrich_with_symbols
from app.services.github.service import GitHubService, RetrievalResult
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider

logger = get_logger(__name__)

#: Appended on a retry. Constrained decoding makes malformed JSON rare, but a
#: model can still return an object that fails our stricter validation.
_RETRY_SUFFIX = (
    "\n\nYour previous reply could not be validated. Reply again with a single "
    "JSON object matching the required schema exactly. Include every required "
    "field. Scores must be integers between 0 and 100."
)


@dataclass
class AnalysisOutcome:
    """The validated analysis plus everything needed to report on the run."""

    retrieval: RetrievalResult
    analysis: ProjectAnalysis
    meta: AnalysisMeta
    #: The evidence the analysis was built from. Carried so Step 5 can generate
    #: interview questions from the same facts without re-running the pipeline.
    context: BuiltContext | None = None


@dataclass
class _StageResults:
    """Raw model output per stage, before assembly."""

    understanding: dict[str, Any] = field(default_factory=dict)
    findings: dict[str, Any] = field(default_factory=dict)
    synthesis: dict[str, Any] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)


class AnalysisService:
    """Runs repository retrieval and local-model analysis end to end."""

    def __init__(
        self,
        settings: Settings,
        github_service: GitHubService,
        llm_provider: LLMProvider,
    ) -> None:
        self._settings = settings
        self._github = github_service
        self._llm = llm_provider

    async def analyze(
        self, github_url: str, *, query_terms: list[str] | None = None
    ) -> AnalysisOutcome:
        """Retrieve a repository and analyse it with the local model.

        Raises:
            InvalidRepositoryUrlError, RepositoryNotFoundError,
            GitHubRateLimitError, ExternalServiceError: from retrieval.
            LLMUnavailableError, LLMModelNotFoundError,
            LLMInvalidResponseError: from the model.
        """
        started = time.monotonic()

        await self._require_model_ready()

        # Each phase is timed separately. On CPU the model dominates so heavily
        # that a single total hides everything useful - these numbers are what
        # tell you whether an analysis was slow because of the network, the
        # parsing, or the thing that is actually slow.
        mark = time.monotonic()
        retrieval = await self._github.retrieve(github_url, query_terms=query_terms)
        retrieval_seconds = time.monotonic() - mark

        mark = time.monotonic()
        context = build_context(
            retrieval,
            max_total_chars=self._settings.max_llm_context_chars,
            max_chars_per_file=self._settings.max_llm_chars_per_file,
            query_terms=query_terms,
        )
        # Deterministic analysis and compression both happen inside
        # build_context, and both are pure CPU over already-fetched text.
        evidence_seconds = time.monotonic() - mark

        # The map was ranked from paths alone. Now that the retrieved files have
        # been parsed, attach their symbols so a cached map can answer "what is
        # in this file?" without re-fetching anything. Line numbers stay with
        # the structures themselves, which is what the evidence validator
        # checks citations against - duplicating them would create a second
        # source of truth.
        enrich_with_symbols(retrieval.repository_map, context.structures)

        logger.info(
            "Analysing %s with %s: %d chars, %d files, domains=%s, confirmed security=%d",
            retrieval.repository.get("full_name", "unknown"),
            self._llm.model_name,
            context.char_count,
            len(context.analyzed),
            context.domain_counts,
            len(context.security.confirmed),
        )

        mark = time.monotonic()
        results = await self._run_stages(context)
        model_seconds = time.monotonic() - mark

        mark = time.monotonic()
        stats = ValidationStats()
        index = EvidenceIndex.from_files(context.sent_files)
        analysis = self._assemble(results, context, index, stats)
        validation_seconds = time.monotonic() - mark

        meta = self._build_meta(context, results, stats, started)

        # One line per completed analysis, carrying what is worth knowing after
        # the fact: how long it took, how much the model was shown, and how much
        # of its own output failed validation. No repository text, no citations,
        # nothing from the model's prose.
        logger.info(
            "Analysed %s in %.1fs: %d chars in %d snippet(s) across %d file(s), "
            "score %d, %d citation(s) dropped, %d line range(s) cleared",
            retrieval.repository.get("full_name", "unknown"),
            meta.duration_seconds,
            meta.context_chars,
            len(meta.snippets),
            len(meta.files_analyzed),
            analysis.overall_score,
            meta.evidence_dropped,
            meta.line_numbers_cleared,
        )
        total = max(time.monotonic() - started, 1e-9)
        logger.info(
            "Phase breakdown: retrieval %.1fs | evidence+compression %.1fs | "
            "model %.1fs (%.0f%%) | validation %.1fs",
            retrieval_seconds,
            evidence_seconds,
            model_seconds,
            100 * model_seconds / total,
            validation_seconds,
        )

        return AnalysisOutcome(
            retrieval=retrieval, analysis=analysis, meta=meta, context=context
        )

    # --- model readiness -----------------------------------------------------

    async def _require_model_ready(self) -> None:
        """Raise a precise, actionable error if the model cannot serve a request."""
        status = await self._llm.status()

        if not status.reachable:
            raise LLMUnavailableError(
                status.detail
                or "The local Ollama server is not reachable. Start it with `ollama serve`."
            )

        if not status.model_available:
            installed = ", ".join(status.available_models) or "none"
            raise LLMModelNotFoundError(
                status.detail or f"Model '{status.model}' is not installed in Ollama.",
                details={"configured_model": status.model, "installed_models": installed},
            )

    # --- stages --------------------------------------------------------------

    async def _run_stages(self, context: BuiltContext) -> _StageResults:
        """Run one bounded pass, or the three-pass pipeline in deep mode.

        Fast is the default because the deep pipeline reads the repository three
        times to answer one question: stages 1 and 2 each receive nearly the same
        context, and on CPU prompt processing is the larger half of the cost.
        Sending it once is worth more than any prompt tuning.
        """
        results = _StageResults()

        if not self._settings.use_multi_stage:
            results.synthesis = await self._call(
                stages.FAST_NAME,
                stages.build_fast_prompt(context.text),
                stages.FAST_SCHEMA,
                stages.FAST_SYSTEM,
            )
            # One object carries what all three stages would have produced, so
            # assembly and validation are identical to the deep path.
            results.understanding = results.synthesis
            results.findings = results.synthesis
            results.completed = [stages.FAST_NAME]
            return results

        results.understanding = await self._call(
            stages.STAGE1_NAME,
            stages.build_stage1_prompt(context.text),
            stages.STAGE1_SCHEMA,
            stages.STAGE1_SYSTEM,
        )
        results.completed.append(stages.STAGE1_NAME)

        results.findings = await self._call(
            stages.STAGE2_NAME,
            stages.build_stage2_prompt(context.text),
            stages.STAGE2_SCHEMA,
            stages.STAGE2_SYSTEM,
        )
        results.completed.append(stages.STAGE2_NAME)

        understanding_text, findings_text = summarise_for_synthesis(
            results.understanding, results.findings, context.security
        )
        results.synthesis = await self._call(
            stages.STAGE3_NAME,
            stages.build_stage3_prompt(understanding_text, findings_text),
            stages.STAGE3_SCHEMA,
            stages.STAGE3_SYSTEM,
        )
        results.completed.append(stages.STAGE3_NAME)

        return results

    async def _call(
        self, name: str, prompt: str, schema: dict, system: str
    ) -> dict[str, Any]:
        """Run one stage, retrying once if the reply cannot be parsed.

        Only `LLMInvalidResponseError` is retryable - a stopped server or a
        missing model will not improve on a second attempt, so those propagate.
        """
        last_error: Exception | None = None

        for attempt in range(1, self._settings.ollama_max_attempts + 1):
            started = time.monotonic()
            try:
                payload = await self._llm.generate_json(
                    prompt if attempt == 1 else prompt + _RETRY_SUFFIX,
                    schema=schema,
                    system=system,
                )
            except LLMInvalidResponseError as exc:
                last_error = exc
                logger.warning("Stage '%s' attempt %d: unparseable JSON", name, attempt)
                continue

            # Constrained decoding should guarantee the required keys, but a
            # reply that parses yet omits them would silently become an analysis
            # made entirely of defaults. Retry instead of quietly degrading.
            missing = [key for key in schema.get("required", []) if key not in payload]
            if missing:
                last_error = LLMInvalidResponseError()
                logger.warning(
                    "Stage '%s' attempt %d: reply omitted %d required field(s)",
                    name,
                    attempt,
                    len(missing),
                )
                continue

            logger.info(
                "Stage '%s' completed in %.1fs", name, time.monotonic() - started
            )
            return payload

        logger.warning("Stage '%s' failed after %d attempts", name, attempt)
        raise LLMInvalidResponseError(
            f"The local model could not produce valid output for the '{name}' "
            "stage. A larger model usually produces more reliable structured output."
        ) from last_error

    # --- assembly ------------------------------------------------------------

    def _assemble(
        self,
        results: _StageResults,
        context: BuiltContext,
        index: EvidenceIndex,
        stats: ValidationStats,
    ) -> ProjectAnalysis:
        """Merge stage output with mechanical facts, validating every citation."""
        understanding, findings, synthesis = (
            results.understanding,
            results.findings,
            results.synthesis,
        )

        # Technologies: the model's list, plus anything the manifests prove.
        # Declared dependencies are fact, so they lead.
        technologies = list(context.declared_technologies)
        for name in understanding.get("technologies") or []:
            if isinstance(name, str) and name not in technologies:
                technologies.append(name)

        payload = {
            "project_summary": understanding.get("project_summary", ""),
            "technologies": technologies,
            "architecture": {
                "summary": understanding.get("architecture_summary", ""),
                "evidence": validate_evidence_items(
                    understanding.get("architecture_evidence") or [], index, stats
                ),
            },
            "code_quality": {
                "score": self._score_without_evidence(
                    synthesis.get("code_quality_score", 50),
                    bool(findings.get("code_quality_findings")),
                ),
                "reason": synthesis.get("code_quality_reason", ""),
                "findings": validate_findings(
                    findings.get("code_quality_findings") or [], index, stats
                ),
            },
            "security": self._build_security(findings, synthesis, context.security, index, stats),
            "performance": {
                "score": self._score_without_evidence(
                    synthesis.get("performance_score", 50),
                    bool(findings.get("performance_findings")),
                ),
                "reason": synthesis.get("performance_reason", ""),
                "findings": validate_findings(
                    findings.get("performance_findings") or [], index, stats
                ),
            },
            "documentation": {
                "score": self._score_without_evidence(
                    synthesis.get("documentation_score", 50),
                    bool(findings.get("documentation_findings")),
                ),
                "reason": synthesis.get("documentation_reason", ""),
                "findings": validate_findings(
                    findings.get("documentation_findings") or [], index, stats
                ),
            },
            "testing": {
                "score": self._score_without_evidence(
                    synthesis.get("testing_score", 50),
                    bool(findings.get("testing_evidence")),
                ),
                "reason": synthesis.get("testing_reason", ""),
                "evidence": validate_evidence_items(
                    findings.get("testing_evidence") or [], index, stats
                ),
            },
            "strengths": synthesis.get("strengths") or [],
            "weaknesses": synthesis.get("weaknesses") or [],
            "overall_score": synthesis.get("overall_score", 50),
        }

        try:
            return ProjectAnalysis.model_validate(payload)
        except ValidationError as exc:
            # The underlying error is logged, never returned: it can quote model
            # output, and internal detail must not reach the client.
            logger.warning("Assembled analysis failed validation: %d issues", len(exc.errors()))
            raise LLMInvalidResponseError() from exc

    @staticmethod
    def _score_without_evidence(score: object, has_evidence: bool) -> object:
        """Enforce the no-evidence rule that the prompt states but a small model
        does not reliably follow.

        The rule: a low score must mean something bad was observed, never that
        nothing was seen. A 4B model routinely returns 0 for "no test files
        found", which reads to a user as "this project's testing is terrible"
        when the honest reading is "we could not tell". When a section produced
        no findings and no evidence at all, the floor is the neutral 50.

        Only applied in the genuinely empty case: a section with findings keeps
        whatever score the model gave it, however harsh.
        """
        if has_evidence:
            return score
        try:
            numeric = float(str(score).strip().rstrip("%"))
        except (TypeError, ValueError):
            return 50
        return max(numeric, 50)

    def _build_security(
        self,
        findings: dict[str, Any],
        synthesis: dict[str, Any],
        scan: SecurityScanReport,
        index: EvidenceIndex,
        stats: ValidationStats,
    ) -> dict[str, Any]:
        """Combine the mechanical scan with the model's contextual risks.

        Confirmed issues come only from the scan - they are matches against real
        lines, so they need no validation and cannot be hallucinated. The model
        contributes potential risks, which are validated like any other finding.
        """
        confirmed = [
            {
                "finding": f"{hit.title}: {hit.reason}",
                "severity": hit.severity,
                "evidence": [
                    {
                        "file": hit.file,
                        "line_start": hit.line,
                        "line_end": hit.line,
                        "reason": hit.excerpt,
                    }
                ],
            }
            for hit in scan.confirmed
        ]

        potential = [
            {
                "finding": f"{hit.title}: {hit.reason}",
                "severity": hit.severity,
                "evidence": [
                    {
                        "file": hit.file,
                        "line_start": hit.line,
                        "line_end": hit.line,
                        "reason": hit.excerpt,
                    }
                ],
            }
            for hit in scan.potential
        ]
        potential += validate_findings(
            findings.get("security_potential_risks") or [], index, stats
        )

        no_evidence = list(scan.checked_with_no_findings)
        for item in findings.get("security_no_evidence") or []:
            if isinstance(item, str) and item not in no_evidence:
                no_evidence.append(item)

        return {
            "score": synthesis.get("security_score", 50),
            "confirmed_issues": confirmed,
            "potential_risks": potential,
            "no_evidence": no_evidence,
            # Flat titles, retained for backwards compatibility.
            "issues": [hit.title for hit in scan.confirmed],
        }

    def _build_meta(
        self,
        context: BuiltContext,
        results: _StageResults,
        stats: ValidationStats,
        started: float,
    ) -> AnalysisMeta:
        return AnalysisMeta(
            model=self._llm.model_name,
            stages_completed=results.completed,
            files_analyzed=[
                FileRecord(
                    path=path,
                    domain=domain,
                    truncated=path in context.truncated,
                    lines_shown=context.compression_ratio.get(path, (0, 0))[0],
                    lines_total=context.compression_ratio.get(path, (0, 0))[1],
                )
                for path, domain in context.analyzed.items()
            ],
            files_truncated=context.truncated,
            files_omitted=[
                OmittedFile(path=record.path, reason=record.reason)
                for record in context.omitted
            ],
            snippets=[
                ContextSnippet(
                    path=record.path,
                    line_start=record.start_line,
                    line_end=record.end_line,
                    reason=record.reason,
                    chars=record.chars,
                )
                for record in context.snippets
            ],
            domain_counts=context.domain_counts,
            dependencies=[
                DependencySummary(
                    file=report.path,
                    ecosystem=report.ecosystem,
                    count=len(report.dependencies),
                    names=[item.name for item in report.dependencies[:20]],
                )
                for report in context.manifests
            ],
            readme_included=context.readme_included,
            context_chars=context.char_count,
            context_limit=self._settings.max_llm_context_chars,
            duration_seconds=round(time.monotonic() - started, 2),
            evidence_dropped=stats.evidence_dropped_unknown_file
            + stats.findings_dropped_without_evidence,
            line_numbers_cleared=stats.line_numbers_cleared,
        )


def get_analysis_service() -> AnalysisService:
    """FastAPI dependency provider for `AnalysisService`."""
    settings = get_settings()
    return AnalysisService(
        settings=settings,
        github_service=GitHubService(settings),
        llm_provider=get_llm_provider(),
    )
