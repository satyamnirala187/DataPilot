// Saved Reports list helpers: the search filter and the saved date. Nothing here changes the list.

import { formatHistoryTime, parseTimestamp } from './history.js'

// Saved Reports and History share one store, so when it is down the backend answers saved-report
// requests with History's code and wording (history_unavailable, "History is not available…").
// On Saved Reports screens and when saving, that reads wrongly; this is what is said there instead.
// History itself keeps the backend's wording.
export const SAVED_REPORTS_UNAVAILABLE = Object.freeze({
  title: 'Saved Reports unavailable',
  message: 'Saved Reports are not available right now. Please try again later.',
})

/** Is this failed saved-report request the store being unavailable (history_unavailable / 503)? */
export function isSavedReportsUnavailable(error) {
  return error?.code === 'history_unavailable' || error?.status === 503
}

/** The reports whose title or question contains the search text, ignoring case and outer spaces. */
export function filterSavedReports(reports, search) {
  const needle = search.trim().toLocaleLowerCase()
  if (!needle) return reports
  return reports.filter((report) =>
    [report.title, report.question].some((text) => String(text ?? '').toLocaleLowerCase().includes(needle)),
  )
}

/** '8 Oct 2026' in the viewer's time zone, or 'Date unavailable' for an unreadable saved_at. */
export function formatSavedDate(savedAt) {
  const date = parseTimestamp(savedAt)
  return date ? formatHistoryTime(date, 'Earlier') : 'Date unavailable'
}
