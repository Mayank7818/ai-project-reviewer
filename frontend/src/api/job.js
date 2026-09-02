/**
 * Job intelligence endpoint bindings.
 *
 * The job description goes to this backend and nowhere else. The backend sends
 * it only to the locally configured Ollama model, which is what the privacy
 * note in the UI claims.
 */
import { request } from './client.js'

/** Matching may have to run a Step 4 analysis first if none is cached. */
const MATCH_TIMEOUT_MS = 30 * 60 * 1000

/** Generating job questions is one small model call on top of the match. */
const START_TIMEOUT_MS = 30 * 60 * 1000

/** Evaluating one answer is one small model call. */
const ANSWER_TIMEOUT_MS = 10 * 60 * 1000

/**
 * POST /api/v1/job/parse
 * @returns {Promise<{job: object, llm_available: boolean, privacy_note: string}>}
 */
export function parseJob(jobDescription, options = {}) {
  return request('/job/parse', {
    method: 'POST',
    body: { job_description: jobDescription },
    timeoutMs: ANSWER_TIMEOUT_MS,
    ...options,
  })
}

/**
 * POST /api/v1/job/match
 *
 * @param {{githubUrl: string, jobDescription: string, targetRole: string,
 *          company?: string, jobTitle?: string}} input
 * @returns {Promise<object>} Match score, per-skill statuses, gaps, plan.
 */
export function matchJob(input, options = {}) {
  return request('/job/match', {
    method: 'POST',
    body: {
      github_url: input.githubUrl,
      job_description: input.jobDescription,
      target_role: input.targetRole,
      company: input.company ?? '',
      job_title: input.jobTitle ?? '',
    },
    timeoutMs: MATCH_TIMEOUT_MS,
    ...options,
  })
}

/** POST /api/v1/job/interview/start */
export function startJobInterview(input, options = {}) {
  return request('/job/interview/start', {
    method: 'POST',
    body: {
      github_url: input.githubUrl,
      job_description: input.jobDescription,
      target_role: input.targetRole,
      company: input.company ?? '',
      job_title: input.jobTitle ?? '',
      difficulty: input.difficulty,
      question_count: input.questionCount,
    },
    timeoutMs: START_TIMEOUT_MS,
    ...options,
  })
}

/** POST /api/v1/job/interview/{session_id}/answer */
export function submitJobAnswer(sessionId, questionId, answer, options = {}) {
  return request(`/job/interview/${sessionId}/answer`, {
    method: 'POST',
    body: { question_id: questionId, answer },
    timeoutMs: ANSWER_TIMEOUT_MS,
    ...options,
  })
}

/** POST /api/v1/job/interview/{session_id}/finish */
export function finishJobInterview(sessionId, options = {}) {
  return request(`/job/interview/${sessionId}/finish`, {
    method: 'POST',
    timeoutMs: ANSWER_TIMEOUT_MS,
    ...options,
  })
}
