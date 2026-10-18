// The only module that talks to the DataPilot backend.

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')

// Gemini plus the database can take a while, but a request should never hang forever.
const REQUEST_TIMEOUT_MS = 60000

// Shown when the backend sends no usable message, keyed by HTTP status.
const MESSAGES_BY_STATUS = {
  400: 'DataPilot could not process this question. Please rephrase it and try again.',
  422: 'The generated query could not be run. Please rephrase your question.',
  429: 'Too many requests to the AI service. Please wait a moment and try again.',
  502: 'The AI service could not answer this question. Please rephrase it.',
  503: 'The service is temporarily unavailable. Please try again in a moment.',
  504: 'The query took too long to run. Try a narrower question.',
}
const DEFAULT_MESSAGE = 'Something went wrong on the server. Please try again.'

const TITLES_BY_STATUS = {
  400: 'Question not accepted',
  422: 'Query failed',
  429: 'Too many requests',
  502: 'AI could not answer',
  503: 'Service temporarily unavailable',
  504: 'Query timed out',
}

// History and Saved Reports wording, used when the backend sends no usable message.
const APP_DATA_ERRORS = {
  titles: { ...TITLES_BY_STATUS, 404: 'Not found', 409: 'Already saved' },
  messages: {
    ...MESSAGES_BY_STATUS,
    400: 'This request could not be completed. Please try again.',
    404: 'This item was not found.',
    409: 'This analysis is already saved as a report.',
    429: 'Too many requests. Please wait a moment and try again.',
    503: 'History is not available right now. Please try again later.',
  },
}

/**
 * A failed request, safe to show: title and message are user-facing. status is the HTTP status
 * (null when the backend was not reached or the request never left); code is the backend's error
 * code (e.g. "already_saved"), or null.
 */
export class ApiError extends Error {
  constructor(title, message, status = null, code = null) {
    super(message)
    this.name = 'ApiError'
    this.title = title
    this.status = status
    this.code = code
  }
}

/** The demo session ended (expired, revoked or missing): the user has to log in again. */
export class SessionExpiredError extends ApiError {
  constructor() {
    super('Session expired', 'Your session has expired. Please log in again.', 401)
    this.name = 'SessionExpiredError'
  }
}

// --- Demo session token ------------------------------------------------------------------
// The backend issues a signed token at login; it is kept in sessionStorage (this tab only, gone
// when the tab closes) and sent as "Authorization: Bearer <token>". It is never decoded, logged,
// rendered or put in a URL: the backend alone decides whether it is still valid.

const TOKEN_KEY = 'datapilot_demo_token'

export function getToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null // storage unavailable (e.g. blocked): behave as signed out
  }
}

export function setToken(token) {
  try {
    sessionStorage.setItem(TOKEN_KEY, token)
  } catch {
    // Without storage the session lasts until the page is reloaded.
  }
}

export function clearToken() {
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // nothing stored
  }
}

function authHeader() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** fetch with a timeout. Network failures and timeouts become ApiErrors with status null. */
async function send(path, options) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    // No credentials option: cookies are never sent; the session travels in the Authorization header.
    return await fetch(`${API_BASE_URL}${path}`, { ...options, signal: controller.signal })
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new ApiError('Request timed out', 'The request took too long. Please try again.')
    }
    throw new ApiError(
      'Cannot reach the API',
      'Cannot connect to the DataPilot API. Please make sure the backend is running.',
    )
  } finally {
    clearTimeout(timer)
  }
}

// Login errors use fixed, client-side wording: never the server's text.
const LOGIN_MESSAGES = {
  400: 'Please enter your username and password.',
  401: 'Incorrect username or password.',
  429: 'Too many login attempts. Please try again later.',
  503: 'Demo access is temporarily unavailable.',
}
const LOGIN_UNREACHABLE = 'Unable to reach DataPilot right now. Please try again.'
const LOGIN_DEFAULT = 'Something went wrong while signing in. Please try again.'

/** Exchange the shared demo username and password for a session token, and keep it. */
export async function login(username, password) {
  let response
  try {
    response = await send('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
  } catch {
    throw new ApiError('Cannot sign in', LOGIN_UNREACHABLE)
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError('Cannot sign in', LOGIN_MESSAGES[response.status] || LOGIN_DEFAULT, response.status)
  }
  if (typeof body?.token !== 'string' || !body.token) {
    throw new ApiError('Cannot sign in', LOGIN_DEFAULT, response.status)
  }
  setToken(body.token)
}

/**
 * Is the stored session still valid? true or false; a 401 also clears the token. Network and
 * server errors throw instead, so an unreachable backend is not mistaken for a logged-out user.
 */
export async function checkSession() {
  if (!getToken()) return false
  const response = await send('/auth/session', { headers: authHeader() })
  if (response.ok) return true
  if (response.status === 401) {
    clearToken()
    return false
  }
  throw new ApiError('Service unavailable', LOGIN_UNREACHABLE, response.status)
}

/**
 * End the session on the server and forget the token here. The token is cleared at once, so the
 * user is signed out locally even if the backend is offline; a failed request is ignored.
 */
export async function logout() {
  const headers = authHeader()
  clearToken()
  if (!headers.Authorization) return
  try {
    await send('/auth/logout', { method: 'POST', headers })
  } catch {
    // already signed out locally
  }
}

/**
 * A signed-in JSON request: the bearer token goes in the Authorization header (never the URL).
 * 401 ends the session (SessionExpiredError); other failures become ApiErrors worded by errors;
 * a success that isValid rejects is an "Unexpected response". Returns the parsed body as sent.
 */
async function requestJson(path, { method = 'GET', body, errors, isValid }) {
  const headers = { ...authHeader() }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const response = await send(path, {
    method,
    headers,
    ...(body !== undefined && { body: JSON.stringify(body) }),
  })

  const data = await response.json().catch(() => null)

  if (response.status === 401) {
    clearToken()
    throw new SessionExpiredError()
  }
  if (!response.ok) {
    throw errorFromResponse(response.status, data, errors)
  }
  if (!isValid(data)) {
    throw new ApiError('Unexpected response', 'The API returned a response DataPilot could not read.', response.status)
  }
  return data
}

const QUERY_ERRORS = { titles: TITLES_BY_STATUS, messages: MESSAGES_BY_STATUS }

/**
 * Send a business question to POST /query and return
 * { question, sql, columns, rows, row_count, truncated, visualization, insight, analysis_id }.
 */
export async function postQuery(question) {
  return requestJson('/query', { method: 'POST', body: { question }, errors: QUERY_ERRORS, isValid: isQueryResult })
}

// --- History and Saved Reports ---------------------------------------------------------------
// Stored answers of the signed-in account. Reading them never calls the AI or reruns a query.
// A stored analysis has the same fields as a /query answer (plus id and created_at), so the same
// components can show both.

const MAX_LIST_LIMIT = 100
const MAX_TITLE_LENGTH = 120

/** GET /history: the account's analyses, newest first, as { items: [summary, ...] }. */
export async function getHistory(limit = 50) {
  return requestJson(`/history?limit=${checkedLimit(limit)}`, {
    errors: APP_DATA_ERRORS,
    isValid: (data) => isList(data, isAnalysisSummary),
  })
}

/** GET /history/{id}: one stored analysis, with saved_report ({ id, title } or null). */
export async function getAnalysis(analysisId) {
  return requestJson(`/history/${pathId(analysisId)}`, {
    errors: APP_DATA_ERRORS,
    isValid: (data) => isStoredAnalysis(data) && (data.saved_report === null || isReportRef(data.saved_report)),
  })
}

/**
 * POST /saved-reports: save one analysis under a title (trimmed, 1-120 characters). Returns
 * { id, analysis_id, title, saved_at }. A second save of the same analysis is an ApiError with
 * code "already_saved"; how to show it is up to the caller.
 */
export async function saveReport(analysisId, title) {
  const body = { analysis_id: checkedId(analysisId), title: checkedTitle(title) }
  return requestJson('/saved-reports', { method: 'POST', body, errors: APP_DATA_ERRORS, isValid: isSavedReport })
}

/** GET /saved-reports: the account's saved reports, most recently saved first, as { items: [...] }. */
export async function getSavedReports(limit = 50) {
  return requestJson(`/saved-reports?limit=${checkedLimit(limit)}`, {
    errors: APP_DATA_ERRORS,
    isValid: (data) => isList(data, isSavedReportSummary),
  })
}

/** GET /saved-reports/{id}: one saved report, { id, title, saved_at, analysis: stored analysis }. */
export async function getSavedReport(reportId) {
  return requestJson(`/saved-reports/${pathId(reportId)}`, {
    errors: APP_DATA_ERRORS,
    isValid: (data) => isObject(data) && hasStrings(data, ['id', 'title', 'saved_at']) && isStoredAnalysis(data.analysis),
  })
}

/**
 * DELETE /saved-reports/{id}: remove one saved report (its analysis stays in History). Resolves
 * with null (204, no body). A report that no longer exists is an ApiError with status 404.
 */
export async function deleteSavedReport(reportId) {
  return requestJson(`/saved-reports/${pathId(reportId)}`, {
    method: 'DELETE',
    errors: APP_DATA_ERRORS,
    isValid: (data) => data === null,
  })
}

// Caller mistakes are refused before any request is sent. The backend still checks everything.
function invalidInput(message) {
  return new ApiError('Invalid request', message, null, 'invalid_request')
}

function checkedLimit(limit) {
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIST_LIMIT) {
    throw invalidInput(`The list limit must be a whole number from 1 to ${MAX_LIST_LIMIT}.`)
  }
  return limit
}

function checkedId(id) {
  if (typeof id !== 'string' || id.trim() === '') throw invalidInput('This item could not be identified.')
  return id
}

// encodeURIComponent leaves "." alone, and the URL parser would resolve "." or ".." to another path.
function pathId(id) {
  const encoded = encodeURIComponent(checkedId(id))
  if (encoded === '.' || encoded === '..') throw invalidInput('This item could not be identified.')
  return encoded
}

function checkedTitle(title) {
  const trimmed = typeof title === 'string' ? title.trim() : ''
  // Counted in characters (code points), as the backend counts them, not UTF-16 units.
  const length = [...trimmed].length
  if (length < 1 || length > MAX_TITLE_LENGTH) {
    throw invalidInput(`Report titles must be 1 to ${MAX_TITLE_LENGTH} characters.`)
  }
  return trimmed
}

function errorFromResponse(status, body, { titles, messages }) {
  const title = titles[status] || 'Unexpected error'
  const error = body?.error
  // The backend's error messages are written for end users. Request-shape errors are not,
  // and anything that doesn't match the documented shape (e.g. a proxy's HTML page) is ignored.
  const usable = typeof error?.message === 'string' && error.message.length > 0 && error.code !== 'invalid_request'
  const message = usable ? error.message : messages[status] || DEFAULT_MESSAGE
  // Only a plain code word is kept, never other text from the body.
  const code = typeof error?.code === 'string' && /^[a-z0-9_]{1,64}$/.test(error.code) ? error.code : null
  return new ApiError(title, message, status, code)
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function hasStrings(value, keys) {
  return keys.every((key) => typeof value[key] === 'string')
}

function isQueryResult(body) {
  return isObject(body) && typeof body.sql === 'string' && Array.isArray(body.columns) && Array.isArray(body.rows)
}

function isStoredAnalysis(body) {
  return (
    isQueryResult(body) &&
    hasStrings(body, ['id', 'question', 'created_at']) &&
    Number.isInteger(body.row_count) &&
    typeof body.truncated === 'boolean' &&
    isObject(body.visualization) &&
    (body.insight === null || typeof body.insight === 'string')
  )
}

function isList(body, isItem) {
  return isObject(body) && Array.isArray(body.items) && body.items.every(isItem)
}

function isAnalysisSummary(item) {
  return isObject(item) && hasStrings(item, ['id', 'question', 'created_at'])
}

function isSavedReportSummary(item) {
  return isObject(item) && hasStrings(item, ['id', 'analysis_id', 'title', 'question', 'saved_at'])
}

function isReportRef(value) {
  return isObject(value) && hasStrings(value, ['id', 'title'])
}

function isSavedReport(body) {
  return isObject(body) && hasStrings(body, ['id', 'analysis_id', 'title', 'saved_at'])
}
