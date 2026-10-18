import { jsPDF } from 'jspdf'
import { describe, expect, it } from 'vitest'
import { FONTS, registerFonts } from './fonts.js'
import { PAGE, renderReport } from './renderReport.js'
import { buildReportContent } from './reportContent.js'

// The PDF layout, drawn into a real jsPDF document with the embedded fonts. Every string drawn is
// recorded, so the tests can say exactly what the PDF contains. Every analysis is a test fixture.

const GENERATED = new Date(2026, 9, 19, 14, 5)

function base(fields) {
  return { question: 'What is our total revenue?', sql: 'SELECT 1', truncated: false, insight: null, ...fields }
}

const KPI = base({
  sql: 'SELECT SUM(oi.quantity * oi.unit_price) AS total_revenue\nFROM order_items oi\nJOIN orders o ON o.order_id = oi.order_id',
  columns: ['total_revenue'],
  rows: [[21304631.99]],
  row_count: 1,
  visualization: { type: 'kpi', y_key: 'total_revenue' },
  insight: 'Revenue is ₹2.1 Cr across delivered orders.',
})

const BAR = base({
  question: 'Profit for each Electronics product?',
  columns: ['product_id', 'product_name', 'profit'],
  rows: [[4, 'Orvexa Bluetooth Speaker', 560801.88], [5, 'Nuvaro Smart TV', 132624.67]],
  row_count: 2,
  visualization: { type: 'bar', x_key: 'product_name', y_key: 'profit' },
})

const LINE = base({
  question: 'Monthly revenue?',
  columns: ['month', 'revenue'],
  rows: [['2026-04-01', 600000], ['2026-05-01', null], ['2026-06-01', 750000]],
  row_count: 3,
  visualization: { type: 'line', x_key: 'month', y_key: 'revenue' },
})

function render(result, options = {}) {
  const doc = new jsPDF({ unit: 'mm', format: 'a4', compress: true })
  registerFonts(doc)
  const drawn = []
  const text = doc.text.bind(doc)
  doc.text = (value, ...rest) => {
    drawn.push({ page: doc.getCurrentPageInfo().pageNumber, text: Array.isArray(value) ? value.join('\n') : String(value), y: rest[1] })
    return text(value, ...rest)
  }
  renderReport(doc, buildReportContent(result, { generatedAt: GENERATED, ...options }), options.render)
  return { doc, drawn, texts: drawn.map((entry) => entry.text) }
}

describe('renderReport', () => {
  it('draws a KPI report: brand, title, value in ₹, insight, table, SQL and page numbers', () => {
    const { doc, texts } = render(KPI)
    expect(doc.getNumberOfPages()).toBe(1)
    expect(texts).toEqual(expect.arrayContaining([
      'DataPilot', // the wordmark, when the logo image is not available
      'Analysis report',
      'ANALYSIS',
      'What is our total revenue?',
      'Total revenue',
      '₹2,13,04,631.99',
      'Insight',
      'Revenue is ₹2.1 Cr across delivered orders.',
      'Data',
      'Generated SQL',
      'FROM order_items oi',
      'Page 1 of 1',
      'DataPilot · Generated 19 Oct 2026, 14:05',
    ]))
    expect(texts.join('\n')).toContain('Generated 19 Oct 2026, 14:05  ·  1 row')
  })

  it('never draws app chrome: buttons, navigation or the sidebar', () => {
    for (const result of [KPI, BAR, LINE]) {
      const all = render(result).texts.join('\n')
      for (const chrome of ['Export PDF', 'Save Report', 'Saved', 'Ask DataPilot', 'History', 'Saved Reports', 'Log out', 'Back to', 'Ask another', 'Copy']) {
        expect(all.split('\n')).not.toContain(chrome)
      }
    }
  })

  it('draws a bar chart with its bars and values', () => {
    const { texts } = render(BAR)
    expect(texts).toEqual(expect.arrayContaining(['Profit by product name', 'Orvexa Bluetooth Speaker', '₹5.6 L', 'Highest shown', '₹5,60,801.88']))
  })

  it('draws a line chart with month labels and y-axis values', () => {
    const { texts } = render(LINE)
    expect(texts).toEqual(expect.arrayContaining(['Revenue by month', 'Apr 2026 – Jun 2026', 'Apr 2026', 'Jun 2026', '₹0', '₹8 L', 'NULL']))
  })

  it('draws a table-only result and an empty result without a chart', () => {
    const table = render(base({ columns: ['full_name', 'city'], rows: [['Girik Menon', 'Pune']], row_count: 1, visualization: { type: 'table' } }))
    expect(table.texts).toEqual(expect.arrayContaining(['Girik Menon', 'Full name', 'AI insight is unavailable for this result.']))
    const empty = render(base({ columns: ['x'], rows: [], row_count: 0, visualization: { type: 'table' } }))
    expect(empty.texts).toEqual(expect.arrayContaining(['No matching data was found for this question.', 'Generated SQL']))
    expect(empty.texts).not.toContain('Data')
  })

  it('splits a long table across pages, repeating the header, keeping every row and numbering every page', () => {
    const rows = Array.from({ length: 140 }, (_, i) => [`Customer ${String(i + 1).padStart(3, '0')}`, `City ${i}`, 1000 + i * 37.5])
    const { doc, drawn, texts } = render(base({ columns: ['full_name', 'city', 'total_spending'], rows, row_count: 140, visualization: { type: 'table' } }))
    const pages = doc.getNumberOfPages()
    expect(pages).toBeGreaterThanOrEqual(3)
    for (let page = 1; page <= pages; page += 1) expect(texts).toContain(`Page ${page} of ${pages}`)
    for (let i = 1; i <= 140; i += 1) expect(texts).toContain(`Customer ${String(i).padStart(3, '0')}`)
    expect(texts).toContain('₹6,212.50') // the last row's ₹ value
    // The header row is drawn at the top of every page the table reaches.
    const headerPages = new Set(drawn.filter((entry) => entry.text === 'Total spending').map((entry) => entry.page))
    const rowPages = new Set(drawn.filter((entry) => entry.text.startsWith('Customer ')).map((entry) => entry.page))
    expect([...rowPages].every((page) => headerPages.has(page))).toBe(true)
    // Nothing is drawn below the content area except the footer.
    const body = drawn.filter((entry) => !/^Page \d+ of \d+$/.test(entry.text) && !entry.text.startsWith('DataPilot · Generated'))
    expect(body.every((entry) => entry.y === undefined || entry.y < PAGE.bottom)).toBe(true)
  })

  it('cuts a very long cell instead of letting one row outgrow a page', () => {
    const long = 'word '.repeat(600)
    const { doc } = render(base({ columns: ['note'], rows: [[long]], row_count: 1, visualization: { type: 'table' } }))
    expect(doc.getNumberOfPages()).toBeLessThanOrEqual(2)
  })

  it('continues a long SQL query on the next page', () => {
    const sql = Array.from({ length: 160 }, (_, i) => `  AND column_${i} IS NOT NULL`).join('\n')
    const { doc, texts } = render({ ...KPI, sql })
    expect(doc.getNumberOfPages()).toBeGreaterThanOrEqual(3)
    expect(texts.join('\n')).toContain('AND column_159 IS NOT NULL')
  })

  it('uses the logo image in the header when it is available', () => {
    const doc = new jsPDF({ unit: 'mm', format: 'a4' })
    registerFonts(doc)
    const images = []
    doc.addImage = (...args) => images.push(args)
    renderReport(doc, buildReportContent(KPI, { generatedAt: GENERATED }), { logo: { dataUrl: 'data:image/png;base64,AAAA', ratio: 296.34 / 52 } })
    expect(images).toHaveLength(1)
    expect(images[0].slice(1, 3)).toEqual(['PNG', PAGE.margin])
  })

  it('embeds the subset fonts and produces a PDF file', () => {
    const { doc } = render(KPI)
    expect(Object.keys(doc.getFontList())).toEqual(expect.arrayContaining([FONTS.text, FONTS.mono, FONTS.brand]))
    const bytes = new Uint8Array(doc.output('arraybuffer'))
    expect(String.fromCharCode(...bytes.slice(0, 5))).toBe('%PDF-')
  })
})
