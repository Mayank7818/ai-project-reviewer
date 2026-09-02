"""Match parsed job requirements against Step 4 repository evidence.

The product principle in one sentence: a skill is never credited because the job
asked for it, only because the repository shows it.

Status is decided by **evidence strength**, not by a model's opinion:

    strong    a declared dependency, or a real import in the code   -> VERIFIED
    moderate  a file path, or a technology Step 4 already detected  -> PARTIAL
    weak      a mention in prose (README) and nowhere else          -> PARTIAL
    none      nothing at all                                        -> NOT_VERIFIED

Two consequences fall out of that model rather than being special-cased:

* A specific variant whose parent is evidenced - "AWS Lambda" when the repo only
  shows the AWS SDK - lands on PARTIALLY_VERIFIED, because the parent is
  moderate evidence for the child and never strong evidence (Feature 7).
* CONTRADICTED is reserved for the rare case where the repository shows strong
  evidence of a mutually exclusive alternative and none of the required skill -
  a React codebase against a job demanding Angular.

Nothing here performs I/O, and nothing here calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.interview.store import CachedAnalysis
from app.services.job import vocabulary
from app.services.job.parser import JobRequirement, ParsedJob

# --- status -------------------------------------------------------------------

VERIFIED = "verified"
PARTIALLY_VERIFIED = "partially_verified"
NOT_VERIFIED = "not_verified"
CONTRADICTED = "contradicted"

STATUSES: tuple[str, ...] = (VERIFIED, PARTIALLY_VERIFIED, NOT_VERIFIED, CONTRADICTED)

#: Credit each status contributes to the match score. Documented in the README.
STATUS_CREDIT: dict[str, float] = {
    VERIFIED: 1.0,
    PARTIALLY_VERIFIED: 0.5,
    NOT_VERIFIED: 0.0,
    CONTRADICTED: 0.0,
}

STRONG, MODERATE, WEAK, NONE = "strong", "moderate", "weak", "none"

NOT_VERIFIED_NOTE = "Not verified from repository evidence."


@dataclass
class SkillMatch:
    """One job requirement, judged against the repository."""

    skill: str
    category: str
    importance: str
    status: str
    #: Citations into files that were actually analysed.
    evidence: list[dict[str, Any]] = field(default_factory=list)
    #: Why this status, in one sentence, for the UI.
    reason: str = ""
    strength: str = NONE
    alternative_group: str | None = None

    @property
    def credit(self) -> float:
        return STATUS_CREDIT[self.status]

    @property
    def is_gap(self) -> bool:
        return self.status in (NOT_VERIFIED, CONTRADICTED)


@dataclass
class JobProjectMatch:
    """The full comparison of one job against one repository."""

    repository: str
    matches: list[SkillMatch] = field(default_factory=list)
    #: Requirements excluded from scoring: responsibilities, and skills a
    #: repository cannot evidence (Agile, communication).
    unscored: list[SkillMatch] = field(default_factory=list)

    def by_status(self, status: str) -> list[SkillMatch]:
        return [item for item in self.matches if item.status == status]

    @property
    def verified(self) -> list[SkillMatch]:
        return self.by_status(VERIFIED)

    @property
    def partial(self) -> list[SkillMatch]:
        return self.by_status(PARTIALLY_VERIFIED)

    @property
    def gaps(self) -> list[SkillMatch]:
        return [item for item in self.matches if item.is_gap]


# --- evidence lookup ----------------------------------------------------------


def _evidence(file: str, line: int | None, reason: str) -> dict[str, Any]:
    return {"file": file, "line_start": line, "line_end": line, "reason": reason}


@dataclass
class RepositoryEvidence:
    """A queryable view of what a repository demonstrably contains.

    Built once per match from the cached Step 4 analysis, so matching every
    skill is a dictionary lookup rather than a rescan.
    """

    dependencies: dict[str, str] = field(default_factory=dict)   # name -> manifest path
    imports: dict[str, str] = field(default_factory=dict)        # module -> file path
    import_lines: dict[str, int] = field(default_factory=dict)   # module -> line
    paths: list[str] = field(default_factory=list)
    technologies: set[str] = field(default_factory=set)
    #: code_structure language id -> a representative file written in it.
    languages: dict[str, str] = field(default_factory=dict)
    readme_text: str = ""
    readme_path: str | None = None

    @classmethod
    def from_cache(cls, cached: CachedAnalysis) -> RepositoryEvidence:
        dependencies: dict[str, str] = {}
        for report in cached.manifests:
            for dependency in report.dependencies:
                dependencies.setdefault(dependency.name.lower(), report.path)

        imports: dict[str, str] = {}
        import_lines: dict[str, int] = {}
        for structure in cached.structures:
            for symbol in structure.imports:
                key = symbol.name.lower()
                if key not in imports:
                    imports[key] = structure.path
                    import_lines[key] = symbol.line

        languages: dict[str, str] = {}
        for structure in cached.structures:
            if structure.language and structure.language != "unknown":
                languages.setdefault(structure.language, structure.path)

        readme_path = cached.readme_path
        readme_text = (cached.evidence_files or {}).get(readme_path or "", "")

        return cls(
            languages=languages,
            dependencies=dependencies,
            imports=imports,
            import_lines=import_lines,
            paths=list(cached.analyzed or {}),
            technologies={name.lower() for name in cached.technologies},
            readme_text=readme_text,
            readme_path=readme_path,
        )

    # --- lookups ----------------------------------------------------------

    def find_dependency(self, tokens: tuple[str, ...]) -> tuple[str, str] | None:
        """Strong evidence: the project declares this package."""
        for token in tokens:
            for name, manifest in self.dependencies.items():
                if name == token or name.startswith(f"{token}-") or token in name.split("/"):
                    return name, manifest
        return None

    def find_import(self, tokens: tuple[str, ...]) -> tuple[str, str, int] | None:
        """Strong evidence: the code imports this module."""
        for token in tokens:
            for module, path in self.imports.items():
                if module == token or module.startswith(f"{token}."):
                    return module, path, self.import_lines.get(module, 0)
        return None

    def find_path(self, tokens: tuple[str, ...]) -> str | None:
        """Moderate evidence: a filename says so (Dockerfile, main.tf)."""
        for token in tokens:
            if len(token) < 4:
                continue
            for path in self.paths:
                if token in path.lower():
                    return path
        return None

    def find_technology(self, name: str) -> bool:
        """Moderate evidence: Step 4 already concluded this technology is used."""
        return name.lower() in self.technologies

    def find_in_readme(self, tokens: tuple[str, ...]) -> bool:
        """Weak evidence: the README talks about it, but no code shows it."""
        if not self.readme_text:
            return False
        lowered = self.readme_text.lower()
        return any(len(token) >= 3 and token in lowered for token in tokens)


# --- matching -----------------------------------------------------------------


def _match_one(
    requirement: JobRequirement, evidence: RepositoryEvidence
) -> SkillMatch:
    """Judge a single requirement against the repository."""
    skill = vocabulary.get(requirement.skill)
    base = SkillMatch(
        skill=requirement.skill,
        category=requirement.category,
        importance=requirement.importance,
        status=NOT_VERIFIED,
        alternative_group=requirement.alternative_group,
    )

    if skill is None:
        base.reason = NOT_VERIFIED_NOTE
        return base

    tokens = vocabulary.detection_tokens(skill)

    # --- strong: source files are written in this language ----------------
    # Checked before dependencies because a language is evidenced by the code
    # itself, not by a package named after it.
    language_id = vocabulary.LANGUAGE_IDS.get(skill.name)
    if language_id and language_id in evidence.languages:
        path = evidence.languages[language_id]
        base.status = VERIFIED
        base.strength = STRONG
        base.evidence = [_evidence(path, None, f"Source file written in {skill.name}.")]
        base.reason = f"This project contains {skill.name} source files."
        return base

    # --- strong: a declared dependency ------------------------------------
    dependency = evidence.find_dependency(tokens)
    if dependency:
        name, manifest = dependency
        base.status = VERIFIED
        base.strength = STRONG
        base.evidence = [_evidence(manifest, None, f"Declares the `{name}` dependency.")]
        base.reason = f"{skill.name} is a declared dependency of this project."
        return base

    # --- strong: an import in real code -----------------------------------
    imported = evidence.find_import(tokens)
    if imported:
        module, path, line = imported
        base.status = VERIFIED
        base.strength = STRONG
        base.evidence = [_evidence(path, line or None, f"Imports `{module}`.")]
        base.reason = f"{skill.name} is imported by the project's code."
        return base

    # --- moderate: Step 4 detected it -------------------------------------
    if evidence.find_technology(skill.name):
        base.status = PARTIALLY_VERIFIED
        base.strength = MODERATE
        if evidence.readme_path:
            base.evidence = [
                _evidence(evidence.readme_path, None, f"{skill.name} detected in this project.")
            ]
        base.reason = (
            f"{skill.name} was detected in the analysis, but no dependency or "
            "import pins it down."
        )
        return base

    # --- moderate: a filename says so -------------------------------------
    path = evidence.find_path(tokens)
    if path:
        base.status = PARTIALLY_VERIFIED
        base.strength = MODERATE
        base.evidence = [_evidence(path, None, f"File named for {skill.name}.")]
        base.reason = f"A file in this project is named for {skill.name}."
        return base

    # --- moderate: the parent is evidenced, the specific variant is not ---
    # "AWS Lambda" required, repository shows the AWS SDK: real but partial.
    if skill.parent:
        parent = vocabulary.get(skill.parent)
        if parent:
            parent_match = _match_one(
                JobRequirement(
                    skill=parent.name,
                    category=parent.category,
                    importance=requirement.importance,
                ),
                evidence,
            )
            if parent_match.status == VERIFIED:
                base.status = PARTIALLY_VERIFIED
                base.strength = MODERATE
                base.evidence = list(parent_match.evidence)
                base.reason = (
                    f"{parent.name} is evidenced, but nothing in the analysed "
                    f"files shows {skill.name} specifically."
                )
                return base

    # --- weak: prose only --------------------------------------------------
    if evidence.find_in_readme(tokens) and evidence.readme_path:
        base.status = PARTIALLY_VERIFIED
        base.strength = WEAK
        base.evidence = [
            _evidence(evidence.readme_path, None, f"The README mentions {skill.name}.")
        ]
        base.reason = (
            f"The README mentions {skill.name}, but no code or dependency "
            "confirms it is actually used."
        )
        return base

    # --- contradicted: a peer is strongly evidenced instead ---------------
    for peer_name in skill.exclusive_with:
        peer = vocabulary.get(peer_name)
        if peer is None:
            continue
        peer_tokens = vocabulary.detection_tokens(peer)
        found = evidence.find_dependency(peer_tokens) or evidence.find_import(peer_tokens)
        if found:
            location = found[1] if len(found) > 1 else ""
            base.status = CONTRADICTED
            base.strength = NONE
            base.evidence = [
                _evidence(location, None, f"This project uses {peer.name} instead.")
            ]
            base.reason = (
                f"{NOT_VERIFIED_NOTE} This project uses {peer.name}, which is an "
                f"alternative to {skill.name}."
            )
            return base

    base.reason = (
        f"{NOT_VERIFIED_NOTE} No dependency, import or file in the analysed "
        f"selection shows {skill.name}."
    )
    return base


def match_job(job: ParsedJob, cached: CachedAnalysis) -> JobProjectMatch:
    """Compare every parsed requirement against the repository evidence.

    Args:
        job: The parsed job description.
        cached: The Step 4 analysis, reused rather than recomputed.

    Returns:
        A `JobProjectMatch` split into scored matches and unscored context.
    """
    evidence = RepositoryEvidence.from_cache(cached)
    result = JobProjectMatch(repository=cached.repository_full_name)

    for requirement in job.requirements:
        match = _match_one(requirement, evidence)
        if requirement.is_scored:
            result.matches.append(match)
        else:
            result.unscored.append(match)

    order = {VERIFIED: 0, PARTIALLY_VERIFIED: 1, NOT_VERIFIED: 2, CONTRADICTED: 3}
    importance_order = {"required": 0, "preferred": 1, "nice_to_have": 2, "responsibility": 3}
    result.matches.sort(
        key=lambda item: (importance_order.get(item.importance, 9), order[item.status], item.skill)
    )
    result.unscored.sort(key=lambda item: item.skill)

    return result
