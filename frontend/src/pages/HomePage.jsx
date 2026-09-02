/**
 * Homepage: analyse a repository, then either practise a general interview or
 * measure the project against a specific job posting.
 *
 * Both follow-on modes only appear once an analysis exists, because both are
 * built from that analysis's evidence — offering them earlier would imply
 * questions could be generated without a repository to ground them in. The
 * stepper says so up front, so the shape of the product is visible from the
 * empty state rather than discovered by clicking.
 */
import { useState } from 'react'

import AnalysisResult from '../components/AnalysisResult.jsx'
import InterviewPanel from '../components/InterviewPanel.jsx'
import JobPanel from '../components/JobPanel.jsx'
import JourneyStepper from '../components/JourneyStepper.jsx'
import RepoUrlForm from '../components/RepoUrlForm.jsx'

const PRACTICE = 'practice'
const JOB = 'job'

export default function HomePage() {
  const [result, setResult] = useState(null)
  const [analysedUrl, setAnalysedUrl] = useState('')
  const [mode, setMode] = useState(PRACTICE)

  function handleResult(data, submittedUrl) {
    setResult(data)
    setAnalysedUrl(submittedUrl)
    setMode(PRACTICE)
  }

  const repository = result?.repository?.full_name
  const githubUrl = analysedUrl || result?.repository?.html_url
  const reached = !result ? 'repository' : mode === JOB ? 'job' : 'analysis'

  return (
    <section className="mx-auto max-w-4xl px-4 py-14 sm:px-6 sm:py-20">
      <h1 className="text-3xl font-bold tracking-tight text-white sm:text-5xl">
        AI Project Reviewer
      </h1>
      <p className="mt-4 max-w-2xl text-base text-slate-400 sm:text-lg">
        Paste one of your GitHub repositories. It is analysed against its real
        code, matched against a job posting, and then you are interviewed about
        it — with every claim checked against what the repository actually
        contains.
      </p>

      <JourneyStepper reached={reached} />

      <RepoUrlForm onResult={handleResult} onReset={() => setResult(null)} />

      {result && (
        <>
          <AnalysisResult data={result} />

          <nav
            className="mt-10 border-t border-white/10 pt-8"
            aria-label="What to do next"
          >
            <h2 className="text-sm font-semibold text-white">What next?</h2>
            <p className="mt-1 text-sm text-slate-400">
              Both use the analysis above, so neither costs another run.
            </p>

            <div className="mt-4 flex flex-wrap gap-2">
              {[
                [PRACTICE, 'Practice interview', 'Questions from your own code'],
                [JOB, 'Analyze a job', 'Match this project to a posting'],
              ].map(([key, label, hint]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setMode(key)}
                  aria-pressed={mode === key}
                  className={`flex-1 rounded-lg px-4 py-3 text-left text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400 sm:flex-none ${
                    mode === key
                      ? 'bg-brand-600 text-white'
                      : 'border border-white/10 bg-white/5 text-slate-300 hover:bg-white/10'
                  }`}
                >
                  {label}
                  <span
                    className={`mt-0.5 block text-xs font-normal ${
                      mode === key ? 'text-white/70' : 'text-slate-500'
                    }`}
                  >
                    {hint}
                  </span>
                </button>
              ))}
            </div>
          </nav>

          {/* Keyed by repository so switching projects resets both flows. */}
          {mode === PRACTICE ? (
            <InterviewPanel
              key={`practice-${repository}`}
              githubUrl={githubUrl}
              repository={repository}
            />
          ) : (
            <JobPanel
              key={`job-${repository}`}
              githubUrl={githubUrl}
              repository={repository}
            />
          )}
        </>
      )}
    </section>
  )
}
