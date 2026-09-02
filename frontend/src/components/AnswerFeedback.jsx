/**
 * Feedback on one answer: score, what was right, what was missing, the
 * follow-up, and any claim the repository could not corroborate.
 *
 * The unverified-claims block is worded carefully. It never accuses — the
 * analysis only ever saw a bounded subset of the repository, so "not verified"
 * is the honest ceiling on what we can say.
 */

function scoreTone(score) {
  if (score >= 7) return { text: 'text-emerald-300', ring: 'border-emerald-400/40' }
  if (score >= 4) return { text: 'text-amber-300', ring: 'border-amber-400/40' }
  return { text: 'text-rose-300', ring: 'border-rose-400/40' }
}

function PointList({ title, items, markerClass, titleClass }) {
  if (!items?.length) return null

  return (
    <div>
      <p className={`mb-1.5 text-xs uppercase tracking-wide ${titleClass}`}>{title}</p>
      <ul className="space-y-1.5">
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
    </div>
  )
}

export default function AnswerFeedback({
  evaluation,
  onContinue,
  isComplete,
  isFinishing,
}) {
  const tone = scoreTone(evaluation.score)

  return (
    <section className="mt-8 space-y-5" aria-label="Answer feedback">
      <div className={`rounded-lg border bg-surface-raised p-5 ${tone.ring}`}>
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Evaluation
          </h3>
          <p className={`text-3xl font-bold tabular-nums ${tone.text}`}>
            {evaluation.score}
            <span className="text-base font-normal text-slate-500">/10</span>
          </p>
        </div>

        {evaluation.feedback && (
          <p className="mt-3 text-sm leading-relaxed text-slate-300">
            {evaluation.feedback}
          </p>
        )}

        <div className="mt-5 space-y-4">
          <PointList
            title="Correct"
            items={evaluation.correct_points}
            markerClass="bg-emerald-400"
            titleClass="text-emerald-300/70"
          />
          <PointList
            title="Missing"
            items={evaluation.missing_points}
            markerClass="bg-amber-400"
            titleClass="text-amber-300/70"
          />
          <PointList
            title="Incorrect"
            items={evaluation.incorrect_points}
            markerClass="bg-rose-400"
            titleClass="text-rose-300/70"
          />
        </div>

        <p className="mt-5 border-t border-white/10 pt-3 text-xs text-slate-500">
          Communication: {evaluation.communication_score}/10
        </p>
      </div>

      {/* --- claims the repository does not corroborate ---------------------- */}
      {evaluation.unverified_claims?.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-5">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-amber-300/80">
            Claims not verified from repository evidence
          </h3>
          <ul className="mt-3 space-y-2">
            {evaluation.unverified_claims.map((claim) => (
              <li key={claim.technology} className="text-sm">
                <span className="font-medium text-amber-200">{claim.technology}</span>
                <span className="text-amber-200/70"> — {claim.note}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {evaluation.verified_claims?.length > 0 && (
        <div className="rounded-lg border border-white/10 bg-surface-raised px-4 py-3">
          <p className="text-xs uppercase tracking-wide text-slate-500">
            Verified against your repository
          </p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {evaluation.verified_claims.map((claim) => (
              <li
                key={claim.technology}
                className="rounded border border-emerald-400/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-200"
                title={claim.found_in}
              >
                {claim.technology}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* --- follow-up ------------------------------------------------------- */}
      {evaluation.follow_up_question && (
        <div className="rounded-lg border border-brand-400/30 bg-brand-500/10 p-5">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-brand-200/80">
            Follow-up
          </h3>
          <p className="mt-2 text-sm leading-relaxed text-brand-100">
            {evaluation.follow_up_question}
          </p>
          <p className="mt-2 text-xs text-brand-200/60">
            Worth thinking through — a real interviewer would ask this next.
          </p>
        </div>
      )}

      <button
        type="button"
        onClick={onContinue}
        disabled={isFinishing}
        className="inline-flex items-center justify-center gap-2 rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400 disabled:cursor-not-allowed disabled:border disabled:border-white/10 disabled:bg-white/5 disabled:text-slate-500"
      >
        {isFinishing && (
          <span
            aria-hidden="true"
            className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white"
          />
        )}
        {isComplete ? (isFinishing ? 'Preparing results…' : 'See results') : 'Continue'}
      </button>
    </section>
  )
}
