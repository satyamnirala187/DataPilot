import { useEffect, useId, useRef, useState } from 'react'
import { ApiError } from '../services/api.js'
import { SAVED_REPORTS_UNAVAILABLE, isSavedReportsUnavailable } from '../utils/savedReports.js'

/**
 * "Remove from Saved Reports" on a reopened report, with a simple inline confirmation: nothing is
 * removed until "Remove report" is pressed. onRemove() is the page's remover; it resolves
 * { status: 'removed' } (the page then leaves this report, so nothing more happens here) or
 * { status: 'expired' }, and throws anything else for this action to show. The analysis itself
 * stays in History, which the confirmation says.
 */
export default function RemoveReportAction({ title, onRemove }) {
  const [step, setStep] = useState('idle') // idle | confirm | removing
  const [error, setError] = useState(null)
  const id = useId()
  const triggerRef = useRef(null)
  const cancelRef = useRef(null)
  const returnToTrigger = useRef(false)

  // Asking to confirm focuses the safe choice; cancelling returns focus to the trigger.
  useEffect(() => {
    if (step === 'confirm' && !error) cancelRef.current?.focus()
    if (step === 'idle' && returnToTrigger.current) {
      returnToTrigger.current = false
      triggerRef.current?.focus()
    }
  }, [step, error])

  function cancel() {
    returnToTrigger.current = true
    setError(null)
    setStep('idle')
  }

  async function confirm() {
    setStep('removing')
    setError(null)
    try {
      const outcome = await onRemove()
      if (outcome?.status !== 'removed') setStep('idle')
    } catch (err) {
      setError(removeError(err))
      setStep('confirm')
    }
  }

  const removing = step === 'removing'
  return (
    <div className="dp-remove-action">
      {step === 'idle' ? (
        <button ref={triggerRef} type="button" className="dp-button-quiet" onClick={() => setStep('confirm')}>
          Remove from Saved Reports
        </button>
      ) : (
        <div role="group" aria-labelledby={`${id}-question`} className="dp-remove-confirm">
          <p id={`${id}-question`} className="dp-remove-question">
            Remove “{title}” from Saved Reports? The analysis stays in History.
          </p>
          <button type="button" className="dp-button-quiet dp-remove-confirm-button" disabled={removing} onClick={confirm}>
            {removing ? 'Removing…' : 'Remove report'}
          </button>
          <button ref={cancelRef} type="button" className="dp-button-quiet" disabled={removing} onClick={cancel}>
            Cancel
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="dp-remove-error">
          {error.title}: {error.message}
        </p>
      )}
    </div>
  )
}

/** A failed removal, as safe text: never raw response details. */
function removeError(error) {
  if (!(error instanceof ApiError)) return { title: 'Unexpected error', message: 'Something went wrong. Please try again.' }
  if (isSavedReportsUnavailable(error)) return SAVED_REPORTS_UNAVAILABLE
  return { title: error.title, message: error.message }
}
