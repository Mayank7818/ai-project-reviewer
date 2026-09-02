/**
 * One interview question, its repository evidence, and the answer box.
 *
 * The evidence block is the whole point: the candidate can see exactly which
 * file the question came from, which is what separates this from a generic
 * question generator.
 */
import { useEffect, useRef, useState } from 'react'

import { EvidenceList, SeverityChip } from './Finding.jsx'

const CATEGORY_LABELS = {
  project_understanding: 'Project understanding',
  architecture: 'Architecture',
  code: 'Code',
  technology: 'Technology',
  database: 'Database',
  api: 'API',
  security: 'Security',
  performance: 'Performance',
  testing: 'Testing',
  deployment: 'Deployment',
  problem_solving: 'Problem solving',
  project_decisions: 'Project decisions',
}

const DIFFICULTY_STYLES = {
  easy: 'bg-emerald-500/15 text-emerald-200 border-emerald-400/40',
  medium: 'bg-amber-500/15 text-amber-200 border-amber-400/40',
  hard: 'bg-rose-500/15 text-rose-200 border-rose-400/40',
}

export default function InterviewQuestion({
  question,
  index,
  total,
  onSubmit,
  isSubmitting,
}) {
  const [answer, setAnswer] = useState('')
  const textareaRef = useRef(null)

  // Clear and refocus whenever a new question arrives, so the candidate can
  // start typing straight away.
  useEffect(() => {
    setAnswer('')
    textareaRef.current?.focus()
  }, [question.id])

  function handleSubmit(event) {
    event.preventDefault()
    onSubmit(answer)
  }

  const progress = Math.round((index / total) * 100)

  return (
    <section className="mt-10 space-y-5" aria-label="Interview question">
      {/* --- progress ------------------------------------------------------ */}
      <div>
        <div className="flex items-baseline justify-between text-xs text-slate-500">
          <span>
            Question {index + 1} of {total}
          </span>
          <span>{progress}% complete</span>
        </div>
        <div
          className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-white/10"
          role="progressbar"
          aria-valuenow={index}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuetext={`${index} of ${total} questions answered`}
          aria-label="Interview progress"
        >
          <div
            className="h-full rounded-full bg-brand-500 transition-all"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* --- the question --------------------------------------------------- */}
      <div className="rounded-lg border border-white/10 bg-surface-raised p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">
            {CATEGORY_LABELS[question.category] ?? question.category}
          </span>
          <span
            className={`rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
              DIFFICULTY_STYLES[question.difficulty] ?? DIFFICULTY_STYLES.medium
            }`}
          >
            {question.difficulty}
          </span>
        </div>

        <p className="mt-3 text-lg leading-relaxed text-white">{question.question}</p>

        {question.why_this_question && (
          <p className="mt-2 text-sm text-slate-500">{question.why_this_question}</p>
        )}

        <div className="mt-4 border-t border-white/10 pt-3">
          {question.is_hypothetical ? (
            <>
              {/* Never implies the project contains this technology. */}
              <p className="mb-2 text-xs uppercase tracking-wide text-amber-300/80">
                {question.hypothetical_label || 'Job requirement / hypothetical'}
              </p>
              <p className="text-xs text-slate-400">
                <span className="text-slate-300">{question.job_requirement}</span>{' '}
                is asked for by the job but is not verified from your repository
                evidence. This asks what you would do — not what the project
                already does.
              </p>
            </>
          ) : (
            <>
              <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                From your repository
                {question.job_requirement && (
                  <span className="ml-2 normal-case tracking-normal text-brand-300">
                    · job requirement: {question.job_requirement}
                  </span>
                )}
              </p>
              <EvidenceList
                evidence={question.evidence}
                emptyText="No evidence attached."
              />
            </>
          )}
        </div>
      </div>

      {/* --- the answer ----------------------------------------------------- */}
      <form onSubmit={handleSubmit}>
        <label htmlFor="answer" className="block text-sm font-medium text-slate-300">
          Your answer
        </label>
        <textarea
          id="answer"
          ref={textareaRef}
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          disabled={isSubmitting}
          rows={7}
          placeholder="Answer as you would in a real interview — be specific about what you built."
          className="mt-2 w-full rounded-lg border border-white/10 bg-surface-raised px-4 py-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-brand-400 focus:outline-none disabled:opacity-60"
        />

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={isSubmitting}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400 disabled:cursor-not-allowed disabled:border disabled:border-white/10 disabled:bg-white/5 disabled:text-slate-500"
          >
            {isSubmitting && (
              <span
                aria-hidden="true"
                className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white"
              />
            )}
            {isSubmitting ? 'Evaluating…' : 'Submit Answer'}
          </button>

          <span className="text-xs text-slate-600">
            {answer.trim().length} characters
          </span>
        </div>

        {isSubmitting && (
          <p role="status" aria-live="polite" className="mt-3 text-sm text-slate-400">
            Evaluating your answer against the repository evidence…
          </p>
        )}
      </form>
    </section>
  )
}

export { CATEGORY_LABELS, SeverityChip }
