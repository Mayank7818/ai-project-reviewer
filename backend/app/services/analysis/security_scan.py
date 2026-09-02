"""Deterministic security scan over retrieved file content.

Why this exists rather than leaving security to the model: a 4B model asked
"find security issues" will produce plausible-sounding issues whether or not any
exist. A regular expression that matches `eval(user_input)` on line 42 either
matched or it did not. Findings from this module are therefore *confirmed* -
they name a real line in a real file - while the model is left to reason about
softer, contextual risks.

Three confidence levels, mapped straight onto Feature 4's requirement:

    confirmed  - the pattern matched; the code demonstrably does this
    potential  - the shape is risky but correctness depends on context
    no_evidence- checked for, nothing found (reported so absence is visible)

A missing best practice is never reported as a vulnerability. "No rate limiting
found" belongs in `no_evidence`, not in `confirmed`.

Secrets are never echoed. When a credential-shaped value is matched, the finding
records the file and line only - the value itself is discarded, never stored.

Nothing here performs I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.github.redaction import REDACTED

CONFIRMED = "confirmed"
POTENTIAL = "potential"

SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH = "low", "medium", "high"

#: Cap per rule, so one noisy file cannot dominate the report.
MAX_HITS_PER_RULE = 8


@dataclass(frozen=True)
class SecurityHit:
    """One match, anchored to a real file and line."""

    rule: str
    title: str
    severity: str
    confidence: str
    file: str
    line: int
    #: A redacted excerpt. Never contains a credential value.
    excerpt: str
    reason: str


@dataclass
class SecurityScanReport:
    """Everything the deterministic scan established."""

    confirmed: list[SecurityHit] = field(default_factory=list)
    potential: list[SecurityHit] = field(default_factory=list)
    #: Rules that ran and found nothing - so absence of evidence is explicit.
    checked_with_no_findings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Rule:
    key: str
    title: str
    severity: str
    confidence: str
    pattern: re.Pattern[str]
    reason: str
    #: When True the matched text is a credential and must never be shown.
    redact_match: bool = False
    #: Rules sharing a group describe the same underlying problem. Only the
    #: first match per group is kept for a given line, so a line that trips two
    #: overlapping SQL-injection patterns is reported once, not twice.
    group: str = ""


# --- rules --------------------------------------------------------------------
# Each rule states plainly why the match matters. `confidence` reflects how much
# the pattern alone proves: `eval()` on a variable is confirmed dangerous; a
# wildcard CORS origin is confirmed configuration but may be intentional.

_RULES: tuple[_Rule, ...] = (
    _Rule(
        key="hardcoded_secret",
        title="Hardcoded credential in source",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(
            r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
            r"client[_-]?secret|password|passwd)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
            re.I,
        ),
        reason="A credential-shaped literal is assigned directly in source rather than read from the environment.",
        redact_match=True,
        group="secret",
    ),
    _Rule(
        key="provider_token",
        title="Provider API token in source",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(
            r"\b(gh[pousr]_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{20,}|"
            r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[abprs]-[A-Za-z0-9-]{10,})\b"
        ),
        reason="A string matching a known provider token format appears in the source.",
        redact_match=True,
        group="secret",
    ),
    _Rule(
        key="private_key",
        title="Private key material in repository",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
        reason="A private key block is committed to the repository.",
        redact_match=True,
    ),
    _Rule(
        key="sql_string_building",
        title="SQL built by string concatenation or interpolation",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(
            r"(?:execute|executemany|query|raw)\s*\(\s*"
            r"(?:f[\"']|[\"'][^\"']*[\"']\s*(?:\+|%)|[\"'][^\"']*\{)",
            re.I,
        ),
        reason="Query text is assembled from variables instead of using bound parameters, which is how SQL injection happens.",
        group="sql_injection",
    ),
    _Rule(
        key="sql_fstring",
        title="SQL statement inside an f-string",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(
            r"f[\"'][^\"']*\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b[^\"']*\{",
            re.I,
        ),
        reason="An f-string interpolates values directly into SQL, bypassing parameter binding.",
        group="sql_injection",
    ),
    _Rule(
        key="command_injection",
        title="Shell command built from variables",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(
            r"(?:os\.system|os\.popen|subprocess\.(?:run|call|Popen|check_output))\s*\(\s*"
            r"(?:f[\"']|[\"'][^\"']*[\"']\s*(?:\+|%)|[^)]*\+)",
        ),
        reason="A shell command is constructed from variable input, which allows command injection.",
    ),
    _Rule(
        key="shell_true",
        title="Subprocess call with shell=True",
        severity=SEVERITY_MEDIUM,
        confidence=POTENTIAL,
        pattern=re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
        reason="shell=True passes the command through a shell. Safe with a fixed string, dangerous with any variable input.",
    ),
    _Rule(
        key="dynamic_eval",
        title="Dynamic code execution",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(r"(?<![.\w])(?:eval|exec)\s*\(\s*(?!['\"]\s*\))"),
        reason="eval/exec runs arbitrary code. If any part of the argument is user-controlled this is remote code execution.",
    ),
    _Rule(
        key="insecure_deserialisation",
        title="Insecure deserialisation",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(r"\b(?:pickle\.loads?|yaml\.load\s*\((?![^)]*Loader)|marshal\.loads)\b"),
        reason="Deserialising untrusted data with pickle or yaml.load without a safe loader permits code execution.",
    ),
    _Rule(
        key="cors_wildcard",
        title="CORS allows any origin",
        severity=SEVERITY_MEDIUM,
        confidence=CONFIRMED,
        pattern=re.compile(
            r"allow_origins\s*=\s*\[\s*[\"']\*[\"']|"
            r"Access-Control-Allow-Origin[\"']?\s*[:,]\s*[\"']\*[\"']|"
            r"origin\s*:\s*[\"']\*[\"']"
        ),
        reason="A wildcard origin lets any site call this API from a browser. Combined with credentials this is a serious exposure.",
    ),
    _Rule(
        key="tls_verification_disabled",
        title="TLS certificate verification disabled",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true"),
        reason="Disabling certificate verification removes protection against man-in-the-middle attacks.",
    ),
    _Rule(
        key="debug_enabled",
        title="Debug mode enabled in code",
        severity=SEVERITY_MEDIUM,
        confidence=POTENTIAL,
        pattern=re.compile(r"\b(?:DEBUG|debug)\s*[:=]\s*True\b|app\.run\([^)]*debug\s*=\s*True"),
        reason="Debug mode can expose stack traces and an interactive console. Acceptable in development, dangerous in production.",
    ),
    _Rule(
        key="path_traversal",
        title="File path built from input",
        severity=SEVERITY_MEDIUM,
        confidence=POTENTIAL,
        pattern=re.compile(
            r"open\s*\(\s*(?:f[\"']|[^)]*\+\s*\w+|os\.path\.join\s*\([^)]*request)",
        ),
        reason="A file path assembled from input can escape its directory with ../ unless the path is validated.",
    ),
    _Rule(
        key="secret_in_log",
        title="Possible credential written to logs",
        severity=SEVERITY_MEDIUM,
        confidence=POTENTIAL,
        pattern=re.compile(
            r"(?:print|console\.log|logger?\.\w+)\s*\([^)]*\b"
            r"(?:password|token|secret|api[_-]?key|credential)\b",
            re.I,
        ),
        reason="A logging call references a credential-named value, which would place secrets in log output.",
    ),
    _Rule(
        key="weak_hash",
        title="Weak hash used for credentials",
        severity=SEVERITY_MEDIUM,
        confidence=POTENTIAL,
        pattern=re.compile(r"\b(?:md5|sha1)\s*\(", re.I),
        reason="MD5 and SHA-1 are unsuitable for passwords. Harmless for checksums, so context decides.",
    ),
    _Rule(
        key="jwt_verification_skipped",
        title="JWT signature verification disabled",
        severity=SEVERITY_HIGH,
        confidence=CONFIRMED,
        pattern=re.compile(r"verify_signature[\"']?\s*:\s*False|verify\s*=\s*False[^)]*jwt|algorithms\s*=\s*\[\s*[\"']none"),
        reason="An unverified JWT can be forged by anyone, which defeats authentication entirely.",
    ),
)

#: Plain-language descriptions for the "we looked, found nothing" list.
_RULE_DESCRIPTIONS: dict[str, str] = {rule.key: rule.title for rule in _RULES}


def _excerpt(line: str, rule: _Rule) -> str:
    """Return a short, safe excerpt of the matching line.

    For credential rules the matched span is replaced outright, so no part of a
    real secret is ever stored in a finding, logged, or returned by the API.
    """
    text = line.strip()

    if rule.redact_match:
        text = rule.pattern.sub(
            lambda match: _mask_preserving_label(match.group(0)), text
        )

    if len(text) > 200:
        text = text[:200] + " …"
    return text


def _mask_preserving_label(matched: str) -> str:
    """Keep the variable name, drop the value.

    `API_KEY = "a7Fk29..."` becomes `API_KEY = "[REDACTED]"`, which shows the
    reviewer where the problem is without reproducing the credential.
    """
    separator = re.search(r"[:=]", matched)
    if separator:
        return f"{matched[: separator.end()]} \"{REDACTED}\""
    return REDACTED


def scan_file(path: str, content: str) -> list[SecurityHit]:
    """Run every rule against one file."""
    if not content:
        return []

    hits: list[SecurityHit] = []
    counts: dict[str, int] = {}

    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        # A pattern inside a comment is discussion, not behaviour. Credential
        # rules are exempt: a key committed inside a comment is still leaked.
        is_comment = stripped.startswith(("#", "//", "*", "/*", "<!--"))

        # One report per problem per line, even when several patterns match.
        groups_seen_on_line: set[str] = set()

        for rule in _RULES:
            if is_comment and not rule.redact_match:
                continue
            if rule.group and rule.group in groups_seen_on_line:
                continue
            if counts.get(rule.key, 0) >= MAX_HITS_PER_RULE:
                continue
            if not rule.pattern.search(line):
                continue

            if rule.group:
                groups_seen_on_line.add(rule.group)
            counts[rule.key] = counts.get(rule.key, 0) + 1
            hits.append(
                SecurityHit(
                    rule=rule.key,
                    title=rule.title,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    file=path,
                    line=line_number,
                    excerpt=_excerpt(line, rule),
                    reason=rule.reason,
                )
            )

    return hits


def scan_files(files: dict[str, str]) -> SecurityScanReport:
    """Scan a path -> content mapping and split findings by confidence.

    Args:
        files: Retrieved file contents. These have already been through Step 2's
            redaction pass, so most real secrets are gone before this runs -
            this scan catches the *shape* of a problem and reports where it is.

    Returns:
        A `SecurityScanReport`. Rules that matched nothing are listed in
        `checked_with_no_findings`, so the analysis can distinguish "we found
        nothing" from "we did not look".
    """
    report = SecurityScanReport()
    triggered: set[str] = set()

    for path, content in files.items():
        for hit in scan_file(path, content):
            triggered.add(hit.rule)
            target = report.confirmed if hit.confidence == CONFIRMED else report.potential
            target.append(hit)

    # Highest severity first, then by file, so the important lines lead.
    severity_order = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    for bucket in (report.confirmed, report.potential):
        bucket.sort(key=lambda hit: (severity_order[hit.severity], hit.file, hit.line))

    report.checked_with_no_findings = sorted(
        _RULE_DESCRIPTIONS[key] for key in _RULE_DESCRIPTIONS if key not in triggered
    )

    return report
