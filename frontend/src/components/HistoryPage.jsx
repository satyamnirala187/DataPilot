import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { ApiError, SessionExpiredError, getAnalysis, getHistory } from '../services/api.js'
import { describeHistoryItem, filterHistory, groupHistory, historyKind, kindIcon, parseTimestamp } from '../utils/history.js'
import ErrorMessage from './ErrorMessage.jsx'
import ExportPdfAction from './ExportPdfAction.jsx'
import ExportPptxAction from './ExportPptxAction.jsx'
import Icon from './Icon.jsx'
import { PageBar, PageSkeleton } from './PageLoader.jsx'
import ResultView from './ResultView.jsx'
import SaveReportAction from './SaveReportAction.jsx'

const LIST_LIMIT = 100
// As on the Ask page: statuses that mean "try again shortly" are a calm warning, not a failure.
// status null = the backend could not be reached or the request timed out.
const TEMPORARY_STATUSES = new Set([null, 429, 503, 504])

/**
 * The History page: the account's recorded analyses (the newest 100), grouped by day, searchable,
 * and each one reopenable from its stored snapshot. It is shown only while History is open, so
 * every visit loads the list once; a new question may have added to it since.
 *
 * Reopening an analysis reads GET /history/{id}. It never asks the question again, and it never
 * touches the Ask page's own question and answer, which App keeps separately.
 *
 * listRequest changes when the History button is used again while History is open: an open
 * analysis then closes back to the list (without reloading it).
 *
 * isSaved(analysisId) and onSaveReport(analysisId, title) come from useSavedReports: a reopened
 * analysis can be saved, and its row shows "Saved" afterwards without reloading the list.
 */
export default function HistoryPage({ listRequest = 0, onSessionExpired, isSaved = () => false, onSaveReport }) {
  const [list, setList] = useState({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [search, setSearch] = useState('')
  const [detail, setDetail] = useState(null) // { request, id, status: loading | ready | error, analysis, error }
  const [returnFocus, setReturnFocus] = useState(null)
  const rowRefs = useRef(new Map())
  const backRef = useRef(null)

  // The latest session-expired callback, without making it a reason to reload the list.
  const sessionExpired = useRef(onSessionExpired)
  useEffect(() => {
    sessionExpired.current = onSessionExpired
  })

  useEffect(() => {
    let active = true // a list that arrives after leaving History (or after a retry) is dropped
    getHistory(LIST_LIMIT).then(
      (data) => active && setList({ status: 'ready', items: data.items, now: new Date() }),
      (error) => {
        if (!active) return
        if (error instanceof SessionExpiredError) sessionExpired.current?.()
        else setList({ status: 'error', error: errorState(error) })
      },
    )
    return () => {
      active = false
    }
  }, [attempt])

  const showingDetail = detail !== null && detail.request === listRequest

  useEffect(() => {
    if (showingDetail) backRef.current?.focus()
  }, [showingDetail, detail?.id])

  // Back from an analysis: the keyboard focus returns to the row that opened it.
  useEffect(() => {
    if (!showingDetail && returnFocus) rowRefs.current.get(returnFocus)?.focus()
  }, [showingDetail, returnFocus])

  function retryList() {
    setList({ status: 'loading' })
    setAttempt((count) => count + 1)
  }

  function loadAnalysis(id, request) {
    const current = (state) => state !== null && state.id === id && state.request === request
    getAnalysis(id).then(
      (analysis) => setDetail((state) => (current(state) ? { ...state, status: 'ready', analysis } : state)),
      (error) => {
        if (error instanceof SessionExpiredError) {
          sessionExpired.current?.()
          return
        }
        setDetail((state) => (current(state) ? { ...state, status: 'error', error: errorState(error, 'This analysis was not found.') } : state))
      },
    )
  }

  function openAnalysis(id) {
    setReturnFocus(null)
    setDetail({ request: listRequest, id, status: 'loading' })
    loadAnalysis(id, listRequest)
  }

  function retryAnalysis() {
    setDetail((state) => ({ ...state, status: 'loading', error: null }))
    loadAnalysis(detail.id, detail.request)
  }

  function backToList() {
    setReturnFocus(detail.id)
    setDetail(null)
  }

  if (showingDetail) {
    return (
      <AnalysisDetail
        detail={detail}
        backRef={backRef}
        onBack={backToList}
        onRetry={retryAnalysis}
        isSaved={isSaved}
        onSaveReport={onSaveReport}
      />
    )
  }
  if (list.status === 'loading') {
    return (
      <>
        <PageBar />
        <div className="dp-page dp-history">
          <PageSkeleton label="Loading history…" variant="list" />
        </div>
      </>
    )
  }

  return (
    <div className="dp-page dp-history">
      <HistoryHeader search={list.status === 'ready' && list.items.length > 0 ? search : null} onSearch={setSearch} />

      {list.status === 'error' ? (
        <ErrorMessage title={list.error.title} message={list.error.message} tone={list.error.tone}>
          <button type="button" className="dp-button-quiet" onClick={retryList}>
            Try again
          </button>
        </ErrorMessage>
      ) : list.items.length === 0 ? (
        <div className="dp-card dp-history-empty">
          <span className="dp-icon-chip">
            <Icon name="clock" size={20} strokeWidth={1.7} />
          </span>
          <div>
            <h2 className="dp-card-title">No analyses yet</h2>
            <p className="dp-history-empty-text">Questions you ask in DataPilot will appear here once they are answered.</p>
          </div>
        </div>
      ) : (
        <HistoryGroups items={list.items} now={list.now} search={search} rowRefs={rowRefs} isSaved={isSaved} onOpen={openAnalysis} />
      )}
    </div>
  )
}

function HistoryHeader({ search, onSearch }) {
  const id = useId()
  return (
    <div className="dp-history-head">
      <div>
        <h1 className="dp-page-title">History</h1>
        <p className="dp-page-intro">Completed analyses are recorded here automatically.</p>
      </div>
      {search !== null && (
        <div className="dp-history-search">
          <label htmlFor={id} className="visually-hidden">
            Search history
          </label>
          <Icon name="search" size={18} strokeWidth={1.8} />
          <input id={id} type="search" placeholder="Search history" value={search} onChange={(event) => onSearch(event.target.value)} />
        </div>
      )}
    </div>
  )
}

function HistoryGroups({ items, now, search, rowRefs, isSaved, onOpen }) {
  const groups = useMemo(() => groupHistory(filterHistory(items, search), now), [items, now, search])
  if (groups.length === 0) {
    return (
      <p className="dp-history-nomatch" role="status">
        No analyses match “{search.trim()}”.
      </p>
    )
  }
  return (
    <div className="dp-history-groups">
      {groups.map((group) => (
        <HistoryGroup key={group.label} group={group} rowRefs={rowRefs} isSaved={isSaved} onOpen={onOpen} />
      ))}
    </div>
  )
}

function HistoryGroup({ group, rowRefs, isSaved, onOpen }) {
  const id = useId()
  return (
    <section aria-labelledby={id}>
      <h2 id={id} className="dp-section-label">
        {group.label}
      </h2>
      <ul className="dp-card dp-history-list">
        {group.items.map(({ item, time }) => {
          const kind = historyKind(item.visualization_type)
          const timestamp = parseTimestamp(item.created_at)
          return (
            <li key={item.id}>
              <button
                type="button"
                className="dp-history-row"
                ref={(element) => {
                  if (element) rowRefs.current.set(item.id, element)
                  else rowRefs.current.delete(item.id)
                }}
                onClick={() => onOpen(item.id)}
              >
                <span className="dp-history-chip" data-kind={kind}>
                  <Icon name={kindIcon(item.visualization_type)} size={20} strokeWidth={1.7} />
                </span>
                <span className="dp-history-text">
                  <span className="dp-history-question">{item.question}</span>
                  <span className="dp-history-meta">{describeHistoryItem(item)}</span>
                </span>
                <span className="dp-history-side">
                  {/* From the list's saved_report_id, or a save made since the list was loaded. */}
                  {(item.saved_report_id || isSaved(item.id)) && <span className="dp-saved-pill">Saved</span>}
                  <time dateTime={timestamp ? timestamp.toISOString() : undefined}>{time}</time>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function BackButton({ backRef, onBack }) {
  return (
    <button type="button" ref={backRef} className="dp-back" onClick={onBack}>
      <Icon name="arrowLeft" size={18} strokeWidth={1.8} />
      Back to History
    </button>
  )
}

/**
 * A reopened analysis: the stored snapshot in the same ResultView as a fresh answer. The page and
 * its Back button stay the same element from loading to loaded, so keyboard focus stays on it.
 */
function AnalysisDetail({ detail, backRef, onBack, onRetry, isSaved, onSaveReport }) {
  const analysis = detail.analysis
  return (
    <>
      {detail.status === 'loading' && <PageBar />}
      <div className="dp-page dp-page-result dp-history-detail">
        <BackButton backRef={backRef} onBack={onBack} />
        {detail.status === 'loading' ? (
          <PageSkeleton label="Opening analysis…" variant="result" />
        ) : detail.status === 'ready' ? (
          <ResultView
            result={analysis}
            actions={
              <>
                {/* The PDF and PPTX are made from this stored snapshot only. */}
                <ExportPdfAction result={analysis} source="history" />
                <ExportPptxAction result={analysis} source="history" />
                {onSaveReport && (
                  <SaveReportAction
                    analysisId={analysis.id}
                    question={analysis.question}
                    saved={Boolean(analysis.saved_report) || isSaved(analysis.id)}
                    onSave={onSaveReport}
                  />
                )}
              </>
            }
          />
        ) : (
          <ErrorMessage title={detail.error.title} message={detail.error.message} tone={detail.error.tone}>
            {detail.error.retry && (
              <button type="button" className="dp-button-quiet" onClick={onRetry}>
                Try again
              </button>
            )}
          </ErrorMessage>
        )}
      </div>
    </>
  )
}

/** A failed History request, as safe text: the API client's title and message, never raw details. */
function errorState(error, notFoundMessage) {
  if (!(error instanceof ApiError)) {
    return { title: 'Unexpected error', message: 'Something went wrong. Please try again.', tone: 'error', retry: true }
  }
  if (error.status === 404) {
    return { title: 'Not found', message: notFoundMessage ?? error.message, tone: 'error', retry: false }
  }
  return { title: error.title, message: error.message, tone: TEMPORARY_STATUSES.has(error.status ?? null) ? 'warning' : 'error', retry: true }
}
