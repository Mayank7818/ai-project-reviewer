# Security

## Reporting a vulnerability

Please report security issues privately through
[GitHub's private vulnerability reporting](https://github.com/Mayank7818/ai-project-reviewer/security/advisories/new)
rather than opening a public issue.

## What this application handles

It reads public GitHub repositories, and it accepts two kinds of text that a
user considers theirs: a job description, and their own interview answers.

| Data | Where it goes |
|---|---|
| Repository content | Fetched from GitHub, held in memory, sent to a **local** Ollama model |
| Job description | Held in memory, sent to the local model, never written to disk or logs |
| Interview answers | Held in the in-memory session store for its TTL |
| `GITHUB_TOKEN` | Server-side only. Never returned by any endpoint, never sent to the browser |

Nothing is sent to a third-party AI provider. There is no paid API and no cloud
model — the analysis runs against Ollama on the machine hosting the backend.

## Design decisions that are security decisions

**Untrusted input is treated as data.** A README, a source comment, a job
posting and a candidate's answer are all text this project did not write, and
any of them can address the model directly. The defence is three layers deep and
the prompt is the weakest of them:

1. **Structural** — every model call decodes against a JSON Schema, so a reply
   has a fixed shape whatever the input says.
2. **Deterministic** — scores are clamped, citations are validated against the
   files actually sent, and claim verification is arithmetic over the
   repository's own symbols. None of it consults the model's opinion.
3. **Prompt** — untrusted text is fenced, and any attempt to forge that fence
   from inside is defanged before the prompt is assembled
   (`backend/app/core/untrusted.py`).

**Secrets are filtered twice.** Files whose paths look like credential material
are excluded during retrieval, and excluded again when the prompt is built. Any
credential-shaped string that survives is redacted before it can reach the
model or the browser.

**Errors name the field, never its value.** FastAPI would otherwise return the
offending input verbatim, which for `POST /job/match` means the whole job
description in an error body that something downstream may well log.

**Production refuses to start misconfigured.** `ENVIRONMENT=production` with
debug logging on, a wildcard CORS origin, or an empty origin list is a boot
failure rather than a silent one.

## What is not claimed

- There is no authentication or authorisation. Anyone who can reach the API can
  use it. Do not expose it to the internet without something in front of it.
- Sessions and caches are in memory and unencrypted. A restart clears them.
- The security scanner reports **patterns**, not vulnerabilities. It never
  claims a dependency is vulnerable — no CVE data is consulted, and absence of a
  finding is not evidence of safety.
- A local model can still be wrong. The validation layers bound the damage; they
  do not make the output authoritative.

## If you find a credential in this repository

Revoke it first, then report it. The CI pipeline fails the build on any
committed credential and on any tracked `.env`, but a check is not a guarantee.
