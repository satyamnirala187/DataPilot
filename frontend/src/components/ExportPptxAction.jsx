import { useRef, useState } from 'react'
import Icon from './Icon.jsx'

const EXPORT_FAILED = 'The PowerPoint file could not be created. Please try again.'

/**
 * "Export PPTX" under a result, beside Export PDF: downloads a PowerPoint deck of the analysis on
 * screen, made in the browser. source is query (a fresh answer), history or saved; title and
 * savedAt are a saved report's. The PPTX code is loaded only on the first press. Nothing is sent
 * to the backend or the AI: a History or Saved Report export uses exactly the stored analysis
 * passed in as result. load is for tests: it resolves the module that makes the deck.
 */
export default function ExportPptxAction({ result, source = 'query', title = null, savedAt = null, load = loadExporter }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null) // { tone: 'done' | 'error', text }
  const inFlight = useRef(false) // a second press before React re-renders the disabled button

  if (!result) return null

  async function handleExport() {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setMessage(null)
    try {
      const { exportAnalysisPptx } = await load()
      const filename = await exportAnalysisPptx(result, { source, title, savedAt })
      setMessage({ tone: 'done', text: `Downloaded ${filename}` })
    } catch {
      // Never the error itself: it could name internals. The analysis on screen is unchanged.
      setMessage({ tone: 'error', text: EXPORT_FAILED })
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  return (
    <div className="dp-export-action">
      {message?.tone === 'done' && (
        <p role="status" className="dp-save-notice">
          {message.text}
        </p>
      )}
      {message?.tone === 'error' && (
        <p role="alert" className="dp-export-error">
          {message.text}
        </p>
      )}
      <button type="button" className="dp-button-quiet dp-save-button" disabled={busy} onClick={handleExport}>
        <Icon name="download" size={16} strokeWidth={1.8} />
        {busy ? 'Preparing PPTX…' : 'Export PPTX'}
      </button>
    </div>
  )
}

function loadExporter() {
  return import('../pptx/exportPptx.js')
}
