const EXAMPLE_QUESTIONS = [
  'What is our total revenue?',
  'What are the top 5 product categories by revenue?',
  'What is our average order value?',
  'Show monthly revenue for the last 6 months.',
]

const MAX_QUESTION_LENGTH = 500

/** The question box, the Analyze button and the example questions. */
export default function QuestionForm({ question, onQuestionChange, onSubmit, loading, validationError }) {
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
    <form className="card question-form" onSubmit={handleSubmit} aria-busy={loading}>
      <label htmlFor="question" className="field-label">
        Your question
      </label>
      <textarea
        id="question"
        name="question"
        rows={3}
        maxLength={MAX_QUESTION_LENGTH}
        placeholder="e.g. What is our total revenue?"
        value={question}
        onChange={(event) => onQuestionChange(event.target.value)}
        onKeyDown={handleKeyDown}
        aria-invalid={validationError ? true : undefined}
        aria-describedby={validationError ? 'question-hint question-error' : 'question-hint'}
      />
      <p id="question-hint" className="hint">
        Press Enter to analyze, Shift+Enter for a new line.
      </p>
      {validationError && (
        <p id="question-error" className="field-error" role="alert">
          {validationError}
        </p>
      )}

      <div className="form-actions">
        <button type="submit" className="primary-button" disabled={loading}>
          {loading ? 'Analyzing…' : 'Analyze'}
        </button>
      </div>

      <div className="examples">
        <p className="examples-label" id="examples-label">
          Try an example:
        </p>
        <ul className="example-list" aria-labelledby="examples-label">
          {EXAMPLE_QUESTIONS.map((example) => (
            <li key={example}>
              <button type="button" className="example-button" onClick={() => onQuestionChange(example)}>
                {example}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </form>
  )
}
