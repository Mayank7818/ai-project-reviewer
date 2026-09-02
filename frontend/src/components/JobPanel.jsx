/**
 * The "Analyze a Job" experience: paste a posting, see how this project
 * evidences it, then interview against it.
 *
 * The interview reuses `InterviewPanel` with the job endpoints injected, so
 * there is one implementation of the question/answer/feedback loop rather than
 * two that can drift apart.
 */
import { useEffect, useRef, useState } from 'react'

import { fetchInterviewOptions } from '../api/interview.js'
import {
  finishJobInterview,
  matchJob,
  startJobInterview,
  submitJobAnswer,
} from '../api/job.js'
import { describeError } from '../utils/errorHints.js'
import ErrorPanel from './ErrorPanel.jsx'
import InterviewPanel from './InterviewPanel.jsx'
import JobMatchResult from './JobMatchResult.jsx'

const FORM = 'form'
const MATCH = 'match'
const INTERVIEW = 'interview'

export default function JobPanel({ githubUrl, repository }) {
  const [phase, setPhase] = useState(FORM)
  const [description, setDescription] = useState('')
  const [targetRole, setTargetRole] = useState('software_developer')
  const [company, setCompany] = useState('')
  const [jobTitle, setJobTitle] = useState('')
  const [roles, setRoles] = useState([
    { key: 'software_developer', label: 'Software Developer' },
  ])
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const abortRef = useRef(null)
  useEffect(() => () => abortRef.current?.abort(), [])

  useEffect(() => {
    const controller = new AbortController()
    fetchInterviewOptions({ signal: controller.signal })
      .then((data) => setRoles(data.roles))
      .catch(() => {
        // The form works with its default; a real failure surfaces on submit.
      })
    return () => controller.abort()
  }, [])

  async function handleSubmit(event) {
    event.preventDefault()

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setBusy(true)
    setError(null)

    try {
      const data = await matchJob(
        { githubUrl, jobDescription: description, targetRole, company, jobTitle },
        { signal: controller.signal },
      )
      setResult(data)
      setPhase(MATCH)
    } catch (requestError) {
      if (controller.signal.aborted) return
      setError(describeError(requestError))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  // The job interview needs the posting alongside the usual setup, so the
  // adapter closes over it and InterviewPanel stays unaware of job specifics.
  const interviewApi = {
    start: (setup, options) =>
      startJobInterview(
        {
          githubUrl,
          jobDescription: description,
          targetRole: setup.targetRole,
          company,
          jobTitle,
          difficulty: setup.difficulty,
          questionCount: setup.questionCount,
        },
        options,
      ),
    answer: submitJobAnswer,
    finish: finishJobInterview,
  }

  if (phase === INTERVIEW) {
    return (
      <div className="mt-10 border-t border-white/10 pt-8">
        <button
          type="button"
          onClick={() => setPhase(MATCH)}
          className="text-xs text-slate-500 hover:text-slate-300"
        >
          ← Back to the job match
        </button>
        <InterviewPanel
          githubUrl={githubUrl}
          repository={repository}
          api={interviewApi}
          heading="Job interview"
          intro={
            result?.job?.title
              ? `Questions are drawn from ${repository} and from this ${result.job.title} posting.`
              : undefined
          }
        />
      </div>
    )
  }

  return (
    <section className="mt-10 border-t border-white/10 pt-8">
      <h2 className="text-lg font-semibold text-white">Analyze a Job</h2>
      <p className="mt-1 text-sm text-slate-400">
        Paste a job posting to see how <span className="text-slate-300">{repository}</span>{' '}
        evidences what it asks for.
      </p>

      <form onSubmit={handleSubmit} className="mt-5 space-y-4">
        <div>
          <label
            htmlFor="job-description"
            className="block text-xs uppercase tracking-wide text-slate-500"
          >
            Job description
          </label>
          <textarea
            id="job-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            disabled={busy}
            rows={10}
            required
            aria-describedby="job-description-help"
            placeholder={'Paste the posting here, including its requirements.'}
            className="mt-1 w-full rounded-lg border border-white/10 bg-surface-raised px-4 py-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-brand-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/60 disabled:opacity-60"
          />
          <p id="job-description-help" className="mt-1 text-xs text-slate-500">
            {description.trim()
              ? `${description.trim().length} characters. The requirements section is the part that matters.`
              : 'Paste the whole posting — a job title alone cannot be matched against evidence.'}
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Target role
            </span>
            <select
              value={targetRole}
              onChange={(event) => setTargetRole(event.target.value)}
              disabled={busy}
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
              Company <span className="normal-case text-slate-600">(optional)</span>
            </span>
            <input
              type="text"
              value={company}
              onChange={(event) => setCompany(event.target.value)}
              disabled={busy}
              className="mt-1 w-full rounded-lg border border-white/10 bg-surface-raised px-3 py-2 text-sm text-slate-100 disabled:opacity-60"
            />
          </label>

          <label className="block">
            <span className="text-xs uppercase tracking-wide text-slate-500">
              Job title <span className="normal-case text-slate-600">(optional)</span>
            </span>
            <input
              type="text"
              value={jobTitle}
              onChange={(event) => setJobTitle(event.target.value)}
              disabled={busy}
              className="mt-1 w-full rounded-lg border border-white/10 bg-surface-raised px-3 py-2 text-sm text-slate-100 disabled:opacity-60"
            />
          </label>
        </div>

        <button
          type="submit"
          disabled={busy || !description.trim()}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400 disabled:cursor-not-allowed disabled:border disabled:border-white/10 disabled:bg-white/5 disabled:text-slate-500"
        >
          {busy && (
            <span
              aria-hidden="true"
              className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white"
            />
          )}
          {busy ? 'Analyzing job…' : 'Analyze Job'}
        </button>

        {/* Feature 17: only claimed because the architecture actually does this. */}
        <p className="text-xs text-slate-500">
          Your job description is processed locally by the configured Ollama
          model. It is not stored on disk, not written to logs, and not sent to
          any third-party service.
        </p>

        {busy && (
          <p role="status" aria-live="polite" className="text-sm text-slate-400">
            Comparing the posting against the analysed repository evidence. The
            comparison itself is arithmetic; the model only writes it up, so this
            is quicker than the analysis was.
          </p>
        )}

        <ErrorPanel error={error} onRetry={() => setError(null)} />
      </form>

      {phase === MATCH && result && (
        <JobMatchResult result={result} onStartInterview={() => setPhase(INTERVIEW)} />
      )}
    </section>
  )
}
