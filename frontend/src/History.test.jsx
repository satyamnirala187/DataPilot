import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'
import { stubFetch } from './test/fetch.js'

// History through the page, with a mocked API: no backend, Gemini or database calls. The clock is
// fixed (Thu 15 Oct 2026, 10:00 local) and every timestamp is built in local time, so grouping
// holds in any time zone. The rows below are test fixtures only.

const local = (...parts) => new Date(...parts)
const at = (...parts) => local(...parts).toISOString()

const ITEMS = [
  { id: 'a1', question: 'Show monthly revenue trends.', visualization_type: 'line', row_count: 12, truncated: false, created_at: at(2026, 9, 15, 9, 42), saved_report_id: 'r1' },
  { id: 'a2', question: 'Which category generated the most revenue?', visualization_type: 'bar', row_count: 2, truncated: false, created_at: at(2026, 9, 15, 9, 15), saved_report_id: null },
  { id: 'a3', question: 'What is our total revenue?', visualization_type: 'kpi', row_count: 1, truncated: false, created_at: at(2026, 9, 14, 16, 20), saved_report_id: null },
  { id: 'a4', question: 'Show every order.', visualization_type: 'table', row_count: 500, truncated: true, created_at: at(2026, 9, 12, 14, 48), saved_report_id: null },
  { id: 'a5', question: 'Who are our top customers?', visualization_type: 'bar', row_count: 10, truncated: false, created_at: at(2026, 8, 8, 9, 0), saved_report_id: 'r2' },
]

const STORED = {
  id: 'a2',
  question: 'Which category generated the most revenue?',
  sql: 'SELECT category_name, revenue FROM stored LIMIT 500',
  columns: ['category_name', 'revenue'],
  rows: [['Electronics', 7371769.52], ['Apparel', 4012345.25]],
  row_count: 2,
  truncated: false,
  visualization: { type: 'bar', x_key: 'category_name', y_key: 'revenue' },
  insight: 'Stored insight text.',
  created_at: at(2026, 9, 15, 9, 15),
  saved_report: null,
}

const FRESH = {
  question: 'How many customers do we have?',
  sql: 'SELECT COUNT(*) AS customer_count FROM customers',
  columns: ['customer_count'],
  rows: [[1000]],
  row_count: 1,
  truncated: false,
  visualization: { type: 'kpi', y_key: 'customer_count' },
  insight: null,
  analysis_id: 'fresh-1',
}

let fetchMock
let savedListMock

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(local(2026, 9, 15, 10, 0))
  fetchMock = vi.fn()
  savedListMock = stubFetch(fetchMock)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function respond(status, body) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

function errorBody(code, message) {
  return { error: { code, message } }
}

// Answers by "METHOD /path"; anything unexpected fails the test loudly.
function route(handlers) {
  fetchMock.mockImplementation((url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${new URL(url).pathname}`
    if (!handlers[key]) throw new Error(`Unexpected request: ${key}`)
    return handlers[key](url, options)
  })
}

function calls(method, path) {
  return fetchMock.mock.calls.filter(([url, options = {}]) => (options.method ?? 'GET') === method && new URL(url).pathname === path)
}

function navButton(name) {
  return within(screen.getByRole('navigation')).getByRole('button', { name })
}

function row(question) {
  return screen.getByRole('button', { name: new RegExp(`^${question.replace(/[.?]/g, '\\$&')}`) })
}

async function openHistory(user, handlers = {}) {
  route({ 'GET /history': () => respond(200, { items: ITEMS }), ...handlers })
  await user.click(navButton('History'))
  await screen.findByRole('heading', { level: 1, name: 'History' })
}

describe('opening History', () => {
  it('requests the newest 100 analyses once, shows a loader, then the rows', async () => {
    let finish
    route({ 'GET /history': () => new Promise((resolve) => (finish = resolve)) })
    const user = userEvent.setup()
    render(<App />)
    await user.click(navButton('History'))

    expect(screen.getByRole('status')).toHaveTextContent('Loading history…')
    expect(screen.queryByRole('heading', { name: 'History' })).not.toBeInTheDocument()
    expect(calls('GET', '/history')).toHaveLength(1)
    expect(new URL(fetchMock.mock.calls[0][0]).search).toBe('?limit=100')

    await act(async () => finish(new Response(JSON.stringify({ items: ITEMS }), { status: 200 })))
    expect(screen.getByRole('heading', { level: 1, name: 'History' })).toBeInTheDocument()
    expect(screen.getByText('Completed analyses are recorded here automatically.')).toBeInTheDocument()
    expect(row('Show monthly revenue trends.')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('loads the list again on every visit, and never on other pages', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user)
    await user.click(navButton('Ask DataPilot'))
    await user.click(navButton('Saved Reports'))
    expect(calls('GET', '/history')).toHaveLength(1)
    await user.click(navButton('History'))
    await screen.findByRole('heading', { level: 1, name: 'History' })
    expect(calls('GET', '/history')).toHaveLength(2)
    // Nothing else was requested; Saved Reports read only its own list (on opening and on its page).
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(savedListMock).toHaveBeenCalledTimes(2)
  })
})

describe('the list', () => {
  it('groups rows by local day in display order, keeping the backend order inside each group', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user)
    const groups = screen.getAllByRole('heading', { level: 2 }).map((heading) => {
      const list = heading.parentElement.querySelector('ul')
      return [heading.textContent, within(list).getAllByRole('button').map((button) => button.querySelector('time').textContent)]
    })
    expect(groups).toEqual([
      ['Today', ['09:42', '09:15']],
      ['Yesterday', ['16:20']],
      ['Earlier this week', ['Mon, 14:48']],
      ['Earlier', ['8 Sep 2026']],
    ])
  })

  it('describes each row only with what the list returns', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user)
    expect(row('Show monthly revenue trends.')).toHaveTextContent('Line chart · 12 rows')
    expect(row('What is our total revenue?')).toHaveTextContent('KPI · 1 row')
    expect(row('Show every order.')).toHaveTextContent('Table · 500 rows · Limited result')
    expect(row('Who are our top customers?')).toHaveTextContent('Bar chart · 10 rows')
    // No answer summaries: the list API has none.
    expect(screen.queryByText(/₹|reached|highest/)).not.toBeInTheDocument()
  })

  it('shows "Saved" only for rows with a saved report, as a label rather than a control', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user)
    const saved = screen.getAllByText('Saved')
    expect(saved).toHaveLength(2)
    expect(saved.map((label) => label.closest('button').textContent)).toEqual([
      expect.stringContaining('Show monthly revenue trends.'),
      expect.stringContaining('Who are our top customers?'),
    ])
    expect(screen.queryByRole('button', { name: 'Saved' })).not.toBeInTheDocument()
    expect(row('Which category generated the most revenue?')).not.toHaveTextContent('Saved')
  })

  it('shows questions as text, never as markup', async () => {
    const user = userEvent.setup()
    render(<App />)
    const question = '<img src=x onerror="alert(1)"> revenue?'
    await openHistory(user, { 'GET /history': () => respond(200, { items: [{ ...ITEMS[0], question }] }) })
    expect(screen.getByText(question)).toBeInTheDocument()
    expect(document.querySelector('.dp-history img')).toBeNull()
  })

  it('handles unknown types and unreadable times without crashing', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user, {
      'GET /history': () => respond(200, { items: [{ id: 'x', question: 'Odd one', visualization_type: 'pie', row_count: 3, truncated: false, created_at: '2026-13-45T10:00:00Z', saved_report_id: null }] }),
    })
    expect(row('Odd one')).toHaveTextContent('Result · 3 rows')
    expect(row('Odd one')).toHaveTextContent('Time unavailable')
    expect(screen.getByRole('heading', { level: 2, name: 'Earlier' })).toBeInTheDocument()
  })

  it('shows a quiet empty state, with no search, when nothing has been recorded yet', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user, { 'GET /history': () => respond(200, { items: [] }) })
    expect(screen.getByRole('heading', { name: 'No analyses yet' })).toBeInTheDocument()
    expect(screen.getByText('Questions you ask in DataPilot will appear here once they are answered.')).toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(screen.queryByText(/only you/i)).not.toBeInTheDocument()
  })
})

describe('search', () => {
  it('filters the loaded rows by question, ignoring case, without any request', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user)
    const search = screen.getByRole('searchbox', { name: 'Search history' })

    await user.type(search, '  REVENUE ')
    const shown = within(document.querySelector('.dp-history-groups')).getAllByRole('button').map((button) => button.querySelector('.dp-history-question').textContent)
    expect(shown).toEqual(['Show monthly revenue trends.', 'Which category generated the most revenue?', 'What is our total revenue?'])
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual(['Today', 'Yesterday'])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('says when nothing matches, and brings every row back when cleared', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user)
    const search = screen.getByRole('searchbox', { name: 'Search history' })
    await user.type(search, 'payroll')
    expect(screen.getByRole('status')).toHaveTextContent('No analyses match “payroll”.')
    expect(screen.queryAllByRole('heading', { level: 2 })).toHaveLength(0)

    await user.clear(search)
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(4)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('reopening an analysis', () => {
  it('reads the stored snapshot and shows it in the shared result view, without asking again', async () => {
    let finish
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user, { 'GET /history/a2': () => new Promise((resolve) => (finish = resolve)) })

    await user.click(row('Which category generated the most revenue?'))
    expect(screen.getByRole('status')).toHaveTextContent('Opening analysis…')
    expect(screen.getByRole('button', { name: 'Back to History' })).toHaveFocus()

    await act(async () => finish(new Response(JSON.stringify(STORED), { status: 200 })))
    // The same Back button keeps the focus once the analysis has loaded.
    expect(screen.getByRole('button', { name: 'Back to History' })).toHaveFocus()
    expect(screen.getByRole('heading', { level: 1, name: STORED.question })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Data' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Electronics' })).toBeInTheDocument()
    expect(screen.getByText('Stored insight text.')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /^Bar chart of revenue by category name/ })).toBeInTheDocument()

    expect(calls('GET', '/history/a2')).toHaveLength(1)
    expect(calls('POST', '/query')).toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledTimes(2) // the list and this one analysis
  })

  it('goes back to the same list and search, without loading it again, and returns focus to the row', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user, { 'GET /history/a2': () => respond(200, STORED) })
    await user.type(screen.getByRole('searchbox', { name: 'Search history' }), 'category')
    await user.click(row('Which category generated the most revenue?'))
    await screen.findByRole('heading', { name: 'Data' })

    await user.click(screen.getByRole('button', { name: 'Back to History' }))
    expect(screen.getByRole('searchbox', { name: 'Search history' })).toHaveValue('category')
    expect(row('Which category generated the most revenue?')).toHaveFocus()
    expect(calls('GET', '/history')).toHaveLength(1)
  })

  it('closes an open analysis when History is chosen in the sidebar, without reloading the list', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user, { 'GET /history/a2': () => respond(200, STORED) })
    await user.click(row('Which category generated the most revenue?'))
    await screen.findByRole('heading', { name: 'Data' })

    await user.click(navButton('History'))
    expect(screen.getByRole('heading', { level: 1, name: 'History' })).toBeInTheDocument()
    expect(calls('GET', '/history')).toHaveLength(1)
  })

  it('never replaces the Ask page answer', async () => {
    const user = userEvent.setup()
    render(<App />)
    route({ 'POST /query': () => respond(200, FRESH) })
    await user.type(screen.getByLabelText('Your question'), FRESH.question)
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    await screen.findByRole('heading', { level: 1, name: FRESH.question })

    await openHistory(user, { 'POST /query': () => respond(200, FRESH), 'GET /history/a2': () => respond(200, STORED) })
    await user.click(row('Which category generated the most revenue?'))
    await screen.findByRole('cell', { name: 'Electronics' })

    await user.click(navButton('Ask DataPilot'))
    expect(screen.getByRole('heading', { level: 1, name: FRESH.question })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '1,000' })).toBeInTheDocument()
    expect(screen.queryByText('Electronics')).not.toBeInTheDocument()
    expect(calls('POST', '/query')).toHaveLength(1)
  })
})

describe('errors', () => {
  it('ends the session when the list request gets 401', async () => {
    const onSessionExpired = vi.fn()
    route({ 'GET /history': () => respond(401, errorBody('unauthorized', 'x')) })
    const user = userEvent.setup()
    render(<App onSessionExpired={onSessionExpired} />)
    await user.click(navButton('History'))
    await vi.waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1))
  })

  it('ends the session when reopening gets 401', async () => {
    const onSessionExpired = vi.fn()
    const user = userEvent.setup()
    render(<App onSessionExpired={onSessionExpired} />)
    await openHistory(user, { 'GET /history/a2': () => respond(401, errorBody('unauthorized', 'x')) })
    await user.click(row('Which category generated the most revenue?'))
    await vi.waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1))
  })

  it.each([
    [429, 'too_many_requests', 'Too many requests. Please wait a moment and try again.', 'Too many requests'],
    [503, 'history_unavailable', 'History is not available right now. Please try again later.', 'Service temporarily unavailable'],
  ])('shows HTTP %i (%s) as a temporary problem that can be retried', async (status, code, message, title) => {
    const user = userEvent.setup()
    render(<App />)
    route({ 'GET /history': () => respond(status, errorBody(code, message)) })
    await user.click(navButton('History'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'warning')
    expect(within(alert).getByRole('heading', { name: title })).toBeInTheDocument()
    expect(alert).toHaveTextContent(message)

    route({ 'GET /history': () => respond(200, { items: ITEMS }) })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: /^Show monthly revenue trends\./ })).toBeInTheDocument()
    expect(calls('GET', '/history')).toHaveLength(2)
  })

  it('shows an unreachable backend safely, without the browser error text', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const user = userEvent.setup()
    render(<App />)
    await user.click(navButton('History'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'warning')
    expect(alert).toHaveTextContent('Cannot connect to the DataPilot API.')
    expect(alert).not.toHaveTextContent('Failed to fetch')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('never shows raw server text', async () => {
    route({ 'GET /history': () => Promise.resolve(new Response('psycopg.OperationalError: connection to 10.0.0.1 failed', { status: 500 })) })
    const user = userEvent.setup()
    render(<App />)
    await user.click(navButton('History'))
    const alert = await screen.findByRole('alert')
    expect(alert).not.toHaveTextContent(/psycopg|10\.0\.0\.1/)
  })

  it('says a missing analysis was not found, offers no retry, and keeps the list', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user, { 'GET /history/a2': () => respond(404, errorBody('analysis_not_found', 'This analysis was not found.')) })
    await user.click(row('Which category generated the most revenue?'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This analysis was not found.')
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Back to History' }))
    expect(row('Show monthly revenue trends.')).toBeInTheDocument()
    expect(calls('GET', '/history')).toHaveLength(1)
  })

  it('lets a failed reopen be retried', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user, { 'GET /history/a2': () => respond(503, errorBody('history_unavailable', 'History is not available right now. Please try again later.')) })
    await user.click(row('Which category generated the most revenue?'))
    expect(await screen.findByRole('alert')).toHaveAttribute('data-tone', 'warning')

    route({ 'GET /history/a2': () => respond(200, STORED) })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('cell', { name: 'Electronics' })).toBeInTheDocument()
    expect(calls('POST', '/query')).toHaveLength(0)
  })
})

describe('page structure', () => {
  it('has one main, a labelled search, headings under the page title and rows as buttons', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user)
    expect(screen.getAllByRole('main')).toHaveLength(1)
    expect(navButton('History')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('searchbox', { name: 'Search history' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'History' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(4)
    expect(document.querySelectorAll('.dp-history-row')).toHaveLength(ITEMS.length)
    for (const button of document.querySelectorAll('.dp-history-row')) expect(button.tagName).toBe('BUTTON')
  })

  it('"Ask another question…" leaves History for the question box, asking nothing', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openHistory(user)
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Ask another question…' }))
    expect(screen.getByLabelText('Your question')).toHaveFocus()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
