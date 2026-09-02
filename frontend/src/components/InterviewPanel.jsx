/**
 * Owns the interview session state machine.
 *
 *   setup -> question -> feedback -> question -> … -> summary
 *
 * Kept separate from HomePage so the analysis flow and the interview flow do
 * not share state, and so this component can later be mounted on its own route
 * without changes.
 */
import { useEffect, useRef, useState } from 'react'

import {
  finishInterview,
  startInterview,
  submitAnswer,
} from '../api/interview.js'
import { describeError } from '../utils/errorHints.js'
import AnswerFeedback from './AnswerFeedback.jsx'
import ErrorPanel from './ErrorPanel.jsx'
import InterviewQuestion from './InterviewQuestion.jsx'
import InterviewSetup from './InterviewSetup.jsx'
import InterviewSummary from './InterviewSummary.jsx'

const SETUP = 'setup'
const QUESTION = 'question'
const FEEDBACK = 'feedback'
const SUMMARY = 'summary'

/**
 * Step 6 injects the job endpoints here. Defaulting to the Step 5 ones means
 * the plain interview keeps working with no call-site change, and there is one
 * implementation of the question/answer/feedback loop rather than two.
 */
const DEFAULT_API = {
  start: (setup, options) => startInterview(setup, options),
  answer: submitAnswer,
  finish: finishInterview,
}

export default function InterviewPanel({
  githubUrl,
  repository,
  api = DEFAULT_API,
  heading = 'Interview in progress',
  intro,
}) {
  const [phase, setPhase] = useState(SETUP)
  const [session, setSession] = useState(null)
  const [question, setQuestion] = useState(null)
  const [evaluation, setEvaluation] = useState(null)
  const [answeredCount, setAnsweredCount] = useState(0)
  const [isLastAnswer, setIsLastAnswer] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const abortRef = useRef(null)
  useEffect(() => () => abortRef.current?.abort(), [])

  const describe = describeError

  function freshController() {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    return controller
  }

  async function handleStart(setup) {
    const controller = freshController()
    setBusy(true)
    setError(null)

    try {
      const created = await api.start(
        { githubUrl, ...setup },
        { signal: controller.signal },
      )
      setSession(created)
      setQuestion(created.current_question)
      setAnsweredCount(0)
      setPhase(QUESTION)
    } catch (requestError) {
      if (controller.signal.aborted) return
      setError(describe(requestError))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  async function handleAnswer(answer) {
    const controller = freshController()
    setBusy(true)
    setError(null)

    try {
      const result = await api.answer(
        session.session_id,
        question.id,
        answer,
        { signal: controller.signal },
      )
      setEvaluation(result.evaluation)
      setAnsweredCount(result.answered)
      setIsLastAnswer(result.is_complete)
      // Hold the next question until the candidate has read their feedback.
      setQuestion(result.next_question ?? question)
      setPhase(FEEDBACK)
    } catch (requestError) {
      if (controller.signal.aborted) return
      setError(describe(requestError))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  async function handleContinue() {
    if (!isLastAnswer) {
      setEvaluation(null)
      setPhase(QUESTION)
      return
    }

    const controller = freshController()
    setBusy(true)
    setError(null)

    try {
      const finished = await api.finish(session.session_id, {
        signal: controller.signal,
      })
      setSession(finished)
      setPhase(SUMMARY)
    } catch (requestError) {
      if (controller.signal.aborted) return
      setError(describe(requestError))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  function handleRestart() {
    setPhase(SETUP)
    setSession(null)
    setQuestion(null)
    setEvaluation(null)
    setAnsweredCount(0)
    setIsLastAnswer(false)
    setError(null)
  }

  if (phase === SETUP) {
    return (
      <InterviewSetup
        repository={repository}
        onStart={handleStart}
        isStarting={busy}
        error={error}
        intro={intro}
      />
    )
  }

  if (phase === SUMMARY && session?.summary) {
    return <InterviewSummary session={session} onRestart={handleRestart} />
  }

  return (
    <div className="mt-10 border-t border-white/10 pt-8">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-white">{heading}</h2>
        <p className="text-xs text-slate-500">
          {session.repository} · {session.target_role_label} · {session.difficulty}
        </p>
      </header>

      {session.role_notice && (
        <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          {session.role_notice}
        </p>
      )}

      {phase === QUESTION && question && (
        <InterviewQuestion
          question={question}
          index={answeredCount}
          total={session.total_questions}
          onSubmit={handleAnswer}
          isSubmitting={busy}
        />
      )}

      {phase === FEEDBACK && evaluation && (
        <AnswerFeedback
          evaluation={evaluation}
          onContinue={handleContinue}
          isComplete={isLastAnswer}
          isFinishing={busy}
        />
      )}

      <ErrorPanel error={error} className="mt-4" />
    </div>
  )
}
