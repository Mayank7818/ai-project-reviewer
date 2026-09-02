/**
 * Top navigation bar: product identity, backend health, and local model status.
 *
 * Both pills are live checks against the backend - the model one tells the user
 * whether an analysis can succeed before they start one.
 */
import BackendStatus from './BackendStatus.jsx'
import OllamaStatus from './OllamaStatus.jsx'

export default function Header() {
  return (
    <header className="border-b border-white/10 bg-surface/80 backdrop-blur">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-6 py-4">
        <div className="flex items-center gap-3">
          <span
            className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white"
            aria-hidden="true"
          >
            AI
          </span>
          <span className="text-sm font-semibold tracking-tight text-white">
            AI Project Reviewer
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <BackendStatus />
          <OllamaStatus />
        </div>
      </div>
    </header>
  )
}
