import { useRef, useState } from 'react'
import ErrorMessage from './components/ErrorMessage.jsx'
import Icon from './components/Icon.jsx'
import InsightCard from './components/InsightCard.jsx'
import QuestionForm from './components/QuestionForm.jsx'
import ResultsTable from './components/ResultsTable.jsx'
import SqlViewer from './components/SqlViewer.jsx'
import Visualization from './components/Visualization.jsx'
import { postQuery } from './services/api.js'

// Statuses that mean "try again shortly" are shown as a calm warning, not as a failure.
// status null = the backend could not be reached or the request timed out.
const TEMPORARY_STATUSES = new Set([null, 429, 503, 504])

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
        tone: err.title && TEMPORARY_STATUSES.has(err.status ?? null) ? 'warning' : 'error',
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
    <div className="app">
      <header className="topbar">
        <div className="container topbar-inner">
          <div className="brand">
            <span className="brand-mark">
              <Icon name="logo" size={20} />
            </span>
            <div>
              <h1>DataPilot</h1>
              <p className="brand-tagline">AI Business Data Analyst</p>
            </div>
          </div>
          <p className="pipeline-pill">Natural language → SQL → business insight</p>
        </div>
      </header>

      <main className="container main">
        <QuestionForm
          question={question}
          onQuestionChange={handleQuestionChange}
          onSubmit={handleSubmit}
          loading={loading}
          validationError={validationError}
        />

        <section className="results" aria-label="Answer">
          <div role="status" aria-live="polite" className="status">
            {loading && (
              <>
                <span className="status-dot" aria-hidden="true" />
                Analyzing your business question...
              </>
            )}
          </div>

          {loading && <ResultSkeleton />}

          {error && <ErrorMessage title={error.title} message={error.message} tone={error.tone} />}

          {!loading && !error && !result && <EmptyState />}

          {result && (
            <>
              <div className="results-heading">
                <h2>Answer</h2>
                <p className="results-question">{result.question}</p>
              </div>
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
        </section>
      </main>

      <footer className="container">
        <p className="footer">
          <Icon name="shield" size={14} />
          Read-only by design: every generated query is validated and runs with a read-only database role.
        </p>
      </footer>
    </div>
  )
}

/** Grey placeholder shapes while the answer loads. Decorative: the status line announces progress. */
function ResultSkeleton() {
  return (
    <div className="skeleton" aria-hidden="true">
      <div className="skeleton-block" style={{ height: 76 }} />
      <div className="skeleton-block" style={{ height: 220 }} />
      <div className="skeleton-block" style={{ height: 120 }} />
    </div>
  )
}

/** Shown before the first question. */
function EmptyState() {
  return (
    <div className="empty-state">
      <p className="empty-state-title">Your answer will appear here</p>
      <p className="empty-state-text">Ask a question above, or start with one of the examples.</p>
      <ul className="empty-steps">
        <li>
          <Icon name="sparkle" size={16} /> AI insight
        </li>
        <li>
          <Icon name="chart" size={16} /> Chart or KPI
        </li>
        <li>
          <Icon name="table" size={16} /> Result table
        </li>
        <li>
          <Icon name="code" size={16} /> The SQL that ran
        </li>
      </ul>
    </div>
  )
}
