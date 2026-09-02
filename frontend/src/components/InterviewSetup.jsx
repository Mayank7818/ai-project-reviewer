/**
 * The pre-interview form: target role, difficulty and question count.
 *
 * Options come from the backend rather than being duplicated here, so adding a
 * role server-side makes it appear in this dropdown with no frontend change.
 */
import { useEffect, useState } from 'react'

import { fetchInterviewOptions } from '../api/interview.js'
import ErrorPanel from './ErrorPanel.jsx'

const DIFFICULTY_LABELS = {
  easy: 'Easy — your own project, explained',
  medium: 'Medium — implementation and design',
  hard: 'Hard — trade-offs, scale, failure',
  mixed: 'Mixed — 30% easy, 50% medium, 20% hard',
}

export default function InterviewSetup({
  repository,
  onStart,
  isStarting,
  error,
  intro,
}) {
  const [options, setOptions] = useState(null)
  const [targetRole, setTargetRole] = useState('software_developer')
  const [difficulty, setDifficulty] = useState('mixed')
  const [questionCount, setQuestionCount] = useState(10)

  useEffect(() => {
    const controller = new AbortController()

    fetchInterviewOptions({ signal: controller.signal })
      .then((data) => {
        setOptions(data)
        setQuestionCount(data.default_question_count)
      })
      .catch(() => {
        // The form still works with its defaults; the Start call will surface
        // any real backend problem.
      })

    return () => controller.abort()
  }, [])

  function handleSubmit(event) {
    event.preventDefault()
    onStart({ targetRole, difficulty, questionCount })
  }

  const roles = options?.roles ?? [
    { key: 'software_developer', label: 'Software Developer' },
  ]
  const difficulties = options?.difficulties ?? ['easy', 'medium', 'hard', 'mixed']

  return (
    <section className="mt-10 rounded-lg border border-brand-400/30 bg-brand-500/5 p-5">
      <h2 className="text-lg font-semibold text-white">Practice interview</h2>
      <p className="mt-1 text-sm text-slate-400">
        {intro ?? (
          <>
            Questions are generated from{' '}
            <span className="text-slate-300">{repository}</span> — every one
            cites a file that was actually analysed.
          </>
        )}
      </p>

      <form onSubmit={handleSubmit} className="mt-5 space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Target role
            </span>
            <select
              value={targetRole}
              onChange={(event) => setTargetRole(event.target.value)}
              disabled={isStarting}
              className="mt-1 w-full rounded-lg border border-white/10 bg-surface-raised px-3 py-2 text-sm text-slate-100 disabled:opacity-60"
            >
              {roles.map((role) => (
                <option key={role.key} value={role.key}>
                  {role.label}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Difficulty
            </span>
            <select
              value={difficulty}
              onChange={(event) => setDifficulty(event.target.value)}
              disabled={isStarting}
              className="mt-1 w-full rounded-lg border border-white/10 bg-surface-raised px-3 py-2 text-sm text-slate-100 disabled:opacity-60"
            >
              {difficulties.map((level) => (
                <option key={level} value={level}>
                  {DIFFICULTY_LABELS[level] ?? level}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Questions
            </span>
            <input
              type="number"
              min={options?.min_questions ?? 3}
              max={options?.max_questions ?? 20}
              value={questionCount}
              onChange={(event) => setQuestionCount(Number(event.target.value))}
              disabled={isStarting}
              className="mt-1 w-full rounded-lg border border-white/10 bg-surface-raised px-3 py-2 text-sm text-slate-100 disabled:opacity-60"
            />
          </label>
        </div>

        <button
          type="submit"
          disabled={isStarting}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400 disabled:cursor-not-allowed disabled:border disabled:border-white/10 disabled:bg-white/5 disabled:text-slate-500"
        >
          {isStarting && (
            <span
              aria-hidden="true"
              className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white"
            />
          )}
          {isStarting ? 'Preparing questions…' : 'Start Interview'}
        </button>

        {isStarting && (
          <p role="status" aria-live="polite" className="text-sm text-slate-400">
            Generating questions from your repository evidence. This is one local
            model call, so it is much quicker than the analysis.
          </p>
        )}

        <ErrorPanel error={error} />
      </form>
    </section>
  )
}
