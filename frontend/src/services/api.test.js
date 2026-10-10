import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  API_BASE_URL,
  ApiError,
  SessionExpiredError,
  checkSession,
  clearToken,
  getToken,
  login,
  logout,
  postQuery,
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
