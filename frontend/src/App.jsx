import { useEffect, useRef, useState } from 'react'
import AnalysisProgress from './components/AnalysisProgress.jsx'
import AppShell from './components/AppShell.jsx'
import AskHome from './components/AskHome.jsx'
import ErrorMessage from './components/ErrorMessage.jsx'
import Icon from './components/Icon.jsx'
import InsightCard from './components/InsightCard.jsx'
import QuestionForm from './components/QuestionForm.jsx'
import ResultsTable from './components/ResultsTable.jsx'
import SqlViewer from './components/SqlViewer.jsx'
import Visualization from './components/Visualization.jsx'
import { SessionExpiredError, postQuery } from './services/api.js'

// Statuses that mean "try again shortly" are shown as a calm warning, not as a failure.
// status null = the backend could not be reached or the request timed out.
const TEMPORARY_STATUSES = new Set([null, 429, 503, 504])

/**
 * The DataPilot workspace, shown by AccessGate only to a signed-in demo user.
 * onLogout: the Log out button. onSessionExpired: a question was refused with 401.
 *
 * view is the signed-in page: ask | history | saved. The question, answer and error live here, not
 * in the Ask page, so moving between pages never clears an answer or asks again.
 */
export default function App({ onLogout = () => {}, onSessionExpired = () => {} }) {
  const [view, setView] = useState('ask')
  // Bumped by the top bar's "Ask another question…": once the Ask page is shown, focus the question.
  const [focusRequest, setFocusRequest] = useState(0)
  const questionRef = useRef(null)
  const [question, setQuestion] = useState('')
  const [validationError, setValidationError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  // Guards against a second submit before React has re-rendered the disabled button.
  const inFlight = useRef(false)

  // text: a quick question, asked without typing it; otherwise the question in the box.
  async function handleSubmit(text) {
    if (inFlight.current) return

    const trimmed = (typeof text === 'string' ? text : question).trim()
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
      // Only a 401 ends the session; rate limits, timeouts and query errors are shown as before.
      if (err instanceof SessionExpiredError) {
        onSessionExpired()
        return
      }
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

  // A quick question goes into the box and is asked through the same path as a typed one.
  function handleQuickQuestion(text) {
    setQuestion(text)
    handleSubmit(text)
  }

  function handleAskAnother() {
    setView('ask')
    setFocusRequest((count) => count + 1)
  }

  useEffect(() => {
    if (focusRequest) questionRef.current?.focus()
  }, [focusRequest])

  // The empty Ask page has the question box in view, so the top bar's shortcut is not shown there.
  const askHome = view === 'ask' && !loading && !result && !error

  return (
    <AppShell
      view={view}
      onNavigate={setView}
      onAskAnother={handleAskAnother}
      showAskAnother={!askHome}
      onLogout={onLogout}
    >
      {view === 'history' && <ViewPlaceholder title="History" text="History will appear here." />}
      {view === 'saved' && <ViewPlaceholder title="Saved Reports" text="Saved reports will appear here." />}
      {view === 'ask' && (
        <AskPage
          question={question}
          onQuestionChange={handleQuestionChange}
          onSubmit={handleSubmit}
          onQuickQuestion={handleQuickQuestion}
          questionRef={questionRef}
          loading={loading}
          validationError={validationError}
          result={result}
          error={error}
        />
      )}
    </AppShell>
  )
}

/**
 * The Ask page, in one of three states:
 *   no question yet    the V2 home: greeting, question box, quick questions
 *   waiting            the V2 analysis progress for the question that was sent
 *   answer or error    still the pre-V2 answer (redesigned in a later batch), with the question
 *                      box above it for the next question
 */
function AskPage({ question, onQuestionChange, onSubmit, onQuickQuestion, questionRef, loading, validationError, result, error }) {
  const formProps = { question, onQuestionChange, onSubmit, loading, validationError, inputRef: questionRef }
  return (
    <>
      {/* Always present, so the one change to "Analyzing…" is announced; the steps are not. */}
      <p role="status" className="visually-hidden">
        {loading ? 'Analyzing your question…' : ''}
      </p>
      {loading ? (
        <AnalysisProgress question={question.trim()} />
      ) : result || error ? (
        <AnswerPage formProps={formProps} result={result} error={error} />
      ) : (
        <AskHome onAsk={onQuickQuestion} {...formProps} />
      )}
    </>
  )
}

/** The answer or error, still the pre-V2 components, unchanged. */
function AnswerPage({ formProps, result, error }) {
  return (
    <div className="dp-legacy-page">
      <div className="container main">
        <QuestionForm {...formProps} />

        <section className="results" aria-label="Answer">
          {error && <ErrorMessage title={error.title} message={error.message} tone={error.tone} />}

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
      </div>

      <footer className="container">
        <p className="footer">
          <Icon name="shield" size={14} />
          Read-only by design: every generated query is validated and runs with a read-only database role.
        </p>
      </footer>
    </div>
  )
}

/** A temporary stand-in for a page that is built in a later batch: a heading and one line. */
function ViewPlaceholder({ title, text }) {
  return (
    <div className="dp-placeholder">
      <h1>{title}</h1>
      <p>{text}</p>
    </div>
  )
}
