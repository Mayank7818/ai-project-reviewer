/**
 * Health endpoint bindings.
 *
 * One module per backend resource keeps URLs out of components: a component
 * calls `fetchHealth()` and never needs to know the path.
 */
import { request } from './client.js'

/**
 * GET /api/v1/health
 * @returns {Promise<{status: string, app_name: string, version: string,
 *                    environment: string, timestamp: string}>}
 */
export function fetchHealth(options = {}) {
  return request('/health', options)
}
