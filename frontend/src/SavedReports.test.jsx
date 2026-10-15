import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'

// Saved Reports, saving and the sidebar count, through the page with a mocked API: no backend,
// Gemini or database calls. The clock is fixed (Thu 15 Oct 2026, 10:00 local) and timestamps are
// built in local time. Everything below is a test fixture.

const local = (...parts) => new Date(...parts)
const at = (...parts) => local(...parts).toISOString()

const REPORTS = [
  { id: 'r1', analysis_id: 'a1', title: 'Monthly revenue trend', question: 'Show monthly revenue trends.', visualization_type: 'line', row_count: 12, truncated: false, saved_at: at(2026, 9, 8, 12, 0), analysis_created_at: at(2026, 9, 8, 11, 0) },
  { id: 'r2', analysis_id: 'a5', title: 'Top customers', question: 'Who are our top customers?', visualization_type: 'bar', row_count: 10, truncated: false, saved_at: at(2026, 8, 29, 12, 0), analysis_created_at: at(2026, 8, 29, 11, 0) },
  { id: 'r3', analysis_id: 'a6', title: 'Every order', question: 'Show every order.', visualization_type: 'table', row_count: 500, truncated: true, saved_at: 'not a date', analysis_created_at: 'not a date' },
]

const REPORT_DETAIL = {
  id: 'r1',
  title: 'Monthly revenue trend',
  saved_at: at(2026, 9, 8, 12, 0),
  analysis: {
    id: 'a1',
    question: 'Show monthly revenue trends.',
    sql: 'SELECT month, revenue FROM stored LIMIT 500',
    columns: ['month', 'revenue'],
    rows: [['2026-04-01', 600000], ['2026-05-01', 800000]],
    row_count: 2,
    truncated: false,
    visualization: { type: 'line', x_key: 'month', y_key: 'revenue' },
    insight: 'Stored insight text.',
    created_at: at(2026, 9, 8, 11, 0),
  },
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

const HISTORY_ITEM = { id: 'h1', question: 'Which category generated the most revenue?', visualization_type: 'bar', row_count: 2, truncated: false, created_at: at(2026, 9, 15, 9, 15), saved_report_id: null }
const STORED = {
  id: 'h1',
  question: 'Which category generated the most revenue?',
  sql: 'SELECT category_name, revenue FROM stored LIMIT 500',
  columns: ['category_name', 'revenue'],
  rows: [['Electronics', 7371769.52], ['Apparel', 4012345.25]],
  row_count: 2,
  truncated: false,
  visualization: { type: 'bar', x_key: 'category_name', y_key: 'revenue' },
  insight: null,
  created_at: at(2026, 9, 15, 9, 15),
  saved_report: null,
}

const SAVED = { id: 'r9', analysis_id: 'fresh-1', title: 'Customer count', saved_at: at(2026, 9, 15, 10, 0) }

let fetchMock
let handlers

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(local(2026, 9, 15, 10, 0))
  handlers = { 'GET /saved-reports': () => respond(200, { items: REPORTS }) }
  // Answers by "METHOD /path"; anything unexpected fails loudly.
  fetchMock = vi.fn((url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${new URL(url).pathname}`
    if (!handlers[key]) throw new Error(`Unexpected request: ${key}`)
    return handlers[key](url, options)
  })
  vi.stubGlobal('fetch', fetchMock)
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

function route(more) {
  handlers = { ...handlers, ...more }
}

function calls(method, path) {
  return fetchMock.mock.calls.filter(([url, options = {}]) => (options.method ?? 'GET') === method && new URL(url).pathname === path)
}

function navButton(name) {
  return within(screen.getByRole('navigation')).getByRole('button', { name })
}

function badge() {
  return navButton('Saved Reports').querySelector('.dp-nav-badge')
}

function reportRow(title) {
  return screen.getByRole('button', { name: new RegExp(`^${title}`) })
}

async function settle() {
  await act(async () => {})
}

async function openSaved(user) {
  await user.click(navButton('Saved Reports'))
  await screen.findByRole('heading', { level: 1, name: 'Saved Reports' })
}

async function askFresh(user) {
  route({ 'POST /query': () => respond(200, FRESH) })
  await user.type(screen.getByLabelText('Your question'), FRESH.question)
  await user.click(screen.getByRole('button', { name: 'Ask' }))
  await screen.findByRole('heading', { level: 1, name: FRESH.question })
}

async function openSaveDialog(user) {
  await user.click(screen.getByRole('button', { name: 'Save Report' }))
  return screen.getByRole('dialog', { name: 'Save report' })
}

describe('sidebar count', () => {
  it('reads the saved list once when the app opens and shows the real count, out of the nav name', async () => {
    render(<App />)
    await settle()
    expect(calls('GET', '/saved-reports')).toHaveLength(1)
    expect(new URL(fetchMock.mock.calls[0][0]).search).toBe('?limit=50')
    expect(badge()).toHaveTextContent('3')
    expect(badge()).toHaveAttribute('aria-hidden', 'true')
    expect(navButton('Saved Reports')).toBeInTheDocument() // the accessible name is unchanged
  })

  it('shows no badge with no saved reports', async () => {
    route({ 'GET /saved-reports': () => respond(200, { items: [] }) })
    render(<App />)
    await settle()
    expect(badge()).toBeNull()
  })

  it.each([
    [49, '49'],
    [50, '50+'], // a full page of 50 may mean more: never shown as exactly 50
  ])('shows %i reports as "%s"', async (count, text) => {
    const items = Array.from({ length: count }, (_, i) => ({ ...REPORTS[0], id: `r${i}`, analysis_id: `a${i}` }))
    route({ 'GET /saved-reports': () => respond(200, { items }) })
    render(<App />)
    await settle()
    expect(badge()).toHaveTextContent(text)
  })

  it('shows no badge and no error while working elsewhere when the count cannot be read', async () => {
    route({ 'GET /saved-reports': () => respond(503, errorBody('history_unavailable', 'History is not available right now. Please try again later.')) })
    render(<App />)
    await settle()
    expect(badge()).toBeNull()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('ends the session when the count request gets 401', async () => {
    const onSessionExpired = vi.fn()
    route({ 'GET /saved-reports': () => respond(401, errorBody('unauthorized', 'x')) })
    render(<App onSessionExpired={onSessionExpired} />)
    await vi.waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1))
  })
})

describe('Saved Reports page', () => {
  it('shows a loader, then the reports, loading the list once per visit', async () => {
    const user = userEvent.setup()
    render(<App />)
    await settle()
    let finish
    route({ 'GET /saved-reports': () => new Promise((resolve) => (finish = resolve)) })
    await user.click(navButton('Saved Reports'))
    expect(screen.getByRole('status')).toHaveTextContent('Loading saved reports…')
    await act(async () => finish(new Response(JSON.stringify({ items: REPORTS }), { status: 200 })))
    expect(reportRow('Monthly revenue trend')).toBeInTheDocument()
    expect(calls('GET', '/saved-reports')).toHaveLength(2) // opening the app, then this visit

    route({ 'GET /saved-reports': () => respond(200, { items: REPORTS }) })
    await user.click(navButton('Ask DataPilot'))
    await user.click(navButton('History'))
    expect(calls('GET', '/saved-reports')).toHaveLength(2)
    await openSaved(user)
    expect(calls('GET', '/saved-reports')).toHaveLength(3)
  })

  it('shows each report only with what the list returns, and no remove, rename or export', async () => {
    route({ 'GET /history': () => respond(200, { items: [] }) })
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    const row = reportRow('Monthly revenue trend')
    expect(row).toHaveTextContent('Show monthly revenue trends.')
    expect(row).toHaveTextContent('Line chart · 12 rows')
    expect(row).toHaveTextContent('8 Oct 2026')
    expect(reportRow('Top customers')).toHaveTextContent('29 Sep 2026')
    expect(reportRow('Every order')).toHaveTextContent('Table · 500 rows · Limited result')
    expect(reportRow('Every order')).toHaveTextContent('Date unavailable')
    expect(screen.queryByRole('button', { name: /export|remove|delete|rename/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/only you/i)).not.toBeInTheDocument()
  })

  it('says when the list may be only the most recent 50', async () => {
    const items = Array.from({ length: 50 }, (_, i) => ({ ...REPORTS[0], id: `r${i}`, analysis_id: `a${i}`, title: `Report ${i}` }))
    route({ 'GET /saved-reports': () => respond(200, { items }) })
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    expect(screen.getByText('Showing the 50 most recently saved')).toBeInTheDocument()
  })

  it('shows a quiet empty state, with no search, when nothing is saved', async () => {
    route({ 'GET /saved-reports': () => respond(200, { items: [] }) })
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    expect(screen.getByRole('heading', { name: 'No saved reports yet' })).toBeInTheDocument()
    expect(screen.getByText('Use Save Report on a completed analysis to keep it here.')).toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
  })

  it('searches titles and questions on the page, without any request', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    const before = fetchMock.mock.calls.length
    const search = screen.getByRole('searchbox', { name: 'Search saved reports' })

    await user.type(search, '  CUSTOMERS ')
    expect(screen.getAllByRole('button', { name: /^(Monthly|Top|Every)/ }).map((b) => b.querySelector('.dp-saved-title').textContent)).toEqual(['Top customers'])
    await user.clear(search)
    await user.type(search, 'trend')
    expect(screen.getAllByRole('button', { name: /^(Monthly|Top|Every)/ })).toHaveLength(1)
    await user.clear(search)
    await user.type(search, 'payroll')
    expect(screen.getByRole('status')).toHaveTextContent('No saved reports match “payroll”.')
    await user.clear(search)
    expect(screen.getAllByRole('button', { name: /^(Monthly|Top|Every)/ })).toHaveLength(3)
    expect(fetchMock.mock.calls.length).toBe(before)
  })
})

describe('reopening a saved report', () => {
  it('reads it once and shows the stored analysis in the shared result view, with its title, already saved', async () => {
    let finish
    route({ 'GET /saved-reports/r1': () => new Promise((resolve) => (finish = resolve)) })
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    await user.click(reportRow('Monthly revenue trend'))
    expect(screen.getByRole('status')).toHaveTextContent('Opening saved report…')
    expect(screen.getByRole('button', { name: 'Back to Saved Reports' })).toHaveFocus()

    await act(async () => finish(new Response(JSON.stringify(REPORT_DETAIL), { status: 200 })))
    expect(screen.getByRole('button', { name: 'Back to Saved Reports' })).toHaveFocus()
    // The report title is metadata; the heading stays the original question.
    expect(screen.getByText('Monthly revenue trend', { selector: '.dp-report-meta-title' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Show monthly revenue trends.' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Data' })).toBeInTheDocument()
    expect(screen.getByText('Stored insight text.')).toBeInTheDocument()
    expect(screen.getByText('Saved', { selector: '.dp-saved-state' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save Report' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Saved' })).not.toBeInTheDocument()

    expect(calls('GET', '/saved-reports/r1')).toHaveLength(1)
    expect(calls('POST', '/query')).toHaveLength(0)
  })

  it('goes back to the same list and search without loading it again, focusing the row', async () => {
    route({ 'GET /saved-reports/r1': () => respond(200, REPORT_DETAIL) })
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    await user.type(screen.getByRole('searchbox', { name: 'Search saved reports' }), 'trend')
    await user.click(reportRow('Monthly revenue trend'))
    await screen.findByRole('heading', { name: 'Data' })
    const lists = calls('GET', '/saved-reports').length

    await user.click(screen.getByRole('button', { name: 'Back to Saved Reports' }))
    expect(screen.getByRole('searchbox', { name: 'Search saved reports' })).toHaveValue('trend')
    expect(reportRow('Monthly revenue trend')).toHaveFocus()
    expect(calls('GET', '/saved-reports')).toHaveLength(lists)
  })

  it('closes an open report when Saved Reports is chosen in the sidebar, without reloading', async () => {
    route({ 'GET /saved-reports/r1': () => respond(200, REPORT_DETAIL) })
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    await user.click(reportRow('Monthly revenue trend'))
    await screen.findByRole('heading', { name: 'Data' })
    const lists = calls('GET', '/saved-reports').length
    await user.click(navButton('Saved Reports'))
    expect(reportRow('Monthly revenue trend')).toBeInTheDocument()
    expect(calls('GET', '/saved-reports')).toHaveLength(lists)
  })

  it('never replaces the Ask page answer', async () => {
    route({ 'GET /saved-reports/r1': () => respond(200, REPORT_DETAIL) })
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    await openSaved(user)
    await user.click(reportRow('Monthly revenue trend'))
    await screen.findByRole('heading', { level: 1, name: 'Show monthly revenue trends.' })
    await user.click(navButton('Ask DataPilot'))
    expect(screen.getByRole('heading', { level: 1, name: FRESH.question })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '1,000' })).toBeInTheDocument()
    expect(calls('POST', '/query')).toHaveLength(1)
  })
})

describe('Saved Reports errors', () => {
  it.each([
    [429, 'too_many_requests', 'Too many requests. Please wait a moment and try again.', 'Too many requests', 'Too many requests. Please wait a moment and try again.'],
    [503, 'history_unavailable', 'History is not available right now. Please try again later.', 'Saved Reports unavailable', 'Saved Reports are not available right now. Please try again later.'],
  ])('shows HTTP %i (%s) on the list as temporary, with a retry that loads the list once', async (status, code, sent, title, shown) => {
    const user = userEvent.setup()
    render(<App />)
    await settle()
    route({ 'GET /saved-reports': () => respond(status, errorBody(code, sent)) })
    await user.click(navButton('Saved Reports'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'warning')
    expect(within(alert).getByRole('heading', { name: title })).toBeInTheDocument()
    expect(alert).toHaveTextContent(shown)
    expect(alert).not.toHaveTextContent('History is not available')

    route({ 'GET /saved-reports': () => respond(200, { items: REPORTS }) })
    const lists = calls('GET', '/saved-reports').length
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: /^Monthly revenue trend/ })).toBeInTheDocument()
    expect(calls('GET', '/saved-reports')).toHaveLength(lists + 1)
  })

  it.each([
    ['history_unavailable', () => respond(503, errorBody('history_unavailable', 'History is not available right now. Please try again later.'))],
    ['a 503 with no usable body', () => Promise.resolve(new Response('Service Unavailable', { status: 503 }))],
  ])('shows an unavailable store (%s) on an opened report in Saved Reports wording, and retries once', async (_name, fail) => {
    route({ 'GET /saved-reports/r1': fail })
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    await user.click(reportRow('Monthly revenue trend'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'warning')
    expect(within(alert).getByRole('heading', { name: 'Saved Reports unavailable' })).toBeInTheDocument()
    expect(alert).toHaveTextContent('Saved Reports are not available right now. Please try again later.')
    expect(alert).not.toHaveTextContent('History is not available')
    expect(screen.getByRole('button', { name: 'Back to Saved Reports' })).toHaveFocus()

    route({ 'GET /saved-reports/r1': () => respond(200, REPORT_DETAIL) })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Data' })).toBeInTheDocument()
    expect(calls('GET', '/saved-reports/r1')).toHaveLength(2)
  })

  it('shows an unreachable backend safely', async () => {
    const user = userEvent.setup()
    render(<App />)
    await settle()
    route({ 'GET /saved-reports': () => Promise.reject(new TypeError('Failed to fetch')) })
    await user.click(navButton('Saved Reports'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Cannot connect to the DataPilot API.')
    expect(alert).not.toHaveTextContent('Failed to fetch')
  })

  it('never shows raw server text', async () => {
    const user = userEvent.setup()
    render(<App />)
    await settle()
    route({ 'GET /saved-reports': () => Promise.resolve(new Response('psycopg.errors.UniqueViolation: saved_reports_pkey', { status: 500 })) })
    await user.click(navButton('Saved Reports'))
    expect(await screen.findByRole('alert')).not.toHaveTextContent(/psycopg|pkey/)
  })

  it('says a missing report was not found, offers no retry and keeps the list', async () => {
    route({ 'GET /saved-reports/r1': () => respond(404, errorBody('report_not_found', 'This saved report was not found.')) })
    const user = userEvent.setup()
    render(<App />)
    await openSaved(user)
    await user.click(reportRow('Monthly revenue trend'))
    expect(await screen.findByRole('alert')).toHaveTextContent('This saved report was not found.')
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Back to Saved Reports' }))
    expect(reportRow('Top customers')).toBeInTheDocument()
  })

  it('ends the session when reopening gets 401', async () => {
    const onSessionExpired = vi.fn()
    route({ 'GET /saved-reports/r1': () => respond(401, errorBody('unauthorized', 'x')) })
    const user = userEvent.setup()
    render(<App onSessionExpired={onSessionExpired} />)
    await openSaved(user)
    await user.click(reportRow('Monthly revenue trend'))
    await vi.waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1))
  })
})

describe('Save Report dialog', () => {
  it('opens from a fresh answer with the question as an editable, selected title, and saves nothing yet', async () => {
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const dialog = await openSaveDialog(user)
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const input = within(dialog).getByLabelText('Report title')
    expect(input).toHaveValue(FRESH.question)
    expect(input).toHaveFocus()
    expect(input.selectionStart).toBe(0)
    expect(input.selectionEnd).toBe(FRESH.question.length)
    expect(calls('POST', '/saved-reports')).toHaveLength(0)
  })

  it.each([
    ['Cancel', (user) => user.click(screen.getByRole('button', { name: 'Cancel' }))],
    ['Escape', (user) => user.keyboard('{Escape}')],
  ])('closes with %s, saving nothing and returning focus to Save Report', async (_name, close) => {
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    await openSaveDialog(user)
    await close(user)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save Report' })).toHaveFocus()
    expect(calls('POST', '/saved-reports')).toHaveLength(0)
  })

  it('keeps Tab inside the dialog', async () => {
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const dialog = await openSaveDialog(user)
    await user.tab()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await user.tab()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toHaveFocus()
    await user.tab()
    expect(within(dialog).getByLabelText('Report title')).toHaveFocus()
    await user.tab({ shift: true })
    expect(within(dialog).getByRole('button', { name: 'Save' })).toHaveFocus()
  })

  it.each([
    ['', 'Enter a title for this report.'],
    ['    ', 'Enter a title for this report.'],
    ['x'.repeat(121), 'Titles can be up to 120 characters. This one has 121.'],
    ['Tab\there', 'Titles cannot contain tabs or line breaks.'],
  ])('blocks the title %j with a message and sends nothing', async (title, message) => {
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const dialog = await openSaveDialog(user)
    const input = within(dialog).getByLabelText('Report title')
    fireEvent.change(input, { target: { value: title } })
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(within(dialog).getByText(message)).toBeInTheDocument()
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(calls('POST', '/saved-reports')).toHaveLength(0)
  })

  it('counts characters as the backend does: 120 emoji are a valid title', async () => {
    route({ 'POST /saved-reports': () => respond(201, SAVED) })
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const dialog = await openSaveDialog(user)
    fireEvent.change(within(dialog).getByLabelText('Report title'), { target: { value: '📈'.repeat(120) } })
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await vi.waitFor(() => expect(calls('POST', '/saved-reports')).toHaveLength(1))
  })

  it('points out at once when a long question makes the prefilled title too long', async () => {
    const long = { ...FRESH, question: `${'Revenue by month '.repeat(8)}?` }
    route({ 'POST /query': () => respond(200, long) })
    const user = userEvent.setup()
    render(<App />)
    await user.type(screen.getByLabelText('Your question'), 'long')
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    await screen.findByRole('heading', { level: 1, name: long.question })
    const dialog = await openSaveDialog(user)
    expect(within(dialog).getByText(/Titles can be up to 120 characters/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Report title')).toHaveValue(long.question) // never cut short
  })
})

describe('saving', () => {
  it('sends one save with the analysis id and the edited title, then shows Saved and updates the count', async () => {
    route({
      'POST /saved-reports': () => {
        handlers['GET /saved-reports'] = () => respond(200, { items: [{ ...REPORTS[0], id: 'r9', analysis_id: 'fresh-1' }, ...REPORTS] })
        return respond(201, SAVED)
      },
    })
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    expect(badge()).toHaveTextContent('3')
    const dialog = await openSaveDialog(user)
    const input = within(dialog).getByLabelText('Report title')
    await user.clear(input)
    await user.type(input, '  Customer count  ')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const saves = calls('POST', '/saved-reports')
    expect(saves).toHaveLength(1)
    expect(JSON.parse(saves[0][1].body)).toEqual({ analysis_id: 'fresh-1', title: 'Customer count' })
    const saved = screen.getByText('Saved', { selector: '.dp-saved-state' })
    expect(saved).toHaveFocus()
    expect(screen.queryByRole('button', { name: 'Save Report' })).not.toBeInTheDocument()
    expect(screen.getByText('Saved to Saved Reports.')).toBeInTheDocument()
    await vi.waitFor(() => expect(badge()).toHaveTextContent('4'))
    expect(calls('GET', '/saved-reports')).toHaveLength(2) // opening the app, then after the save
    expect(calls('POST', '/query')).toHaveLength(1)
    // Still on the answer, not moved to Saved Reports.
    expect(navButton('Ask DataPilot')).toHaveAttribute('aria-current', 'page')
  })

  it('sends only one save when Save is pressed twice before the page updates', async () => {
    route({ 'POST /saved-reports': () => new Promise(() => {}) })
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const dialog = await openSaveDialog(user)
    const form = within(dialog).getByLabelText('Report title').closest('form')
    act(() => {
      fireEvent.submit(form)
      fireEvent.submit(form)
    })
    expect(calls('POST', '/saved-reports')).toHaveLength(1)
    expect(within(dialog).getByRole('button', { name: 'Saving…' })).toBeDisabled()
  })

  it('treats 409 already_saved as saved: no second save, the label shows, the count is read again', async () => {
    route({ 'POST /saved-reports': () => respond(409, errorBody('already_saved', 'This analysis is already saved as a report.')) })
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const dialog = await openSaveDialog(user)
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByText('This analysis is already saved.')).toBeInTheDocument()
    expect(screen.getByText('Saved', { selector: '.dp-saved-state' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(calls('POST', '/saved-reports')).toHaveLength(1)
    await vi.waitFor(() => expect(calls('GET', '/saved-reports')).toHaveLength(2))
  })

  it('ends the session when saving gets 401', async () => {
    const onSessionExpired = vi.fn()
    route({ 'POST /saved-reports': () => respond(401, errorBody('unauthorized', 'x')) })
    const user = userEvent.setup()
    render(<App onSessionExpired={onSessionExpired} />)
    await askFresh(user)
    const dialog = await openSaveDialog(user)
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await vi.waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1))
  })

  it.each([
    ['429', () => respond(429, errorBody('too_many_requests', 'Too many requests. Please wait a moment and try again.')), 'Too many requests. Please wait a moment and try again.'],
    ['503 history_unavailable', () => respond(503, errorBody('history_unavailable', 'History is not available right now. Please try again later.')), 'Saved Reports unavailableSaved Reports are not available right now. Please try again later.'],
    ['a network failure', () => Promise.reject(new TypeError('Failed to fetch')), 'Cannot connect to the DataPilot API.'],
    ['raw server text', () => Promise.resolve(new Response('Traceback: psycopg.errors.UniqueViolation', { status: 500 })), 'Something went wrong on the server. Please try again.'],
  ])('keeps the dialog and typed title after %s, and a retry sends exactly one new save', async (_name, fail, message) => {
    route({ 'POST /saved-reports': fail })
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const dialog = await openSaveDialog(user)
    const input = within(dialog).getByLabelText('Report title')
    await user.clear(input)
    await user.type(input, 'My title')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent(message)
    expect(alert).not.toHaveTextContent(/Traceback|psycopg|Failed to fetch|History is not available/)
    expect(input).toHaveValue('My title')
    expect(calls('POST', '/saved-reports')).toHaveLength(1)

    route({ 'POST /saved-reports': () => respond(201, SAVED) })
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls('POST', '/saved-reports')).toHaveLength(2)
    expect(JSON.parse(calls('POST', '/saved-reports')[1][1].body).title).toBe('My title')
  })

  it('offers no Save Report for an answer that was not recorded', async () => {
    route({ 'POST /query': () => respond(200, { ...FRESH, analysis_id: null }) })
    const user = userEvent.setup()
    render(<App />)
    await user.type(screen.getByLabelText('Your question'), FRESH.question)
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    await screen.findByRole('heading', { level: 1, name: FRESH.question })
    expect(screen.queryByRole('button', { name: 'Save Report' })).not.toBeInTheDocument()
  })

  it('shows Saved on a fresh answer whose analysis is already in the saved list', async () => {
    route({ 'GET /saved-reports': () => respond(200, { items: [{ ...REPORTS[0], analysis_id: 'fresh-1' }] }) })
    const user = userEvent.setup()
    render(<App />)
    await settle()
    await askFresh(user)
    expect(screen.getByText('Saved', { selector: '.dp-saved-state' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save Report' })).not.toBeInTheDocument()
  })
})

describe('saving from History', () => {
  it('saves a reopened analysis and its row shows Saved back in the list, without reloading or asking again', async () => {
    route({
      'GET /history': () => respond(200, { items: [HISTORY_ITEM] }),
      'GET /history/h1': () => respond(200, STORED),
      'POST /saved-reports': () => respond(201, { ...SAVED, analysis_id: 'h1', title: 'Category revenue' }),
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(navButton('History'))
    expect(await screen.findByRole('button', { name: /^Which category/ })).not.toHaveTextContent('Saved')
    await user.click(screen.getByRole('button', { name: /^Which category/ }))
    await screen.findByRole('heading', { name: 'Data' })

    const dialog = await openSaveDialog(user)
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(JSON.parse(calls('POST', '/saved-reports')[0][1].body)).toEqual({ analysis_id: 'h1', title: STORED.question })
    expect(screen.getByText('Saved', { selector: '.dp-saved-state' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Back to History' }))
    expect(screen.getByRole('button', { name: /^Which category/ })).toHaveTextContent('Saved')
    expect(calls('GET', '/history')).toHaveLength(1)
    expect(calls('POST', '/query')).toHaveLength(0)
  })

  it('shows Saved, not Save Report, for a reopened analysis that already has a report', async () => {
    route({
      'GET /history': () => respond(200, { items: [{ ...HISTORY_ITEM, saved_report_id: 'r7' }] }),
      'GET /history/h1': () => respond(200, { ...STORED, saved_report: { id: 'r7', title: 'Kept' } }),
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(navButton('History'))
    await user.click(await screen.findByRole('button', { name: /^Which category/ }))
    await screen.findByRole('heading', { name: 'Data' })
    expect(screen.getByText('Saved', { selector: '.dp-saved-state' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save Report' })).not.toBeInTheDocument()
  })
})
