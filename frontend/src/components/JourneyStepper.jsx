/**
 * The path through the product, shown as a path.
 *
 *   Repository → Analysis → Job match → Interview → Readiness
 *
 * A first-time user arriving at a URL box has no way to know that pasting one
 * leads to an interview about their own code. This says so before they start,
 * and then tracks where they are, so the next action is always visible.
 *
 * Purely presentational: it reflects state the page already holds and never
 * changes it. State is carried by text and a check mark as well as colour.
 */
const STEPS = [
  { key: 'repository', label: 'Repository', hint: 'Paste a public GitHub URL' },
  { key: 'analysis', label: 'Analysis', hint: 'Evidence-based project review' },
  { key: 'job', label: 'Job match', hint: 'Compare against a real posting' },
  { key: 'interview', label: 'Interview', hint: 'Questions from your own code' },
  { key: 'readiness', label: 'Readiness', hint: 'Where you stand, and why' },
]

const DONE = 'done'
const CURRENT = 'current'
const TODO = 'todo'

export default function JourneyStepper({ reached = 'repository' }) {
  const activeIndex = Math.max(
    0,
    STEPS.findIndex((step) => step.key === reached),
  )

  return (
    <nav aria-label="Where you are" className="mt-8">
      <ol className="flex flex-wrap items-stretch gap-2 sm:gap-3">
        {STEPS.map((step, index) => {
          const state =
            index < activeIndex ? DONE : index === activeIndex ? CURRENT : TODO

          return (
            <li
              key={step.key}
              className={`min-w-[8.5rem] flex-1 rounded-lg border px-3 py-2 ${
                state === CURRENT
                  ? 'border-brand-400/50 bg-brand-500/10'
                  : state === DONE
                    ? 'border-emerald-400/25 bg-emerald-500/5'
                    : 'border-white/10 bg-white/[0.02]'
              }`}
            >
              <p className="flex items-center gap-1.5 text-xs font-medium">
                <span
                  aria-hidden="true"
                  className={
                    state === DONE
                      ? 'text-emerald-400'
                      : state === CURRENT
                        ? 'text-brand-300'
                        : 'text-slate-600'
                  }
                >
                  {state === DONE ? '✓' : index + 1}
                </span>
                <span
                  className={
                    state === TODO
                      ? 'text-slate-500'
                      : state === DONE
                        ? 'text-slate-300'
                        : 'text-white'
                  }
                >
                  {step.label}
                </span>
                {/* Not colour alone: the state is readable as words too. */}
                <span className="sr-only">
                  {state === DONE
                    ? ' (completed)'
                    : state === CURRENT
                      ? ' (current step)'
                      : ' (not started)'}
                </span>
              </p>
              <p className="mt-0.5 text-[11px] leading-snug text-slate-500">
                {step.hint}
              </p>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
