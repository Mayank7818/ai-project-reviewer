"""Turn a repository retrieval into a bounded, evidence-rich prompt context.

The change from Step 3 is what gets sent. Previously the model received raw file
text and had to infer everything. Now it receives *facts* first - domain
classification, extracted declarations and routes with real line numbers,
declared dependencies, mechanical security scan results - followed by a bounded
selection of raw source for the judgement calls that need it.

That ordering matters for a small model: it spends its limited attention
reasoning about established facts instead of re-deriving them from text, and
every citation it makes can be checked against the inventory it was handed.

Prompt priority (Feature 10), least useful dropped first when the budget runs out:

    1. README
    2. dependency and config manifests
    3. application entry points
    4. important backend / frontend files
    5. database and authentication files
    6. tests
    7. remaining relevant source

Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.analysis import classifier
from app.services.analysis import compression
from app.services.analysis.code_structure import (
    FileStructure,
    aggregate_signals,
    extract_all,
)
from app.services.analysis.dependencies import (
    ManifestReport,
    analyse_dependencies,
    infer_technologies,
    is_manifest,
)
from app.services.analysis.security_scan import SecurityScanReport, scan_files
from app.services.github.file_filter import is_secret_material
from app.services.github.service import RetrievalResult, RetrievedFile

TRUNCATION_NOTE = "... [TRUNCATED - the rest of this file was not sent]"

#: Structure listing is informative but cheap to overspend on.
MAX_STRUCTURE_PATHS = 120

#: How much of the total budget the facts digest may consume before raw file
#: content starts being dropped. Facts are denser than source, so they earn it.
DIGEST_BUDGET_RATIO = 0.45

#: The share of the budget held back for code extracts, whatever the digest,
#: the file listing and the README would otherwise take.
#:
#: Without this the prelude simply won a bigger repository: for psf/requests the
#: metadata, the 120-path listing, the facts digest and a 2,500-character README
#: consumed nearly the whole 8,000 characters, and the model saw a Makefile.
#: A summary of a project is worth less than the code it summarises, so the code
#: is reserved first and the prelude spends what is left.
SOURCE_RESERVE_RATIO = 0.45

#: Ceiling on the file listing. It orients the model; it is not evidence.
STRUCTURE_BUDGET_RATIO = 0.15

#: Ceiling on the README. The point is the project's own summary of itself, and
#: the first paragraphs carry that - the install instructions below do not.
README_BUDGET_RATIO = 0.12

#: Heading above the compressed file blocks.
FILE_CONTENTS_HEADER = "## FILE CONTENTS\n\n"

#: Sections are joined with a newline; each one therefore costs a character
#: more than its own length.
JOIN_COST = 1

#: Reasons a retrieved file did not reach the prompt, surfaced to the UI.
OMIT_SECRET = "excluded as possible secret material"
OMIT_BUDGET = "did not fit the prompt character budget"
OMIT_IRRELEVANT = "not useful for analysis"
OMIT_NO_ALLOWANCE = "budget too small to show a useful extract"


# --- prompt ordering ----------------------------------------------------------
# Combines the retrieval tier (what kind of file) with the analysis domain
# (which part of the system), because neither alone gives the right order.

_ENTRYPOINT_RANK = 2
_DOMAIN_RANK: dict[str, int] = {
    classifier.CONFIGURATION: 1,
    classifier.BACKEND: 3,
    classifier.FRONTEND: 3,
    classifier.DATABASE: 4,
    classifier.SECURITY: 4,
    classifier.INFRASTRUCTURE: 5,
    classifier.TESTING: 6,
    classifier.SOURCE_CODE: 7,
    classifier.DOCUMENTATION: 7,
    classifier.UNKNOWN: 8,
}


def prompt_rank(file: RetrievedFile, domain: str) -> int:
    """Position in the Feature 10 priority list. Lower is sent first."""
    if file.category == "manifest":
        return 1
    if file.category == "entrypoint":
        return _ENTRYPOINT_RANK
    return _DOMAIN_RANK.get(domain, _DOMAIN_RANK[classifier.UNKNOWN])


#: Prompt priority bands, driving how much of the character budget a file gets.
#: HIGH  - entry points, routes, core source, manifests, security findings
#: MEDIUM- models, services, database, tests
#: LOW   - documentation, examples, boilerplate
_HIGH_DOMAINS = frozenset({classifier.BACKEND, classifier.FRONTEND, classifier.SECURITY})
_MEDIUM_DOMAINS = frozenset(
    {classifier.DATABASE, classifier.TESTING, classifier.INFRASTRUCTURE}
)


def _priority_band(file: RetrievedFile, domain: str) -> str:
    """Which share of the budget this file earns.

    Manifests and entry points are always HIGH: a manifest states what the
    project is built from, and an entry point states where it begins. Neither
    is large, and both explain more per character than anything else.
    """
    if file.category in ("manifest", "entrypoint"):
        return "high"
    if domain in _HIGH_DOMAINS:
        return "high"
    if domain in _MEDIUM_DOMAINS:
        return "medium"
    if domain in (classifier.DOCUMENTATION,):
        return "low"
    return "medium"


@dataclass
class OmittedRecord:
    path: str
    reason: str


@dataclass
class SnippetRecord:
    """One extract placed in the prompt, with its original position."""

    path: str
    start_line: int
    end_line: int
    reason: str
    chars: int


@dataclass
class BuiltContext:
    """The prompt text plus a full account of how it was assembled."""

    text: str
    #: path -> domain, for every file whose content reached the prompt.
    analyzed: dict[str, str] = field(default_factory=dict)
    #: path -> content actually sent, used to validate the model's citations.
    #: Narrow on purpose: the model may only cite what it was shown.
    sent_files: dict[str, str] = field(default_factory=dict)
    #: path -> content for every file that was mechanically analysed, whether or
    #: not its raw text fitted the prompt budget. Structure extraction, the
    #: dependency parse and the security scan all run over this wider set, so a
    #: citation derived from them is valid even when the file itself was too
    #: large to include. Step 5 validates interview evidence against this.
    evidence_files: dict[str, str] = field(default_factory=dict)
    #: path -> domain for that same wider set.
    all_domains: dict[str, str] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)
    omitted: list[OmittedRecord] = field(default_factory=list)
    #: Step 8. Every snippet placed in the prompt, with its original line range.
    snippets: list[SnippetRecord] = field(default_factory=list)
    #: path -> (lines shown, lines in file), for files sent as extracts.
    compression_ratio: dict[str, tuple[int, int]] = field(default_factory=dict)
    domain_counts: dict[str, int] = field(default_factory=dict)
    readme_included: bool = False
    readme_truncated: bool = False
    structure_truncated: bool = False
    #: Deterministic products, reused by the service for the final response.
    manifests: list[ManifestReport] = field(default_factory=list)
    structures: list[FileStructure] = field(default_factory=list)
    security: SecurityScanReport = field(default_factory=SecurityScanReport)
    declared_technologies: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cut `text` to `limit` characters, marking it clearly when cut."""
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "\n" + TRUNCATION_NOTE, True


#: A language has to account for at least this share of the repository's bytes
#: before it is called a technology. GitHub reports every language it detects,
#: including the 200 bytes of HTML in a docs folder, and listing that beside
#: Python would be true but useless.
_LANGUAGE_SHARE_FLOOR = 0.05


def _evidenced_technologies(
    manifests: list[ManifestReport], result: RetrievalResult
) -> list[str]:
    """Technologies the repository demonstrably uses, from two kinds of fact.

    Declared dependencies come first because a manifest is an explicit statement
    of intent. GitHub's own language breakdown follows, which catches the thing
    manifests miss: a Python library whose dependencies are all test tooling
    still evidences Python, and saying so needs no model.

    This used to be padded with whatever the model volunteered. Asking it to
    retype a list already in hand cost output tokens - the slowest thing this
    application does - to re-derive a fact, and occasionally to invent one.
    """
    technologies = list(infer_technologies(manifests))
    known = {name.lower() for name in technologies}

    languages = result.languages or {}
    total = sum(languages.values()) or 1
    for name, size in sorted(languages.items(), key=lambda item: -item[1]):
        if size / total < _LANGUAGE_SHARE_FLOOR:
            continue
        if name.lower() not in known:
            technologies.append(name)
            known.add(name.lower())

    return technologies


def build_context(
    result: RetrievalResult,
    *,
    max_total_chars: int,
    max_chars_per_file: int,
    query_terms: list[str] | None = None,
) -> BuiltContext:
    """Assemble the evidence digest and file extracts for one repository.

    Args:
        result: Output of the Step 2 GitHub retrieval.
        max_total_chars: Hard ceiling on the whole context.
        max_chars_per_file: Per-file cap applied before the total budget.
        query_terms: Optional terms - job skills, or an interview question's
            subject - that bias which declarations are extracted from each file.

    Returns:
        A `BuiltContext` whose `text` is guaranteed to be within budget.
    """
    from app.services.github import relevance

    terms = relevance.normalise_query_terms(query_terms)
    built = BuiltContext(text="")

    # --- run the deterministic analysers over everything retrieved -----------
    # Secret material is filtered first, so it can never reach an analyser, a
    # prompt, or the model. Retrieval already excludes it; this is the second
    # independent check.
    usable: list[RetrievedFile] = []
    for item in result.files:
        if is_secret_material(item.path):
            built.omitted.append(OmittedRecord(item.path, OMIT_SECRET))
            continue
        usable.append(item)

    contents = {item.path: item.content for item in usable}

    built.manifests = analyse_dependencies(contents)
    built.declared_technologies = _evidenced_technologies(built.manifests, result)
    built.structures = extract_all(
        {path: text for path, text in contents.items() if not is_manifest(path)}
    )
    built.security = scan_files(contents)

    domains = {item.path: classifier.classify_file(item.path, item.content) for item in usable}
    built.evidence_files = dict(contents)
    built.all_domains = dict(domains)

    # --- assemble, section by section, against the budget --------------------
    # Sections are joined with a newline and the file block carries a heading,
    # so both are charged as they are spent. Counting only section bodies would
    # let the finished text sit a few characters over a limit this whole layer
    # exists to honour.
    sections: list[str] = []
    remaining = max_total_chars

    # Everything above the file blocks spends from `prelude_room`; the rest is
    # held for code extracts. Whatever the prelude leaves unspent flows back to
    # the extracts, so a small repository still sends its files whole.
    source_reserve = int(max_total_chars * SOURCE_RESERVE_RATIO)
    prelude_room = max_total_chars - source_reserve

    def take(section: str) -> bool:
        """Add a prelude section if it fits without eating the source reserve."""
        nonlocal remaining, prelude_room
        cost = len(section) + JOIN_COST
        if cost > min(prelude_room, remaining):
            return False
        sections.append(section)
        remaining -= cost
        prelude_room -= cost
        return True

    # Repository identity is small and orients everything else, so it is taken
    # before the reserve applies.
    metadata = _metadata_section(result)
    sections.append(metadata)
    remaining -= len(metadata) + JOIN_COST

    structure_section, structure_truncated = _structure_section(
        result, domains, max_chars=int(max_total_chars * STRUCTURE_BUDGET_RATIO)
    )
    if take(structure_section):
        built.structure_truncated = structure_truncated

    # Facts before source. Capped so the digest cannot starve the excerpts.
    digest_budget = int(max_total_chars * DIGEST_BUDGET_RATIO)
    for section in (
        _dependency_section(built.manifests, built.declared_technologies),
        _code_facts_section(built.structures),
        _security_section(built.security),
    ):
        if len(section) + JOIN_COST <= min(digest_budget, prelude_room, remaining):
            sections.append(section)
            remaining -= len(section) + JOIN_COST
            prelude_room -= len(section) + JOIN_COST
            digest_budget -= len(section) + JOIN_COST

    if result.readme:
        readme_room = min(
            max_chars_per_file, int(max_total_chars * README_BUDGET_RATIO)
        )
        body, truncated = _truncate(result.readme, readme_room)
        if take(f"## README\n\n{body}\n"):
            built.readme_included = True
            built.readme_truncated = truncated

    # --- raw file excerpts, in priority order --------------------------------
    ordered = sorted(
        usable,
        key=lambda item: (
            prompt_rank(item, domains[item.path]),
            item.path.count("/"),
            item.path,
        ),
    )

    # --- Step 8: split the remaining budget by priority, then compress -------
    # Previously each file took a 2,500-character whole-file block, so about two
    # files ever reached the model however many were retrieved. Now the budget is
    # shared out by band and each file is reduced to the declarations that carry
    # meaning, which fits an order of magnitude more files in the same space.
    # The file heading is paid for before any block is measured against the
    # budget, so `remaining` below is the room blocks may genuinely use.
    file_section_overhead = len(FILE_CONTENTS_HEADER) + JOIN_COST
    remaining = max(0, remaining - file_section_overhead)

    structures_by_path = {item.path: item for item in built.structures}
    bands = [(item.path, _priority_band(item, domains[item.path])) for item in ordered]
    allowances = compression.allocate(bands, min(remaining, max_total_chars))

    blocks: list[str] = []
    for item in ordered:
        domain = domains[item.path]
        allowance = min(allowances.get(item.path, 0), max_chars_per_file, remaining)

        if allowance < compression.MIN_USEFUL_ALLOWANCE:
            built.omitted.append(OmittedRecord(item.path, OMIT_NO_ALLOWANCE))
            continue

        compressed = compression.extract_snippets(
            item.path,
            item.content,
            structures_by_path.get(item.path),
            allowance=allowance,
            terms=terms,
        )
        if compressed.is_empty:
            built.omitted.append(OmittedRecord(item.path, OMIT_BUDGET))
            continue

        block = compression.render(compressed, domain)
        if len(block) > remaining:
            # A whole block or none: never half a snippet, because a citation
            # into a severed block would point at lines the model never saw.
            built.omitted.append(OmittedRecord(item.path, OMIT_BUDGET))
            continue

        blocks.append(block)
        remaining -= len(block)
        built.analyzed[item.path] = domain

        # The evidence index keeps the file's FULL text on purpose. Line
        # validation asks "does line 412 exist in this file?", and the answer
        # must come from the real file - indexing the extract instead would
        # wrongly reject a correct citation into a part that was shown.
        built.sent_files[item.path] = item.content

        if not compressed.whole:
            built.truncated.append(item.path)
            built.compression_ratio[item.path] = (
                compressed.lines_shown,
                compressed.total_lines,
            )

        built.snippets.extend(
            SnippetRecord(
                path=snippet.path,
                start_line=snippet.start_line,
                end_line=snippet.end_line,
                reason=snippet.reason,
                chars=len(snippet.text),
            )
            for snippet in compressed.snippets
        )

    if blocks:
        sections.append(FILE_CONTENTS_HEADER + "".join(blocks))

    # Counted over what was actually analysed, not merely retrieved - the UI
    # renders this beside the analysed-file list, so the two must agree.
    built.domain_counts = classifier.summarise_domains(built.analyzed)

    built.text = "\n".join(sections)
    return built


# --- sections -----------------------------------------------------------------


def _fence(path: str, domain: str, content: str) -> str:
    """Render one file as a labelled block the model can attribute correctly."""
    return f"--- FILE: {path} [{domain}] ---\n{content}\n--- END FILE: {path} ---\n"


def _metadata_section(result: RetrievalResult) -> str:
    raw = result.repository
    owner = (raw.get("owner") or {}).get("login", "unknown")
    license_info = raw.get("license") or {}
    languages = (
        ", ".join(sorted(result.languages, key=result.languages.get, reverse=True))
        or "unknown"
    )

    return "\n".join(
        [
            "## REPOSITORY METADATA",
            "",
            f"Name: {raw.get('full_name') or raw.get('name', 'unknown')}",
            f"Owner: {owner}",
            f"Description: {raw.get('description') or 'none provided'}",
            f"Primary language: {raw.get('language') or 'unknown'}",
            f"Languages present: {languages}",
            f"Default branch: {raw.get('default_branch', 'unknown')}",
            f"Stars: {raw.get('stargazers_count', 0)}",
            f"Forks: {raw.get('forks_count', 0)}",
            f"Open issues: {raw.get('open_issues_count', 0)}",
            f"License: {license_info.get('spdx_id') or 'none declared'}",
            f"Topics: {', '.join(raw.get('topics') or []) or 'none'}",
            f"Archived: {bool(raw.get('archived'))}",
            "",
        ]
    )


def _structure_section(
    result: RetrievalResult, domains: dict[str, str], *, max_chars: int | None = None
) -> tuple[str, bool]:
    """The file tree, capped, with a domain label where one is known.

    Capped by characters as well as by path count: on a large repository the
    listing alone could otherwise crowd out the code it is meant to introduce.
    """
    paths = result.tree_paths[:MAX_STRUCTURE_PATHS]
    truncated = len(result.tree_paths) > MAX_STRUCTURE_PATHS

    if max_chars is not None:
        # ~2 characters of slack per line for the domain tag and newline.
        while paths and sum(len(item) + 24 for item in paths) > max_chars:
            paths.pop()
            truncated = True

    header = (
        f"## FILE STRUCTURE ({len(paths)} of {result.tree_total_entries} entries"
        f"{', listing truncated' if truncated or result.tree_truncated else ''})"
    )
    note = (
        "Dependency, build and binary directories were excluded before this "
        "listing was made. A [domain] tag means that file's content was analysed."
    )

    lines = [
        f"{path} [{domains[path]}]" if path in domains else path for path in paths
    ]
    return f"{header}\n{note}\n\n" + "\n".join(lines) + "\n", truncated


def _dependency_section(
    manifests: list[ManifestReport], technologies: list[str]
) -> str:
    """Declared dependencies, parsed from manifests rather than guessed."""
    if not manifests:
        return ""

    lines = [
        "## DECLARED DEPENDENCIES",
        "",
        "Parsed directly from manifest files. This is factual. No vulnerability "
        "data is available - never claim a dependency is vulnerable.",
        "",
    ]

    for report in manifests:
        if report.parse_error:
            lines.append(f"{report.path} ({report.ecosystem}): {report.parse_error}")
            continue
        runtime = [d for d in report.dependencies if not d.dev]
        dev = [d for d in report.dependencies if d.dev]
        lines.append(f"{report.path} ({report.ecosystem}, {len(report.dependencies)} declared)")
        if runtime:
            lines.append("  runtime: " + ", ".join(f"{d.name}{d.version}" for d in runtime))
        if dev:
            lines.append("  dev: " + ", ".join(d.name for d in dev))

    if technologies:
        lines += ["", "Technologies implied by these dependencies: " + ", ".join(technologies)]

    return "\n".join(lines) + "\n"


def _code_facts_section(structures: list[FileStructure]) -> str:
    """Declarations, routes and behavioural signals, with real line numbers."""
    # Sorted by path: the digest must read the same however the files were
    # fetched, so two runs over one repository produce one prompt.
    informative = sorted(
        (s for s in structures if not s.is_empty), key=lambda item: item.path
    )
    if not informative:
        return ""

    lines = [
        "## EXTRACTED CODE STRUCTURE",
        "",
        "Mechanically extracted. Line numbers here are exact and safe to cite.",
        "",
    ]

    routes: list[str] = []
    for structure in informative:
        parts: list[str] = []
        if structure.imports:
            names = ", ".join(item.name for item in structure.imports[:12])
            parts.append(f"  imports: {names}")
        if structure.classes:
            names = ", ".join(f"{item.name}:{item.line}" for item in structure.classes[:10])
            parts.append(f"  classes: {names}")
        if structure.functions:
            names = ", ".join(f"{item.name}:{item.line}" for item in structure.functions[:12])
            parts.append(f"  functions: {names}")
        if structure.methods:
            names = ", ".join(f"{item.name}:{item.line}" for item in structure.methods[:12])
            parts.append(f"  methods: {names}")
        if structure.parse_error:
            parts.append(f"  NOTE: {structure.parse_error}")

        if parts:
            lines.append(f"{structure.path} ({structure.language}, {structure.line_count} lines)")
            lines.extend(parts)

        routes.extend(f"  {item.detail}  ({structure.path}:{item.line})" for item in structure.routes)

    if routes:
        lines += ["", "HTTP ROUTES FOUND:", *routes[:40]]

    aggregated = aggregate_signals(informative)
    if aggregated:
        lines += ["", "BEHAVIOURAL SIGNALS (file:line):"]
        for signal, references in sorted(aggregated.items()):
            lines.append(f"  {signal}: {', '.join(references[:8])}")

    return "\n".join(lines) + "\n"


def _security_section(report: SecurityScanReport) -> str:
    """Mechanical scan results - fact, not opinion."""
    lines = [
        "## MECHANICAL SECURITY SCAN",
        "",
        "Produced by pattern matching, already verified. Treat as fact. "
        "Credential values were redacted before you saw them.",
        "",
    ]

    if report.confirmed:
        lines.append("CONFIRMED (the pattern matched real code):")
        for hit in report.confirmed[:15]:
            lines.append(f"  [{hit.severity}] {hit.file}:{hit.line} - {hit.title}")
            lines.append(f"      {hit.excerpt}")
    else:
        lines.append("CONFIRMED: none found.")

    if report.potential:
        lines += ["", "POTENTIAL (risky shape, depends on context):"]
        for hit in report.potential[:15]:
            lines.append(f"  [{hit.severity}] {hit.file}:{hit.line} - {hit.title}")

    if report.checked_with_no_findings:
        lines += [
            "",
            "CHECKED, NOTHING FOUND: " + "; ".join(report.checked_with_no_findings[:12]),
            "This means the pattern did not match. It does NOT mean the project "
            "is secure in that respect, and it is NOT a vulnerability.",
        ]

    return "\n".join(lines) + "\n"


# --- stage 3 input ------------------------------------------------------------


def summarise_for_synthesis(stage1: dict, stage2: dict, security: SecurityScanReport) -> tuple[str, str]:
    """Compact stages 1 and 2 into text for the synthesis prompt.

    Stage 3 never sees the repository - only what the earlier stages
    established. That keeps its prompt small and stops it re-deriving facts.
    """
    understanding = "\n".join(
        [
            f"Summary: {stage1.get('project_summary', '')}",
            f"Technologies: {', '.join(stage1.get('technologies') or []) or 'none identified'}",
            f"Architecture: {stage1.get('architecture_summary', '')}",
        ]
    )

    def render(title: str, findings: list) -> list[str]:
        if not findings:
            return [f"{title}: none reported."]
        rows = [f"{title}:"]
        for item in findings[:10]:
            if isinstance(item, dict):
                rows.append(f"  [{item.get('severity', 'medium')}] {item.get('finding', '')}")
        return rows

    lines: list[str] = []
    lines += [
        f"Confirmed security issues (mechanical scan): {len(security.confirmed)}"
    ]
    for hit in security.confirmed[:10]:
        lines.append(f"  [{hit.severity}] {hit.title} ({hit.file}:{hit.line})")

    lines += render("Code quality findings", stage2.get("code_quality_findings") or [])
    lines += render("Additional security risks", stage2.get("security_potential_risks") or [])
    lines += render("Performance findings", stage2.get("performance_findings") or [])
    lines += render("Documentation findings", stage2.get("documentation_findings") or [])

    testing = stage2.get("testing_evidence") or []
    lines.append(
        f"Test files found: {len(testing)}"
        if testing
        else "Test files found: none in the retrieved selection."
    )

    return understanding, "\n".join(lines)
