/**
 * Local model status bindings.
 *
 * Lets the UI show whether Ollama is ready *before* the user starts a run that
 * would fail minutes later.
 */
import { request } from './client.js'

/**
 * GET /api/v1/llm/status
 *
 * @returns {Promise<{ready: boolean, reachable: boolean,
 *   model_available: boolean, model: string, available_models: string[],
 *   detail: string|null}>}
 */
export function fetchLlmStatus(options = {}) {
  return request('/llm/status', options)
}
