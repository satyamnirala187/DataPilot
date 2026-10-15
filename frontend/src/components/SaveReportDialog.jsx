import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ApiError } from '../services/api.js'
import { MAX_TITLE_LENGTH, isTitleTooLong, titleProblem } from '../utils/reportTitle.js'
import { SAVED_REPORTS_UNAVAILABLE, isSavedReportsUnavailable } from '../utils/savedReports.js'

/**
 * The Save Report dialog: the title starts as the analysis question and can be edited before
 * saving. onSave(title) performs the one save request; it resolves when done (the caller then
 * closes the dialog) and throws when it failed, in which case the typed title stays for a retry.
 * Escape and Cancel close it without saving.
 */
export default function SaveReportDialog({ initialTitle, onSave, onCancel }) {
  const id = useId()
  const [title, setTitle] = useState(initialTitle)
  const [submitted, setSubmitted] = useState(false)
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState(null)
  const inFlight = useRef(false) // a second Save before React re-renders must not send again
  const dialogRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  const problem = titleProblem(title)
  // An over-long title is pointed out at once (a long question is prefilled); an empty one on Save.
  const showProblem = problem !== null && (submitted || isTitleTooLong(title))

  async function handleSubmit(event) {
    event.preventDefault()
    setSubmitted(true)
    if (problem || inFlight.current) return
    inFlight.current = true
    setSaving(true)
    setFailure(null)
    try {
      await onSave(title.trim())
    } catch (error) {
      setFailure(saveFailure(error))
      inputRef.current?.focus()
    } finally {
      inFlight.current = false
      setSaving(false)
    }
  }

  function handleKeyDown(event) {
    if (event.key === 'Escape') {
      event.preventDefault()
      if (!saving) onCancel()
      return
    }
    // Keep Tab inside the dialog while it is open.
    if (event.key === 'Tab') {
      const focusable = [...dialogRef.current.querySelectorAll('input, button:not([disabled])')]
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
  }

  const describedBy = [`${id}-note`, showProblem && `${id}-problem`].filter(Boolean).join(' ')

  return createPortal(
    // The backdrop only covers the page; it is not a control.
    <div className="dp-dialog-backdrop">
      <div
        ref={dialogRef}
        className="dp-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        onKeyDown={handleKeyDown}
      >
        <h2 id={`${id}-title`} className="dp-dialog-title">
          Save report
        </h2>
        <form onSubmit={handleSubmit} noValidate>
          <label htmlFor={`${id}-input`} className="dp-dialog-label">
            Report title
          </label>
          <input
            ref={inputRef}
            id={`${id}-input`}
            className="dp-dialog-input"
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            aria-invalid={showProblem ? true : undefined}
            aria-describedby={describedBy}
            autoComplete="off"
          />
          <p id={`${id}-note`} className="dp-dialog-note">
            Up to {MAX_TITLE_LENGTH} characters. The saved report keeps this analysis exactly as it is.
          </p>
          {showProblem && (
            <p id={`${id}-problem`} className="dp-dialog-problem">
              {problem}
            </p>
          )}
          {failure && (
            <div className="dp-dialog-failure" role="alert">
              <p className="dp-dialog-failure-title">{failure.title}</p>
              <p>{failure.message}</p>
            </div>
          )}
          <div className="dp-dialog-actions">
            <button type="button" className="dp-button-quiet" onClick={onCancel} disabled={saving}>
              Cancel
            </button>
            <button type="submit" className="dp-button-primary" disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}

/** A failed save, as safe text: the API client's wording, never the raw response. */
function saveFailure(error) {
  if (!(error instanceof ApiError)) {
    return { title: 'Could not save', message: 'Something went wrong. Please try again.' }
  }
  if (isSavedReportsUnavailable(error)) return SAVED_REPORTS_UNAVAILABLE
  return { title: error.status === null ? 'Could not save' : error.title, message: error.message }
}
