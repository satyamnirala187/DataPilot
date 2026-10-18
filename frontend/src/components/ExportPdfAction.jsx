import { useRef, useState } from 'react'
import Icon from './Icon.jsx'

const EXPORT_FAILED = 'The PDF could not be created. Please try again.'

/**
 * "Export PDF" under a result: downloads a PDF of the analysis on screen, made in the browser.
 * source is query (a fresh answer), history or saved; title and savedAt are a saved report's.
 * The PDF code is loaded only on the first press. Nothing is sent to the backend or the AI: a
 * History or Saved Report export uses exactly the stored analysis passed in as result.
 * load is for tests: it resolves the module that makes the PDF.
 */
export default function ExportPdfAction({ result, source = 'query', title = null, savedAt = null, load = loadExporter }) {
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
      const { exportAnalysisPdf } = await load()
      const filename = await exportAnalysisPdf(result, { source, title, savedAt })
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
        {busy ? 'Preparing PDF…' : 'Export PDF'}
      </button>
    </div>
  )
}

function loadExporter() {
  return import('../pdf/exportPdf.js')
}
