// Fixtures and helpers shared by the PPTX tests. Every analysis here is made up for the tests.

import JSZip from 'jszip'

export const GENERATED = new Date(2026, 9, 19, 14, 5)

export const KPI = {
  question: 'What is our total revenue?',
  sql: 'SELECT SUM(oi.quantity * oi.unit_price) AS total_revenue FROM order_items oi',
  columns: ['total_revenue'],
  rows: [[21304631.99]],
  row_count: 1,
  truncated: false,
  visualization: { type: 'kpi', y_key: 'total_revenue' },
  insight: 'Revenue is ₹2.13 Cr.',
}

export const BAR = {
  question: 'What is the profit for each product in the Electronics category?',
  sql: 'SELECT product_name, profit FROM x',
  columns: ['product_id', 'product_name', 'profit'],
  rows: [[4, 'Orvexa Bluetooth Speaker', 560801.88], [5, 'Nuvaro Smart TV', 132624.67], [3, 'Kalvex Laptop', 1923.18]],
  row_count: 3,
  truncated: false,
  visualization: { type: 'bar', x_key: 'product_name', y_key: 'profit' },
  insight: 'Orvexa leads.',
}

export const LINE = {
  question: 'Show monthly revenue for the last 6 months.',
  sql: 'SELECT month, revenue FROM x',
  columns: ['month', 'revenue'],
  rows: [['2026-04-01', 600000], ['2026-05-01', 800000], ['2026-06-01', null], ['2026-07-01', 750000]],
  row_count: 4,
  truncated: false,
  visualization: { type: 'line', x_key: 'month', y_key: 'revenue' },
  insight: 'Revenue dipped in June.',
}

export const TABLE_ONLY = {
  question: 'Who are our top customers?',
  sql: 'SELECT full_name, email, total_spending FROM x',
  columns: ['full_name', 'email', 'total_spending'],
  rows: [['Girik Menon', 'girik@example.com', 370418.49], ['Tanish Om', null, 267573.19]],
  row_count: 2,
  truncated: true,
  visualization: { type: 'table' },
  insight: null,
}

export const EMPTY = { ...TABLE_ONLY, rows: [], row_count: 0, truncated: false }

/** A table-only result of count rows, each named "Row 0001", "Row 0002", … */
export function manyRows(count, columns = ['label', 'amount']) {
  const rows = Array.from({ length: count }, (_, index) => {
    const row = [`Row ${String(index + 1).padStart(4, '0')}`, 1000 + index]
    while (row.length < columns.length) row.push(`value ${index + 1}`)
    return row
  })
  return { ...TABLE_ONLY, columns, rows, row_count: count, truncated: false, insight: null, sql: 'SELECT label FROM x' }
}

export function deepFreeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(deepFreeze)
    Object.freeze(value)
  }
  return value
}

/** A saved .pptx opened as a zip, with the text of each slide. */
export async function openDeck(blob) {
  const zip = await JSZip.loadAsync(await blob.arrayBuffer())
  const slideNames = Object.keys(zip.files)
    .filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
    .sort((a, b) => Number(a.match(/\d+/)[0]) - Number(b.match(/\d+/)[0]))
  const xml = await Promise.all(slideNames.map((name) => zip.file(name).async('string')))
  const chartNames = Object.keys(zip.files).filter((name) => /^ppt\/charts\/chart\d+\.xml$/.test(name))
  const layoutNames = Object.keys(zip.files).filter((name) => /^ppt\/slide(Layouts|Masters)\/[^/]+\.xml$/.test(name))
  return {
    zip,
    names: Object.keys(zip.files),
    chartNames,
    // The first chart's XML (PptxGenJS numbers charts across decks, so the file name is not fixed).
    chart: () => zip.file(chartNames[0]).async('string'),
    // What every slide shares (logo, header, footer) lives in the layout and master parts.
    shared: async () => (await Promise.all(layoutNames.map((name) => zip.file(name).async('string')))).join('\n'),
    slideCount: slideNames.length,
    xml,
    // The text of slide n (1-based), one string per text run, with XML escapes undone.
    text: (n) => [...xml[n - 1].matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((match) => unescapeXml(match[1])),
    all: () => xml.flatMap((_, index) => [...xml[index].matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((match) => unescapeXml(match[1]))),
  }
}

function unescapeXml(value) {
  return value.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, '&')
}
