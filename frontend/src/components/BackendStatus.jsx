/**
 * Live indicator that the React app can actually reach FastAPI.
 *
 * This is the visible proof that step 5 ("connect the frontend to the backend")
 * works: it calls the real health endpoint on mount and reports what came back.
 */
import { useEffect, useState } from 'react'

import { fetchHealth } from '../api/health.js'

const DOT_STYLES = {
  checking: 'bg-amber-400 animate-pulse',
  online: 'bg-emerald-400',
  offline: 'bg-rose-500',
}

export default function BackendStatus() {
  const [state, setState] = useState({ status: 'checking', label: 'Checking API…' })

  useEffect(() => {
    // Abort the in-flight request if the component unmounts, so we never call
    // setState on an unmounted component.
    const controller = new AbortController()

    fetchHealth({ signal: controller.signal })
      .then((data) => {
        setState({
          status: 'online',
          label: `API online · v${data.version} · ${data.environment}`,
        })
      })
      .catch((error) => {
        if (controller.signal.aborted) return
        setState({ status: 'offline', label: `API offline · ${error.message}` })
      })

    return () => controller.abort()
  }, [])

  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-slate-300"
      role="status"
      aria-live="polite"
    >
      <span className={`h-2 w-2 rounded-full ${DOT_STYLES[state.status]}`} aria-hidden="true" />
      {state.label}
    </span>
  )
}
