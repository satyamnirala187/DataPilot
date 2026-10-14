import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  API_BASE_URL,
  ApiError,
  SessionExpiredError,
  checkSession,
  clearToken,
  getAnalysis,
  getHistory,
  getSavedReport,
  getSavedReports,
  getToken,
  login,
  logout,
  postQuery,
  saveReport,
  setToken,
} from './api.js'

const TOKEN = 'v1.1900000000.fake-session-id-0000000.fake-signature-for-tests-only-0000000000000'
const RESULT = { question: 'q', sql: 'SELECT 1', columns: ['x'], rows: [[1]], row_count: 1, truncated: false }

beforeEach(() => {
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

function stubFetch(response) {
  const fetchMock = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('postQuery', () => {
  it('posts JSON to {API_BASE_URL}/query', async () => {
    const body = { question: 'q', sql: 'SELECT 1', columns: ['x'], rows: [[1]], row_count: 1, truncated: false }
    const fetchMock = stubFetch(new Response(JSON.stringify(body), { status: 200 }))

    await expect(postQuery('q')).resolves.toEqual(body)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/query`)
    expect(options.headers['Content-Type']).toBe('application/json')
    expect(API_BASE_URL).not.toMatch(/\/$/)
  })

  it('replaces the backend request-shape message with a user-facing one', async () => {
    stubFetch(new Response(JSON.stringify({ error: { code: 'invalid_request', message: 'Send JSON like {"question": "..."}' } }), { status: 400 }))
    const error = await postQuery('q').catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(400)
    expect(error.message).not.toContain('JSON')
  })

  it('rejects a 200 response that is not a query result', async () => {
    stubFetch(new Response('{"unexpected": true}', { status: 200 }))
    await expect(postQuery('q')).rejects.toMatchObject({ title: 'Unexpected response' })
  })
})

function json(status, body) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status })
}

describe('session token storage', () => {
  it('keeps the token in sessionStorage under one key, never localStorage', () => {
    expect(getToken()).toBeNull()
    setToken(TOKEN)
    expect(getToken()).toBe(TOKEN)
    expect(sessionStorage.getItem('datapilot_demo_token')).toBe(TOKEN)
    expect(localStorage.length).toBe(0)
    clearToken()
    expect(getToken()).toBeNull()
  })
})

describe('login', () => {
  it('posts the credentials without an Authorization header and stores the token', async () => {
    const fetchMock = stubFetch(json(200, { token: TOKEN, expires_at: '2030-01-01T00:00:00Z' }))
    await login('demo', 'secret-password')
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/auth/login`)
    expect(JSON.parse(options.body)).toEqual({ username: 'demo', password: 'secret-password' })
    expect(options.headers.Authorization).toBeUndefined()
    expect(options.credentials).toBeUndefined()
    expect(getToken()).toBe(TOKEN)
  })

  it.each([
    [401, { error: { code: 'invalid_credentials', message: 'server text' } }, 'Incorrect username or password.'],
    [429, { error: { code: 'too_many_login_attempts', message: 'server text' } }, 'Too many login attempts. Please try again later.'],
    [503, { error: { code: 'login_unavailable', message: 'server text' } }, 'Demo access is temporarily unavailable.'],
    [400, { error: { code: 'invalid_request', message: 'Send JSON like ...' } }, 'Please enter your username and password.'],
    [500, null, 'Something went wrong while signing in. Please try again.'],
  ])('maps HTTP %i to a fixed message and stores nothing', async (status, body, message) => {
    stubFetch(json(status, body))
    const error = await login('demo', 'wrong-password').catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe(message)
    expect(error.message).not.toContain('server text')
    expect(getToken()).toBeNull()
  })

  it('reports an unreachable backend', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(login('demo', 'pw')).rejects.toMatchObject({ message: 'Unable to reach DataPilot right now. Please try again.' })
  })

  it('rejects a 200 response without a token', async () => {
    stubFetch(json(200, { unexpected: true }))
    await expect(login('demo', 'pw')).rejects.toBeInstanceOf(ApiError)
    expect(getToken()).toBeNull()
  })
})

describe('checkSession', () => {
  it('is false without a token and makes no request', async () => {
    const fetchMock = stubFetch(json(200, { authenticated: true }))
    await expect(checkSession()).resolves.toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends the bearer token and accepts a 200', async () => {
    setToken(TOKEN)
    const fetchMock = stubFetch(json(200, { authenticated: true, expires_at: '2030-01-01T00:00:00Z' }))
    await expect(checkSession()).resolves.toBe(true)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/auth/session`)
    expect(options.headers.Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it('clears the token on 401', async () => {
    setToken(TOKEN)
    stubFetch(json(401, { error: { code: 'unauthorized', message: 'Please log in to use DataPilot.' } }))
    await expect(checkSession()).resolves.toBe(false)
    expect(getToken()).toBeNull()
  })

  it('throws, keeping the token, when the backend is unreachable or failing', async () => {
    setToken(TOKEN)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(checkSession()).rejects.toBeInstanceOf(ApiError)
    stubFetch(json(503, null))
    await expect(checkSession()).rejects.toBeInstanceOf(ApiError)
    expect(getToken()).toBe(TOKEN)
  })
})

describe('logout', () => {
  it('sends the bearer token and clears it', async () => {
    setToken(TOKEN)
    const fetchMock = stubFetch(json(204))
    await logout()
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/auth/logout`)
    expect(options.method).toBe('POST')
    expect(options.headers.Authorization).toBe(`Bearer ${TOKEN}`)
    expect(getToken()).toBeNull()
  })

  it('clears the token even when the backend is offline', async () => {
    setToken(TOKEN)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(logout()).resolves.toBeUndefined()
    expect(getToken()).toBeNull()
  })

  it('makes no request without a token', async () => {
    const fetchMock = stubFetch(json(204))
    await logout()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('postQuery and the session', () => {
  it('attaches the bearer token', async () => {
    setToken(TOKEN)
    const fetchMock = stubFetch(json(200, RESULT))
    await postQuery('q')
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it('sends no Authorization header at all without a token (never "Bearer null")', async () => {
    const fetchMock = stubFetch(json(200, RESULT))
    await postQuery('q')
    const { headers } = fetchMock.mock.calls[0][1]
    expect('Authorization' in headers).toBe(false)
    expect(JSON.stringify(headers)).not.toMatch(/null|undefined/)
  })

  it('turns a 401 into SessionExpiredError and clears the token', async () => {
    setToken(TOKEN)
    stubFetch(json(401, { error: { code: 'unauthorized', message: 'Please log in to use DataPilot.' } }))
    const error = await postQuery('q').catch((e) => e)
    expect(error).toBeInstanceOf(SessionExpiredError)
    expect(error.message).toBe('Your session has expired. Please log in again.')
    expect(getToken()).toBeNull()
  })

  it.each([
    [429, 'too_many_requests'],
    [429, 'daily_limit_reached'],
    [422, 'result_too_large'],
    [503, 'generation_unavailable'],
  ])('keeps the session for HTTP %i %s', async (status, code) => {
    setToken(TOKEN)
    stubFetch(json(status, { error: { code, message: 'A safe message.' } }))
    const error = await postQuery('q').catch((e) => e)
    expect(error).not.toBeInstanceOf(SessionExpiredError)
    expect(error).toMatchObject({ status, message: 'A safe message.' })
    expect(getToken()).toBe(TOKEN)
  })
})

// --- History and Saved Reports ---------------------------------------------------------------

const ANALYSIS_ID = '3f2b8c1e-5d4a-4e7b-9c1d-2a6f8e0b7c55'
const REPORT_ID = '9a1c2d3e-4f50-4a6b-8c7d-0e1f2a3b4c5d'
const STORED = {
  id: ANALYSIS_ID,
  question: 'What is our total revenue?',
  sql: 'SELECT 1 AS total_revenue',
  columns: ['total_revenue'],
  rows: [[1]],
  row_count: 1,
  truncated: false,
  visualization: { type: 'kpi', x_key: null, y_key: 'total_revenue' },
  insight: null,
  created_at: '2026-10-15T09:42:00Z',
}
const HISTORY_ITEM = {
  id: ANALYSIS_ID,
  question: 'What is our total revenue?',
  visualization_type: 'kpi',
  row_count: 1,
  truncated: false,
  created_at: '2026-10-15T09:42:00Z',
  saved_report_id: null,
}
const SAVED = { id: REPORT_ID, analysis_id: ANALYSIS_ID, title: 'Total revenue', saved_at: '2026-10-15T09:45:00Z' }
const SAVED_ITEM = {
  ...SAVED,
  question: 'What is our total revenue?',
  visualization_type: 'kpi',
  row_count: 1,
  truncated: false,
  analysis_created_at: '2026-10-15T09:42:00Z',
}

// A fresh Response for every call: a body can be read only once.
function stubFetchEach(makeResponse) {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(makeResponse()))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function apiError(code, message = 'A safe message.') {
  return { error: { code, message } }
}

describe('getHistory', () => {
  it('GETs /history with the default limit and the bearer token, and returns the list', async () => {
    setToken(TOKEN)
    const body = { items: [HISTORY_ITEM] }
    const fetchMock = stubFetch(json(200, body))
    await expect(getHistory()).resolves.toEqual(body)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/history?limit=50`)
    expect(options.method).toBe('GET')
    expect(options.headers.Authorization).toBe(`Bearer ${TOKEN}`)
    expect(options.body).toBeUndefined()
    expect(url).not.toContain(TOKEN)
  })

  it.each([1, 100])('accepts limit %i', async (limit) => {
    const fetchMock = stubFetch(json(200, { items: [] }))
    await expect(getHistory(limit)).resolves.toEqual({ items: [] })
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/history?limit=${limit}`)
  })

  it.each([0, 101, 2.5, '50', null, Number.NaN])('refuses limit %s without a request', async (limit) => {
    const fetchMock = stubFetch(json(200, { items: [] }))
    await expect(getHistory(limit)).rejects.toMatchObject({ code: 'invalid_request', status: null })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['an array', [HISTORY_ITEM]],
    ['no items', {}],
    ['items that are not a list', { items: {} }],
    ['an item without an id', { items: [{ ...HISTORY_ITEM, id: undefined }] }],
    ['an item that is not an object', { items: ['x'] }],
    ['null', null],
  ])('rejects a 200 response with %s', async (_, body) => {
    stubFetch(json(200, body))
    await expect(getHistory()).rejects.toMatchObject({ title: 'Unexpected response' })
  })

  it('turns a 401 into SessionExpiredError and clears the token', async () => {
    setToken(TOKEN)
    stubFetch(json(401, apiError('unauthorized')))
    await expect(getHistory()).rejects.toBeInstanceOf(SessionExpiredError)
    expect(getToken()).toBeNull()
  })

  it('keeps the session and the code on 429', async () => {
    setToken(TOKEN)
    stubFetch(json(429, apiError('too_many_requests', 'Too many requests. Please wait a moment and try again.')))
    await expect(getHistory()).rejects.toMatchObject({ status: 429, code: 'too_many_requests' })
    expect(getToken()).toBe(TOKEN)
  })

  it('reports an unreachable backend with the existing network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(getHistory()).rejects.toMatchObject({ title: 'Cannot reach the API', status: null, code: null })
  })
})

describe('getAnalysis', () => {
  it('GETs the encoded id and returns the stored snapshot as sent', async () => {
    setToken(TOKEN)
    const body = { ...STORED, saved_report: { id: REPORT_ID, title: 'Total revenue' } }
    const fetchMock = stubFetch(json(200, body))
    await expect(getAnalysis(ANALYSIS_ID)).resolves.toEqual(body)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/history/${ANALYSIS_ID}`)
    expect(options.headers.Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it('encodes path characters in the id', async () => {
    const fetchMock = stubFetch(json(404, apiError('analysis_not_found')))
    await getAnalysis('a/b?c#d').catch(() => {})
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/history/a%2Fb%3Fc%23d`)
  })

  it.each(['', '   ', null, undefined, 42, '.', '..'])('refuses id %s without a request', async (id) => {
    const fetchMock = stubFetch(json(200, { ...STORED, saved_report: null }))
    await expect(getAnalysis(id)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['no saved_report field', STORED],
    ['no sql', { ...STORED, sql: undefined, saved_report: null }],
    ['rows that are not a list', { ...STORED, rows: {}, saved_report: null }],
    ['no visualization', { ...STORED, visualization: null, saved_report: null }],
    ['a malformed saved_report', { ...STORED, saved_report: { id: REPORT_ID } }],
  ])('rejects a snapshot with %s', async (_, body) => {
    stubFetch(json(200, body))
    await expect(getAnalysis(ANALYSIS_ID)).rejects.toMatchObject({ title: 'Unexpected response' })
  })

  it('turns a 401 into SessionExpiredError', async () => {
    setToken(TOKEN)
    stubFetch(json(401, apiError('unauthorized')))
    await expect(getAnalysis(ANALYSIS_ID)).rejects.toBeInstanceOf(SessionExpiredError)
    expect(getToken()).toBeNull()
  })

  it.each([
    [404, 'analysis_not_found', 'This analysis was not found.'],
    [503, 'history_unavailable', 'History is not available right now. Please try again later.'],
  ])('keeps HTTP %i %s with its message', async (status, code, message) => {
    setToken(TOKEN)
    stubFetch(json(status, apiError(code, message)))
    const error = await getAnalysis(ANALYSIS_ID).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).not.toBeInstanceOf(SessionExpiredError)
    expect(error).toMatchObject({ status, code, message })
    expect(getToken()).toBe(TOKEN)
  })

  it('keeps only a plain code word, never other body text', async () => {
    stubFetch(json(404, { error: { code: '<b>not a code</b>', message: 'This analysis was not found.' } }))
    await expect(getAnalysis(ANALYSIS_ID)).rejects.toMatchObject({ status: 404, code: null })
  })

  it('uses a fixed message when the error body is not the documented shape', async () => {
    stubFetch(new Response('<html>Bad gateway</html>', { status: 503 }))
    const error = await getAnalysis(ANALYSIS_ID).catch((e) => e)
    expect(error).toMatchObject({ status: 503, code: null })
    expect(error.message).toBe('History is not available right now. Please try again later.')
  })
})

describe('saveReport', () => {
  it('POSTs the id and the trimmed title as JSON with the bearer token', async () => {
    setToken(TOKEN)
    const fetchMock = stubFetch(json(201, SAVED))
    await expect(saveReport(ANALYSIS_ID, '  Total revenue  ')).resolves.toEqual(SAVED)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/saved-reports`)
    expect(options.method).toBe('POST')
    expect(options.headers['Content-Type']).toBe('application/json')
    expect(options.headers.Authorization).toBe(`Bearer ${TOKEN}`)
    expect(JSON.parse(options.body)).toEqual({ analysis_id: ANALYSIS_ID, title: 'Total revenue' })
  })

  it('accepts a 120-character title, counting characters rather than UTF-16 units', async () => {
    const fetchMock = stubFetchEach(() => json(201, SAVED))
    await saveReport(ANALYSIS_ID, 'x'.repeat(120))
    await saveReport(ANALYSIS_ID, '📈'.repeat(120))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['blank', '   '],
    ['empty', ''],
    ['too long', 'x'.repeat(121)],
    ['not a string', 42],
    ['missing', undefined],
  ])('refuses a %s title without a request', async (_, title) => {
    const fetchMock = stubFetch(json(201, SAVED))
    await expect(saveReport(ANALYSIS_ID, title)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('refuses a missing analysis id without a request', async () => {
    const fetchMock = stubFetch(json(201, SAVED))
    await expect(saveReport(null, 'Total revenue')).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    [409, 'already_saved', 'This analysis is already saved as a report.'],
    [404, 'analysis_not_found', 'This analysis was not found.'],
    [503, 'history_unavailable', 'History is not available right now. Please try again later.'],
    [429, 'too_many_requests', 'Too many requests. Please wait a moment and try again.'],
  ])('rejects HTTP %i, keeping the code %s', async (status, code, message) => {
    stubFetch(json(status, apiError(code, message)))
    const error = await saveReport(ANALYSIS_ID, 'Total revenue').catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status, code, message })
  })

  it('keeps the invalid_request code but replaces its developer-facing message', async () => {
    stubFetch(json(400, apiError('invalid_request', 'The request is not valid. Check the id, the limit (1-100)')))
    const error = await saveReport(ANALYSIS_ID, 'Total revenue').catch((e) => e)
    expect(error).toMatchObject({ status: 400, code: 'invalid_request' })
    expect(error.message).toBe('This request could not be completed. Please try again.')
  })

  it('rejects a 201 response that is not a saved report', async () => {
    stubFetch(json(201, { id: REPORT_ID }))
    await expect(saveReport(ANALYSIS_ID, 'Total revenue')).rejects.toMatchObject({ title: 'Unexpected response' })
  })
})

describe('getSavedReports', () => {
  it('GETs /saved-reports with the limit and the bearer token, and returns the list', async () => {
    setToken(TOKEN)
    const body = { items: [SAVED_ITEM] }
    const fetchMock = stubFetchEach(() => json(200, body))
    await expect(getSavedReports()).resolves.toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/saved-reports?limit=50`)
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${TOKEN}`)

    await getSavedReports(100)
    expect(fetchMock.mock.calls[1][0]).toBe(`${API_BASE_URL}/saved-reports?limit=100`)
  })

  it('refuses an invalid limit without a request', async () => {
    const fetchMock = stubFetch(json(200, { items: [] }))
    await expect(getSavedReports(0)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(getSavedReports(101)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['no items', {}],
    ['an item without a title', { items: [{ ...SAVED_ITEM, title: undefined }] }],
    ['an item without an analysis id', { items: [{ ...SAVED_ITEM, analysis_id: null }] }],
  ])('rejects a 200 response with %s', async (_, body) => {
    stubFetch(json(200, body))
    await expect(getSavedReports()).rejects.toMatchObject({ title: 'Unexpected response' })
  })

  it('turns a 401 into SessionExpiredError', async () => {
    setToken(TOKEN)
    stubFetch(json(401, apiError('unauthorized')))
    await expect(getSavedReports()).rejects.toBeInstanceOf(SessionExpiredError)
  })
})

describe('getSavedReport', () => {
  it('GETs the encoded id and returns the report with its stored analysis', async () => {
    setToken(TOKEN)
    const body = { id: REPORT_ID, title: 'Total revenue', saved_at: SAVED.saved_at, analysis: STORED }
    const fetchMock = stubFetch(json(200, body))
    await expect(getSavedReport(REPORT_ID)).resolves.toEqual(body)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/saved-reports/${REPORT_ID}`)
    expect(options.headers.Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it('encodes path characters in the id', async () => {
    const fetchMock = stubFetch(json(404, apiError('report_not_found')))
    await getSavedReport('a b/c').catch(() => {})
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/saved-reports/a%20b%2Fc`)
  })

  it.each([
    ['no analysis', { id: REPORT_ID, title: 't', saved_at: SAVED.saved_at }],
    ['a malformed analysis', { id: REPORT_ID, title: 't', saved_at: SAVED.saved_at, analysis: { id: ANALYSIS_ID } }],
    ['no title', { id: REPORT_ID, saved_at: SAVED.saved_at, analysis: STORED }],
  ])('rejects a report with %s', async (_, body) => {
    stubFetch(json(200, body))
    await expect(getSavedReport(REPORT_ID)).rejects.toMatchObject({ title: 'Unexpected response' })
  })

  it('keeps report_not_found on 404 and ends the session on 401', async () => {
    setToken(TOKEN)
    stubFetch(json(404, apiError('report_not_found', 'This saved report was not found.')))
    await expect(getSavedReport(REPORT_ID)).rejects.toMatchObject({
      status: 404,
      code: 'report_not_found',
      message: 'This saved report was not found.',
    })
    stubFetch(json(401, apiError('unauthorized')))
    await expect(getSavedReport(REPORT_ID)).rejects.toBeInstanceOf(SessionExpiredError)
    expect(getToken()).toBeNull()
  })
})

describe('ApiError code', () => {
  it('is kept for /query errors too, without changing their wording', async () => {
    stubFetch(json(400, apiError('unsafe_sql', 'The generated query was rejected by the safety checks.')))
    const error = await postQuery('q').catch((e) => e)
    expect(error).toMatchObject({
      title: 'Question not accepted',
      message: 'The generated query was rejected by the safety checks.',
      status: 400,
      code: 'unsafe_sql',
    })
  })

  it('defaults to null, so existing callers are unaffected', () => {
    expect(new ApiError('t', 'm', 500).code).toBeNull()
    expect(new SessionExpiredError().code).toBeNull()
  })
})
