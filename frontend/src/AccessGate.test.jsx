import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AccessGate from './AccessGate.jsx'
import { API_BASE_URL } from './services/api.js'

// All tests mock fetch: no real backend, Gemini or database calls. Credentials are fake.

const TOKEN = 'v1.1900000000.fake-session-id-0000000.fake-signature-for-tests-only-0000000000000'
const KEY = 'datapilot_demo_token'
const RESULT = {
  question: 'What is our total revenue?',
  sql: 'SELECT 1 AS total_revenue LIMIT 501',
  columns: ['total_revenue'],
  rows: [[21304631.99]],
  row_count: 1,
  truncated: false,
  visualization: { type: 'kpi', y_key: 'total_revenue' },
  insight: null,
}

function respond(status, body) {
  return Promise.resolve(new Response(body === undefined ? null : JSON.stringify(body), { status }))
}

function errorBody(code, message = 'A safe message.') {
  return { error: { code, message } }
}

let fetchMock
let routes

/** Route fetch calls by path; each route returns a Promise<Response> or throws. */
function route(path, handler) {
  routes[path] = handler
}

beforeEach(() => {
  sessionStorage.clear()
  routes = {}
  fetchMock = vi.fn((url, options) => {
    const handler = routes[url.replace(API_BASE_URL, '')]
    if (!handler) throw new Error(`unexpected request to ${url}`)
    return handler(options)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

function calls(path) {
  return fetchMock.mock.calls.filter(([url]) => url === `${API_BASE_URL}${path}`)
}

async function signIn(user, username = 'demo-user-fake', password = 'fake-password-for-tests') {
  await user.type(screen.getByLabelText('Username'), username)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Enter DataPilot' }))
}

async function ask(user, question = 'What is our total revenue?') {
  await user.type(screen.getByLabelText('Your question'), question)
  await user.click(screen.getByRole('button', { name: 'Analyze' }))
}

describe('login page', () => {
  it('is shown when there is no token, without checking a session', () => {
    render(<AccessGate />)
    expect(screen.getByRole('heading', { level: 1, name: 'Private Demo Access' })).toBeInTheDocument()
    expect(screen.getByText('AI-powered business data analyst')).toBeInTheDocument()
    expect(screen.getByText('Private portfolio demonstration')).toBeInTheDocument()
    expect(screen.queryByLabelText('Your question')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('has labelled username and password fields with sensible autocomplete', () => {
    render(<AccessGate />)
    const username = screen.getByLabelText('Username')
    const password = screen.getByLabelText('Password')
    expect(username).toHaveAttribute('type', 'text')
    expect(username).toHaveAttribute('autocomplete', 'username')
    expect(password).toHaveAttribute('type', 'password')
    expect(password).toHaveAttribute('autocomplete', 'current-password')
    expect(username).toHaveFocus()
    expect(screen.queryByText(/sign up|forgot|reset|remember me/i)).not.toBeInTheDocument()
  })

  it('signs in, stores the token and opens DataPilot', async () => {
    route('/auth/login', () => respond(200, { token: TOKEN, expires_at: '2030-01-01T00:00:00Z' }))
    const user = userEvent.setup()
    render(<AccessGate />)
    await signIn(user)
    expect(await screen.findByLabelText('Your question')).toBeInTheDocument()
    expect(sessionStorage.getItem(KEY)).toBe(TOKEN)
    expect(JSON.parse(calls('/auth/login')[0][1].body)).toEqual({
      username: 'demo-user-fake',
      password: 'fake-password-for-tests',
    })
    expect(calls('/auth/login')[0][1].headers.Authorization).toBeUndefined()
  })

  it('submits with the Enter key', async () => {
    route('/auth/login', () => respond(200, { token: TOKEN, expires_at: '2030-01-01T00:00:00Z' }))
    const user = userEvent.setup()
    render(<AccessGate />)
    await user.type(screen.getByLabelText('Username'), 'demo-user-fake')
    await user.type(screen.getByLabelText('Password'), 'fake-password-for-tests{Enter}')
    expect(await screen.findByLabelText('Your question')).toBeInTheDocument()
  })

  it('shows "Signing in..." and disables the form while the request runs', async () => {
    let finish
    route('/auth/login', () => new Promise((resolve) => (finish = resolve)))
    const user = userEvent.setup()
    render(<AccessGate />)
    await signIn(user)
    expect(screen.getByRole('button', { name: 'Signing in...' })).toBeDisabled()
    expect(screen.getByLabelText('Username')).toBeDisabled()
    expect(screen.getByLabelText('Password')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Signing in...' }))
    expect(calls('/auth/login')).toHaveLength(1)
    finish(new Response(JSON.stringify({ token: TOKEN, expires_at: '2030-01-01T00:00:00Z' }), { status: 200 }))
    expect(await screen.findByLabelText('Your question')).toBeInTheDocument()
  })

  it('asks for both fields without calling the API', async () => {
    const user = userEvent.setup()
    render(<AccessGate />)
    await user.click(screen.getByRole('button', { name: 'Enter DataPilot' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Please enter your username and password.')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['wrong credentials', () => respond(401, errorBody('invalid_credentials', 'server wording')), 'Incorrect username or password.'],
    ['too many attempts', () => respond(429, errorBody('too_many_login_attempts', 'server wording')), 'Too many login attempts. Please try again later.'],
    ['login unavailable', () => respond(503, errorBody('login_unavailable', 'server wording')), 'Demo access is temporarily unavailable.'],
    ['network failure', () => Promise.reject(new TypeError('Failed to fetch')), 'Unable to reach DataPilot right now. Please try again.'],
    ['unexpected error', () => respond(500, { detail: 'Traceback (most recent call last)' }), 'Something went wrong while signing in. Please try again.'],
  ])('shows a safe message for %s and stays on the login page', async (_, handler, message) => {
    route('/auth/login', handler)
    const user = userEvent.setup()
    render(<AccessGate />)
    await signIn(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(document.body.textContent).not.toMatch(/server wording|Traceback|fake-password/)
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(screen.getByLabelText('Password')).toHaveFocus()
    expect(screen.queryByLabelText('Your question')).not.toBeInTheDocument()
    expect(sessionStorage.getItem(KEY)).toBeNull()
  })
})

describe('initial session check', () => {
  it('shows only "Checking access..." while a stored token is verified, then DataPilot', async () => {
    sessionStorage.setItem(KEY, TOKEN)
    let finish
    route('/auth/session', () => new Promise((resolve) => (finish = resolve)))
    render(<AccessGate />)
    expect(screen.getByRole('status')).toHaveTextContent('Checking access...')
    expect(screen.queryByLabelText('Your question')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
    expect(calls('/auth/session')[0][1].headers.Authorization).toBe(`Bearer ${TOKEN}`)
    finish(new Response(JSON.stringify({ authenticated: true, expires_at: '2030-01-01T00:00:00Z' }), { status: 200 }))
    expect(await screen.findByLabelText('Your question')).toBeInTheDocument()
  })

  it('clears an invalid, expired or revoked token and shows the login page', async () => {
    sessionStorage.setItem(KEY, TOKEN)
    route('/auth/session', () => respond(401, errorBody('unauthorized')))
    render(<AccessGate />)
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(sessionStorage.getItem(KEY)).toBeNull()
  })

  it('does not reveal DataPilot or discard the token when the backend is unreachable', async () => {
    sessionStorage.setItem(KEY, TOKEN)
    route('/auth/session', () => Promise.reject(new TypeError('Failed to fetch')))
    const user = userEvent.setup()
    render(<AccessGate />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to reach DataPilot right now. Please try again.')
    expect(screen.queryByLabelText('Your question')).not.toBeInTheDocument()
    expect(sessionStorage.getItem(KEY)).toBe(TOKEN)

    route('/auth/session', () => respond(200, { authenticated: true, expires_at: '2030-01-01T00:00:00Z' }))
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByLabelText('Your question')).toBeInTheDocument()
  })
})

describe('questions while signed in', () => {
  async function signedIn(user) {
    route('/auth/login', () => respond(200, { token: TOKEN, expires_at: '2030-01-01T00:00:00Z' }))
    render(<AccessGate />)
    await signIn(user)
    await screen.findByLabelText('Your question')
  }

  it('sends the bearer token with each question', async () => {
    route('/query', () => respond(200, RESULT))
    const user = userEvent.setup()
    await signedIn(user)
    await ask(user)
    expect(await screen.findByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(calls('/query')[0][1].headers.Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it('returns to the login page with a notice when a question gets 401', async () => {
    route('/query', () => respond(401, errorBody('unauthorized', 'Please log in to use DataPilot.')))
    const user = userEvent.setup()
    await signedIn(user)
    await ask(user)
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Your session has expired. Please log in again.')
    expect(sessionStorage.getItem(KEY)).toBeNull()
    expect(calls('/query')).toHaveLength(1) // no retry loop
  })

  it.each([
    [429, 'too_many_requests', 'You are asking questions too quickly.'],
    [429, 'daily_limit_reached', 'The service has reached its daily AI request limit.'],
    [422, 'result_too_large', 'The query result is too large to return safely.'],
    [503, 'generation_unavailable', 'The AI service is unavailable right now.'],
  ])('keeps the user signed in for HTTP %i %s', async (status, code, message) => {
    route('/query', () => respond(status, errorBody(code, message)))
    const user = userEvent.setup()
    await signedIn(user)
    await ask(user)
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.getByLabelText('Your question')).toBeInTheDocument()
    expect(sessionStorage.getItem(KEY)).toBe(TOKEN)
  })

  it('never renders the token, even in error text', async () => {
    route('/query', () => respond(503, errorBody('generation_unavailable', 'The AI service is unavailable right now.')))
    const user = userEvent.setup()
    await signedIn(user)
    await ask(user)
    await screen.findByRole('alert')
    expect(document.body.innerHTML).not.toContain(TOKEN)
    expect(document.body.innerHTML).not.toContain('fake-session-id')
  })
})

describe('logout', () => {
  async function signedInWithStoredToken() {
    sessionStorage.setItem(KEY, TOKEN)
    route('/auth/session', () => respond(200, { authenticated: true, expires_at: '2030-01-01T00:00:00Z' }))
    render(<AccessGate />)
    await screen.findByLabelText('Your question')
  }

  it('revokes the session with the bearer token and returns to the login page', async () => {
    route('/auth/logout', () => respond(204))
    const user = userEvent.setup()
    await signedInWithStoredToken()
    await user.click(screen.getByRole('button', { name: 'Log out' }))
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(sessionStorage.getItem(KEY)).toBeNull()
    await waitFor(() => expect(calls('/auth/logout')).toHaveLength(1))
    expect(calls('/auth/logout')[0][1].headers.Authorization).toBe(`Bearer ${TOKEN}`)
    expect(screen.queryByRole('status')).not.toBeInTheDocument() // no "session expired" notice
  })

  it('signs out locally even when the logout request fails', async () => {
    route('/auth/logout', () => Promise.reject(new TypeError('Failed to fetch')))
    const user = userEvent.setup()
    await signedInWithStoredToken()
    await user.click(screen.getByRole('button', { name: 'Log out' }))
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(sessionStorage.getItem(KEY)).toBeNull()
  })
})
