import { vi } from 'vitest'

// The signed-in app reads the saved-reports list (GET /saved-reports) once when it opens, for the
// sidebar count. Tests about other requests stub fetch with this: that list request goes to its
// own mock (an empty list unless the test says otherwise) and everything else to fetchMock, so
// their request counts stay exact. Returns the saved-list mock.
export function stubFetch(fetchMock) {
  const savedListMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ items: [] }), { status: 200 })))
  vi.stubGlobal('fetch', (url, options) => {
    const isSavedList = (options?.method ?? 'GET') === 'GET' && new URL(url).pathname === '/saved-reports'
    return isSavedList ? savedListMock(url, options) : fetchMock(url, options)
  })
  return savedListMock
}
