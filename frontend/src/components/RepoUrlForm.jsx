/**
 * The GitHub repository URL input and the "Analyze Project" button.
 *
 * Owns the request lifecycle - idle, retrieving, analysing, error - and hands a
 * successful result up to the page via `onResult`. Client-side validation runs
 * first so an obviously wrong URL never costs a round-trip, but the backend
 * re-validates regardless.
 *
 * The work is deliberately split into two requests rather than one:
 *
 *   POST /analyze-repository   fetch the repository            seconds
 *   POST /analyze-project      analyse it with the local model minutes
 *
 * That is not a performance trick - the backend caches the retrieval, so the
 * second call re-fetches nothing from GitHub. It is what makes the progress
 * display honest: the browser can say "retrieving" and then "analysing" because
 * those are two requests it actually makes, rather than a timer pretending to
 * know what the server is doing.
 */
import { useEffect, useRef, useState } from 'react'

import { analyzeProject } from '../api/analysis.js'
import { analyzeRepository } from '../api/repository.js'
import { describeError } from '../utils/errorHints.js'
import { validateRepoUrl } from '../utils/validateRepoUrl.js'
import AnalysisProgress, { ANALYSING, RETRIEVING } from './AnalysisProgress.jsx'
import ErrorPanel from './ErrorPanel.jsx'

const EXAMPLE_URL = 'https://github.com/psf/requests'

export default function RepoUrlForm({ onResult, onReset }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState(null)
  const [phase, setPhase] = useState(null) // null | RETRIEVING | ANALYSING
  const [startedAt, setStartedAt] = useState(null)
  const [retrieved, setRetrieved] = useState(null)

  const isLoading = phase !== null

  // Lets us cancel an in-flight request if the user submits again or navigates.
  const abortRef = useRef(null)
  useEffect(() => () => abortRef.current?.abort(), [])

  async function run(submitted, controller) {
    setPhase(RETRIEVING)
    const retrieval = await analyzeRepository(submitted, {
      signal: controller.signal,
      includeContent: false,
    })
    if (controller.signal.aborted) return null

    setRetrieved({
      fullName: retrieval.repository?.full_name,
      fileCount: retrieval.files?.length ?? 0,
    })

    setPhase(ANALYSING)
    return analyzeProject(submitted, { signal: controller.signal })
  }

  async function handleSubmit(event) {
    event.preventDefault()

    const validation = validateRepoUrl(url)
    if (!validation.valid) {
      setError({ message: validation.error })
      onReset?.()
      return
    }

    // Supersede any request still in flight.
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setError(null)
    setRetrieved(null)
    setStartedAt(Date.now())
    onReset?.()

    const submitted = url.trim()
    try {
      const data = await run(submitted, controller)
      if (data) {
        // Pass the URL up so a following interview reuses it verbatim and hits
        // the backend's analysis cache.
        onResult(data, submitted)
      }
    } catch (requestError) {
      if (controller.signal.aborted) return // superseded, not a failure
      setError(describeError(requestError))
    } finally {
      if (!controller.signal.aborted) setPhase(null)
    }
  }

  function handleChange(event) {
    setUrl(event.target.value)
    if (error) setError(null) // clear as soon as the user edits
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="mt-8">
      <label htmlFor="repo-url" className="block text-sm font-medium text-slate-300">
        GitHub repository URL
      </label>
      <p id="repo-url-help" className="mt-1 text-xs text-slate-500">
        Any public repository. Nothing is uploaded anywhere — the analysis runs
        against your local Ollama model.
      </p>

      <div className="mt-2 flex flex-col gap-3 sm:flex-row">
        <input
          id="repo-url"
          name="repo-url"
          type="url"
          value={url}
          onChange={handleChange}
          disabled={isLoading}
          placeholder={EXAMPLE_URL}
          autoComplete="off"
          spellCheck="false"
          aria-invalid={Boolean(error)}
          aria-describedby={error ? 'repo-url-error repo-url-help' : 'repo-url-help'}
          className={`w-full flex-1 rounded-lg border bg-surface-raised px-4 py-3 text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/60 disabled:opacity-60 ${
            error ? 'border-rose-500' : 'border-white/10 focus:border-brand-400'
          }`}
        />

        <button
          type="submit"
          disabled={isLoading || !url.trim()}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400 disabled:cursor-not-allowed disabled:border disabled:border-white/10 disabled:bg-white/5 disabled:text-slate-500 sm:w-auto"
        >
          {isLoading && (
            <span
              aria-hidden="true"
              className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white"
            />
          )}
          {isLoading ? 'Analyzing…' : 'Analyze Project'}
        </button>
      </div>

      {!isLoading && !error && !url.trim() && (
        <p className="mt-3 text-xs text-slate-500">
          No repository to hand?{' '}
          <button
            type="button"
            onClick={() => setUrl(EXAMPLE_URL)}
            className="text-brand-300 underline underline-offset-2 hover:text-brand-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400"
          >
            Use {EXAMPLE_URL.replace('https://github.com/', '')}
          </button>{' '}
          as an example.
        </p>
      )}

      {isLoading && (
        <AnalysisProgress
          phase={phase}
          startedAt={startedAt}
          note={
            retrieved
              ? `${retrieved.fullName}: ${retrieved.fileCount} file(s) retrieved. A previously analysed repository returns from the cache immediately.`
              : undefined
          }
        />
      )}

      <ErrorPanel error={error} className="mt-3" />
      {error && <span id="repo-url-error" className="sr-only">{error.message}</span>}
    </form>
  )
}
