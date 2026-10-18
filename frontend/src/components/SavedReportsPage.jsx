import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { ApiError, SessionExpiredError, getSavedReport, getSavedReports } from '../services/api.js'
import { SAVED_LIST_LIMIT } from '../hooks/useSavedReports.js'
import { describeHistoryItem, historyKind, kindIcon, parseTimestamp } from '../utils/history.js'
import { SAVED_REPORTS_UNAVAILABLE, filterSavedReports, formatSavedDate, isSavedReportsUnavailable } from '../utils/savedReports.js'
import ErrorMessage from './ErrorMessage.jsx'
import RemoveReportAction from './RemoveReportAction.jsx'
import Icon from './Icon.jsx'
import { PageBar, PageSkeleton } from './PageLoader.jsx'
import ResultView from './ResultView.jsx'

// As on the Ask page: statuses that mean "try again shortly" are a calm warning, not a failure.
const TEMPORARY_STATUSES = new Set([null, 429, 503, 504])

/**
 * The Saved Reports page: the account's saved reports (the most recent 50), searchable, each
 * reopenable with its stored analysis. Shown only while open, so every visit loads the list once;
 * the loaded list also updates the sidebar count (onListLoaded).
 *
 * Reopening reads GET /saved-reports/{id}: the question is never asked again, and the Ask page's
 * own question and answer are not touched. An open report can be removed (onRemoveReport, after an
 * inline confirmation): the page then returns to the list without it. There is no rename or export.
 *
 * listRequest changes when Saved Reports is chosen again while open: an open report then closes
 * back to the list (without reloading it).
 */
export default function SavedReportsPage({ listRequest = 0, onSessionExpired, onListLoaded, onRemoveReport }) {
  const [list, setList] = useState({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [search, setSearch] = useState('')
  const [detail, setDetail] = useState(null) // { request, id, status: loading | ready | error, report, error }
  const [returnFocus, setReturnFocus] = useState(null)
  const [removedNotice, setRemovedNotice] = useState('')
  const rowRefs = useRef(new Map())
  const backRef = useRef(null)
  const noticeRef = useRef(null)

  // The latest callbacks, without making them a reason to reload the list.
  const callbacks = useRef({ onSessionExpired, onListLoaded })
  useEffect(() => {
    callbacks.current = { onSessionExpired, onListLoaded }
  })

  useEffect(() => {
    let active = true // a list that arrives after leaving the page (or after a retry) is dropped
    getSavedReports(SAVED_LIST_LIMIT).then(
      (data) => {
        if (!active) return
        setList({ status: 'ready', items: data.items })
        callbacks.current.onListLoaded?.(data.items)
      },
      (error) => {
        if (!active) return
        if (error instanceof SessionExpiredError) callbacks.current.onSessionExpired?.()
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

  // Back from a report: the keyboard focus returns to the row that opened it.
  useEffect(() => {
    if (!showingDetail && returnFocus) rowRefs.current.get(returnFocus)?.focus()
  }, [showingDetail, returnFocus])

  // After a removal the report's row is gone, so focus goes to the notice that says so.
  useEffect(() => {
    if (!showingDetail && removedNotice) noticeRef.current?.focus()
  }, [showingDetail, removedNotice])

  function retryList() {
    setList({ status: 'loading' })
    setAttempt((count) => count + 1)
  }

  function loadReport(id, request) {
    const current = (state) => state !== null && state.id === id && state.request === request
    getSavedReport(id).then(
      (report) => setDetail((state) => (current(state) ? { ...state, status: 'ready', report } : state)),
      (error) => {
        if (error instanceof SessionExpiredError) {
          callbacks.current.onSessionExpired?.()
          return
        }
        setDetail((state) => (current(state) ? { ...state, status: 'error', error: errorState(error, 'This saved report was not found.') } : state))
      },
    )
  }

  function openReport(id) {
    setReturnFocus(null)
    setDetail({ request: listRequest, id, status: 'loading' })
    loadReport(id, listRequest)
  }

  function retryReport() {
    setDetail((state) => ({ ...state, status: 'loading', error: null }))
    loadReport(detail.id, detail.request)
  }

  function backToList() {
    setRemovedNotice('')
    setReturnFocus(detail.id)
    setDetail(null)
  }

  // Remove the open report; on success it leaves this page's list at once and the list shows again.
  async function removeReport() {
    const { id, report } = detail
    const outcome = await onRemoveReport(id, report.analysis.id)
    if (outcome.status === 'removed') {
      setList((state) => (state.status === 'ready' ? { ...state, items: state.items.filter((item) => item.id !== id) } : state))
      setReturnFocus(null)
      setRemovedNotice(`Removed “${report.title}” from Saved Reports.`)
      setDetail(null)
    }
    return outcome
  }

  if (showingDetail) {
    return (
      <ReportDetail
        detail={detail}
        backRef={backRef}
        onBack={backToList}
        onRetry={retryReport}
        onRemove={onRemoveReport ? removeReport : null}
      />
    )
  }
  if (list.status === 'loading') {
    return (
      <>
        <PageBar />
        <div className="dp-page dp-saved">
          <PageSkeleton label="Loading saved reports…" variant="list" />
        </div>
      </>
    )
  }

  const hasReports = list.status === 'ready' && list.items.length > 0
  return (
    <div className="dp-page dp-saved">
      <SavedHeader search={hasReports ? search : null} onSearch={setSearch} />
      {removedNotice && (
        <p ref={noticeRef} tabIndex={-1} role="status" className="dp-removed-notice">
          {removedNotice}
        </p>
      )}

      {list.status === 'error' ? (
        <ErrorMessage title={list.error.title} message={list.error.message} tone={list.error.tone}>
          <button type="button" className="dp-button-quiet" onClick={retryList}>
            Try again
          </button>
        </ErrorMessage>
      ) : !hasReports ? (
        <div className="dp-card dp-history-empty">
          <span className="dp-icon-chip">
            <Icon name="bookmark" size={20} strokeWidth={1.7} />
          </span>
          <div>
            <h2 className="dp-card-title">No saved reports yet</h2>
            <p className="dp-history-empty-text">Use Save Report on a completed analysis to keep it here.</p>
          </div>
        </div>
      ) : (
        <ReportList reports={list.items} search={search} rowRefs={rowRefs} onOpen={openReport} />
      )}
    </div>
  )
}

function SavedHeader({ search, onSearch }) {
  const id = useId()
  return (
    <div className="dp-history-head">
      <div>
        <h1 className="dp-page-title">Saved Reports</h1>
        <p className="dp-page-intro">Analyses saved in this workspace, each kept with its original result.</p>
      </div>
      {search !== null && (
        <div className="dp-history-search">
          <label htmlFor={id} className="visually-hidden">
            Search saved reports
          </label>
          <Icon name="search" size={18} strokeWidth={1.8} />
          <input id={id} type="search" placeholder="Search saved reports" value={search} onChange={(event) => onSearch(event.target.value)} />
        </div>
      )}
    </div>
  )
}

function ReportList({ reports, search, rowRefs, onOpen }) {
  const id = useId()
  const shown = useMemo(() => filterSavedReports(reports, search), [reports, search])
  return (
    <section className="dp-card dp-saved-card" aria-labelledby={id}>
      <div className="dp-saved-card-head">
        <h2 id={id} className="dp-card-title">
          Reports
        </h2>
        {/* The API has no total: a full page is "the most recent 50", never presented as all. */}
        {reports.length >= SAVED_LIST_LIMIT && <p className="dp-saved-limit">Showing the {SAVED_LIST_LIMIT} most recently saved</p>}
      </div>
      {shown.length === 0 ? (
        <p className="dp-history-nomatch dp-saved-nomatch" role="status">
          No saved reports match “{search.trim()}”.
        </p>
      ) : (
        <>
          {/* Column labels for sighted users; each row's button already reads all three. */}
          <div className="dp-saved-columns" aria-hidden="true">
            <span>Report</span>
            <span>Question</span>
            <span>Saved</span>
          </div>
          <ul className="dp-saved-list">
            {shown.map((report) => {
              const savedAt = parseTimestamp(report.saved_at)
              return (
                <li key={report.id}>
                  <button
                    type="button"
                    className="dp-saved-row"
                    ref={(element) => {
                      if (element) rowRefs.current.set(report.id, element)
                      else rowRefs.current.delete(report.id)
                    }}
                    onClick={() => onOpen(report.id)}
                  >
                    <span className="dp-saved-report">
                      <span className="dp-history-chip" data-kind={historyKind(report.visualization_type)}>
                        <Icon name={kindIcon(report.visualization_type)} size={20} strokeWidth={1.7} />
                      </span>
                      <span className="dp-saved-name">
                        <span className="dp-saved-title">{report.title}</span>
                        <span className="dp-history-meta">{describeHistoryItem(report)}</span>
                      </span>
                    </span>
                    <span className="dp-saved-question">{report.question}</span>
                    <time className="dp-saved-date" dateTime={savedAt ? savedAt.toISOString() : undefined}>
                      {formatSavedDate(report.saved_at)}
                    </time>
                  </button>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </section>
  )
}

function BackButton({ backRef, onBack }) {
  return (
    <button type="button" ref={backRef} className="dp-back" onClick={onBack}>
      <Icon name="arrowLeft" size={18} strokeWidth={1.8} />
      Back to Saved Reports
    </button>
  )
}

/**
 * A reopened report: its title and saved date, then the stored analysis in the same ResultView as
 * a fresh answer (whose heading stays the original question). It is already saved, so its actions
 * are the quiet "Saved" label and "Remove from Saved Reports". The page and Back button stay the
 * same element from loading to loaded, so keyboard focus stays on Back.
 */
function ReportDetail({ detail, backRef, onBack, onRetry, onRemove }) {
  const report = detail.report
  return (
    <>
      {detail.status === 'loading' && <PageBar />}
      <div className="dp-page dp-page-result dp-saved-detail">
        <BackButton backRef={backRef} onBack={onBack} />
        {detail.status === 'loading' ? (
          <PageSkeleton label="Opening saved report…" variant="result" />
        ) : detail.status === 'ready' ? (
          <>
            <p className="dp-report-meta">
              <Icon name="bookmark" size={16} strokeWidth={1.8} />
              <span className="dp-report-meta-label">Saved report</span>
              <span className="dp-report-meta-title">{report.title}</span>
              <span className="dp-report-meta-date">Saved {formatSavedDate(report.saved_at)}</span>
            </p>
            <ResultView
              result={report.analysis}
              actions={
                <div className="dp-report-actions">
                  <p className="dp-saved-state">
                    <Icon name="bookmark" size={16} strokeWidth={1.8} />
                    Saved
                  </p>
                  {onRemove && <RemoveReportAction title={report.title} onRemove={onRemove} />}
                </div>
              }
            />
          </>
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

/** A failed Saved Reports request, as safe text: never raw response details. */
function errorState(error, notFoundMessage) {
  if (!(error instanceof ApiError)) {
    return { title: 'Unexpected error', message: 'Something went wrong. Please try again.', tone: 'error', retry: true }
  }
  if (error.status === 404) {
    return { title: 'Not found', message: notFoundMessage ?? error.message, tone: 'error', retry: false }
  }
  if (isSavedReportsUnavailable(error)) {
    return { ...SAVED_REPORTS_UNAVAILABLE, tone: 'warning', retry: true }
  }
  return { title: error.title, message: error.message, tone: TEMPORARY_STATUSES.has(error.status ?? null) ? 'warning' : 'error', retry: true }
}
