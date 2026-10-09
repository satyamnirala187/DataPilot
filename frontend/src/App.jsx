import { useRef, useState } from 'react'
import ErrorMessage from './components/ErrorMessage.jsx'
import InsightCard from './components/InsightCard.jsx'
import QuestionForm from './components/QuestionForm.jsx'
import ResultsTable from './components/ResultsTable.jsx'
import SqlViewer from './components/SqlViewer.jsx'
import Visualization from './components/Visualization.jsx'
import { postQuery } from './services/api.js'

export default function App() {
  const [question, setQuestion] = useState('')
  const [validationError, setValidationError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  // Guards against a second submit before React has re-rendered the disabled button.
  const inFlight = useRef(false)

  async function handleSubmit() {
    if (inFlight.current) return

    const trimmed = question.trim()
    if (!trimmed) {
      setValidationError('Please enter a business question.')
      return
    }

    // A new question clears both the previous error and the previous result,
    // so an old answer is never shown under a new question.
    inFlight.current = true
    setValidationError(null)
    setError(null)
    setResult(null)
    setLoading(true)
    try {
      setResult(await postQuery(trimmed))
    } catch (err) {
      setError({
        title: err.title || 'Unexpected error',
        message: err.title ? err.message : 'Something went wrong. Please try again.',
      })
    } finally {
      inFlight.current = false
      setLoading(false)
    }
  }

  function handleQuestionChange(value) {
    setQuestion(value)
    if (validationError) setValidationError(null)
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>DataPilot</h1>
        <p className="subtitle">Ask business questions in plain English</p>
      </header>

      <main className="content">
        <QuestionForm
          question={question}
          onQuestionChange={handleQuestionChange}
          onSubmit={handleSubmit}
          loading={loading}
          validationError={validationError}
        />

        <div role="status" aria-live="polite" className="loading">
          {loading && 'Analyzing your business question...'}
        </div>

        {error && <ErrorMessage title={error.title} message={error.message} />}

        {result && (
          <>
            {/* No rows means nothing to summarise, so no insight is expected. */}
            {result.rows.length > 0 && <InsightCard insight={result.insight} />}
            <Visualization visualization={result.visualization} columns={result.columns} rows={result.rows} />
            {/* The table is always shown; the chart only complements it. */}
            <ResultsTable
              columns={result.columns}
              rows={result.rows}
              rowCount={result.row_count ?? result.rows.length}
              truncated={result.truncated}
            />
            <SqlViewer sql={result.sql} />
          </>
        )}
      </main>
    </div>
  )
}
