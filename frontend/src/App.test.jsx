import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'

// All tests mock fetch: no real backend, Gemini or database calls.

const SUCCESS = {
  question: 'What are the top 2 categories by revenue?',
  sql: "SELECT c.name AS category, SUM(oi.quantity * oi.unit_price) AS revenue FROM categories AS c LIMIT 500",
  columns: ['category', 'revenue'],
  rows: [
    ['Electronics', 8123456.5],
    ['Apparel', 4012345.25],
  ],
  row_count: 2,
  truncated: false,
}

function jsonResponse(status, body) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

async function ask(user, question) {
  await user.type(screen.getByLabelText('Your question'), question)
  await user.click(screen.getByRole('button', { name: 'Analyze' }))
}

let fetchMock

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('DataPilot page', () => {
  it('renders the header, question field and Analyze button', () => {
    render(<App />)
    expect(screen.getByRole('heading', { level: 1, name: 'DataPilot' })).toBeInTheDocument()
    expect(screen.getByText('AI Business Data Analyst')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Ask a business question' })).toBeInTheDocument()
    expect(screen.getByLabelText('Your question')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeEnabled()
  })

  it('shows an empty state before the first question and replaces it with the answer', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup()
    render(<App />)
    expect(screen.getByText('Your answer will appear here')).toBeInTheDocument()

    await ask(user, 'What are the top 2 categories by revenue?')
    expect(await screen.findByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(screen.getByText(SUCCESS.question, { selector: 'p' })).toBeInTheDocument()
    expect(screen.queryByText('Your answer will appear here')).not.toBeInTheDocument()
  })

  it('fills the question field when an example is clicked, without calling the API', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'What is our average order value?' }))
    expect(screen.getByLabelText('Your question')).toHaveValue('What is our average order value?')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects an empty or whitespace-only question without calling the API', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Please enter a business question.')

    await ask(user, '   ')
    expect(screen.getByLabelText('Your question')).toHaveAttribute('aria-invalid', 'true')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends the trimmed question and renders headers, rows and the generated SQL', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, '  What are the top 2 categories by revenue?  ')

    expect(await screen.findByRole('columnheader', { name: 'category' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'revenue' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Electronics' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '4012345.25' })).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(3) // header + 2 rows
    expect(screen.getByText('2 rows')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Generated SQL' })).toBeInTheDocument()
    expect(screen.getByText(SUCCESS.sql)).toBeInTheDocument()

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/query$/)
    expect(options.method).toBe('POST')
    expect(JSON.parse(options.body)).toEqual({ question: 'What are the top 2 categories by revenue?' })
  })

  it('shows the loading state and disables Analyze while a request is running', async () => {
    let finish
    fetchMock.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'What is our total revenue?')

    expect(screen.getByRole('status')).toHaveTextContent('Analyzing your business question...')
    const button = screen.getByRole('button', { name: 'Analyzing…' })
    expect(button).toBeDisabled()

    // Pressing Enter again must not send a second request.
    await user.type(screen.getByLabelText('Your question'), '{Enter}')
    expect(fetchMock).toHaveBeenCalledTimes(1)

    finish(new Response(JSON.stringify(SUCCESS), { status: 200 }))
    expect(await screen.findByRole('button', { name: 'Analyze' })).toBeEnabled()
    expect(screen.getByRole('status')).toHaveTextContent('')
  })

  it('renders the backend error message for an API error', async () => {
    fetchMock.mockReturnValue(jsonResponse(503, {
      error: { code: 'generation_unavailable', message: 'The AI service is unavailable right now. Please try again later.' },
    }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'What is our total revenue?')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Service temporarily unavailable')
    expect(alert).toHaveTextContent('The AI service is unavailable right now. Please try again later.')
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeEnabled()
  })

  it.each([
    [400, 'unsafe_sql', 'Question not accepted'],
    [429, 'rate_limited', 'Too many requests'],
    [502, 'generation_failed', 'AI could not answer'],
    [504, 'query_timeout', 'Query timed out'],
    [500, 'internal_error', 'Unexpected error'],
  ])('shows a clear title for HTTP %i', async (status, code, title) => {
    fetchMock.mockReturnValue(jsonResponse(status, { error: { code, message: 'A safe message.' } }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'Revenue?')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(title)
    expect(alert).toHaveTextContent('A safe message.')
  })

  it('shows the backend rate-limit message and lets the user try again', async () => {
    fetchMock.mockReturnValue(jsonResponse(429, {
      error: { code: 'too_many_requests', message: 'You are asking questions too quickly. Please wait a minute and try again.' },
    }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'Revenue?')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Too many requests')
    expect(alert).toHaveTextContent('You are asking questions too quickly. Please wait a minute and try again.')
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeEnabled()
  })

  it.each([
    [429, 'warning'],
    [503, 'warning'],
    [504, 'warning'],
    [400, 'error'],
    [500, 'error'],
  ])('shows HTTP %i as a %s', async (status, tone) => {
    fetchMock.mockReturnValue(jsonResponse(status, { error: { code: 'x', message: 'A safe message.' } }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'Revenue?')
    expect(await screen.findByRole('alert')).toHaveAttribute('data-tone', tone)
  })

  it('treats an unreachable backend as a temporary problem', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'Revenue?')
    expect(await screen.findByRole('alert')).toHaveAttribute('data-tone', 'warning')
  })

  it('copies the generated SQL to the clipboard', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup() // provides a test clipboard
    render(<App />)
    await ask(user, 'Revenue?')
    await user.click(await screen.findByRole('button', { name: 'Copy SQL' }))
    expect(await navigator.clipboard.readText()).toBe(SUCCESS.sql)
    expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })

  it('says so when the clipboard is not available', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'Revenue?')
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    await user.click(await screen.findByRole('button', { name: 'Copy SQL' }))
    expect(await screen.findByRole('button', { name: 'Copy failed' })).toBeInTheDocument()
  })

  it('does not show raw response bodies that are not in the documented error shape', async () => {
    fetchMock.mockReturnValue(Promise.resolve(new Response('<html>Traceback (most recent call last)</html>', { status: 500 })))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'Revenue?')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong on the server. Please try again.')
    expect(alert).not.toHaveTextContent('Traceback')
  })

  it('shows a friendly message when the backend cannot be reached', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'What is our total revenue?')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Cannot connect to the DataPilot API. Please make sure the backend is running.')
    expect(alert).not.toHaveTextContent('Failed to fetch')
  })

  it('shows the truncated indicator', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, { ...SUCCESS, truncated: true }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'Show every order.')
    expect(await screen.findByText(/Results truncated: only the first 2 rows are shown\./)).toBeInTheDocument()
  })

  it('renders a zero-row result cleanly', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, { ...SUCCESS, rows: [], row_count: 0 }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'Orders from 1990?')

    expect(await screen.findByText('The query ran successfully but returned no rows.')).toBeInTheDocument()
    expect(screen.getByText('0 rows')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText(SUCCESS.sql)).toBeInTheDocument()
  })

  it('clears the previous result and error when a new question starts', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse(200, SUCCESS))
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'First question')
    await screen.findByRole('table')

    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await screen.findByRole('alert')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
