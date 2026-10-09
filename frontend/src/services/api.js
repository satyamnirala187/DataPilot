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

/** Send a business question to POST /query and return { question, sql, columns, rows, row_count, truncated }. */
export async function postQuery(question) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

  let response
  try {
    response = await fetch(`${API_BASE_URL}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    })
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

  const body = await response.json().catch(() => null)

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
