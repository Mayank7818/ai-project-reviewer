/**
 * Header pill showing whether the local model is ready.
 *
 * Distinguishes "Ollama is not running" from "Ollama is running but the model
 * is not installed", because the fix differs and telling the user up front
 * saves them a run that would fail minutes later.
 */
import { useEffect, useState } from 'react'

import { fetchLlmStatus } from '../api/llm.js'

const DOT_STYLES = {
  checking: 'bg-amber-400 animate-pulse',
  ready: 'bg-emerald-400',
  degraded: 'bg-amber-400',
  offline: 'bg-rose-500',
}

export default function OllamaStatus() {
  const [state, setState] = useState({
    status: 'checking',
    label: 'Checking model…',
    title: '',
  })

  useEffect(() => {
    const controller = new AbortController()

    fetchLlmStatus({ signal: controller.signal })
      .then((data) => {
        if (data.ready) {
          setState({
            status: 'ready',
            label: `Ollama · ${data.model}`,
            title: 'The local model is installed and ready.',
          })
        } else if (data.reachable) {
          setState({
            status: 'degraded',
            label: `Model missing · ${data.model}`,
            title: data.detail ?? '',
          })
        } else {
          setState({
            status: 'offline',
            label: 'Ollama offline',
            title: data.detail ?? '',
          })
        }
      })
      .catch((error) => {
        if (controller.signal.aborted) return
        setState({ status: 'offline', label: 'Ollama status unknown', title: error.message })
      })

    return () => controller.abort()
  }, [])

  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-slate-300"
      role="status"
      aria-live="polite"
      title={state.title}
    >
      <span className={`h-2 w-2 rounded-full ${DOT_STYLES[state.status]}`} aria-hidden="true" />
      {state.label}
    </span>
  )
}
