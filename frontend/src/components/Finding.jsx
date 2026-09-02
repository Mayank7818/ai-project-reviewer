/**
 * A single finding: what was observed, how serious it is, and the files that
 * support it.
 *
 * Evidence is the point of this component. A finding without a citation should
 * never reach the browser — the backend drops those — so anything rendered here
 * can be traced back to a real file, and to a real line range whenever the
 * backend was able to verify one.
 */

const SEVERITY_STYLES = {
  high: {
    chip: 'bg-rose-500/15 text-rose-200 border-rose-400/40',
    bar: 'bg-rose-400',
    label: 'High',
  },
  medium: {
    chip: 'bg-amber-500/15 text-amber-200 border-amber-400/40',
    bar: 'bg-amber-400',
    label: 'Medium',
  },
  low: {
    chip: 'bg-slate-500/15 text-slate-300 border-slate-400/30',
    bar: 'bg-slate-400',
    label: 'Low',
  },
}

/** Render a citation as `path:12-18`, `path:12`, or just `path`. */
export function formatLocation({ file, line_start: start, line_end: end }) {
  if (!start) return file
  if (!end || end === start) return `${file}:${start}`
  return `${file}:${start}-${end}`
}

export function SeverityChip({ severity }) {
  const style = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.low

  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${style.chip}`}
    >
      {style.label}
    </span>
  )
}

/** A bare list of citations, used where a finding wrapper would be overkill. */
export function EvidenceList({ evidence, emptyText = 'No evidence cited.' }) {
  if (!evidence?.length) {
    return <p className="text-xs text-slate-500">{emptyText}</p>
  }

  return (
    <ul className="space-y-1">
      {evidence.map((item, position) => (
        <li key={`${item.file}-${item.line_start ?? 'x'}-${position}`} className="text-xs">
          <span className="font-mono text-slate-300">{formatLocation(item)}</span>
          {item.reason && <span className="text-slate-500"> — {item.reason}</span>}
        </li>
      ))}
    </ul>
  )
}

export default function Finding({ finding }) {
  const style = SEVERITY_STYLES[finding.severity] ?? SEVERITY_STYLES.low

  return (
    <li className="flex gap-3">
      {/* A coloured rail gives severity at a glance without reading the chip. */}
      <span className={`mt-1 w-0.5 shrink-0 rounded-full ${style.bar}`} aria-hidden="true" />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-start gap-2">
          <SeverityChip severity={finding.severity} />
          <p className="min-w-0 flex-1 text-sm text-slate-300">{finding.finding}</p>
        </div>

        {finding.evidence?.length > 0 && (
          <div className="mt-1.5 border-l border-white/10 pl-3">
            <EvidenceList evidence={finding.evidence} />
          </div>
        )}
      </div>
    </li>
  )
}

/** A titled list of findings, with an honest empty state. */
export function FindingList({ findings, emptyText }) {
  if (!findings?.length) {
    return <p className="text-sm text-slate-500">{emptyText}</p>
  }

  return (
    <ul className="space-y-3">
      {findings.map((finding, position) => (
        <Finding key={`${finding.finding}-${position}`} finding={finding} />
      ))}
    </ul>
  )
}
