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

export class ApiError extends Error {
  constructor(title, message, status = null) {
    super(message)
    this.name = 'ApiError'
    this.title = title
    this.status = status
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

/** Send a business question to POST /query and return { question, sql, columns, rows, row_count, truncated }. */
export async function postQuery(question) {
  const response = await send('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader() },
    body: JSON.stringify({ question }),
  })

  const body = await response.json().catch(() => null)

  if (response.status === 401) {
    clearToken()
    throw new SessionExpiredError()
  }
  if (!response.ok) {
    throw errorFromResponse(response.status, body)
  }
  if (!isQueryResult(body)) {
    throw new ApiError('Unexpected response', 'The API returned a response DataPilot could not read.', response.status)
  }
  return body
}

function errorFromResponse(status, body) {
  const title = TITLES_BY_STATUS[status] || 'Unexpected error'
  const error = body?.error
  // The backend's error messages are written for end users. Request-shape errors are not,
  // and anything that doesn't match the documented shape (e.g. a proxy's HTML page) is ignored.
  const usable = typeof error?.message === 'string' && error.message.length > 0 && error.code !== 'invalid_request'
  const message = usable ? error.message : MESSAGES_BY_STATUS[status] || DEFAULT_MESSAGE
  return new ApiError(title, message, status)
}

function isQueryResult(body) {
  return (
    body !== null &&
    typeof body === 'object' &&
    typeof body.sql === 'string' &&
    Array.isArray(body.columns) &&
    Array.isArray(body.rows)
  )
}
