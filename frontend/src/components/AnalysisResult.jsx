/**
 * Renders the evidence-based analysis produced by the local model.
 *
 * Layout follows the reading order a reviewer actually wants: the headline
 * score and summary first, then what the project is, then what was found in it,
 * then the audit trail.
 *
 * Detail-heavy sections are collapsed by default so the page stays readable —
 * the requirement is depth on demand, not everything at once.
 */
import { useState } from 'react'

import { EvidenceList, FindingList } from './Finding.jsx'

function scoreTone(score) {
  if (score >= 75) return { text: 'text-emerald-300', bar: 'bg-emerald-400' }
  if (score >= 50) return { text: 'text-amber-300', bar: 'bg-amber-400' }
  return { text: 'text-rose-300', bar: 'bg-rose-400' }
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value ?? 0)
}

function Panel({ title, children, aside }) {
  return (
    <section className="rounded-lg border border-white/10 bg-surface-raised p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          {title}
        </h3>
        {aside}
      </div>
      <div className="mt-3">{children}</div>
    </section>
  )
}

/** Compact score readout, used in a panel header. */
function ScorePill({ score }) {
  const tone = scoreTone(score)
  return (
    <span className={`text-lg font-bold tabular-nums ${tone.text}`}>
      {score}
      <span className="text-xs font-normal text-slate-500">/100</span>
    </span>
  )
}

function ScoreBar({ label, score }) {
  const tone = scoreTone(score)

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-slate-400">{label}</span>
        <span className={`text-sm font-semibold tabular-nums ${tone.text}`}>{score}</span>
      </div>
      <div
        className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-white/10"
        role="meter"
        aria-valuenow={score}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} score`}
      >
        <div className={`h-full rounded-full ${tone.bar}`} style={{ width: `${score}%` }} />
      </div>
    </div>
  )
}

function Disclosure({ title, count, children, defaultOpen = false, tone }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className="rounded-lg border border-white/10 bg-surface-raised">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm font-medium text-slate-200"
      >
        <span className="flex flex-wrap items-baseline gap-2">
          {title}
          {count !== undefined && (
            <span className={tone ?? 'text-slate-500'}>({formatNumber(count)})</span>
          )}
        </span>
        <span aria-hidden="true" className="text-slate-500">
          {open ? '−' : '+'}
        </span>
      </button>
      {open && <div className="border-t border-white/10 px-4 py-4">{children}</div>}
    </div>
  )
}

export default function AnalysisResult({ data }) {
  const { repository, analysis, meta } = data
  const overallTone = scoreTone(analysis.overall_score)

  const { security } = analysis
  const confirmedCount = security.confirmed_issues?.length ?? 0
  const potentialCount = security.potential_risks?.length ?? 0

  // Extracts are listed under the file they came from, so the audit trail reads
  // as "this file, these line ranges" rather than as one flat list.
  const snippetsByPath = (meta.snippets ?? []).reduce((grouped, snippet) => {
    ;(grouped[snippet.path] ??= []).push(snippet)
    return grouped
  }, {})

  return (
    <section className="mt-10 space-y-6" aria-label="AI analysis">
      {/* --- identity + headline scores ------------------------------------ */}
      <header className="rounded-lg border border-white/10 bg-surface-raised p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-baseline gap-x-3">
              <h2 className="text-xl font-bold text-white">{repository.full_name}</h2>
              <a
                href={repository.html_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-brand-300 hover:text-brand-200"
              >
                View on GitHub ↗
              </a>
            </div>
            {repository.description && (
              <p className="mt-1 text-sm text-slate-400">{repository.description}</p>
            )}
            <p className="mt-2 text-xs text-slate-500">
              {formatNumber(repository.stars)} stars ·{' '}
              {formatNumber(repository.forks)} forks ·{' '}
              {repository.primary_language ?? 'language unknown'} ·{' '}
              {repository.license ?? 'no license'}
            </p>
            {/* Said plainly rather than implied: a cached result is the earlier
                run, not a fresh one, and the reader is entitled to know. */}
            {meta.cached && (
              <p className="mt-2 inline-flex items-center gap-1.5 rounded border border-white/10 bg-white/5 px-2 py-1 text-xs text-slate-400">
                <span aria-hidden="true">⌛</span>
                Reused from an earlier analysis of this repository — no model was
                run.
              </p>
            )}
          </div>

          <div className="text-right">
            <p className="text-xs uppercase tracking-wide text-slate-500">Overall</p>
            <p className={`text-4xl font-bold tabular-nums ${overallTone.text}`}>
              {analysis.overall_score}
              <span className="text-base font-normal text-slate-500">/100</span>
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <ScoreBar label="Code quality" score={analysis.code_quality.score} />
          <ScoreBar label="Security" score={security.score} />
          <ScoreBar label="Performance" score={analysis.performance.score} />
          <ScoreBar label="Documentation" score={analysis.documentation.score} />
          <ScoreBar label="Testing" score={analysis.testing.score} />
        </div>
      </header>

      {/* --- summary -------------------------------------------------------- */}
      <Panel title="Project summary">
        <p className="text-sm leading-relaxed text-slate-300">
          {analysis.project_summary || 'The model did not produce a summary.'}
        </p>
      </Panel>

      {/* --- technologies --------------------------------------------------- */}
      <Panel title="Technologies">
        {analysis.technologies?.length ? (
          <ul className="flex flex-wrap gap-2">
            {analysis.technologies.map((tech) => (
              <li
                key={tech}
                className="rounded-full border border-brand-400/30 bg-brand-500/15 px-3 py-1 text-sm text-brand-200"
              >
                {tech}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">
            No technologies could be identified from the retrieved files.
          </p>
        )}
      </Panel>

      {/* --- architecture + its evidence ------------------------------------ */}
      <Panel title="Architecture">
        <p className="text-sm leading-relaxed text-slate-300">
          {analysis.architecture.summary || 'The model did not describe the architecture.'}
        </p>

        <div className="mt-4 border-t border-white/10 pt-3">
          <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
            Evidence
          </p>
          <EvidenceList
            evidence={analysis.architecture.evidence}
            emptyText="No files were cited for this description."
          />
        </div>
      </Panel>

      {/* --- security ------------------------------------------------------- */}
      <Disclosure
        title="Security"
        count={confirmedCount}
        tone={confirmedCount > 0 ? 'text-rose-300' : 'text-emerald-300'}
        defaultOpen={confirmedCount > 0}
      >
        <div className="space-y-5">
          <div>
            <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
              Confirmed issues
              <span className="ml-2 normal-case tracking-normal text-slate-600">
                found by pattern matching against real lines
              </span>
            </p>
            <FindingList
              findings={security.confirmed_issues}
              emptyText="No confirmed issues were found in the analysed files."
            />
          </div>

          <div>
            <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
              Potential risks
              <span className="ml-2 normal-case tracking-normal text-slate-600">
                depend on context
              </span>
            </p>
            <FindingList
              findings={security.potential_risks}
              emptyText="No potential risks were identified."
            />
          </div>

          {security.no_evidence?.length > 0 && (
            <div>
              <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                Checked, no evidence found
              </p>
              <ul className="flex flex-wrap gap-1.5">
                {security.no_evidence.map((item) => (
                  <li
                    key={item}
                    className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-slate-400"
                  >
                    {item}
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-slate-600">
                These patterns did not match. That is not the same as the project
                being secure in those respects, and none of them is a vulnerability.
              </p>
            </div>
          )}
        </div>
      </Disclosure>

      {/* --- code quality --------------------------------------------------- */}
      <Disclosure
        title="Code quality"
        count={analysis.code_quality.findings?.length}
      >
        {analysis.code_quality.reason && (
          <p className="mb-4 text-sm text-slate-400">{analysis.code_quality.reason}</p>
        )}
        <FindingList
          findings={analysis.code_quality.findings}
          emptyText="No specific code quality findings were reported."
        />
      </Disclosure>

      {/* --- performance ---------------------------------------------------- */}
      <Disclosure title="Performance" count={analysis.performance.findings?.length}>
        {analysis.performance.reason && (
          <p className="mb-4 text-sm text-slate-400">{analysis.performance.reason}</p>
        )}
        <FindingList
          findings={analysis.performance.findings}
          emptyText="No performance issues were identified in the analysed files."
        />
      </Disclosure>

      {/* --- documentation -------------------------------------------------- */}
      <Disclosure title="Documentation" count={analysis.documentation.findings?.length}>
        {analysis.documentation.reason && (
          <p className="mb-4 text-sm text-slate-400">{analysis.documentation.reason}</p>
        )}
        <FindingList
          findings={analysis.documentation.findings}
          emptyText="No documentation findings were reported."
        />
      </Disclosure>

      {/* --- testing -------------------------------------------------------- */}
      <Disclosure title="Testing" count={analysis.testing.evidence?.length}>
        {analysis.testing.reason && (
          <p className="mb-4 text-sm text-slate-400">{analysis.testing.reason}</p>
        )}
        <EvidenceList
          evidence={analysis.testing.evidence}
          emptyText="No test files appear in the retrieved selection."
        />
      </Disclosure>

      {/* --- strengths / weaknesses ----------------------------------------- */}
      <div className="grid gap-6 md:grid-cols-2">
        <Panel title="Strengths">
          {analysis.strengths?.length ? (
            <ul className="space-y-2">
              {analysis.strengths.map((item) => (
                <li key={item} className="flex gap-2 text-sm text-slate-300">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" aria-hidden="true" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">No strengths were identified.</p>
          )}
        </Panel>

        <Panel title="Weaknesses">
          {analysis.weaknesses?.length ? (
            <ul className="space-y-2">
              {analysis.weaknesses.map((item) => (
                <li key={item} className="flex gap-2 text-sm text-slate-300">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">No weaknesses were identified.</p>
          )}
        </Panel>
      </div>

      {/* --- the audit trail ------------------------------------------------ */}
      <Disclosure title="Evidence the model analysed" count={meta.files_analyzed?.length}>
        <p className="mb-3 text-xs text-slate-500">
          Produced locally by <span className="text-slate-300">{meta.model}</span> in{' '}
          {meta.duration_seconds}s from {formatNumber(meta.context_chars)}
          {meta.context_limit > 0 && ` of ${formatNumber(meta.context_limit)}`} characters
          of repository text{meta.readme_included ? ', including the README' : ''}
          {meta.snippets?.length > 0 &&
            `, sent as ${meta.snippets.length} code extract(s)`}
          .
          {meta.stages_completed?.length > 1 &&
            ` Pipeline: ${meta.stages_completed.join(' → ')}.`}
        </p>

        {Object.keys(meta.domain_counts ?? {}).length > 0 && (
          <ul className="mb-3 flex flex-wrap gap-1.5">
            {Object.entries(meta.domain_counts).map(([domain, count]) => (
              <li
                key={domain}
                className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-xs text-slate-400"
              >
                {domain.replace('_', ' ')} · {count}
              </li>
            ))}
          </ul>
        )}

        {meta.dependencies?.length > 0 && (
          <p className="mb-3 text-xs text-slate-500">
            Dependency manifests parsed:{' '}
            {meta.dependencies
              .map((item) => `${item.file} (${item.count})`)
              .join(', ')}
          </p>
        )}

        <ul className="max-h-72 overflow-y-auto text-xs">
          {meta.files_analyzed?.map((record) => (
            <li key={record.path} className="py-0.5">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="font-mono text-slate-300">{record.path}</span>
                <span className="text-slate-600">{record.domain.replace('_', ' ')}</span>
                {record.truncated && (
                  <span className="text-amber-400">
                    {record.lines_total > 0
                      ? `${record.lines_shown} of ${record.lines_total} lines`
                      : 'extracts only'}
                  </span>
                )}
              </div>
              {/* The line ranges are the file's own, so they can be opened on
                  GitHub and will land on the code the model actually read. */}
              {snippetsByPath[record.path]?.length > 0 && (
                <ul className="ml-3 mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
                  {snippetsByPath[record.path].map((snippet) => (
                    <li key={`${snippet.line_start}-${snippet.line_end}`}>
                      <span className="font-mono text-slate-400">
                        L{snippet.line_start}–{snippet.line_end}
                      </span>{' '}
                      {snippet.reason}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>

        {meta.files_omitted?.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-slate-500">
              {formatNumber(meta.files_omitted.length)} retrieved file(s) were not
              analysed
            </summary>
            <ul className="mt-2 text-xs">
              {meta.files_omitted.map((item) => (
                <li key={item.path} className="py-0.5">
                  <span className="font-mono text-slate-400">{item.path}</span>
                  <span className="text-slate-600"> — {item.reason}</span>
                </li>
              ))}
            </ul>
          </details>
        )}

        {(meta.evidence_dropped > 0 || meta.line_numbers_cleared > 0) && (
          <p className="mt-3 border-t border-white/10 pt-2 text-xs text-slate-500">
            Validation: {meta.evidence_dropped} unverifiable citation(s) discarded,{' '}
            {meta.line_numbers_cleared} line reference(s) cleared because they did
            not exist in the files sent.
          </p>
        )}
      </Disclosure>

      <p className="text-center text-xs text-slate-500">
        Generated locally with Ollama. Findings are grounded in the files listed
        above — treat them as a starting point, not a verdict.
      </p>
    </section>
  )
}
