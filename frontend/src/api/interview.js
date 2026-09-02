/**
 * Interview endpoint bindings.
 *
 * The browser never talks to Ollama. Every call goes to this FastAPI backend,
 * which owns the model, the repository evidence and the session state.
 */
import { request } from './client.js'

/**
 * Generating questions may have to run a Step 4 analysis first if none is
 * cached, so it inherits that timeout. Once an analysis is cached, starting an
 * interview is a single small model call.
 */
const START_TIMEOUT_MS = 30 * 60 * 1000

/** Evaluating one answer is one small model call. */
const ANSWER_TIMEOUT_MS = 10 * 60 * 1000

/**
 * GET /api/v1/interview/options
 * @returns {Promise<{roles: {key: string, label: string}[],
 *   difficulties: string[], default_question_count: number,
 *   min_questions: number, max_questions: number}>}
 */
export function fetchInterviewOptions(options = {}) {
  return request('/interview/options', options)
}

/**
 * POST /api/v1/interview/start
 *
 * @param {{githubUrl: string, targetRole: string, difficulty: string,
 *          questionCount: number}} setup
 * @returns {Promise<object>} The new session, including its first question.
 * @throws {ApiError} INSUFFICIENT_EVIDENCE when the repository cannot ground
 *   any question, plus the usual GitHub and Ollama error codes.
 */
export function startInterview(setup, options = {}) {
  return request('/interview/start', {
    method: 'POST',
    body: {
      github_url: setup.githubUrl,
      target_role: setup.targetRole,
      difficulty: setup.difficulty,
      question_count: setup.questionCount,
    },
    timeoutMs: START_TIMEOUT_MS,
    ...options,
  })
}

/**
 * POST /api/v1/interview/{session_id}/answer
 *
 * @returns {Promise<{evaluation: object, answered: number, total: number,
 *   next_question: object|null, is_complete: boolean}>}
 */
export function submitAnswer(sessionId, questionId, answer, options = {}) {
  return request(`/interview/${sessionId}/answer`, {
    method: 'POST',
    body: { question_id: questionId, answer },
    timeoutMs: ANSWER_TIMEOUT_MS,
    ...options,
  })
}

/** POST /api/v1/interview/{session_id}/finish */
export function finishInterview(sessionId, options = {}) {
  return request(`/interview/${sessionId}/finish`, {
    method: 'POST',
    timeoutMs: ANSWER_TIMEOUT_MS,
    ...options,
  })
}

/** GET /api/v1/interview/{session_id} */
export function fetchSession(sessionId, options = {}) {
  return request(`/interview/${sessionId}`, options)
}
