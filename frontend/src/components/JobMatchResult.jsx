/**
 * The job ↔ project comparison.
 *
 * The wording throughout is deliberate. A skill the repository does not show is
 * "not verified from repository evidence" — never "you don't know it". The tool
 * only knows what the analysed files contain, and says so.
 */
import { EvidenceList } from './Finding.jsx'

const STATUS_STYLES = {
  verified: {
    chip: 'bg-emerald-500/15 text-emerald-200 border-emerald-400/40',
    dot: 'bg-emerald-400',
    label: 'Verified',
  },
  partially_verified: {
    chip: 'bg-amber-500/15 text-amber-200 border-amber-400/40',
    dot: 'bg-amber-400',
    label: 'Partial',
  },
  not_verified: {
    chip: 'bg-slate-500/15 text-slate-300 border-slate-400/30',
    dot: 'bg-slate-400',
    label: 'Not verified',
  },
  contradicted: {
    chip: 'bg-rose-500/15 text-rose-200 border-rose-400/40',
    dot: 'bg-rose-400',
    label: 'Contradicted',
  },
}

const IMPORTANCE_LABELS = {
  required: 'Required',
  preferred: 'Preferred',
  nice_to_have: 'Nice to have',
  responsibility: 'Responsibility',
}

function scoreTone(score) {
  if (score >= 70) return { text: 'text-emerald-300', bar: 'bg-emerald-400' }
  if (score >= 40) return { text: 'text-amber-300', bar: 'bg-amber-400' }
  return { text: 'text-rose-300', bar: 'bg-rose-400' }
}

function ScoreDial({ label, score, caption }) {
  const tone = scoreTone(score)

  return (
    <div className="rounded-lg border border-white/10 bg-surface-raised p-4">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-3xl font-bold tabular-nums ${tone.text}`}>
        {score}
        <span className="text-base font-normal text-slate-500">/100</span>
      </p>
      <div
        className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/10"
        role="meter"
        aria-valuenow={score}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} score`}
      >
        <div className={`h-full rounded-full ${tone.bar}`} style={{ width: `${score}%` }} />
      </div>
      {caption && <p className="mt-2 text-xs text-slate-500">{caption}</p>}
    </div>
  )
}

function SkillRow({ match }) {
  const style = STATUS_STYLES[match.status] ?? STATUS_STYLES.not_verified

  return (
    <li className="rounded-lg border border-white/10 bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${style.chip}`}>
          {style.label}
        </span>
        <span className="text-sm font-medium text-slate-200">{match.skill}</span>
        <span className="text-xs text-slate-600">
          {IMPORTANCE_LABELS[match.importance] ?? match.importance} · {match.category.replace('_', ' ')}
        </span>
      </div>

      {match.reason && <p className="mt-1.5 text-xs text-slate-400">{match.reason}</p>}

      {match.evidence?.length > 0 && (
        <div className="mt-2 border-l border-white/10 pl-3">
          <EvidenceList evidence={match.evidence} />
        </div>
      )}
    </li>
  )
}

function SkillGroup({ title, matches, caption }) {
  if (!matches.length) return null

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <h4 className="text-xs uppercase tracking-wide text-slate-500">{title}</h4>
        <span className="text-xs text-slate-600">({matches.length})</span>
        {caption && <span className="text-xs text-slate-600">{caption}</span>}
      </div>
      <ul className="space-y-2">
        {matches.map((match) => (
          <SkillRow key={match.skill} match={match} />
        ))}
      </ul>
    </div>
  )
}

export default function JobMatchResult({ result, onStartInterview }) {
  const { match_score: score, readiness, matches, job } = result

  const grouped = {
    verified: matches.filter((item) => item.status === 'verified'),
    partial: matches.filter((item) => item.status === 'partially_verified'),
    missing: matches.filter(
      (item) => item.status === 'not_verified' || item.status === 'contradicted',
    ),
  }

  return (
    <section className="mt-8 space-y-6" aria-label="Job match">
      {/* --- headline ------------------------------------------------------- */}
      <header className="rounded-lg border border-white/10 bg-surface-raised p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h3 className="text-lg font-bold text-white">
              {job.title || 'This role'}
              {job.seniority && (
                <span className="ml-2 text-sm font-normal text-slate-500">
                  {job.seniority}
                </span>
              )}
            </h3>
            <p className="mt-1 text-sm text-slate-400">
              Compared against {result.repository}
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-3">
          <ScoreDial
            label="Job match"
            score={score.score}
            caption={`Required coverage ${score.required.percent}%`}
          />
          <ScoreDial
            label="Job readiness"
            score={readiness.score}
            caption={
              readiness.interview_taken
                ? `Includes interview ${readiness.interview_score}`
                : 'Before any interview'
            }
          />
          <div className="rounded-lg border border-white/10 bg-surface-raised p-4">
            <p className="text-xs uppercase tracking-wide text-slate-500">How it is scored</p>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">{score.formula}</p>
            <p className="mt-2 text-xs text-slate-600">
              {score.counted_groups} requirement{score.counted_groups === 1 ? '' : 's'} counted
              {score.excluded_requirements > 0 &&
                `, ${score.excluded_requirements} excluded as unevidenceable`}
              .
            </p>
          </div>
        </div>

        {result.interpretation && (
          <p className="mt-5 border-t border-white/10 pt-4 text-sm leading-relaxed text-slate-300">
            {result.interpretation}
          </p>
        )}

        {!result.llm_available && (
          <p className="mt-3 text-xs text-amber-300/80">
            The local model was unavailable, so the written summary was skipped.
            Every score above is computed deterministically and is unaffected.
          </p>
        )}
      </header>

      {/* --- why you match --------------------------------------------------- */}
      {result.strengths?.length > 0 && (
        <section className="rounded-lg border border-white/10 bg-surface-raised p-5">
          <h4 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Why your project matches
          </h4>
          <ul className="mt-3 space-y-2">
            {result.strengths.map((line) => (
              <li key={line} className="flex gap-2 text-sm text-slate-300">
                <span className="text-emerald-400" aria-hidden="true">✓</span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* --- skills ---------------------------------------------------------- */}
      <div className="space-y-5">
        <SkillGroup title="Strong match" matches={grouped.verified} />
        <SkillGroup
          title="Partial"
          matches={grouped.partial}
          caption="some evidence, but not conclusive"
        />
        <SkillGroup title="Not verified" matches={grouped.missing} />
      </div>

      {/* --- gaps ------------------------------------------------------------ */}
      {result.gaps?.length > 0 && (
        <section className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-5">
          <h4 className="text-sm font-semibold uppercase tracking-wide text-amber-300/80">
            Skill gaps
          </h4>
          <ul className="mt-3 space-y-1.5">
            {result.gaps.map((gap) => (
              <li key={gap.skill} className="text-sm">
                <span className="text-amber-300" aria-hidden="true">⚠ </span>
                <span className="font-medium text-amber-200">{gap.skill}</span>
                <span className="text-amber-200/70">
                  {' '}— {IMPORTANCE_LABELS[gap.importance] ?? gap.importance}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-amber-200/70">
            These are not verified from repository evidence. The analysis only
            sees a bounded selection of files, so this shows what your project
            demonstrates — not the limits of what you know.
          </p>
        </section>
      )}

      {/* --- preparation plan ------------------------------------------------ */}
      {result.learning_plan?.length > 0 && (
        <section className="rounded-lg border border-white/10 bg-surface-raised p-5">
          <h4 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Recommended preparation
          </h4>
          <ol className="mt-3 space-y-2">
            {result.learning_plan.map((item) => (
              <li key={item.skill} className="flex gap-3 text-sm">
                <span className="shrink-0 rounded bg-brand-500/15 px-1.5 py-0.5 text-xs text-brand-200">
                  {item.priority}
                </span>
                <span>
                  <span className="font-medium text-slate-200">{item.skill}</span>
                  <span className="text-slate-500"> — {item.reason}</span>
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <button
        type="button"
        onClick={onStartInterview}
        className="rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-brand-500"
      >
        Start Job Interview
      </button>

      <p className="text-center text-xs text-slate-500">
        Based on the available repository evidence. This is a comparison of what
        your project demonstrates against what the posting asks for — not an
        assessment of you, and not a hiring prediction.
      </p>
    </section>
  )
}
