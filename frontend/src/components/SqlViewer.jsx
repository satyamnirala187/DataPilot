import { useEffect, useState } from 'react'
import Icon from './Icon.jsx'

/** Shows the validated SQL exactly as the backend ran it. It is display-only. */
export default function SqlViewer({ sql }) {
  const [copyState, setCopyState] = useState('idle') // idle | copied | failed

  useEffect(() => {
    if (copyState === 'idle') return undefined
    const timer = setTimeout(() => setCopyState('idle'), 2000)
    return () => clearTimeout(timer)
  }, [copyState])

  async function copy() {
    try {
      await navigator.clipboard.writeText(sql)
      setCopyState('copied')
    } catch {
      setCopyState('failed') // e.g. no clipboard permission; the SQL can still be selected by hand
    }
  }

  return (
    <section className="card sql-card" aria-labelledby="sql-heading">
      <div className="card-header">
        <div>
          <h2 id="sql-heading" className="card-title">
            <Icon name="code" size={16} />
            Generated SQL
          </h2>
          <p className="card-caption">The validated, read-only query that produced this answer.</p>
        </div>
        <button type="button" className="secondary-button" onClick={copy}>
          <Icon name={copyState === 'copied' ? 'check' : 'copy'} size={14} />
          {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy SQL'}
        </button>
      </div>
      <pre className="sql-block">
        <code>{sql}</code>
      </pre>
    </section>
  )
}
