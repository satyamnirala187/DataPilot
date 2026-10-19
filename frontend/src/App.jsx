import { useEffect, useRef, useState } from 'react'
import AnalysisProgress from './components/AnalysisProgress.jsx'
import AppShell from './components/AppShell.jsx'
import AskHome from './components/AskHome.jsx'
import ErrorMessage from './components/ErrorMessage.jsx'
import ExportPdfAction from './components/ExportPdfAction.jsx'
import ExportPptxAction from './components/ExportPptxAction.jsx'
import HistoryPage from './components/HistoryPage.jsx'
import QuestionForm from './components/QuestionForm.jsx'
import QuestionHeader from './components/QuestionHeader.jsx'
import ResultView from './components/ResultView.jsx'
import SaveReportAction from './components/SaveReportAction.jsx'
import SavedReportsPage from './components/SavedReportsPage.jsx'
import { useSavedReports } from './hooks/useSavedReports.js'
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
  // Bumped by the History button: if an analysis is open in History, it goes back to the list.
  const [historyRequest, setHistoryRequest] = useState(0)
  // The same for Saved Reports and an open saved report.
  const [savedRequest, setSavedRequest] = useState(0)
  // Saved-report state for the sidebar count, "Saved" labels and saving (read once on opening).
  const savedReports = useSavedReports(onSessionExpired)
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
        // The backend's error code word (e.g. unsafe_sql), and the question the error belongs to.
        code: err.code ?? null,
        question: trimmed,
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

  function handleNavigate(next) {
    if (next === 'history') setHistoryRequest((count) => count + 1)
    if (next === 'saved') setSavedRequest((count) => count + 1)
    setView(next)
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
      onNavigate={handleNavigate}
      onAskAnother={handleAskAnother}
      showAskAnother={!askHome}
      onLogout={onLogout}
      savedBadge={savedReports.badge}
    >
      {/* Shown only while open, so each visit loads the list again. Its reopened analyses never
          replace the Ask page's question and answer below. */}
      {view === 'history' && (
        <HistoryPage
          listRequest={historyRequest}
          onSessionExpired={onSessionExpired}
          isSaved={savedReports.isSaved}
          onSaveReport={savedReports.save}
        />
      )}
      {view === 'saved' && (
        <SavedReportsPage
          listRequest={savedRequest}
          onSessionExpired={onSessionExpired}
          onListLoaded={savedReports.replaceList}
          onRemoveReport={savedReports.remove}
        />
      )}
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
          isSaved={savedReports.isSaved}
          onSaveReport={savedReports.save}
        />
      )}
    </AppShell>
  )
}

/**
 * The Ask page, in one of three states:
 *   no question yet    the V2 home: greeting, question box, quick questions
 *   waiting            the V2 analysis progress for the question that was sent
 *   answer or error    the V2 answer (ResultView) or error card, with the question box above
 *                      it for the next question
 */
function AskPage({
  question,
  onQuestionChange,
  onSubmit,
  onQuickQuestion,
  questionRef,
  loading,
  validationError,
  result,
  error,
  isSaved,
  onSaveReport,
}) {
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
        <AnswerPage
          formProps={formProps}
          result={result}
          error={error}
          onTryQuestion={onQuickQuestion}
          isSaved={isSaved}
          onSaveReport={onSaveReport}
        />
      ) : (
        <AskHome onAsk={onQuickQuestion} {...formProps} />
      )}
    </>
  )
}

/**
 * The answer or the error for the last question, below a compact question box for the next one.
 * A stopped question's "Try instead" questions are asked like quick questions.
 */
function AnswerPage({ formProps, result, error, onTryQuestion, isSaved, onSaveReport }) {
  return (
    <div className="dp-page dp-page-result">
      <QuestionForm {...formProps} compact />
      {result ? (
        <ResultView
          result={result}
          actions={
            <>
              <ExportPdfAction result={result} source="query" />
              <ExportPptxAction result={result} source="query" />
              {/* Only an answer that was recorded (it has an analysis_id) can be saved. */}
              {result.analysis_id ? (
                <SaveReportAction
                  analysisId={result.analysis_id}
                  question={result.question}
                  saved={isSaved(result.analysis_id)}
                  onSave={onSaveReport}
                />
              ) : null}
            </>
          }
        />
      ) : (
        <div className="dp-result">
          <QuestionHeader question={error.question} />
          <ErrorMessage
            title={error.title}
            message={error.message}
            tone={error.tone}
            code={error.code}
            onTryQuestion={onTryQuestion}
          />
        </div>
      )}
    </div>
  )
}
