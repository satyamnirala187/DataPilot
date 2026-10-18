import { describe, expect, it } from 'vitest'
import { MAX_PDF_BARS, buildReportContent, niceTicks, pdfSafeText, reportFilename } from './reportContent.js'

// What goes into a PDF report, without drawing it. Every analysis here is a test fixture.

const GENERATED = new Date(2026, 9, 19, 14, 5)

const KPI = {
  question: 'What is our total revenue?',
  sql: '  SELECT SUM(oi.quantity * oi.unit_price) AS total_revenue FROM order_items oi  ',
  columns: ['total_revenue'],
  rows: [[21304631.99]],
  row_count: 1,
  truncated: false,
  visualization: { type: 'kpi', y_key: 'total_revenue' },
  insight: 'Revenue is ₹2.13 Cr.',
}

const BAR = {
  question: 'What is the profit for each product in the Electronics category?',
  sql: 'SELECT product_name, profit FROM x',
  columns: ['product_id', 'product_name', 'profit'],
  rows: [[4, 'Orvexa Bluetooth Speaker', 560801.88], [5, 'Nuvaro Smart TV', 132624.67], [3, 'Kalvex Laptop', 1923.18]],
  row_count: 3,
  truncated: false,
  visualization: { type: 'bar', x_key: 'product_name', y_key: 'profit' },
  insight: null,
}

const LINE = {
  question: 'Show monthly revenue for the last 6 months.',
  sql: 'SELECT month, revenue FROM x',
  columns: ['month', 'revenue'],
  rows: [['2026-04-01', 600000], ['2026-05-01', 800000], ['2026-06-01', null], ['2026-07-01', 750000]],
  row_count: 4,
  truncated: false,
  visualization: { type: 'line', x_key: 'month', y_key: 'revenue' },
  insight: 'Revenue dipped in June.',
}

const TABLE_ONLY = {
  question: 'Who are our top customers?',
  sql: 'SELECT full_name, email, total_spending FROM x',
  columns: ['full_name', 'email', 'total_spending'],
  rows: [['Girik Menon', 'girik@example.com', 370418.49], ['Tanish Om', null, 267573.19]],
  row_count: 2,
  truncated: true,
  visualization: { type: 'table' },
  insight: '   ',
}

function deepFreeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(deepFreeze)
    Object.freeze(value)
  }
  return value
}

describe('buildReportContent', () => {
  it('shows a KPI as one exact value in ₹, with the question as title', () => {
    const content = buildReportContent(KPI, { generatedAt: GENERATED })
    expect(content.kicker).toBe('Analysis')
    expect(content.title).toBe('What is our total revenue?')
    expect(content.question).toBeNull()
    expect(content.kpi).toEqual({ label: 'Total revenue', value: '₹2,13,04,631.99' })
    expect(content.headline).toBe('Total revenue is ₹2.1 Cr.') // the screen's headline, unchanged
    expect(content.tiles).toEqual([])
    expect(content.chart).toBeNull()
    expect(content.table.rows).toEqual([['₹2,13,04,631.99']])
    expect(content.sql).toBe('SELECT SUM(oi.quantity * oi.unit_price) AS total_revenue FROM order_items oi')
    expect(content.generated).toBe('19 Oct 2026, 14:05')
    expect(content.meta).toEqual(['1 row'])
  })

  it('gives a bar result its summary tiles, bars and the highlighted top bar', () => {
    const content = buildReportContent(BAR, { generatedAt: GENERATED })
    expect(content.chart.kind).toBe('bar')
    expect(content.chart.title).toBe('Profit by product name')
    expect(content.chart.bars.map((bar) => [bar.label, bar.text, bar.highlight])).toEqual([
      ['Orvexa Bluetooth Speaker', '₹5.6 L', true],
      ['Nuvaro Smart TV', '₹1.3 L', false],
      ['Kalvex Laptop', '₹1.9 K', false],
    ])
    expect(content.chart.note).toBeNull()
    expect(content.tiles.map((tile) => tile.label)).toEqual(['Highest shown', 'Lowest shown', 'Total of shown', 'Average of shown'])
    // The table keeps every column, the id included, formatted as on screen.
    expect(content.table.columns).toEqual([
      { label: 'Product id', numeric: true },
      { label: 'Product name', numeric: false },
      { label: 'Profit', numeric: true },
    ])
    expect(content.table.rows[0]).toEqual(['4', 'Orvexa Bluetooth Speaker', '₹5,60,801.88'])
    expect(content.insight).toBeNull()
  })

  it('charts at most MAX_PDF_BARS bars and says the table has the rest', () => {
    const rows = Array.from({ length: 30 }, (_, i) => [`City ${i}`, 1000 - i])
    const content = buildReportContent({ ...BAR, columns: ['city', 'customer_count'], rows, row_count: 30, visualization: { type: 'bar', x_key: 'city', y_key: 'customer_count' } })
    expect(content.chart.bars).toHaveLength(MAX_PDF_BARS)
    expect(content.chart.note).toBe('Showing the first 20 of 30 rows; every row is in the Data table.')
    expect(content.table.rows).toHaveLength(30)
  })

  it('gives a line result month labels, a gap for a missing value and readable y ticks', () => {
    const content = buildReportContent(LINE, { generatedAt: GENERATED })
    expect(content.chart.kind).toBe('line')
    expect(content.chart.points).toEqual([
      { label: 'Apr 2026', value: 600000 },
      { label: 'May 2026', value: 800000 },
      { label: 'Jun 2026', value: null },
      { label: 'Jul 2026', value: 750000 },
    ])
    expect(content.chart.range).toBe('Apr 2026 – Jul 2026')
    expect(content.chart.ticks.map((tick) => tick.text)).toEqual(['₹0', '₹2 L', '₹4 L', '₹6 L', '₹8 L'])
    expect(content.table.rows[2]).toEqual(['Jun 2026', 'NULL'])
  })

  it('shows a table-only result with no chart, no KPI and the limited-result note', () => {
    const content = buildReportContent(TABLE_ONLY, { generatedAt: GENERATED })
    expect(content.chart).toBeNull()
    expect(content.kpi).toBeNull()
    expect(content.tiles).toEqual([])
    expect(content.table.rows).toEqual([
      ['Girik Menon', 'girik@example.com', '₹3,70,418.49'],
      ['Tanish Om', 'NULL', '₹2,67,573.19'],
    ])
    expect(content.truncated).toBe(true)
    expect(content.meta).toEqual(['2 rows (limited result)'])
    expect(content.insight).toBeNull() // blank text is no insight
  })

  it('marks an empty result', () => {
    const content = buildReportContent({ ...TABLE_ONLY, rows: [], row_count: 0, truncated: false })
    expect(content.empty).toBe(true)
    expect(content.table.rows).toEqual([])
    expect(content.meta).toEqual(['0 rows'])
  })

  it('titles a saved report by its own title, with the question, saved and answered dates', () => {
    const stored = { ...BAR, id: 'a1', created_at: '2026-10-08T05:30:00.123456+00:00' }
    const content = buildReportContent(stored, { source: 'saved', title: ' Electronics profit ', savedAt: '2026-10-09T06:00:00+00:00', generatedAt: GENERATED })
    expect(content.kicker).toBe('Saved report')
    expect(content.title).toBe('Electronics profit')
    expect(content.question).toBe(BAR.question)
    expect(content.meta[0]).toBe('Saved 9 Oct 2026')
    expect(content.meta[1]).toMatch(/^Answered 8 Oct 2026, \d\d:\d\d$/)
    expect(content.meta[2]).toBe('3 rows')
  })

  it('labels a History export and skips an unreadable date', () => {
    const content = buildReportContent({ ...KPI, created_at: 'not a date' }, { source: 'history', generatedAt: GENERATED })
    expect(content.kicker).toBe('History analysis')
    expect(content.meta).toEqual(['1 row'])
  })

  it('reads only the snapshot given and never changes it', () => {
    const stored = deepFreeze({ ...BAR, id: 'a1', saved_report: { id: 'r1', title: 'x' } })
    expect(() => buildReportContent(stored, { source: 'history', generatedAt: GENERATED })).not.toThrow()
    const content = buildReportContent(stored, { source: 'history', generatedAt: GENERATED })
    expect(JSON.stringify(content)).not.toContain('saved_report')
  })

  it('falls back safely for missing fields', () => {
    const content = buildReportContent({}, { source: 'unknown', generatedAt: GENERATED })
    expect(content.kicker).toBe('Analysis')
    expect(content.title).toBe('DataPilot analysis')
    expect(content.empty).toBe(true)
    expect(content.sql).toBeNull()
  })
})

describe('reportFilename', () => {
  it.each([
    ['Electronics profit', 'datapilot-electronics-profit-2026-10-19.pdf'],
    ['What is the profit for each product in the Electronics category?', 'datapilot-profit-product-electronics-category-2026-10-19.pdf'],
    ['Show monthly revenue for the last 6 months.', 'datapilot-monthly-revenue-last-6-months-2026-10-19.pdf'],
    ['Café & Résumé: ₹ revenue!', 'datapilot-cafe-resume-revenue-2026-10-19.pdf'],
    ['What is the?', 'datapilot-what-is-the-2026-10-19.pdf'], // only stop words: keep them
    ['₹ 📈 !!!', 'datapilot-analysis-2026-10-19.pdf'],
    ['', 'datapilot-analysis-2026-10-19.pdf'],
    [null, 'datapilot-analysis-2026-10-19.pdf'],
  ])('%s -> %s', (title, expected) => {
    expect(reportFilename(title, GENERATED)).toBe(expected)
  })

  it('keeps names short and uses only lowercase letters, digits and dashes', () => {
    const name = reportFilename('Quarterly revenue comparison between northern southern eastern western regions', GENERATED)
    expect(name).toBe('datapilot-quarterly-revenue-comparison-between-northern-southern-2026-10-19.pdf')
    expect(name).toMatch(/^datapilot-[a-z0-9-]+-\d{4}-\d{2}-\d{2}\.pdf$/)
    expect(reportFilename('a'.repeat(200), GENERATED).length).toBeLessThan(90)
  })

  it('uses the local date the PDF was made', () => {
    expect(reportFilename('Revenue', new Date(2026, 0, 5, 23, 59))).toBe('datapilot-revenue-2026-01-05.pdf')
  })
})

describe('pdfSafeText', () => {
  it('keeps ₹, Indian grouping, accents and punctuation the fonts can draw', () => {
    expect(pdfSafeText('₹2,13,04,631.99 · “Café” – 34.56% → €5')).toBe('₹2,13,04,631.99 · “Café” – 34.56% → €5')
  })

  it('replaces characters the fonts cannot draw, and tabs', () => {
    expect(pdfSafeText('Revenue 📈 राजस्व\tdone')).toBe('Revenue ? ??????    done')
    expect(pdfSafeText(null)).toBe('')
    expect(pdfSafeText('a\r\nb')).toBe('a\nb')
  })
})

describe('niceTicks', () => {
  it('covers the range with round steps', () => {
    expect(niceTicks(0, 800000)).toEqual([0, 200000, 400000, 600000, 800000])
    expect(niceTicks(-50, 90)).toEqual([-50, 0, 50, 100])
    expect(niceTicks(0, 0)).toEqual([0, 1])
  })
})
