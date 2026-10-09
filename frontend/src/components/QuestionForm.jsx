const EXAMPLE_QUESTIONS = [
  'What is our total revenue?',
  'What are the top 5 product categories by revenue?',
  'What is our average order value?',
  'Show monthly revenue for the last 6 months.',
]

const MAX_QUESTION_LENGTH = 500

/** The main workspace: the question box, the Analyze button and the example questions. */
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
    <form className="card workspace" onSubmit={handleSubmit} aria-busy={loading} aria-labelledby="workspace-heading">
      <h2 id="workspace-heading">Ask a business question</h2>
      <p className="workspace-intro">
        Ask in plain English. DataPilot writes a safe, read-only SQL query, runs it on the store data and explains
        the answer.
      </p>

      <div className="question-field">
        <label htmlFor="question" className="visually-hidden">
          Your question
        </label>
        <textarea
          id="question"
          name="question"
          rows={3}
          maxLength={MAX_QUESTION_LENGTH}
          placeholder="e.g. Which 5 cities generated the most revenue this year?"
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          onKeyDown={handleKeyDown}
          aria-invalid={validationError ? true : undefined}
          aria-describedby={validationError ? 'question-hint question-error' : 'question-hint'}
        />
        {validationError && (
          <p id="question-error" className="field-error" role="alert">
            {validationError}
          </p>
        )}
      </div>

      <div className="form-actions">
        <p id="question-hint" className="hint">
          Press <kbd>Enter</kbd> to analyze, <kbd>Shift</kbd> + <kbd>Enter</kbd> for a new line.
        </p>
        <button type="submit" className="primary-button" disabled={loading}>
          {loading && <span className="spinner" aria-hidden="true" />}
          {loading ? 'Analyzing…' : 'Analyze'}
        </button>
      </div>

      <div className="examples">
        <p className="eyebrow" id="examples-label">
          Try an example
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
