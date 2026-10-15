import { useEffect, useRef, useState } from 'react'
import Icon from './Icon.jsx'
import SaveReportDialog from './SaveReportDialog.jsx'

/**
 * The action under a result: "Save Report" (which opens the title dialog), or a quiet "Saved" label
 * once the analysis has a saved report. Nothing is shown without an analysis id (an answer that was
 * not recorded cannot be saved). onSave(analysisId, title) is useSavedReports().save.
 */
export default function SaveReportAction({ analysisId, question, saved, onSave }) {
  const [open, setOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const triggerRef = useRef(null)
  const savedRef = useRef(null)
  const focusSaved = useRef(false)

  // After saving from the dialog, focus moves to the "Saved" label that replaced the button.
  useEffect(() => {
    if (saved && focusSaved.current) {
      focusSaved.current = false
      savedRef.current?.focus()
    }
  }, [saved])

  if (!analysisId) return null

  function close() {
    setOpen(false)
    triggerRef.current?.focus()
  }

  async function handleSave(title) {
    const outcome = await onSave(analysisId, title)
    if (outcome.status === 'expired') return
    setNotice(outcome.status === 'already' ? 'This analysis is already saved.' : 'Saved to Saved Reports.')
    focusSaved.current = true
    setOpen(false)
  }

  return (
    <div className="dp-save-action">
      {/* Always present, so the outcome of a save is announced. */}
      <p role="status" className="dp-save-notice">
        {notice}
      </p>
      {saved ? (
        <p ref={savedRef} tabIndex={-1} className="dp-saved-state">
          <Icon name="bookmark" size={16} strokeWidth={1.8} />
          Saved
        </p>
      ) : (
        <button ref={triggerRef} type="button" className="dp-button-quiet dp-save-button" onClick={() => setOpen(true)}>
          <Icon name="bookmark" size={16} strokeWidth={1.8} />
          Save Report
        </button>
      )}
      {open && !saved && <SaveReportDialog initialTitle={question ?? ''} onSave={handleSave} onCancel={close} />}
    </div>
  )
}
