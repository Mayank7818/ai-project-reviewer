/**
 * Honest progress for an operation that takes minutes.
 *
 * There is no percentage here, and there never will be one: the backend runs a
 * retrieval and then a model pass whose duration is not knowable in advance -
 * it depends on the repository, the model and the machine - so any bar would be
 * an animation pretending to be a measurement.
 *
 * What this shows instead is true by construction:
 *
 *   - which of the two real requests is in flight, because the caller only
 *     changes `phase` when it actually starts the next one;
 *   - how long it has been running, counted from a real timestamp;
 *   - what the current phase involves, so a long wait is legible rather than
 *     mysterious.
 *
 * What the analysis pass involves is described but never broken into ticking
 * sub-steps: the browser cannot see inside a single model call.
 */
import { useEffect, useState } from 'react'

export const RETRIEVING = 'retrieving'
export const ANALYSING = 'analysing'

const STEPS = [
  {
    key: RETRIEVING,
    label: 'Retrieving repository',
    detail: 'Reading the file tree from GitHub and downloading the ranked selection.',
  },
  {
    key: ANALYSING,
    label: 'Analysing with the local model',
    detail:
      'The repository is classified, parsed and scanned first — that part takes ' +
      'well under a second. The wait is the model reading the evidence and ' +
      'writing its review, one token at a time on your CPU. Several minutes is ' +
      'normal without a GPU.',
  },
]

function useElapsedSeconds(startedAt) {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    if (!startedAt) return undefined
    setSeconds(Math.floor((Date.now() - startedAt) / 1000))
    const id = setInterval(
      () => setSeconds(Math.floor((Date.now() - startedAt) / 1000)),
      1000,
    )
    return () => clearInterval(id)
  }, [startedAt])

  return seconds
}

function formatElapsed(seconds) {
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`
}

/**
 * @param {object}  props
 * @param {string}  props.phase      RETRIEVING or ANALYSING.
 * @param {number}  props.startedAt  Date.now() when the operation began.
 * @param {string} [props.note]      An extra true fact, e.g. "12 files retrieved".
 */
export default function AnalysisProgress({ phase, startedAt, note }) {
  const elapsed = useElapsedSeconds(startedAt)
  const activeIndex = STEPS.findIndex((step) => step.key === phase)
  const current = STEPS[activeIndex]

  return (
    <div className="mt-4 rounded-lg border border-white/10 bg-surface-raised p-4">
      {/* Only the phase name is announced. A timer in a live region would read
          itself aloud every second. */}
      <p role="status" aria-live="polite" className="sr-only">
        {current ? `${current.label}. This can take several minutes.` : ''}
      </p>

      <ol className="space-y-3">
        {STEPS.map((step, index) => {
          const done = index < activeIndex
          const active = index === activeIndex

          return (
            <li key={step.key} className="flex gap-3">
              <span
                aria-hidden="true"
                className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${
                  done
                    ? 'border-emerald-400/40 bg-emerald-500/15 text-emerald-300'
                    : active
                      ? 'border-brand-400/50 bg-brand-500/15 text-brand-200'
                      : 'border-white/10 text-slate-600'
                }`}
              >
                {done ? '✓' : index + 1}
              </span>

              <div className="min-w-0">
                <p
                  className={`flex flex-wrap items-center gap-2 text-sm ${
                    active
                      ? 'font-medium text-white'
                      : done
                        ? 'text-slate-400'
                        : 'text-slate-600'
                  }`}
                >
                  {step.label}
                  {active && (
                    <>
                      <span
                        aria-hidden="true"
                        className="h-3 w-3 animate-spin rounded-full border-2 border-brand-300/30 border-t-brand-300"
                      />
                      <span className="font-normal tabular-nums text-slate-500">
                        {formatElapsed(elapsed)}
                      </span>
                    </>
                  )}
                  {done && <span className="text-xs text-emerald-400/80">done</span>}
                </p>

                {active && (
                  <p className="mt-1 text-xs leading-relaxed text-slate-500">
                    {step.detail}
                  </p>
                )}
              </div>
            </li>
          )
        })}
      </ol>

      {note && (
        <p className="mt-3 border-t border-white/10 pt-3 text-xs text-slate-400">{note}</p>
      )}

      {/* Stated once, plainly, instead of implied by a bar that keeps moving.
          There is no percentage here because the finish time is not knowable:
          it depends on the repository, the model and the machine. */}
      {phase === ANALYSING && (
        <p className="mt-2 text-xs text-slate-500">
          Nothing is stuck. You can leave this open — the result is cached
          afterwards, so looking at the same repository again is instant.
        </p>
      )}
    </div>
  )
}
