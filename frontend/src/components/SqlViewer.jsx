import { useEffect, useId, useState } from 'react'
import Icon from './Icon.jsx'

/**
 * The SQL card: closed by default, opened with its header button. Shows the validated SQL exactly
 * as the backend ran it, as plain text; it is never run or changed here.
 */
export default function SqlViewer({ sql }) {
  const id = useId()
  const [open, setOpen] = useState(false)
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
    <section className="dp-card dp-sql" aria-labelledby={`${id}-title`}>
      <h2 className="dp-sql-heading">
        <button
          type="button"
          className="dp-sql-toggle"
          aria-expanded={open}
          aria-controls={`${id}-panel`}
          onClick={() => setOpen((current) => !current)}
        >
          <span id={`${id}-title`} className="dp-sql-title">
            SQL query
          </span>
          <span className="dp-sql-note">Read-only, validated before running</span>
          <Icon name="chevronDown" size={18} className="dp-sql-chevron" />
        </button>
      </h2>
      <div id={`${id}-panel`} className="dp-sql-panel" hidden={!open}>
        <button type="button" className="dp-copy" onClick={copy}>
          <Icon name={copyState === 'copied' ? 'check' : 'copy'} size={14} />
          {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy SQL'}
        </button>
        <pre className="dp-sql-code">
          <code>{sql}</code>
        </pre>
      </div>
    </section>
  )
}
