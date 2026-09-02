/**
 * Thin HTTP client shared by every API module.
 *
 * Centralising fetch here buys three things the rest of the app then gets for
 * free: a single base URL, a request timeout (fetch has none by default and
 * would otherwise hang forever), and one normalised error type so components
 * never have to guess what a failure looks like.
 */

// Vite inlines import.meta.env at build time. Falling back to '/api' keeps the
// dev proxy working with zero configuration.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'
const API_VERSION = '/v1'
const DEFAULT_TIMEOUT_MS = 10_000

/** Error type carrying the backend's machine-readable code and HTTP status. */
export class ApiError extends Error {
  constructor(message, { code = 'UNKNOWN_ERROR', status = 0, details = {} } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

/**
 * Perform a JSON request against the API.
 *
 * @param {string} path      Path below the version prefix, e.g. '/health'.
 * @param {object} [options] { method, body, signal, timeoutMs }
 * @returns {Promise<any>}   Parsed JSON body.
 * @throws {ApiError}        On timeout, network failure, or any non-2xx status.
 */
export async function request(path, options = {}) {
  const { method = 'GET', body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options

  // AbortController gives us a real timeout; without it a hung API would leave
  // the UI spinning indefinitely.
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)

  // Respect a caller-supplied signal (e.g. component unmount) as well as ours.
  if (signal) {
    signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  let response
  try {
    response = await fetch(`${BASE_URL}${API_VERSION}${path}`, {
      method,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
  } catch (error) {
    // fetch rejects on network failure and on abort - distinguish the two so
    // the user sees an accurate message.
    if (error.name === 'AbortError') {
      throw new ApiError('The request timed out. Is the backend running?', {
        code: 'TIMEOUT',
      })
    }
    throw new ApiError('Could not reach the API. Is the backend running?', {
      code: 'NETWORK_ERROR',
    })
  } finally {
    clearTimeout(timeoutId)
  }

  // A body is not guaranteed (204, or an HTML error page from a proxy).
  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    const apiError = payload?.error
    throw new ApiError(apiError?.message ?? `Request failed (${response.status}).`, {
      code: apiError?.code ?? 'HTTP_ERROR',
      status: response.status,
      details: apiError?.details ?? {},
    })
  }

  return payload
}
