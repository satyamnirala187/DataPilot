import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildReportContent } from '../pdf/reportContent.js'
import { buildSlides } from './buildSlides.js'
import { PPTX_MIME, exportAnalysisPptx } from './exportPptx.js'
import { BAR, EMPTY, GENERATED, KPI, LINE, TABLE_ONLY, deepFreeze, manyRows, openDeck } from './testSupport.js'

// Making and downloading the deck, with a fixed clock and a fake download: nothing leaves the
// browser. The .pptx is a real file: it is opened again here as a zip and its XML is read.

const LOGO = { dataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', ratio: 5.7 }

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function exportDeck(result, options = {}, overrides = {}) {
  let saved = null
  const download = vi.fn((blob, filename) => { saved = { blob, filename } })
  const filename = await exportAnalysisPptx(result, options, { now: () => GENERATED, loadLogo: async () => LOGO, download, ...overrides })
  return { filename, download, saved, deck: await openDeck(saved.blob) }
}

describe('exportAnalysisPptx', () => {
  it('downloads a real .pptx: a typed zip with the PowerPoint parts, under a dated name', async () => {
    const { filename, saved, deck, download } = await exportDeck(BAR, { source: 'history' })
    expect(filename).toBe('datapilot-profit-product-electronics-category-2026-10-19.pptx')
    expect(download).toHaveBeenCalledTimes(1)
    expect(saved.filename).toBe(filename)
    expect(saved.blob.type).toBe(PPTX_MIME)
    const head = new Uint8Array(await saved.blob.arrayBuffer()).slice(0, 2)
    expect(String.fromCharCode(...head)).toBe('PK') // a zip
    expect(deck.names).toEqual(expect.arrayContaining(['[Content_Types].xml', 'ppt/presentation.xml', 'ppt/slideMasters/slideMaster1.xml']))
    expect(await deck.zip.file('docProps/core.xml').async('string')).toContain('What is the profit for each product in the Electronics category?')
  })

  it('names a saved report by its title', async () => {
    const { filename } = await exportDeck(BAR, { source: 'saved', title: 'Electronics profit', savedAt: '2026-10-19T08:10:00+00:00' })
    expect(filename).toBe('datapilot-electronics-profit-2026-10-19.pptx')
  })

  it('makes one slide per planned slide, each numbered "n of N"', async () => {
    const { deck } = await exportDeck(BAR)
    const planned = buildSlides(buildReportContent(BAR, { generatedAt: GENERATED })).slides.length
    expect(deck.slideCount).toBe(planned)
    for (let n = 1; n <= planned; n += 1) expect(deck.text(n)).toContain(`${n} of ${planned}`)
  })

  it('never changes the analysis it is given', async () => {
    await expect(exportDeck(deepFreeze(structuredClone(manyRows(40))))).resolves.toBeDefined()
    const before = JSON.stringify(BAR)
    await exportDeck(BAR)
    expect(JSON.stringify(BAR)).toBe(before)
  })

  it('makes no request: no fetch, no XMLHttpRequest, nothing to the backend or an AI provider', async () => {
    const fetchSpy = vi.fn()
    const xhrSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    vi.stubGlobal('XMLHttpRequest', xhrSpy)
    await exportDeck(LINE)
    await exportDeck(manyRows(60))
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(xhrSpy).not.toHaveBeenCalled()
  })

  it('needs no eval or Function constructor, which the Content Security Policy forbids', async () => {
    const blocked = vi.fn(() => { throw new EvalError('blocked: the CSP has no unsafe-eval') })
    // Replacing eval is the test: oxlint's no-eval is about calling it, which this never does.
    // oxlint-disable-next-line no-eval
    const { Function: RealFunction, eval: realEval } = globalThis
    globalThis.Function = new Proxy(RealFunction, { construct: blocked, apply: blocked })
    // oxlint-disable-next-line no-eval
    globalThis.eval = blocked
    try {
      await exportDeck(LINE)
      await exportDeck(manyRows(60))
    } finally {
      globalThis.Function = RealFunction
      // oxlint-disable-next-line no-eval
      globalThis.eval = realEval
    }
    expect(blocked).not.toHaveBeenCalled()
  })

  it('rejects, without downloading, when the deck cannot be made', async () => {
    const download = vi.fn()
    await expect(exportAnalysisPptx(BAR, {}, { now: () => GENERATED, loadLogo: async () => { throw new Error('boom') }, download })).rejects.toThrow()
    expect(download).not.toHaveBeenCalled()
  })

  it('still exports when the logo cannot be drawn, with the DataPilot wordmark instead', async () => {
    const { deck } = await exportDeck(KPI, {}, { loadLogo: async () => null })
    expect(deck.names.some((name) => name.endsWith('.png'))).toBe(false)
    expect(await deck.shared()).toContain('<a:t>DataPilot</a:t>')
  })

  it('embeds the logo as a PNG when it is available', async () => {
    const { deck } = await exportDeck(KPI)
    const png = deck.names.find((name) => name.startsWith('ppt/media/') && name.endsWith('.png'))
    expect(png).toBeDefined()
    const bytes = await deck.zip.file(png).async('uint8array')
    expect([...bytes.slice(0, 4)]).toEqual([137, 80, 78, 71])
  })
})

describe('what each kind of result produces', () => {
  it('KPI: the ₹ value, headline, insight, table and SQL, and no chart', async () => {
    const { deck } = await exportDeck(KPI)
    expect(deck.slideCount).toBe(3)
    const summary = deck.text(1)
    expect(summary).toEqual(expect.arrayContaining(['ANALYSIS', 'What is our total revenue?', 'Total revenue', '₹2,13,04,631.99', 'Total revenue is ₹2.1 Cr.', 'Revenue is ₹2.13 Cr.']))
    expect(deck.chartNames).toEqual([])
    expect(deck.text(2)).toEqual(expect.arrayContaining(['Data', 'Total revenue', '₹2,13,04,631.99']))
    expect(deck.text(3)).toContain(KPI.sql)
  })

  it('bar: a native, editable bar chart with its data workbook, tiles and the insight beside it', async () => {
    const { deck } = await exportDeck(BAR)
    expect(deck.text(1)).toEqual(expect.arrayContaining(['Highest shown', '₹5.6 L', 'Orvexa Bluetooth Speaker']))
    const chart = await deck.chart()
    expect(chart).toContain('<c:barChart>')
    expect(chart).toContain('<c:barDir val="bar"/>') // horizontal
    expect(chart).toContain('Orvexa Bluetooth Speaker')
    expect(chart).toContain('560801.88')
    expect(chart).toContain('2A59E6') // the DataPilot blue
    expect(chart).toContain('formatCode="&quot;₹&quot;#,##0.00"')
    expect(deck.names.some((name) => /^ppt\/embeddings\/.*\.xlsx$/.test(name))).toBe(true)
    expect(deck.text(2)).toEqual(expect.arrayContaining(['Profit by product name', 'INSIGHT', 'Orvexa leads.']))
    // The chart is a real graphic frame on the slide, not a picture.
    expect(deck.xml[1]).toContain('drawingml/2006/chart')
    expect(deck.xml[1]).not.toContain('<p:pic>')
  })

  it('bar: the first row is the top bar', async () => {
    const { deck } = await exportDeck(BAR)
    const chart = await deck.chart()
    const labels = [...chart.matchAll(/<c:lvl>(.*?)<\/c:lvl>/g)][0][1]
    expect([...labels.matchAll(/<c:v>([^<]*)<\/c:v>/g)].map((match) => match[1])).toEqual(['Kalvex Laptop', 'Nuvaro Smart TV', 'Orvexa Bluetooth Speaker'])
  })

  it('line: a native line chart with markers, month labels and a gap for the missing month', async () => {
    const { deck } = await exportDeck(LINE)
    const chart = await deck.chart()
    expect(chart).toContain('<c:lineChart>')
    expect(chart).toContain('<c:symbol val="circle"/>')
    expect(chart).toContain('<c:dispBlanksAs val="gap"/>')
    expect(chart).toContain('Apr 2026')
    expect(chart).toContain('Jul 2026')
    expect(chart).toContain('<c:pt idx="2"><c:v></c:v></c:pt>') // the missing June value is blank
    expect(chart).toContain('<c:v>600000</c:v>')
    expect(deck.text(2)).toEqual(expect.arrayContaining(['Revenue by month', 'Apr 2026 – Jul 2026', 'Revenue dipped in June.']))
  })

  it('table-only: no chart, no chart parts, the limited-result note and NULL cells', async () => {
    const { deck } = await exportDeck(TABLE_ONLY)
    expect(deck.slideCount).toBe(3)
    expect(deck.chartNames).toEqual([])
    expect(deck.text(2)).toEqual(expect.arrayContaining(['Girik Menon', 'NULL', '₹3,70,418.49', 'Only the first 2 rows are shown. The full result has more rows than DataPilot displays.']))
  })

  it('empty result: the no-matching-data slide and the SQL', async () => {
    const { deck } = await exportDeck(EMPTY)
    expect(deck.slideCount).toBe(2)
    expect(deck.text(1)).toEqual(expect.arrayContaining(['No matching data was found for this question.', 'The query ran successfully and returned 0 rows.']))
    expect(deck.text(2)).toContain(EMPTY.sql)
  })

  it('History and Saved Report: labelled by source, the saved one under its own title with the question', async () => {
    const history = (await exportDeck(BAR, { source: 'history' })).deck
    expect(history.text(1)).toContain('HISTORY ANALYSIS')
    const saved = (await exportDeck(BAR, { source: 'saved', title: 'Electronics profit', savedAt: '2026-10-16T10:00:00+00:00' })).deck
    const summary = saved.text(1)
    expect(summary).toEqual(expect.arrayContaining(['SAVED REPORT', 'Electronics profit', `Question: ${BAR.question}`]))
    expect(summary.join(' ')).toContain('Saved 16 Oct 2026')
  })

  it('every slide carries the DataPilot header and footer', async () => {
    const { deck } = await exportDeck(BAR)
    const master = await deck.shared()
    expect(master).toContain('Analysis report')
    expect(master).toContain('DataPilot · Generated 19 Oct 2026, 14:05')
  })
})

describe('long results and awkward text', () => {
  async function dataRowCount(result, label = 'Row') {
    const { deck } = await exportDeck(result)
    const found = deck.all().filter((text) => new RegExp(`^${label} \\d{4}$`).test(text))
    return { deck, found }
  }

  it.each([13, 40, 500])('puts every one of %i rows in the deck exactly once, in order', async (count) => {
    const { found } = await dataRowCount(manyRows(count))
    expect(found).toEqual(Array.from({ length: count }, (_, index) => `Row ${String(index + 1).padStart(4, '0')}`))
  })

  it('continues 40 rows over several slides with row ranges, and keeps the header on each', async () => {
    const { deck } = await dataRowCount(manyRows(40))
    const titles = deck.xml.map((_, index) => deck.text(index + 1)).filter((text) => text.some((value) => value.startsWith('Data · rows')))
    expect(titles.length).toBeGreaterThan(1)
    for (const text of titles) expect(text).toEqual(expect.arrayContaining(['Label', 'Amount']))
  })

  it('continues a long query over several slides without losing a line', async () => {
    const lines = Array.from({ length: 70 }, (_, index) => `  column_${index},`)
    const sql = `SELECT\n${lines.join('\n')}\n  last_column\nFROM t`
    const { deck } = await exportDeck({ ...KPI, sql })
    const sqlText = deck.all().filter((text) => text.includes('column_') || text === 'SELECT')
    expect(deck.all().some((text) => text.startsWith('Generated SQL (1 of'))).toBe(true)
    expect(sqlText.join('\n')).toContain('column_69,')
    expect(sqlText.join('\n')).toContain('column_0,')
  })

  it('writes <, > and & as text, and drops characters the file format forbids', async () => {
    const result = { ...KPI, question: 'Revenue <1 & >2\u0000 by city?', insight: 'Up\u000B 5% & "rising"', sql: "SELECT '<a>' AS x, 1\u0001" }
    const { deck } = await exportDeck(result)
    expect(deck.text(1)).toEqual(expect.arrayContaining(['Revenue <1 & >2 by city?', 'Up 5% & "rising"']))
    expect(deck.all()).toContain("SELECT '<a>' AS x, 1")
    const forbidden = [...Array(32).keys()].filter((code) => ![9, 10, 13].includes(code))
    for (const xml of deck.xml) expect(forbidden.some((code) => xml.includes(String.fromCharCode(code)))).toBe(false)
  })

  it('keeps ₹ and accented text', async () => {
    const { deck } = await exportDeck({ ...TABLE_ONLY, rows: [['Zoë Café', 'a@b.in', 1234.5]] })
    expect(deck.all()).toEqual(expect.arrayContaining(['Zoë Café', '₹1,234.50']))
  })
})
