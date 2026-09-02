/**
 * Page footer. States the one thing a first-time reader most needs to know
 * about what they are looking at: this tool credits nothing it cannot show.
 */
export default function Footer() {
  return (
    <footer className="border-t border-white/10 px-6 py-6">
      <p className="mx-auto max-w-5xl text-center text-xs text-slate-500">
        Runs entirely on your machine against a local Ollama model. Skills,
        scores and findings are credited only where your repository evidences
        them.
      </p>
    </footer>
  )
}
