/**
 * One place that turns a backend error code into "what do I do about it".
 *
 * The backend's own message always explains *what* went wrong and is shown
 * verbatim; these add the next step, which the backend has no business
 * knowing (it cannot tell whether the reader is running `ollama serve` in
 * another terminal). Three components used to keep their own partial copies of
 * this map, which meant the same failure read differently depending on where
 * the user happened to be standing.
 */

/** Applies wherever the code can occur. */
const SHARED = {
  NETWORK_ERROR:
    'The backend is not answering. Start it with `uvicorn app.main:app --reload` in the backend folder.',
  TIMEOUT:
    'Local inference on CPU is slow — several minutes is normal for a first analysis. Try again, or configure a smaller model.',
  HTTP_ERROR: 'The backend responded with an unexpected error. Check its console output.',

  INVALID_REPOSITORY_URL:
    'Use the full repository URL, for example https://github.com/psf/requests.',
  REPOSITORY_NOT_FOUND:
    'Check the spelling, and make sure the repository is public — private repositories are not accessible without a token that grants them.',
  GITHUB_RATE_LIMIT:
    'A repository you have already analysed still opens instantly from the cache. For a new one, add a GITHUB_TOKEN to backend/.env — one analysis costs about twenty of the sixty requests an hour allowed without one.',
  GITHUB_AUTH_ERROR:
    'GitHub rejected the configured token. Check GITHUB_TOKEN in backend/.env, or remove it to fall back to unauthenticated access.',
  EXTERNAL_SERVICE_ERROR:
    'GitHub could not be reached. Check your network connection and try again.',

  LLM_UNAVAILABLE:
    'Start the local model server with `ollama serve`, then try again. Nothing is sent to a cloud service.',
  LLM_MODEL_NOT_FOUND:
    'Run `ollama list` to see what is installed, then set OLLAMA_MODEL in backend/.env to one of them (or `ollama pull` the configured one).',
  LLM_INVALID_RESPONSE:
    'The local model returned output that failed validation. Retrying often works; a larger model is markedly more reliable.',

  INSUFFICIENT_EVIDENCE:
    'The analysed selection did not contain enough code to ground questions in. A repository with more source files works better.',
  SESSION_NOT_FOUND:
    'Sessions are held in memory, so restarting the backend clears them. Start a new interview.',
  INVALID_JOB_DESCRIPTION:
    'Paste the full posting, including its requirements section — a job title alone is not enough to match against.',
}

/**
 * Look up the guidance line for an error code.
 *
 * @param {string} [code]  Backend error code, e.g. 'GITHUB_RATE_LIMIT'.
 * @returns {string|undefined} A next step, or undefined when the backend's own
 *   message already says everything useful.
 */
export function hintFor(code) {
  return SHARED[code]
}

/**
 * Normalise a thrown error into what the error panels render.
 *
 * @param {Error} error  Usually an ApiError from `api/client.js`.
 * @returns {{message: string, hint: string|undefined, code: string|undefined}}
 */
export function describeError(error) {
  return {
    message: error?.message || 'Something went wrong.',
    hint: hintFor(error?.code),
    code: error?.code,
  }
}
