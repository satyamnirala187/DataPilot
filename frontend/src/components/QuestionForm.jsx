import { useEffect, useState } from 'react'
import { PLACEHOLDERS } from '../config/askContent.js'
import Icon from './Icon.jsx'

const MAX_QUESTION_LENGTH = 500
const PLACEHOLDER_INTERVAL_MS = 3600

/**
 * The question box: a textarea and the Ask button. Enter asks; Shift+Enter adds a new line.
 * inputRef lets the top bar's "Ask another question…" move the cursor here.
 */
export default function QuestionForm({ question, onQuestionChange, onSubmit, loading, validationError, inputRef }) {
  const [focused, setFocused] = useState(false)
  const placeholder = useCyclingPlaceholder(!focused && !question && !loading)

  function handleSubmit(event) {
    event.preventDefault()
    onSubmit()
  }

  // Enter submits; Shift+Enter adds a new line.
  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form.requestSubmit()
    }
  }

  return (
    <form className="dp-query" onSubmit={handleSubmit} aria-busy={loading}>
      <div className="dp-query-box" data-invalid={validationError ? true : undefined}>
        <label htmlFor="question" className="visually-hidden">
          Your question
        </label>
        <textarea
          id="question"
          ref={inputRef}
          name="question"
          rows={3}
          maxLength={MAX_QUESTION_LENGTH}
          placeholder={placeholder}
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          aria-invalid={validationError ? true : undefined}
          aria-describedby={validationError ? 'question-hint question-error' : 'question-hint'}
        />
        <p id="question-hint" className="visually-hidden">
          Press Enter to ask, or Shift and Enter for a new line.
        </p>
        <div className="dp-query-actions">
          <button type="submit" className="dp-button-primary" disabled={loading}>
            Ask
          </button>
        </div>
      </div>
      {validationError && (
        <p id="question-error" className="dp-query-error" role="alert">
          <Icon name="alertCircle" size={16} />
          {validationError}
        </p>
      )}
    </form>
  )
}

/**
 * The placeholder shown in an empty box: it moves to the next example every 3.6 seconds while
 * active (empty, not focused, nothing loading). With reduced motion it stays on the first one.
 */
function useCyclingPlaceholder(active) {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    if (!active || prefersReducedMotion()) return undefined
    const timer = setInterval(() => setIndex((current) => (current + 1) % PLACEHOLDERS.length), PLACEHOLDER_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [active])

  return PLACEHOLDERS[index]
}

function prefersReducedMotion() {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
