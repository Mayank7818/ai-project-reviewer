/**
 * AI analysis endpoint bindings.
 *
 * The browser never talks to Ollama. It calls this FastAPI endpoint, and the
 * backend is the only thing that knows where the model lives.
 */
import { request } from './client.js'

/**
 * Local CPU inference is slow, and the multi-stage pipeline makes three model
 * calls per analysis (each of which may retry once). This must stay comfortably
 * above 3 x OLLAMA_TIMEOUT_SECONDS.
 */
const ANALYZE_TIMEOUT_MS = 30 * 60 * 1000

/**
 * POST /api/v1/analyze-project
 *
 * @param {string} githubUrl  Public GitHub repository URL.
 * @param {object} [options]  { signal } to cancel an in-flight request.
 * @returns {Promise<{
 *   repository: object,
 *   analysis: {
 *     project_summary: string,
 *     technologies: string[],
 *     architecture: {summary: string, evidence: Evidence[]},
 *     code_quality:  {score: number, reason: string, findings: Finding[]},
 *     security: {
 *       score: number, confirmed_issues: Finding[],
 *       potential_risks: Finding[], no_evidence: string[], issues: string[]
 *     },
 *     performance:   {score: number, reason: string, findings: Finding[]},
 *     documentation: {score: number, reason: string, findings: Finding[]},
 *     testing: {score: number, reason: string, evidence: Evidence[]},
 *     strengths: string[], weaknesses: string[], overall_score: number
 *   },
 *   meta: object
 * }>}
 *
 * Evidence is `{file, line_start, line_end, reason}` where the line numbers are
 * null unless the backend could verify them against the file it sent.
 * Finding is `{finding, severity, evidence}` with severity low|medium|high.
 * @throws {ApiError} `.code` is one of INVALID_REPOSITORY_URL,
 *   REPOSITORY_NOT_FOUND, GITHUB_RATE_LIMIT, LLM_UNAVAILABLE,
 *   LLM_MODEL_NOT_FOUND, LLM_INVALID_RESPONSE, EXTERNAL_SERVICE_ERROR,
 *   TIMEOUT, NETWORK_ERROR.
 */
export function analyzeProject(githubUrl, options = {}) {
  const { refresh = false, ...rest } = options
  return request('/analyze-project', {
    method: 'POST',
    // `refresh` forces a re-run past the backend's analysis cache. Off by
    // default: a repeat request for a repository already analysed returns in
    // milliseconds instead of minutes, and `meta.cached` says which happened.
    body: { github_url: githubUrl, refresh },
    timeoutMs: ANALYZE_TIMEOUT_MS,
    ...rest,
  })
}
