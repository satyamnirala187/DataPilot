import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, SessionExpiredError, getSavedReports, saveReport } from '../services/api.js'

// How many saved reports one list request reads. The API has no total count, so a full page means
// "at least this many", never an exact total.
export const SAVED_LIST_LIMIT = 50

/**
 * The account's saved-report state for the signed-in app: the sidebar badge, whether an analysis
 * is saved, and saving one. The list is read once when the app opens (without blocking anything),
 * again after each save, and whenever the Saved Reports page loads it (replaceList).
 *
 * Nothing here is guessed: with no successful list there is no badge, and an analysis counts as
 * saved only from a loaded list, a report saved in this session, or the backend saying so (409).
 */
export function useSavedReports(onSessionExpired) {
  const [items, setItems] = useState(null) // the last list read, or null when unknown
  const [savedNow, setSavedNow] = useState(() => new Set()) // analysis ids saved during this session

  // The latest session-expired callback, without making it a reason to reload.
  const sessionExpired = useRef(onSessionExpired)
  useEffect(() => {
    sessionExpired.current = onSessionExpired
  })

  const refresh = useCallback(() => {
    getSavedReports(SAVED_LIST_LIMIT).then(
      (data) => setItems(data.items),
      (error) => {
        if (error instanceof SessionExpiredError) sessionExpired.current?.()
        // Anything else: the count is unknown, so no badge. Saved Reports shows the error if opened.
        else setItems(null)
      },
    )
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const markSaved = useCallback((analysisId) => {
    setSavedNow((current) => new Set(current).add(analysisId))
  }, [])

  /**
   * Save an analysis under title: one POST /saved-reports. Resolves { status: 'saved', report },
   * { status: 'already' } (409 already_saved: it was saved before) or { status: 'expired' };
   * any other failure is thrown for the dialog to show. The list is read again after a save, so
   * the badge and Saved Reports follow the backend.
   */
  const save = useCallback(
    async (analysisId, title) => {
      try {
        const report = await saveReport(analysisId, title)
        markSaved(analysisId)
        refresh()
        return { status: 'saved', report }
      } catch (error) {
        if (error instanceof SessionExpiredError) {
          sessionExpired.current?.()
          return { status: 'expired' }
        }
        if (error instanceof ApiError && error.code === 'already_saved') {
          markSaved(analysisId)
          refresh()
          return { status: 'already' }
        }
        throw error
      }
    },
    [markSaved, refresh],
  )

  const isSaved = useCallback(
    (analysisId) => Boolean(analysisId) && (savedNow.has(analysisId) || Boolean(items?.some((item) => item.analysis_id === analysisId))),
    [items, savedNow],
  )

  // The badge: the real number of saved reports, "50+" when a full page could mean more, and none
  // while unknown or when there are none.
  const badge = items === null || items.length === 0 ? null : items.length >= SAVED_LIST_LIMIT ? `${SAVED_LIST_LIMIT}+` : String(items.length)

  return { badge, isSaved, save, replaceList: setItems }
}
