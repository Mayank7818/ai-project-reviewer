/**
 * The one way this application reports a failure.
 *
 * Announced to assistive technology via `role="alert"`, and marked by an icon
 * and a label as well as colour, so the distinction between an error and a
 * notice does not depend on seeing red.
 */
export default function ErrorPanel({ error, onRetry, className = '' }) {
  if (!error) return null

  return (
    <div
      role="alert"
      className={`rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm ${className}`}
    >
      <p className="font-medium text-rose-200">
        <span aria-hidden="true" className="mr-2">
          ⚠
        </span>
        <span className="sr-only">Error: </span>
        {error.message}
      </p>

      {error.hint && <p className="mt-1.5 text-rose-300/80">{error.hint}</p>}

      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md border border-rose-400/40 px-3 py-1.5 text-xs font-medium text-rose-200 transition-colors hover:bg-rose-500/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-400"
        >
          Try again
        </button>
      )}
    </div>
  )
}
