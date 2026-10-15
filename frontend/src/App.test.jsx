import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'
import { PLACEHOLDERS, QUICK_QUESTIONS, STEP_STARTS_MS } from './config/askContent.js'

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
  await user.click(screen.getByRole('button', { name: 'Ask' }))
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
  it('renders the question field and Ask button inside the app shell', () => {
    render(<App />)
    expect(within(screen.getByRole('main')).getByRole('heading', { level: 1, name: 'Welcome back, Saty' })).toBeInTheDocument()
    expect(screen.getByLabelText('Your question')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled()
  })

  it('shows the Ask home before the first question and replaces it with the answer', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup()
    render(<App />)
    expect(screen.getByText('What would you like to know today?')).toBeInTheDocument()

    await ask(user, 'What are the top 2 categories by revenue?')
    expect(await screen.findByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(screen.getByText(SUCCESS.question, { selector: 'p' })).toBeInTheDocument()
    expect(screen.queryByText('What would you like to know today?')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Quick questions' })).not.toBeInTheDocument()
  })

  it('asks a quick question straight away, through the normal question path', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Who are our top customers?' }))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/query$/)
    expect(options.method).toBe('POST')
    expect(JSON.parse(options.body)).toEqual({ question: 'Who are our top customers?' })
    await screen.findByRole('heading', { name: 'Answer' })
    // The question stays in the box, as if it had been typed.
    expect(screen.getByLabelText('Your question')).toHaveValue('Who are our top customers?')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('rejects an empty or whitespace-only question without calling the API', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Ask' }))
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

  it('shows the analysis progress for the question that was sent while the request runs', async () => {
    let finish
    fetchMock.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, '  What is our total revenue?  ')

    expect(screen.getByRole('status')).toHaveTextContent('Analyzing your question…')
    expect(screen.getByRole('heading', { level: 1, name: 'What is our total revenue?' })).toBeInTheDocument()
    expect(screen.getByText('Your question')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Analyzing your question' })).toBeInTheDocument()
    const analysis = screen.getByRole('region', { name: 'Analyzing your question' })
    expect(within(analysis).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Understanding your question',
      'Generating the SQL query',
      'Validating that the query is read-only',
      'Running on the sales database',
      'Preparing the answer',
    ])
    // The question box is replaced while waiting, so it cannot be sent again.
    expect(screen.queryByRole('button', { name: 'Ask' })).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    finish(new Response(JSON.stringify(SUCCESS), { status: 200 }))
    expect(await screen.findByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled()
    expect(screen.getByRole('status')).toHaveTextContent('')
    expect(screen.queryByRole('heading', { name: 'Analyzing your question' })).not.toBeInTheDocument()
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
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled()
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
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled()
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

    await user.click(screen.getByRole('button', { name: 'Ask' }))
    await screen.findByRole('alert')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

describe('app shell', () => {
  function nav() {
    return screen.getByRole('navigation', { name: 'Main menu' })
  }

  function navButton(name) {
    return within(nav()).getByRole('button', { name })
  }

  it('opens on Ask DataPilot with the logo, profile and workspace', () => {
    render(<App />)
    expect(screen.getByRole('img', { name: 'DataPilot' })).toBeInTheDocument()
    expect(navButton('Ask DataPilot')).toHaveAttribute('aria-current', 'page')
    expect(navButton('History')).not.toHaveAttribute('aria-current')
    expect(navButton('Saved Reports')).not.toHaveAttribute('aria-current')
    expect(screen.getByLabelText('Your question')).toBeInTheDocument()

    expect(screen.getByText('Saty')).toBeInTheDocument()
    expect(screen.getByText('Sales')).toBeInTheDocument()
    const header = screen.getByRole('banner')
    expect(within(header).getByText('Stark Industries')).toBeInTheDocument()
    expect(within(header).getByText('Sales workspace')).toBeInTheDocument()
  })

  it('has one navigation, one header and one main landmark, and named buttons', () => {
    render(<App />)
    expect(screen.getAllByRole('navigation')).toHaveLength(1)
    expect(screen.getAllByRole('banner')).toHaveLength(1)
    expect(screen.getAllByRole('main')).toHaveLength(1)
    for (const button of screen.getAllByRole('button')) expect(button).toHaveAccessibleName()
    expect(within(nav()).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Ask DataPilot',
      'History',
      'Saved Reports',
    ])
  })

  it('shows the profile as text, not as a control', () => {
    render(<App />)
    expect(screen.getByText('Saty').closest('button, a, [role="button"], [tabindex]')).toBeNull()
    expect(screen.queryByRole('button', { name: /saty|profile|account|settings/i })).not.toBeInTheDocument()
  })

  it('shows no Saved Reports count before real data is connected', () => {
    render(<App />)
    expect(navButton('Saved Reports').textContent).toBe('Saved Reports')
    expect(nav().textContent).not.toMatch(/\d/)
  })

  it('switches to the History and Saved Reports placeholders and back, without any request', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(navButton('History'))
    expect(screen.getByRole('heading', { level: 1, name: 'History' })).toBeInTheDocument()
    expect(screen.getByText('History will appear here.')).toBeInTheDocument()
    expect(navButton('History')).toHaveAttribute('aria-current', 'page')
    expect(navButton('Ask DataPilot')).not.toHaveAttribute('aria-current')
    expect(screen.queryByLabelText('Your question')).not.toBeInTheDocument()

    await user.click(navButton('Saved Reports'))
    expect(screen.getByRole('heading', { level: 1, name: 'Saved Reports' })).toBeInTheDocument()
    expect(screen.getByText('Saved reports will appear here.')).toBeInTheDocument()
    expect(navButton('Saved Reports')).toHaveAttribute('aria-current', 'page')

    await user.click(navButton('Ask DataPilot'))
    expect(screen.getByLabelText('Your question')).toBeInTheDocument()
    expect(navButton('Ask DataPilot')).toHaveAttribute('aria-current', 'page')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps the answer and the typed question when moving between pages, without asking again', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'What are the top 2 categories by revenue?')
    await screen.findByRole('heading', { name: 'Answer' })
    await user.type(screen.getByLabelText('Your question'), ' And by city?')

    await user.click(navButton('History'))
    await user.click(navButton('Saved Reports'))
    await user.click(navButton('Ask DataPilot'))

    expect(screen.getByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Electronics' })).toBeInTheDocument()
    expect(screen.getByLabelText('Your question')).toHaveValue('What are the top 2 categories by revenue? And by city?')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('lets a question that is still running finish while another page is open', async () => {
    let finish
    fetchMock.mockReturnValue(new Promise((resolve) => (finish = resolve)))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'What are the top 2 categories by revenue?')
    await user.click(navButton('History'))
    finish(new Response(JSON.stringify(SUCCESS), { status: 200 }))
    await user.click(navButton('Ask DataPilot'))
    expect(await screen.findByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('"Ask another question…" opens the Ask page and focuses the question', async () => {
    const user = userEvent.setup()
    render(<App />)
    const askAnother = () => within(screen.getByRole('banner')).getByRole('button', { name: 'Ask another question…' })

    await user.click(navButton('Saved Reports'))
    await user.click(askAnother())
    expect(navButton('Ask DataPilot')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByLabelText('Your question')).toHaveFocus()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('hides "Ask another question…" on the empty Ask page, as in the approved design', async () => {
    const user = userEvent.setup()
    render(<App />)
    expect(within(screen.getByRole('banner')).queryByRole('button', { name: 'Ask another question…' })).not.toBeInTheDocument()
    expect(within(screen.getByRole('banner')).getByText('Stark Industries')).toBeInTheDocument()
    await user.click(navButton('History'))
    expect(within(screen.getByRole('banner')).getByRole('button', { name: 'Ask another question…' })).toBeInTheDocument()
  })

  it('"Ask another question…" on an answer focuses the box, keeps the answer and asks nothing', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'What are the top 2 categories by revenue?')
    await screen.findByRole('heading', { name: 'Answer' })

    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Ask another question…' }))
    expect(screen.getByLabelText('Your question')).toHaveFocus()
    expect(screen.getByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Electronics' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('Log out calls onLogout exactly once and makes no request itself', async () => {
    const onLogout = vi.fn()
    const user = userEvent.setup()
    render(<App onLogout={onLogout} />)
    const logout = screen.getByRole('button', { name: 'Log out' })
    expect(logout).toHaveAttribute('type', 'button')
    await user.click(logout)
    expect(onLogout).toHaveBeenCalledTimes(1)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('Ask page V2', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  function steps() {
    return within(screen.getByRole('region', { name: 'Analyzing your question' })).getAllByRole('listitem')
  }

  function stepStates() {
    return steps().map((item) => item.dataset.state)
  }

  it('shows the greeting, the box with its first placeholder and the four quick questions, and no result', () => {
    render(<App />)
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome back, Saty' })).toBeInTheDocument()
    expect(screen.getByText('What would you like to know today?')).toBeInTheDocument()
    const box = screen.getByLabelText('Your question')
    expect(box.tagName).toBe('TEXTAREA')
    expect(box).toHaveAttribute('placeholder', 'Ask your question…')
    expect(box).toHaveAttribute('maxlength', '500')
    expect(box).toHaveAccessibleDescription('Press Enter to ask, or Shift and Enter for a new line.')

    const quick = within(screen.getByRole('region', { name: 'Quick questions' })).getAllByRole('button')
    expect(quick.map((button) => button.textContent)).toEqual(QUICK_QUESTIONS)
    expect(QUICK_QUESTIONS).toEqual([
      'What is our total revenue?',
      'Show monthly revenue trends.',
      'Which category generated the most revenue?',
      'Who are our top customers?',
    ])

    // No answer of any kind before a question: no numbers, tables, charts or insight.
    const main = screen.getByRole('main')
    expect(main.textContent).not.toMatch(/\d|₹|%/)
    expect(within(main).queryByRole('table')).not.toBeInTheDocument()
    expect(within(main).queryByRole('img')).not.toBeInTheDocument()
  })

  it('Enter asks; Shift+Enter adds a new line without asking', async () => {
    fetchMock.mockReturnValue(jsonResponse(200, SUCCESS))
    const user = userEvent.setup()
    render(<App />)
    const box = screen.getByLabelText('Your question')
    await user.type(box, 'Top categories{Shift>}{Enter}{/Shift}by revenue')
    expect(box).toHaveValue('Top categories\nby revenue')
    expect(fetchMock).not.toHaveBeenCalled()

    await user.type(box, '{Enter}')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ question: 'Top categories\nby revenue' })
  })

  it('keeps questions to 500 characters', async () => {
    const user = userEvent.setup()
    render(<App />)
    const box = screen.getByLabelText('Your question')
    await user.click(box)
    await user.paste('x'.repeat(520))
    expect(box.value).toHaveLength(500)
  })

  it('marks the box invalid for an empty question and clears it when typing', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    const box = screen.getByLabelText('Your question')
    expect(screen.getByRole('alert')).toHaveTextContent('Please enter a business question.')
    expect(box).toHaveAccessibleDescription(/Please enter a business question\./)
    await user.type(box, 'W')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(box).not.toHaveAttribute('aria-invalid')
  })

  it('sends only one request when Enter is pressed twice before the page updates', () => {
    fetchMock.mockReturnValue(new Promise(() => {}))
    render(<App />)
    const box = screen.getByLabelText('Your question')
    fireEvent.change(box, { target: { value: 'What is our total revenue?' } })
    act(() => {
      fireEvent.keyDown(box, { key: 'Enter' })
      fireEvent.keyDown(box, { key: 'Enter' })
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not show the previous answer while a new question is being answered', async () => {
    let finish
    fetchMock.mockReturnValueOnce(jsonResponse(200, SUCCESS))
    fetchMock.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    const user = userEvent.setup()
    render(<App />)
    await ask(user, 'First question')
    await screen.findByRole('table')

    const box = screen.getByLabelText('Your question')
    await user.clear(box)
    await ask(user, 'Second question')
    expect(screen.getByRole('heading', { level: 1, name: 'Second question' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Answer' })).not.toBeInTheDocument()
    expect(screen.queryByText('Electronics')).not.toBeInTheDocument()

    finish(new Response(JSON.stringify({ ...SUCCESS, question: 'Second question' }), { status: 200 }))
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('moves the steps on a timer, holds the last one while waiting and never asks again', async () => {
    vi.useFakeTimers()
    let finish
    fetchMock.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'What is our total revenue?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))

    const bar = () => document.querySelector('.dp-analysis-bar span').style.width
    expect(stepStates()).toEqual(['active', 'todo', 'todo', 'todo', 'todo'])
    expect(steps()[0]).toHaveAttribute('aria-current', 'step')
    expect(steps().filter((step) => step.hasAttribute('aria-current'))).toHaveLength(1)
    expect(bar()).toBe('0%')

    act(() => vi.advanceTimersByTime(STEP_STARTS_MS[0]))
    expect(stepStates()).toEqual(['done', 'active', 'todo', 'todo', 'todo'])
    act(() => vi.advanceTimersByTime(STEP_STARTS_MS[1] - STEP_STARTS_MS[0]))
    expect(stepStates()).toEqual(['done', 'done', 'active', 'todo', 'todo'])

    // Long after the last step starts, it is still the active one and the bar is not full.
    act(() => vi.advanceTimersByTime(60000))
    expect(stepStates()).toEqual(['done', 'done', 'done', 'done', 'active'])
    expect(bar()).toBe('80%')
    expect(screen.getByRole('status')).toHaveTextContent('Analyzing your question…')
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // The answer is shown as soon as it arrives, and the progress timers are gone.
    await act(async () => finish(new Response(JSON.stringify(SUCCESS), { status: 200 })))
    expect(screen.getByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Analyzing your question' })).not.toBeInTheDocument()
    expect(vi.getTimerCount()).toBe(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('shows the answer at once when it arrives before the steps finish', async () => {
    vi.useFakeTimers()
    let finish
    fetchMock.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'What is our total revenue?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    act(() => vi.advanceTimersByTime(100))
    await act(async () => finish(new Response(JSON.stringify(SUCCESS), { status: 200 })))
    expect(screen.getByRole('heading', { name: 'Answer' })).toBeInTheDocument()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops the progress timers when the question fails', async () => {
    vi.useFakeTimers()
    let fail
    fetchMock.mockReturnValue(new Promise((_, reject) => { fail = reject }))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'What is our total revenue?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    await act(async () => fail(new TypeError('Failed to fetch')))
    expect(screen.getByRole('alert')).toHaveTextContent('Cannot reach the API')
    expect(screen.queryByRole('heading', { name: 'Analyzing your question' })).not.toBeInTheDocument()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops the progress timers when the session has expired', async () => {
    vi.useFakeTimers()
    const onSessionExpired = vi.fn()
    let finish
    fetchMock.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    const { unmount } = render(<App onSessionExpired={onSessionExpired} />)
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'What is our total revenue?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    await act(async () => finish(new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'x' } }), { status: 401 })))
    expect(onSessionExpired).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
    unmount()
  })

  it('cycles the placeholder every 3.6 s only while the empty box is not focused', () => {
    vi.useFakeTimers()
    render(<App />)
    const box = screen.getByLabelText('Your question')
    expect(box).toHaveAttribute('placeholder', PLACEHOLDERS[0])
    act(() => vi.advanceTimersByTime(3600))
    expect(box).toHaveAttribute('placeholder', PLACEHOLDERS[1])

    act(() => box.focus())
    act(() => vi.advanceTimersByTime(7200))
    expect(box).toHaveAttribute('placeholder', PLACEHOLDERS[1])

    act(() => box.blur())
    fireEvent.change(box, { target: { value: 'Typed text' } })
    act(() => vi.advanceTimersByTime(7200))
    expect(box).toHaveAttribute('placeholder', PLACEHOLDERS[1])
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps the first placeholder still when reduced motion is preferred', () => {
    vi.useFakeTimers()
    vi.stubGlobal('matchMedia', (query) => ({ matches: query.includes('reduce'), media: query, addEventListener() {}, removeEventListener() {} }))
    render(<App />)
    act(() => vi.advanceTimersByTime(3600 * 3))
    expect(screen.getByLabelText('Your question')).toHaveAttribute('placeholder', 'Ask your question…')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('uses only placeholder examples the store data can answer, with no answers in them', () => {
    expect(PLACEHOLDERS).toEqual([
      'Ask your question…',
      'What is our average order value?',
      'Which customers spend the most?',
      'What is our profit margin?',
      'What are our top 10 products?',
    ])
  })
})
