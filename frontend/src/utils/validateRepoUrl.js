/**
 * Client-side validation for GitHub repository URLs.
 *
 * This is a fast-feedback convenience only. The backend will re-validate every
 * URL it receives - client-side checks are never a security boundary.
 */

// owner/repo: alphanumerics, dot, dash, underscore. Optional .git and trailing
// slash. Accepts an optional scheme and an optional "www.".
const GITHUB_REPO_PATTERN =
  /^(?:https?:\/\/)?(?:www\.)?github\.com\/([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/i

/**
 * @param {string} rawUrl
 * @returns {{valid: boolean, owner?: string, repo?: string, error?: string}}
 */
export function validateRepoUrl(rawUrl) {
  const url = (rawUrl ?? '').trim()

  if (!url) {
    return { valid: false, error: 'Please enter a GitHub repository URL.' }
  }

  const match = url.match(GITHUB_REPO_PATTERN)
  if (!match) {
    return {
      valid: false,
      error: 'Enter a URL in the form https://github.com/owner/repository.',
    }
  }

  const [, owner, repo] = match

  // GitHub reserves "." and ".." as path segments.
  if (['.', '..'].includes(owner) || ['.', '..'].includes(repo)) {
    return { valid: false, error: 'That does not look like a real repository.' }
  }

  return { valid: true, owner, repo }
}
