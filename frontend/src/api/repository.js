/**
 * Repository endpoint bindings.
 *
 * Mirrors `api/health.js`: one module per backend resource, so components never
 * hold URLs or know about request shapes.
 */
import { request } from './client.js'

/**
 * Retrieval is several sequential GitHub round-trips, so it needs far longer
 * than the client's 10s default. Large repositories genuinely take this long.
 */
const ANALYZE_TIMEOUT_MS = 60_000

/**
 * POST /api/v1/analyze-repository
 *
 * @param {string} githubUrl  Public GitHub repository URL.
 * @param {object} [options]  { signal } to cancel an in-flight request.
 * @returns {Promise<{
 *   repository: object,
 *   readme: string|null,
 *   structure: {total_entries: number, returned_entries: number,
 *               truncated: boolean, paths: string[]},
 *   files: Array<{path: string, size_bytes: number, category: string,
 *                 content: string, truncated: boolean, redacted: boolean}>,
 *   retrieval: object,
 *   analysis: null
 * }>}
 * @throws {ApiError} With `.code` set to INVALID_REPOSITORY_URL,
 *   REPOSITORY_NOT_FOUND, GITHUB_RATE_LIMIT, GITHUB_AUTH_ERROR,
 *   EXTERNAL_SERVICE_ERROR, TIMEOUT or NETWORK_ERROR.
 */
export function analyzeRepository(githubUrl, options = {}) {
  const { includeContent = true, ...rest } = options
  return request('/analyze-repository', {
    method: 'POST',
    // The progress panel only needs to know what was retrieved, so it asks for
    // a summary. Shipping every file's text to the browser to render a count
    // would move the whole repository for no reason.
    body: { github_url: githubUrl, include_content: includeContent },
    timeoutMs: ANALYZE_TIMEOUT_MS,
    ...rest,
  })
}
