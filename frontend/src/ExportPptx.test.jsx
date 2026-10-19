import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'

// Export PPTX in the app: where the button is, what it exports and how failures look. The PPTX
// module is replaced by a mock (its own tests are in src/pptx), and the API by a fetch mock: no
// backend, Gemini or database calls. Everything below is a test fixture.

const { exportAnalysisPptx } = vi.hoisted(() => ({ exportAnalysisPptx: vi.fn() }))
vi.mock('./pptx/exportPptx.js', () => ({ exportAnalysisPptx }))

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

const HISTORY_ITEM = { id: 'h1', question: 'Which category generated the most revenue?', visualization_type: 'bar', row_count: 2, truncated: false, created_at: '2026-10-15T09:15:00+00:00', saved_report_id: null }
const STORED = {
  id: 'h1',
  question: 'Which category generated the most revenue?',
  sql: 'SELECT category_name, revenue FROM stored LIMIT 500',
  columns: ['category_name', 'revenue'],
  rows: [['Electronics', 7371769.52], ['Apparel', 4012345.25]],
  row_count: 2,
  truncated: false,
  visualization: { type: 'bar', x_key: 'category_name', y_key: 'revenue' },
  insight: 'Electronics leads.',
  created_at: '2026-10-15T09:15:00+00:00',
  saved_report: null,
}

const REPORT_SUMMARY = { id: 'r1', analysis_id: 'h1', title: 'Electronics profit', question: STORED.question, visualization_type: 'bar', row_count: 2, truncated: false, saved_at: '2026-10-16T10:00:00+00:00', analysis_created_at: STORED.created_at }
const { saved_report: _unused, ...STORED_ANALYSIS } = STORED
const REPORT = { id: 'r1', title: 'Electronics profit', saved_at: '2026-10-16T10:00:00+00:00', analysis: STORED_ANALYSIS }

let fetchMock
let handlers

beforeEach(() => {
  exportAnalysisPptx.mockReset()
  exportAnalysisPptx.mockResolvedValue('datapilot-test-2026-10-19.pptx')
  handlers = { 'GET /saved-reports': () => respond(200, { items: [REPORT_SUMMARY] }) }
  fetchMock = vi.fn((url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${new URL(url).pathname}`
    if (!handlers[key]) throw new Error(`Unexpected request: ${key}`)
    return handlers[key](url, options)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function respond(status, body) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

function route(more) {
  handlers = { ...handlers, ...more }
}

function navButton(name) {
  return within(screen.getByRole('navigation')).getByRole('button', { name })
}

function exportButton() {
  return screen.queryByRole('button', { name: 'Export PPTX' })
}

async function askFresh(user, answer = FRESH) {
  route({ 'POST /query': () => respond(200, answer) })
  await user.type(screen.getByLabelText('Your question'), answer.question)
  await user.click(screen.getByRole('button', { name: 'Ask' }))
  await screen.findByRole('heading', { level: 1, name: answer.question })
}

async function openStoredAnalysis(user) {
  route({ 'GET /history': () => respond(200, { items: [HISTORY_ITEM] }), 'GET /history/h1': () => respond(200, STORED) })
  await user.click(navButton('History'))
  await user.click(await screen.findByRole('button', { name: /^Which category/ }))
  await screen.findByRole('heading', { name: 'Data' })
}

async function openSavedReport(user) {
  route({ 'GET /saved-reports/r1': () => respond(200, REPORT) })
  await user.click(navButton('Saved Reports'))
  await user.click(await screen.findByRole('button', { name: /^Electronics profit/ }))
  await screen.findByRole('heading', { name: 'Data' })
}

function requestCount() {
  return fetchMock.mock.calls.length
}

describe('where Export PPTX is offered', () => {
  it('is not offered before a question is answered, while it runs, or for an error', async () => {
    let finish
    route({ 'POST /query': () => new Promise((resolve) => (finish = resolve)) })
    const user = userEvent.setup()
    render(<App />)
    expect(exportButton()).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Your question'), 'Total revenue?')
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    expect(exportButton()).not.toBeInTheDocument()
    await act(async () => finish(new Response(JSON.stringify({ error: { code: 'unsafe_sql', message: 'Refused.' } }), { status: 400 })))
    await screen.findByRole('heading', { level: 1, name: 'Total revenue?' })
    expect(exportButton()).not.toBeInTheDocument()
  })

  it('is offered for a fresh answer, also one that was not recorded', async () => {
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user, { ...FRESH, analysis_id: null })
    expect(exportButton()).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Save Report' })).not.toBeInTheDocument()
  })

  it('is offered on a reopened History analysis and a reopened saved report, not on their lists', async () => {
    const user = userEvent.setup()
    render(<App />)
    route({ 'GET /history': () => respond(200, { items: [HISTORY_ITEM] }) })
    await user.click(navButton('History'))
    await screen.findByRole('button', { name: /^Which category/ })
    expect(exportButton()).not.toBeInTheDocument()
    await openStoredAnalysis(user)
    expect(exportButton()).toBeEnabled()

    await user.click(navButton('Saved Reports'))
    await screen.findByRole('button', { name: /^Electronics profit/ })
    expect(exportButton()).not.toBeInTheDocument()
    await openSavedReport(user)
    expect(exportButton()).toBeEnabled()
  })
})

describe('what is exported', () => {
  it('exports the fresh answer as it is on screen, with no request', async () => {
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const before = requestCount()
    await user.click(exportButton())
    expect(exportAnalysisPptx).toHaveBeenCalledTimes(1)
    expect(exportAnalysisPptx).toHaveBeenCalledWith(FRESH, { source: 'query', title: null, savedAt: null })
    expect(await screen.findByText('Downloaded datapilot-test-2026-10-19.pptx')).toHaveAttribute('role', 'status')
    expect(requestCount()).toBe(before)
  })

  it('exports a History analysis from its stored snapshot only', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openStoredAnalysis(user)
    const before = requestCount()
    await user.click(exportButton())
    expect(exportAnalysisPptx).toHaveBeenCalledWith(STORED, { source: 'history', title: null, savedAt: null })
    expect(requestCount()).toBe(before) // nothing is asked again or reloaded
  })

  it('exports a saved report from its stored analysis, under the report title', async () => {
    const user = userEvent.setup()
    render(<App />)
    await openSavedReport(user)
    const before = requestCount()
    await user.click(exportButton())
    expect(exportAnalysisPptx).toHaveBeenCalledWith(REPORT.analysis, { source: 'saved', title: 'Electronics profit', savedAt: REPORT.saved_at })
    expect(requestCount()).toBe(before)
  })
})

describe('export errors and repeated presses', () => {
  it('shows a safe message when the PPTX cannot be made, keeps the analysis and allows a retry', async () => {
    exportAnalysisPptx.mockRejectedValueOnce(new Error('PptxGenJS failed at /internal/path with font Arial'))
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    await user.click(exportButton())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The PowerPoint file could not be created. Please try again.')
    expect(alert).not.toHaveTextContent(/PptxGenJS|internal|Arial/)
    expect(screen.getByRole('heading', { level: 1, name: FRESH.question })).toBeInTheDocument()

    await user.click(exportButton())
    expect(exportAnalysisPptx).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the same safe message when the PPTX module cannot be loaded', async () => {
    const { default: ExportPptxAction } = await import('./components/ExportPptxAction.jsx')
    const user = userEvent.setup()
    render(<ExportPptxAction result={FRESH} load={() => Promise.reject(new Error('Failed to fetch dynamically imported module'))} />)
    await user.click(screen.getByRole('button', { name: 'Export PPTX' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The PowerPoint file could not be created. Please try again.')
    expect(screen.getByRole('button', { name: 'Export PPTX' })).toBeEnabled()
  })

  it('makes one deck when pressed twice, and shows that it is working', async () => {
    let finish
    exportAnalysisPptx.mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)))
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    const button = exportButton()
    fireEvent.click(button)
    fireEvent.click(button)
    expect(await screen.findByRole('button', { name: 'Preparing PPTX…' })).toBeDisabled()
    await act(async () => finish('datapilot-x-2026-10-19.pptx'))
    expect(exportAnalysisPptx).toHaveBeenCalledTimes(1)
    expect(exportButton()).toBeEnabled()
  })

  it('renders nothing without an analysis', async () => {
    const { default: ExportPptxAction } = await import('./components/ExportPptxAction.jsx')
    const { container } = render(<ExportPptxAction result={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('beside Export PDF', () => {
  it('is offered next to Export PDF on a fresh answer, History analysis and saved report', async () => {
    const user = userEvent.setup()
    render(<App />)
    await askFresh(user)
    expect(screen.getByRole('button', { name: 'Export PDF' })).toBeEnabled()
    expect(exportButton()).toBeEnabled()

    await openStoredAnalysis(user)
    expect(screen.getByRole('button', { name: 'Export PDF' })).toBeEnabled()
    expect(exportButton()).toBeEnabled()

    await openSavedReport(user)
    expect(screen.getByRole('button', { name: 'Export PDF' })).toBeEnabled()
    expect(exportButton()).toBeEnabled()
  })
})
