/**
 * The closing report: seven scores, what went well, what did not, and what to
 * study next.
 *
 * A dimension nobody was asked about is rendered as "not assessed" rather than
 * as a low score, because a number nobody earned would be misleading.
 */
import { CATEGORY_LABELS } from './InterviewQuestion.jsx'

const NOT_ASSESSED = 50

const DIMENSIONS = [
  ['technical', 'Technical'],
  ['project_knowledge', 'Project knowledge'],
  ['architecture', 'Architecture'],
  ['security', 'Security'],
  ['problem_solving', 'Problem solving'],
  ['communication', 'Communication'],
]

function scoreTone(score) {
  if (score >= 75) return { text: 'text-emerald-300', bar: 'bg-emerald-400' }
  if (score >= 50) return { text: 'text-amber-300', bar: 'bg-amber-400' }
  return { text: 'text-rose-300', bar: 'bg-rose-400' }
}

function ScoreBar({ label, score, assessed }) {
  const tone = scoreTone(score)

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-slate-400">{label}</span>
        <span className={`text-sm font-semibold tabular-nums ${assessed ? tone.text : 'text-slate-600'}`}>
          {assessed ? score : '—'}
        </span>
      </div>
      {/* A dimension nobody was asked about gets no meter at all. Exposing it
          as a meter reading 0 would have a screen reader announce "Security
          score 0" — precisely the "absence reads as failure" misreading the
          backend's neutral scoring exists to prevent. */}
      {assessed ? (
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
      ) : (
        <div
          className="mt-1 h-1.5 w-full rounded-full bg-white/10"
          aria-label={`${label}: not assessed`}
          role="img"
        />
      )}
    </div>
  )
}

function BulletPanel({ title, items, markerClass, emptyText }) {
  return (
    <section className="rounded-lg border border-white/10 bg-surface-raised p-5">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h3>
      {items?.length ? (
        <ul className="mt-3 space-y-2">
          {items.map((item) => (
            <li key={item} className="flex gap-2 text-sm text-slate-300">
              <span
                className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${markerClass}`}
                aria-hidden="true"
              />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-slate-500">{emptyText}</p>
      )}
    </section>
  )
}

export default function InterviewSummary({ session, onRestart }) {
  const { summary, history } = session
  const scores = summary.scores
  const overallTone = scoreTone(scores.overall)

  // A dimension counts as assessed if any answered question fed into it. The
  // backend already reports the neutral 50 for untested ones; we detect that by
  // checking whether it named the dimension in weak_areas as "not assessed".
  const notAssessed = new Set(
    (summary.weak_areas ?? [])
      .filter((item) => item.includes('not assessed'))
      .map((item) => item.split(':')[0].trim().toLowerCase().replace(/ /g, '_'))
  )

  return (
    <section className="mt-10 space-y-6" aria-label="Interview results">
      <header className="rounded-lg border border-white/10 bg-surface-raised p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-white">Interview complete</h2>
            <p className="mt-1 text-sm text-slate-400">
              {session.repository} · {session.target_role_label} ·{' '}
              {history.length} of {session.total_questions} questions answered
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs uppercase tracking-wide text-slate-500">Overall</p>
            <p className={`text-4xl font-bold tabular-nums ${overallTone.text}`}>
              {scores.overall}
              <span className="text-base font-normal text-slate-500">/100</span>
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {DIMENSIONS.map(([key, label]) => (
            <ScoreBar
              key={key}
              label={label}
              score={scores[key]}
              assessed={!notAssessed.has(key)}
            />
          ))}
        </div>

        {summary.overall_feedback && (
          <p className="mt-5 border-t border-white/10 pt-4 text-sm leading-relaxed text-slate-300">
            {summary.overall_feedback}
          </p>
        )}
      </header>

      {/* --- job readiness, when this interview came from a job posting ----- */}
      {session.readiness && (
        <section className="rounded-lg border border-brand-400/30 bg-brand-500/10 p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h3 className="text-sm font-semibold uppercase tracking-wide text-brand-200/80">
                Job readiness
              </h3>
              {session.job_title && (
                <p className="mt-1 text-sm text-slate-400">{session.job_title}</p>
              )}
            </div>
            <p className={`text-4xl font-bold tabular-nums ${scoreTone(session.readiness.score).text}`}>
              {session.readiness.score}
              <span className="text-base font-normal text-slate-500">/100</span>
            </p>
          </div>

          <dl className="mt-4 grid gap-3 sm:grid-cols-3">
            {[
              ['Job match', session.readiness.match_score],
              ['Interview', session.readiness.interview_score],
              ['Required coverage', session.readiness.required_coverage],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
                <dd className="mt-0.5 text-lg font-semibold tabular-nums text-slate-200">
                  {value ?? '—'}
                </dd>
              </div>
            ))}
          </dl>

          <div className="mt-4 grid gap-4 border-t border-white/10 pt-4 sm:grid-cols-2">
            <div>
              <p className="mb-1.5 text-xs uppercase tracking-wide text-emerald-300/70">
                Strong
              </p>
              {session.readiness.strong_skills?.length ? (
                <ul className="flex flex-wrap gap-1.5">
                  {session.readiness.strong_skills.map((skill) => (
                    <li
                      key={skill}
                      className="rounded border border-emerald-400/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-200"
                    >
                      {skill}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-slate-500">
                  No required skill was verified from repository evidence.
                </p>
              )}
            </div>

            <div>
              <p className="mb-1.5 text-xs uppercase tracking-wide text-amber-300/70">
                Needs work
              </p>
              {session.readiness.needs_work?.length ? (
                <ul className="flex flex-wrap gap-1.5">
                  {session.readiness.needs_work.map((skill) => (
                    <li
                      key={skill}
                      className="rounded border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-200"
                    >
                      {skill}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-slate-500">Nothing outstanding.</p>
              )}
            </div>
          </div>

          <p className="mt-4 text-xs text-slate-500">{session.readiness.formula}</p>
        </section>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <BulletPanel
          title="Strong areas"
          items={summary.strong_areas}
          markerClass="bg-emerald-400"
          emptyText="Nothing stood out as a clear strength in this session."
        />
        <BulletPanel
          title="Weak areas"
          items={summary.weak_areas}
          markerClass="bg-amber-400"
          emptyText="No consistent weaknesses were identified."
        />
      </div>

      <BulletPanel
        title="Recommended topics to study"
        items={summary.recommended_topics}
        markerClass="bg-brand-400"
        emptyText="No specific study topics were identified."
      />

      {summary.questions_to_revisit?.length > 0 && (
        <BulletPanel
          title="Questions to revisit"
          items={summary.questions_to_revisit}
          markerClass="bg-rose-400"
          emptyText=""
        />
      )}

      {summary.unverified_claims?.length > 0 && (
        <section className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-5">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-amber-300/80">
            Claims not verified from repository evidence
          </h3>
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {summary.unverified_claims.map((claim) => (
              <li
                key={claim.technology}
                className="rounded border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-200"
              >
                {claim.technology}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-amber-200/70">
            These were mentioned in your answers but do not appear in the analysed
            files. The analysis only sees a bounded subset of the repository, so
            this is a prompt to check rather than a verdict.
          </p>
        </section>
      )}

      {/* --- per-question transcript ---------------------------------------- */}
      <details className="rounded-lg border border-white/10 bg-surface-raised">
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-slate-200">
          Full transcript ({history.length})
        </summary>
        <div className="space-y-4 border-t border-white/10 px-4 py-4">
          {history.map((record) => (
            <div key={record.question.id} className="border-l-2 border-white/10 pl-3">
              <p className="text-xs uppercase tracking-wide text-slate-500">
                {CATEGORY_LABELS[record.question.category] ?? record.question.category} ·{' '}
                {record.evaluation.score}/10
              </p>
              <p className="mt-1 text-sm text-slate-300">{record.question.question}</p>
              <p className="mt-1 whitespace-pre-wrap text-xs text-slate-500">
                {record.answer || '(no answer given)'}
              </p>
            </div>
          ))}
        </div>
      </details>

      <button
        type="button"
        onClick={onRestart}
        className="rounded-lg border border-white/10 bg-white/5 px-6 py-3 text-sm font-semibold text-slate-200 transition-colors hover:bg-white/10"
      >
        Start another interview
      </button>
    </section>
  )
}
