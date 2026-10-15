import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'
import { stubFetch } from './test/fetch.js'
import ResultView from './components/ResultView.jsx'
import { QUICK_QUESTIONS } from './config/askContent.js'

// The V2 answer, driven through the page with a mocked API. No live calls. The numbers below are
// test fixtures only; the app shows whatever rows the backend sends.

const BASE = { question: 'A business question', sql: 'SELECT 1 LIMIT 500', truncated: false, insight: null }

const KPI = {
  ...BASE,
  columns: ['total_revenue'],
  rows: [[21304631.99]],
  row_count: 1,
  visualization: { type: 'kpi', x_key: null, y_key: 'total_revenue' },
  insight: 'Total revenue from delivered orders is ₹2.13 Cr.',
}

// The highest value is deliberately not the first row.
const BAR = {
  ...BASE,
  columns: ['category_name', 'revenue'],
  rows: [['Apparel', 4012345.25], ['Electronics', 7371769.52], ['Home & Kitchen', 2500000]],
  row_count: 3,
  visualization: { type: 'bar', x_key: 'category_name', y_key: 'revenue' },
  insight: 'Electronics generated the highest revenue.',
}

const LINE = {
  ...BASE,
  columns: ['month', 'revenue'],
  rows: [['2026-04-01', 600000], ['2026-05-01', 800000], ['2026-06-01', 1000000]],
  row_count: 3,
  visualization: { type: 'line', x_key: 'month', y_key: 'revenue' },
}

const TABLE_ONLY = {
  ...BASE,
  columns: ['customer_id', 'full_name', 'city', 'lifetime_spend'],
  rows: [[1042, 'Asha Rao', 'Pune', 130100], [2077, 'Vikram Shah', null, 2500.5]],
  row_count: 2,
  visualization: { type: 'table', x_key: null, y_key: null },
}

let fetchMock

beforeEach(() => {
  fetchMock = vi.fn()
  stubFetch(fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function respond(status, body) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

async function ask(question = 'A business question') {
  const user = userEvent.setup()
  const view = render(<App />)
  await user.type(screen.getByLabelText('Your question'), question)
  await user.click(screen.getByRole('button', { name: 'Ask' }))
  return { user, ...view }
}

async function showResult(body) {
  fetchMock.mockImplementation(() => respond(200, body))
  const view = await ask(body.question)
  await screen.findByRole('button', { name: /SQL query/ })
  return view
}

function tiles() {
  const list = screen.queryByRole('list', { name: 'Summary of the rows shown' })
  if (!list) return []
  return within(list).getAllByRole('listitem').map((item) => [...item.querySelectorAll('p')].map((p) => p.textContent))
}

function headline(container) {
  return container.querySelector('.dp-result-headline')?.textContent ?? null
}

function main() {
  return screen.getByRole('main')
}

describe('result header', () => {
  it('shows the question that was sent as the page heading', async () => {
    await showResult({ ...TABLE_ONLY, question: 'Who are our newest customers?' })
    const header = within(main()).getByRole('heading', { level: 1, name: 'Who are our newest customers?' })
    expect(header.previousElementSibling).toHaveTextContent('Your question')
  })

  it('adds a headline only when it follows directly from the rows', async () => {
    const { container } = await showResult(KPI)
    expect(headline(container)).toBe('Total revenue is ₹2.1 Cr.')
  })

  it('writes no headline for a table result', async () => {
    const { container } = await showResult(TABLE_ONLY)
    expect(headline(container)).toBeNull()
  })

  it('writes no headline when the line result was truncated (the newest rows may be missing)', async () => {
    const { container } = await showResult({ ...LINE, truncated: true })
    expect(headline(container)).toBeNull()
  })
})

describe('KPI result', () => {
  it('shows the backend value in Indian format, short and exact, and nothing invented', async () => {
    await showResult(KPI)
    const hero = screen.getByRole('region', { name: 'Total revenue' })
    expect(within(hero).getByText('₹2.1 Cr')).toBeInTheDocument()
    expect(within(hero).getByText('₹2,13,04,631.99')).toBeInTheDocument()
    expect(tiles()).toEqual([])
    expect(within(main()).queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '₹2,13,04,631.99' })).toBeInTheDocument()
  })

  it('formats a count without a currency sign', async () => {
    await showResult({ ...KPI, columns: ['customer_count'], rows: [[1000]], visualization: { type: 'kpi', y_key: 'customer_count' }, insight: null })
    const hero = screen.getByRole('region', { name: 'Customer count' })
    expect(within(hero).getByText('1 K')).toBeInTheDocument()
    expect(within(hero).getByText('1,000')).toBeInTheDocument()
    expect(within(main()).queryByText(/₹/)).not.toBeInTheDocument()
  })

  it('shows a margin as a percentage, as returned, and never as rupees', () => {
    const { container } = render(
      <ResultView result={{ question: 'What is our profit margin?', columns: ['profit_margin'], rows: [[23.4]], visualization: { type: 'kpi', y_key: 'profit_margin' } }} />,
    )
    expect(headline(container)).toBe('Profit margin is 23.4%.')
    expect(within(screen.getByRole('region', { name: 'Profit margin' })).getAllByText('23.4%')).toHaveLength(1)
    expect(screen.getByRole('cell', { name: '23.4%' })).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/₹/)
  })

  it('formats a percentage with a percent sign, shown once when nothing is rounded', async () => {
    await showResult({ ...KPI, columns: ['cancelled_pct'], rows: [[12.5]], visualization: { type: 'kpi', y_key: 'cancelled_pct' } })
    const hero = screen.getByRole('region', { name: 'Cancelled pct' })
    expect(within(hero).getAllByText('12.5%')).toHaveLength(1)
  })
})

describe('line result', () => {
  it('draws the real rows in the hero card with readable month labels', async () => {
    const { container } = await showResult(LINE)
    const chart = screen.getByRole('img', { name: 'Line chart of revenue by month, Apr 2026 – Jun 2026' })
    expect(within(chart).getByText('Apr 2026')).toBeInTheDocument()
    expect(within(chart).queryByText('2026-04-01')).not.toBeInTheDocument()
    // One dot per row; the latest is the highlighted one.
    expect(chart.querySelectorAll('.recharts-area-dots circle')).toHaveLength(3)
    expect(container.querySelectorAll('.dp-hero-dot-latest')).toHaveLength(1)
    expect(within(chart).getByText('₹10 L')).toBeInTheDocument() // y-axis tick in compact Indian format
  })

  it('draws oldest first without reordering the table', async () => {
    await showResult({ ...LINE, rows: [...LINE.rows].reverse() })
    const chart = screen.getByRole('img', { name: /^Line chart of revenue by month/ })
    const ticks = within(chart).getAllByText(/2026$/).map((tick) => tick.textContent)
    expect(ticks).toEqual(['Apr 2026', 'May 2026', 'Jun 2026'])
    const firstDataRow = screen.getAllByRole('row')[1]
    expect(firstDataRow).toHaveTextContent('Jun 2026')
  })

  it('derives latest, previous, change and average from the shown rows', async () => {
    const { container } = await showResult({ ...LINE, rows: [...LINE.rows].reverse() })
    expect(tiles()).toEqual([
      ['Latest', '₹10 L', 'Jun 2026'],
      ['Previous', '₹8 L', 'May 2026'],
      ['Change vs previous', '+25%', 'Jun 2026 vs May 2026'],
      ['Average', '₹8 L', 'Of 3 shown rows'],
    ])
    expect(headline(container)).toBe('Revenue for Jun 2026 is ₹10 L.')
    const hero = screen.getByRole('region', { name: 'Revenue by month' })
    expect(within(hero).getByText('+25% vs previous')).toBeInTheDocument()
  })

  it('shows no percentage change when the previous value is zero', async () => {
    await showResult({ ...LINE, rows: [['2026-04-01', 500], ['2026-05-01', 0], ['2026-06-01', 900]] })
    expect(tiles().map(([label]) => label)).toEqual(['Latest', 'Previous', 'Average'])
    expect(within(main()).queryByText(/vs previous/)).not.toBeInTheDocument()
  })

  it('labels the tiles "shown" when the result was truncated', async () => {
    await showResult({ ...LINE, truncated: true })
    expect(tiles().map(([label]) => label)).toEqual(['Latest shown', 'Previous shown', 'Change vs previous', 'Average'])
  })
})

describe('bar result', () => {
  it('draws one bar per row with the real categories, highlighting the highest value', async () => {
    // Reduced motion: the chart is drawn without animation, so the bars exist at once in jsdom.
    vi.stubGlobal('matchMedia', (query) => ({ matches: query.includes('reduce'), media: query, addEventListener() {}, removeEventListener() {} }))
    const { container } = await showResult(BAR)
    const chart = screen.getByRole('img', { name: 'Bar chart of revenue by category name, 3 rows' })
    expect(within(chart).getByText('Electronics')).toBeInTheDocument()
    const bars = [...container.querySelectorAll('.recharts-bar-rectangle path')]
    expect(bars).toHaveLength(3)
    expect(bars.map((bar) => bar.getAttribute('fill'))).toEqual([
      'var(--dp-hero-mark)',
      'var(--dp-chart-highlight)', // Electronics, the second row, has the highest value
      'var(--dp-hero-mark)',
    ])
    expect(within(chart).getByText('₹73.7 L')).toBeInTheDocument() // bar value label
  })

  it('puts a negative bar value beside the zero line, never on top of the category names', async () => {
    vi.stubGlobal('matchMedia', (query) => ({ matches: query.includes('reduce'), media: query, addEventListener() {}, removeEventListener() {} }))
    await showResult({ ...BAR, columns: ['category_name', 'profit'], rows: [['Apparel', -640000], ['Electronics', 5460000]], visualization: { type: 'bar', x_key: 'category_name', y_key: 'profit' } })
    const chart = screen.getByRole('img', { name: /^Bar chart of profit/ })
    // The positive bar starts at the zero line; the negative bar runs left from it.
    const [, positiveBar] = chart.querySelectorAll('.recharts-bar-rectangle path')
    const zeroX = Number(/^M\s*([\d.]+)/.exec(positiveBar.getAttribute('d'))[1])
    const negative = within(chart).getByText('-₹6.4 L')
    const positive = within(chart).getByText('₹54.6 L')
    expect(Number(negative.getAttribute('x'))).toBeGreaterThan(zeroX)
    expect(Number(positive.getAttribute('x'))).toBeGreaterThan(Number(negative.getAttribute('x')))
    expect(within(chart).getByText('Apparel')).toBeInTheDocument()
  })

  it('labels every derived tile as covering the shown rows', async () => {
    const { container } = await showResult(BAR)
    expect(tiles()).toEqual([
      ['Highest shown', '₹73.7 L', 'Electronics'],
      ['Lowest shown', '₹25 L', 'Home & Kitchen'],
      ['Total of shown', '₹1.4 Cr', 'Across 3 rows'],
      ['Average of shown', '₹46.3 L', 'Across 3 rows'],
    ])
    expect(headline(container)).toBe('Electronics has the highest revenue of the 3 shown: ₹73.7 L.')
  })

  it.each([
    ['share_pct', 'percentages'],
    ['avg_order_value', 'averages'],
    ['conversion_rate', 'rates'],
  ])('never adds up %s (%s)', async (column) => {
    await showResult({ ...BAR, columns: ['category_name', column], visualization: { type: 'bar', x_key: 'category_name', y_key: column } })
    expect(tiles().map(([label]) => label)).toEqual(['Highest shown', 'Lowest shown', 'Average of shown'])
  })

  it('formats a percentage bar result with % and no ₹', async () => {
    await showResult({ ...BAR, columns: ['category_name', 'share_pct'], rows: [['A', 56.9], ['B', 43.1]], visualization: { type: 'bar', x_key: 'category_name', y_key: 'share_pct' } })
    expect(tiles()[0]).toEqual(['Highest shown', '56.9%', 'A'])
    expect(within(main()).queryByText(/₹/)).not.toBeInTheDocument()
  })
})

describe('table result and Data card', () => {
  it('shows only the backend columns, with no chart, tiles or headline', async () => {
    await showResult(TABLE_ONLY)
    expect(within(main()).queryByRole('img')).not.toBeInTheDocument()
    expect(tiles()).toEqual([])
    expect(screen.getAllByRole('columnheader').map((th) => th.textContent)).toEqual(['Customer id', 'Full name', 'City', 'Lifetime spend'])
  })

  it('formats numbers in Indian style, keeps ids as returned and shows NULL for missing values', async () => {
    await showResult(TABLE_ONLY)
    const [, first, second] = screen.getAllByRole('row')
    expect(within(first).getAllByRole('cell').map((td) => td.textContent)).toEqual(['1042', 'Asha Rao', 'Pune', '₹1,30,100.00'])
    expect(within(second).getAllByRole('cell').map((td) => td.textContent)).toEqual(['2077', 'Vikram Shah', 'NULL', '₹2,500.50'])
  })

  it('keeps a wide table inside a keyboard-scrollable area of the card', async () => {
    await showResult(TABLE_ONLY)
    const scroller = screen.getByRole('region', { name: 'Data table' })
    expect(scroller).toHaveAttribute('tabindex', '0')
    expect(within(scroller).getByRole('table')).toBeInTheDocument()
  })

  it('shows the row count and a restrained note when the result is truncated', async () => {
    await showResult({ ...TABLE_ONLY, truncated: true })
    const data = screen.getByRole('region', { name: 'Data' })
    expect(within(data).getByText('2 rows')).toBeInTheDocument()
    expect(within(data).getByText(/^Only the first 2 rows are shown\./)).toBeInTheDocument()
  })

  it('shows only the table when the response has no visualization field', async () => {
    const { visualization: _unused, ...withoutVisualization } = BAR
    await showResult(withoutVisualization)
    expect(within(main()).queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('shows only the table when the chart columns are missing from the result', async () => {
    await showResult({ ...BAR, visualization: { type: 'bar', x_key: 'nope', y_key: 'revenue' } })
    expect(within(main()).queryByRole('img')).not.toBeInTheDocument()
    expect(tiles()).toEqual([])
  })

  it('shows a text-only answer as returned, not as an error', async () => {
    await showResult({
      ...BASE,
      columns: ['message'],
      rows: [['This question cannot be answered from the store data.']],
      row_count: 1,
      visualization: { type: 'table' },
    })
    expect(screen.getByRole('cell', { name: 'This question cannot be answered from the store data.' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it.each([KPI, BAR, LINE])('keeps the table and SQL below the $visualization.type view', async (body) => {
    const { user } = await showResult(body)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader')).toHaveLength(body.columns.length)
    await user.click(screen.getByRole('button', { name: /SQL query/ }))
    expect(screen.getByText(body.sql)).toBeVisible()
  })
})

describe('insight', () => {
  it('shows the insight exactly as sent, with the row count', async () => {
    await showResult(BAR)
    const card = screen.getByRole('region', { name: 'Insight' })
    expect(within(card).getByText('Electronics generated the highest revenue.')).toBeInTheDocument()
    expect(within(card).getByText('Answered from the sales database · 3 rows')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('shows a quiet note, not an error, when the insight is null, and asks for nothing', async () => {
    await showResult(LINE)
    expect(within(screen.getByRole('region', { name: 'Insight' })).getByText('AI insight is unavailable for this result.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('SQL card', () => {
  it('is closed by default and opens and closes with its button', async () => {
    const { user } = await showResult(BAR)
    const toggle = screen.getByRole('button', { name: /SQL query/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText(BAR.sql)).not.toBeVisible()

    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(BAR.sql)).toBeVisible()
    expect(document.getElementById(toggle.getAttribute('aria-controls'))).toContainElement(screen.getByText(BAR.sql))

    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('shows the SQL as plain text, never as markup', async () => {
    const sql = "SELECT '<b>bold</b>' AS label LIMIT 1"
    const { user, container } = await showResult({ ...TABLE_ONLY, sql })
    await user.click(screen.getByRole('button', { name: /SQL query/ }))
    expect(screen.getByText(sql)).toBeVisible()
    expect(container.querySelector('.dp-sql b')).toBeNull()
  })
})

describe('zero-row result', () => {
  it('says no data was found and shows no chart, tiles, table or insight', async () => {
    await showResult({ ...BAR, rows: [], row_count: 0, insight: null, visualization: { type: 'table' } })
    expect(screen.getByRole('heading', { name: 'No matching data was found for this question.' })).toBeInTheDocument()
    expect(within(main()).queryByRole('img')).not.toBeInTheDocument()
    expect(tiles()).toEqual([])
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Insight' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /SQL query/ })).toBeInTheDocument()
  })
})

describe('errors', () => {
  async function showError(status, code, message = 'A safe message.', question = 'Delete all orders from last year') {
    fetchMock.mockImplementation(() => respond(status, { error: { code, message } }))
    const view = await ask(question)
    await screen.findByRole('alert')
    return view
  }

  it.each(['unsafe_sql', 'query_not_allowed'])('shows a stopped question (%s) as "can\'t be run", in general terms', async (code) => {
    await showError(400, code, 'The generated query was rejected by the safety checks.')
    expect(screen.getByRole('heading', { level: 1, name: 'Delete all orders from last year' })).toBeInTheDocument()
    const alert = screen.getByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'error')
    expect(within(alert).getByRole('heading', { name: "This question can't be run" })).toBeInTheDocument()
    expect(alert).toHaveTextContent('DataPilot only runs read-only queries that pass its safety checks')
    // It does not claim a reason the backend did not give.
    expect(alert).not.toHaveTextContent(/delete|write|table|function/i)
  })

  it('offers the quick questions as "Try instead" buttons, which ask one question each', async () => {
    const { user } = await showError(400, 'unsafe_sql')
    const tryInstead = screen.getAllByRole('button').filter((button) => button.classList.contains('dp-try-item'))
    expect(tryInstead.map((button) => button.textContent)).toEqual(QUICK_QUESTIONS)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    fetchMock.mockImplementation(() => respond(200, { ...KPI, question: 'What is our total revenue?' }))
    await user.click(screen.getByRole('button', { name: 'What is our total revenue?' }))
    expect(await screen.findByRole('heading', { name: 'Data' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [url, options] = fetchMock.mock.calls[1]
    expect(url).toMatch(/\/query$/)
    expect(JSON.parse(options.body)).toEqual({ question: 'What is our total revenue?' })
  })

  it.each([
    [429, 'rate_limited', 'Too many requests'],
    [503, 'generation_unavailable', 'Service temporarily unavailable'],
    [504, 'query_timeout', 'Query timed out'],
  ])('shows HTTP %i (%s) as a temporary problem with the safe message', async (status, code, title) => {
    await showError(status, code, 'Please try again in a moment.', 'Revenue?')
    const alert = screen.getByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'warning')
    expect(within(alert).getByRole('heading', { name: title })).toBeInTheDocument()
    expect(alert).toHaveTextContent('Please try again in a moment.')
    expect(screen.queryByText('Try instead')).not.toBeInTheDocument()
  })

  it('shows an unreachable backend as a temporary problem', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await ask('Revenue?')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'warning')
    expect(alert).toHaveTextContent('Cannot connect to the DataPilot API.')
    expect(alert).not.toHaveTextContent('Failed to fetch')
  })

  it('never shows raw server text that is not in the documented error shape', async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(new Response('psycopg.errors.UndefinedTable: relation "x" does not exist', { status: 500 })),
    )
    await ask('Revenue?')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong on the server. Please try again.')
    expect(alert).not.toHaveTextContent(/psycopg|relation/)
  })

  it('has no "outside your workspace" behaviour: no keyword checks on the question', async () => {
    await showResult({ ...TABLE_ONLY, question: 'Show employee salaries by team' })
    expect(screen.queryByText(/outside your workspace/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
})

describe('ResultView', () => {
  it('reads a stored analysis with extra fields, ignores them and does not change the object', () => {
    const stored = Object.freeze({
      ...BAR,
      rows: Object.freeze(BAR.rows.map((row) => Object.freeze([...row]))),
      columns: Object.freeze([...BAR.columns]),
      analysis_id: 'a1b2',
      created_at: '2026-10-08T09:42:00Z',
      saved_report: { id: 'r1', title: 'My kept report' },
    })
    const before = JSON.stringify(stored)
    render(<ResultView result={stored} />)
    expect(screen.getByRole('heading', { level: 1, name: BAR.question })).toBeInTheDocument()
    expect(screen.queryByText(/2026-10-08|My kept report|a1b2/)).not.toBeInTheDocument()
    expect(JSON.stringify(stored)).toBe(before)
  })

  it.each([
    ['rows = []', { columns: ['a'], rows: [], row_count: 0 }],
    ['one KPI row', { columns: ['total_revenue'], rows: [[5]], visualization: { type: 'kpi', y_key: 'total_revenue' } }],
    ['one line point', { columns: ['month', 'revenue'], rows: [['2026-01-01', 5]], visualization: { type: 'line', x_key: 'month', y_key: 'revenue' } }],
    ['numeric nulls in a line', { columns: ['month', 'revenue'], rows: [['2026-01-01', null], ['2026-02-01', 7], ['2026-03-01', null]], visualization: { type: 'line', x_key: 'month', y_key: 'revenue' } }],
    ['numeric nulls in a bar', { columns: ['c', 'revenue'], rows: [['A', null], ['B', 7]], visualization: { type: 'bar', x_key: 'c', y_key: 'revenue' } }],
    ['only null values', { columns: ['c', 'revenue'], rows: [['A', null], ['B', null]], visualization: { type: 'bar', x_key: 'c', y_key: 'revenue' } }],
    ['visualization = table', { columns: ['c', 'revenue'], rows: [['A', 1]], visualization: { type: 'table' } }],
    ['metadata with no keys', { columns: ['c', 'revenue'], rows: [['A', 1], ['B', 2]], visualization: { type: 'bar' } }],
    ['unknown visualization type', { columns: ['c', 'revenue'], rows: [['A', 1], ['B', 2]], visualization: { type: 'pie', x_key: 'c', y_key: 'revenue' } }],
    ['visualization = null', { columns: ['c', 'revenue'], rows: [['A', 1]], visualization: null }],
    ['ragged rows', { columns: ['c', 'revenue'], rows: [['A'], ['B', 2, 'extra']], visualization: { type: 'table' } }],
  ])('renders an edge case (%s) without crashing or inventing values', (_name, result) => {
    const { container } = render(<ResultView result={{ question: 'Q', sql: 'SELECT 1', insight: null, ...result }} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Q' })).toBeInTheDocument()
    // Every tile value is a formatted figure taken from the rows (5, 7 or 2 here), never a placeholder.
    for (const value of container.querySelectorAll('.dp-tile-value')) expect(value.textContent).toMatch(/^₹?[0-9.]+( [KLC]r?)?$|^[+-]?[0-9.]+%$/)
    expect(container.textContent).not.toMatch(/NaN|Infinity|undefined/)
  })

  it('draws one-point and null-value lines from the real points only', () => {
    const { container } = render(
      <ResultView result={{ question: 'Q', columns: ['month', 'revenue'], rows: [['2026-01-01', null], ['2026-02-01', 7]], visualization: { type: 'line', x_key: 'month', y_key: 'revenue' } }} />,
    )
    expect(screen.getByRole('img', { name: 'Line chart of revenue by month, Jan 2026 – Feb 2026' })).toBeInTheDocument()
    expect(container.querySelectorAll('.dp-hero-dot-latest')).toHaveLength(1)
    expect([...container.querySelectorAll('.dp-tile-label')].map((label) => label.textContent)).toEqual(['Latest'])
  })

  it('shows an insight containing markup as plain text', () => {
    const { container } = render(
      <ResultView result={{ ...TABLE_ONLY, insight: '<img src=x onerror="alert(1)"> <b>Bold</b> claim' }} />,
    )
    expect(screen.getByText('<img src=x onerror="alert(1)"> <b>Bold</b> claim')).toBeInTheDocument()
    expect(container.querySelector('.dp-insight img, .dp-insight b')).toBeNull()
  })

  it('renders with incomplete fields without crashing', () => {
    render(<ResultView result={{ question: 'Q', columns: ['a'], rows: [[null]], visualization: { type: 'kpi', y_key: 'a' } }} />)
    expect(screen.getByRole('cell', { name: 'NULL' })).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
