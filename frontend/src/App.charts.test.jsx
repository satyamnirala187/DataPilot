import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'

// Charts, KPIs and insights, driven through the page with a mocked API. No live calls.

const BASE = { question: 'q', sql: 'SELECT 1 LIMIT 500', truncated: false, insight: null }

const KPI = {
  ...BASE,
  columns: ['total_revenue'],
  rows: [[21304631.99]],
  row_count: 1,
  visualization: { type: 'kpi', x_key: null, y_key: 'total_revenue' },
  insight: 'Total revenue from delivered orders is ₹21.30M.',
}

const BAR = {
  ...BASE,
  columns: ['category_name', 'revenue'],
  rows: [['Electronics', 7371769.52], ['Apparel', 4012345.25], ['Home & Kitchen', 2500000]],
  row_count: 3,
  visualization: { type: 'bar', x_key: 'category_name', y_key: 'revenue' },
  insight: 'Electronics generated the highest revenue at ₹7.37M.',
}

const LINE = {
  ...BASE,
  columns: ['month', 'revenue'],
  rows: [['2026-04-01', 784130.39], ['2026-05-01', 801234.1], ['2026-06-01', 799999.99]],
  row_count: 3,
  visualization: { type: 'line', x_key: 'month', y_key: 'revenue' },
}

const TABLE_ONLY = {
  ...BASE,
  columns: ['full_name', 'city'],
  rows: [['Asha Rao', 'Pune'], ['Vikram Shah', 'Delhi']],
  row_count: 2,
  visualization: { type: 'table', x_key: null, y_key: null },
}

let fetchMock

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function showResult(body) {
  fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }))
  const user = userEvent.setup()
  const view = render(<App />)
  await user.type(screen.getByLabelText('Your question'), 'A business question')
  await user.click(screen.getByRole('button', { name: 'Analyze' }))
  await screen.findByRole('heading', { name: 'Generated SQL' })
  return view
}

describe('visualizations', () => {
  it('renders a KPI with a readable label and currency formatting', async () => {
    await showResult(KPI)
    expect(screen.getByRole('heading', { name: 'Total Revenue' })).toBeInTheDocument()
    expect(screen.getByText('₹21,304,631.99')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('formats a count KPI without a currency sign', async () => {
    await showResult({ ...KPI, columns: ['customer_count'], rows: [[1000]], visualization: { type: 'kpi', y_key: 'customer_count' }, insight: null })
    expect(screen.getByRole('heading', { name: 'Customer Count' })).toBeInTheDocument()
    expect(screen.getByText('1,000')).toBeInTheDocument()
    expect(screen.queryByText(/₹/)).not.toBeInTheDocument()
  })

  it('formats a percentage KPI with a percent sign', async () => {
    await showResult({ ...KPI, columns: ['cancelled_pct'], rows: [[12.5]], visualization: { type: 'kpi', y_key: 'cancelled_pct' } })
    expect(screen.getByText('12.5%')).toBeInTheDocument()
  })

  it('renders a bar chart from the backend keys, with one bar per row', async () => {
    const { container } = await showResult(BAR)
    const chart = screen.getByRole('img', { name: 'Bar chart of Revenue by Category Name' })
    expect(chart).toBeInTheDocument()
    expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(3)
    expect(within(chart).getByText('Electronics')).toBeInTheDocument()
  })

  it('renders a line chart with readable month labels', async () => {
    const { container } = await showResult(LINE)
    const chart = screen.getByRole('img', { name: 'Line chart of Revenue by Month' })
    expect(container.querySelector('.recharts-line')).toBeInTheDocument()
    expect(within(chart).getByText('Apr 2026')).toBeInTheDocument()
    expect(within(chart).queryByText('2026-04-01')).not.toBeInTheDocument()
  })

  it('draws a line chart oldest first without reordering the table', async () => {
    const reversed = { ...LINE, rows: [...LINE.rows].reverse() }
    await showResult(reversed)
    const chart = screen.getByRole('img', { name: 'Line chart of Revenue by Month' })
    const ticks = within(chart).getAllByText(/2026$/).map((tick) => tick.textContent)
    expect(ticks).toEqual(['Apr 2026', 'May 2026', 'Jun 2026'])
    const firstDataRow = screen.getAllByRole('row')[1]
    expect(firstDataRow).toHaveTextContent('2026-06-01')
  })

  it('shows no chart for a table-only result', async () => {
    await showResult(TABLE_ONLY)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /by/ })).not.toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Asha Rao' })).toBeInTheDocument()
  })

  it('shows no chart when the response has no visualization field', async () => {
    const { visualization: _unused, ...withoutVisualization } = BAR
    await showResult(withoutVisualization)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it.each([KPI, BAR, LINE])('keeps the results table and SQL visible below the $visualization.type view', async (body) => {
    await showResult(body)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader').map((th) => th.textContent)).toEqual(body.columns)
    expect(screen.getByText(body.sql)).toBeInTheDocument()
  })
})

describe('insight', () => {
  it('renders the AI insight', async () => {
    await showResult(BAR)
    expect(screen.getByRole('heading', { name: 'AI Insight' })).toBeInTheDocument()
    expect(screen.getByText('Electronics generated the highest revenue at ₹7.37M.')).toBeInTheDocument()
  })

  it('shows a quiet note, not an error, when the insight is null', async () => {
    await showResult(LINE)
    expect(screen.getByText('Insight temporarily unavailable.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'AI Insight' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Line chart of Revenue by Month' })).toBeInTheDocument()
  })

  it('shows no insight note for a zero-row result', async () => {
    await showResult({ ...TABLE_ONLY, rows: [], row_count: 0 })
    expect(screen.queryByText('Insight temporarily unavailable.')).not.toBeInTheDocument()
    expect(screen.getByText('The query ran successfully but returned no rows.')).toBeInTheDocument()
  })
})
