import { describe, expect, it } from 'vitest'
import { buildReportContent } from '../pdf/reportContent.js'
import { MAX_INLINE_INSIGHT, MAX_INSIGHT, buildSlides, clip, wrapSql, xmlSafeText } from './buildSlides.js'
import { SLIDE } from './theme.js'
import { BAR, EMPTY, GENERATED, KPI, LINE, TABLE_ONLY, deepFreeze, manyRows } from './testSupport.js'

// The slide plan: what each slide holds and where every page break falls, without making a file.

function plan(result, options = {}) {
  return buildSlides(buildReportContent(result, { generatedAt: GENERATED, ...options }))
}

const types = (deck) => deck.slides.map((slide) => slide.type)
const of = (deck, type) => deck.slides.filter((slide) => slide.type === type)

describe('xmlSafeText', () => {
  it('keeps ₹, Indian grouping, accents, emoji and ordinary punctuation', () => {
    expect(xmlSafeText('₹2,13,04,631.99 · Café – “ok” 😀 <b> & "q"')).toBe('₹2,13,04,631.99 · Café – “ok” 😀 <b> & "q"')
  })

  it('removes characters PowerPoint XML cannot hold, and unpaired surrogates', () => {
    expect(xmlSafeText('a\u0000b\u0008c\u000Bd\u001Fe\u007Ff￾g￿h')).toBe('abcdefgh')
    expect(xmlSafeText('x\uD83Dy')).toBe('xy')
    expect(xmlSafeText('x\uDE00y')).toBe('xy')
  })

  it('turns tabs into spaces and line breaks into \\n', () => {
    expect(xmlSafeText('a\tb\r\nc\rd')).toBe('a    b\nc\nd')
  })

  it('treats null and undefined as empty text', () => {
    expect(xmlSafeText(null)).toBe('')
    expect(xmlSafeText(undefined)).toBe('')
  })
})

describe('clip', () => {
  it('leaves short text and cuts long text with …', () => {
    expect(clip('short', 10)).toBe('short')
    expect(clip('abcdefghij', 6)).toBe('abcde…')
  })

  it('never cuts an emoji in half', () => {
    expect(clip('ab😀cd', 4)).toBe('ab…')
  })
})

describe('slide order by kind of result', () => {
  it('KPI: Summary, Data, SQL, with the value and no chart slide', () => {
    const deck = plan(KPI)
    expect(types(deck)).toEqual(['summary', 'table', 'sql'])
    expect(deck.slides[0].kpi).toEqual({ label: 'Total revenue', value: '₹2,13,04,631.99' })
    expect(deck.slides[0].headline).toBe('Total revenue is ₹2.1 Cr.')
    expect(deck.slides[0].kicker).toBe('Analysis')
  })

  it('bar: Summary with tiles, Chart, Data, SQL', () => {
    const deck = plan(BAR)
    expect(types(deck)).toEqual(['summary', 'chart', 'table', 'sql'])
    expect(deck.slides[0].tiles.map((tile) => tile.label)).toEqual(['Highest shown', 'Lowest shown', 'Total of shown', 'Average of shown'])
    const { chart } = deck.slides[1]
    expect(chart.kind).toBe('bar')
    expect(chart.labels).toEqual(['Orvexa Bluetooth Speaker', 'Nuvaro Smart TV', 'Kalvex Laptop'])
    expect(chart.values).toEqual([560801.88, 132624.67, 1923.18])
    expect(chart.valueFormat).toBe('currency')
  })

  it('line: month labels, a gap kept as null, and the date range', () => {
    const deck = plan(LINE)
    expect(types(deck)).toEqual(['summary', 'chart', 'table', 'sql'])
    const slide = deck.slides[1]
    expect(slide.chart.kind).toBe('line')
    expect(slide.chart.labels).toEqual(['Apr 2026', 'May 2026', 'Jun 2026', 'Jul 2026'])
    expect(slide.chart.values).toEqual([600000, 800000, null, 750000])
    expect(slide.subtitle).toBe('Apr 2026 – Jul 2026')
  })

  it('table-only: no chart slide, no tiles, and the limited-result note on the data slide', () => {
    const deck = plan(TABLE_ONLY)
    expect(types(deck)).toEqual(['summary', 'table', 'sql'])
    expect(deck.slides[0].tiles).toEqual([])
    expect(deck.slides[1].subtitle).toBe('Only the first 2 rows are shown. The full result has more rows than DataPilot displays.')
    expect(deck.slides[1].rows[1]).toEqual(['Tanish Om', 'NULL', '₹2,67,573.19'])
  })

  it('empty: the no-matching-data summary and the SQL, no data or chart slide', () => {
    const deck = plan(EMPTY)
    expect(types(deck)).toEqual(['summary', 'sql'])
    expect(deck.slides[0].empty).toBe(true)
  })

  it('leaves the SQL slide out only when there is no SQL', () => {
    expect(types(plan({ ...KPI, sql: null }))).toEqual(['summary', 'table'])
  })

  it('caps bars at the same 20 as the PDF and says where the rest is', () => {
    const rows = Array.from({ length: 30 }, (_, index) => [`City ${index}`, 1000 - index])
    const deck = plan({ ...BAR, columns: ['city', 'customer_count'], rows, row_count: 30, visualization: { type: 'bar', x_key: 'city', y_key: 'customer_count' } })
    const slide = of(deck, 'chart')[0]
    expect(slide.chart.labels).toHaveLength(20)
    expect(slide.subtitle).toBe('Showing the first 20 of 30 rows; every row is in the Data table.')
    expect(of(deck, 'table').flatMap((table) => table.rows)).toHaveLength(30)
  })
})

describe('where the report came from', () => {
  it('labels a History analysis and has no separate question line', () => {
    const slide = plan(BAR, { source: 'history' }).slides[0]
    expect(slide.kicker).toBe('History analysis')
    expect(slide.question).toBeNull()
  })

  it('titles a saved report by its title and keeps the question beneath it', () => {
    const deck = plan(BAR, { source: 'saved', title: 'Electronics profit', savedAt: '2026-10-16T10:00:00+00:00' })
    expect(deck.title).toBe('Electronics profit')
    expect(deck.slides[0]).toMatchObject({ kicker: 'Saved report', title: 'Electronics profit', question: BAR.question })
    expect(deck.slides[0].meta).toContain('Saved 16 Oct 2026')
    expect(deck.footer).toBe('DataPilot · Generated 19 Oct 2026, 14:05')
  })
})

describe('insight placement', () => {
  const short = 'Revenue is up.'
  const long = `${'Revenue grew steadily across every category. '.repeat(14)}`.trim() // > 420 characters

  it('sits on the summary of a result with no chart when it fits', () => {
    const deck = plan({ ...KPI, insight: short })
    expect(types(deck)).toEqual(['summary', 'table', 'sql'])
    expect(deck.slides[0].insight).toBe(short)
    expect(deck.slides[0].layout.bottom).toBeLessThanOrEqual(SLIDE.contentBottom)
  })

  it('sits beside the chart when there is one, not on the summary', () => {
    const deck = plan({ ...LINE, insight: short })
    expect(deck.slides[0].insight).toBeNull()
    expect(deck.slides[1].insight).toBe(short)
  })

  it('has no insight slide or panel when there is no insight', () => {
    const deck = plan({ ...BAR, insight: '   ' })
    expect(types(deck)).toEqual(['summary', 'chart', 'table', 'sql'])
    expect(deck.slides[0].insight).toBeNull()
    expect(deck.slides[1].insight).toBeNull()
  })

  it('gets its own slide after the chart when it is too long to sit beside it', () => {
    expect(long.length).toBeGreaterThan(MAX_INLINE_INSIGHT)
    const deck = plan({ ...BAR, insight: long })
    expect(types(deck)).toEqual(['summary', 'chart', 'insight', 'table', 'sql'])
    expect(deck.slides[2].text).toBe(long)
  })

  it('gets its own slide after the summary of a KPI when it is too long', () => {
    expect(types(plan({ ...KPI, insight: long }))).toEqual(['summary', 'insight', 'table', 'sql'])
  })

  it('moves to its own slide when the summary has no room left for it', () => {
    const crowded = plan(
      { ...KPI, question: 'Why '.repeat(60).trim(), insight: 'Revenue is up. '.repeat(20).trim() },
      { source: 'saved', title: 'A very long saved report title '.repeat(8) },
    )
    expect(types(crowded)).toEqual(['summary', 'insight', 'table', 'sql'])
    expect(crowded.slides[0].insight).toBeNull()
  })

  it('is cut on its own slide only beyond what a slide can hold', () => {
    const huge = 'word '.repeat(1000)
    const slide = of(plan({ ...KPI, insight: huge }), 'insight')[0]
    expect(slide.text.length).toBeLessThanOrEqual(MAX_INSIGHT)
    expect(slide.text.endsWith('…')).toBe(true)
  })
})

describe('table pagination', () => {
  const dataSlides = (result) => of(plan(result), 'table')
  const rowsOn = (slides) => slides.flatMap((slide) => slide.rows.map((row) => row[0]))
  const expectedLabels = (count) => Array.from({ length: count }, (_, index) => `Row ${String(index + 1).padStart(4, '0')}`)

  it('keeps 13 rows on one slide titled just "Data"', () => {
    const slides = dataSlides(manyRows(13))
    expect(slides).toHaveLength(1)
    expect(slides[0].title).toBe('Data')
    expect(rowsOn(slides)).toEqual(expectedLabels(13))
  })

  it('continues 40 rows across slides with row-range titles, every row once and in order', () => {
    const slides = dataSlides(manyRows(40))
    expect(slides.length).toBeGreaterThan(1)
    expect(rowsOn(slides)).toEqual(expectedLabels(40))
    expect(slides[0].title).toBe(`Data · rows 1–${slides[0].to} of 40`)
    slides.forEach((slide, index) => {
      if (index > 0) expect(slide.from).toBe(slides[index - 1].to + 1) // no gap, no overlap
      expect(slide.title).toBe(`Data · rows ${slide.from}–${slide.to} of 40`)
      expect(slide.total).toBe(40)
    })
    expect(slides[slides.length - 1].to).toBe(40)
  })

  it('repeats the column headers on every slide', () => {
    const slides = dataSlides(manyRows(40))
    for (const slide of slides) expect(slide.columns.map((column) => column.label)).toEqual(['Label', 'Amount'])
  })

  it('fits every slide within the space under the header', () => {
    for (const slide of dataSlides(manyRows(120, ['label', 'amount', 'note']))) {
      const used = slide.headerHeight + slide.rowHeights.reduce((sum, height) => sum + height, 0)
      expect(slide.rows).toHaveLength(slide.rowHeights.length)
      expect(1.65 + used).toBeLessThanOrEqual(SLIDE.contentBottom + 1e-9)
    }
  })

  it('exports the 500-row maximum: every row exactly once, in order', () => {
    const slides = dataSlides(manyRows(500))
    const labels = rowsOn(slides)
    expect(labels).toEqual(expectedLabels(500))
    expect(new Set(labels).size).toBe(500)
    expect(slides.at(-1).title).toBe(`Data · rows ${slides.at(-1).from}–500 of 500`)
  })

  it('shrinks the font and keeps every column on the slide for a wide table', () => {
    const columns = Array.from({ length: 12 }, (_, index) => `column_${index + 1}`)
    const slides = dataSlides(manyRows(30, columns))
    expect(slides[0].fontSize).toBe(8)
    expect(slides[0].columns).toHaveLength(12)
    const total = slides[0].columns.reduce((sum, column) => sum + column.width, 0)
    expect(total).toBeCloseTo(SLIDE.contentWidth, 5)
    for (const column of slides[0].columns) expect(column.width).toBeGreaterThanOrEqual(0.9 - 1e-9)
    expect(rowsOn(slides)).toEqual(expectedLabels(30))
  })

  it('uses a bigger font for a narrow table than for a wide one', () => {
    expect(dataSlides(manyRows(3))[0].fontSize).toBe(12)
    expect(dataSlides(manyRows(3, ['a', 'b', 'c', 'd', 'e', 'f']))[0].fontSize).toBe(11)
  })

  it('cuts one extremely long cell, so the row cannot outgrow a slide', () => {
    const result = manyRows(3)
    result.rows[1][0] = `${'very long text '.repeat(300)}`
    const [slide] = dataSlides(result)
    const cell = slide.rows[1][0]
    expect(cell.length).toBeLessThan(300) // three lines of the column at most, not 4,800 characters
    expect(cell.endsWith('…')).toBe(true)
    expect(Math.max(...slide.rowHeights)).toBeLessThan(1.5)
    expect(slide.rows).toHaveLength(3)
  })

  it('turns line breaks inside a cell into spaces', () => {
    const result = manyRows(2)
    result.rows[0][0] = 'first\nsecond'
    expect(dataSlides(result)[0].rows[0][0]).toBe('first second')
  })

  it('states the row count, or that the result is limited', () => {
    expect(dataSlides(manyRows(1))[0].subtitle).toBe('1 row')
    expect(dataSlides(manyRows(5))[0].subtitle).toBe('5 rows')
  })
})

describe('Generated SQL', () => {
  const sqlSlides = (sql) => of(plan({ ...KPI, sql }), 'sql')

  it('is one slide of plain text for a short query', () => {
    const slides = sqlSlides('SELECT a\nFROM t\nWHERE x < 3 AND y > "q"')
    expect(slides).toHaveLength(1)
    expect(slides[0].title).toBe('Generated SQL')
    expect(slides[0].lines).toEqual(['SELECT a', 'FROM t', 'WHERE x < 3 AND y > "q"'])
  })

  it('continues a long query on further slides, titled "n of N"', () => {
    const sql = Array.from({ length: 60 }, (_, index) => `  col_${index},`).join('\n')
    const slides = sqlSlides(`SELECT\n${sql}\nFROM t`)
    expect(slides.length).toBeGreaterThan(2)
    slides.forEach((slide, index) => expect(slide.title).toBe(`Generated SQL (${index + 1} of ${slides.length})`))
    expect(slides.flatMap((slide) => slide.lines)).toEqual(['SELECT', ...sql.split('\n'), 'FROM t'])
  })

  it('wraps a very long line without losing a word, within the slide width', () => {
    const words = Array.from({ length: 80 }, (_, index) => `column_${index}`)
    const lines = wrapSql(`SELECT ${words.join(', ')} FROM t`)
    expect(lines.length).toBeGreaterThan(1)
    for (const line of lines) expect(line.length).toBeLessThanOrEqual(100)
    expect(lines.join(' ').replace(/\s+/g, ' ')).toBe(`SELECT ${words.join(', ')} FROM t`)
  })

  it('splits a single word longer than the line', () => {
    const lines = wrapSql('x'.repeat(250))
    expect(lines.map((line) => line.length)).toEqual([100, 100, 50])
  })

  it('removes tabs and characters PowerPoint XML cannot hold', () => {
    expect(wrapSql('SELECT\t1\u0000')).toEqual(['SELECT  1'])
  })
})

describe('the plan', () => {
  it('reads only the report and never changes it', () => {
    const content = deepFreeze(buildReportContent(manyRows(40), { generatedAt: GENERATED }))
    expect(() => buildSlides(content)).not.toThrow()
  })

  it('has clean text everywhere: no control characters reach the file', () => {
    const dirty = { ...KPI, question: 'Revenue\u0000 by\u0008 city?', insight: 'Up\u000B by 5%', sql: 'SELECT 1\u0001' }
    expect(JSON.stringify(plan(dirty))).not.toMatch(/\\u00(0[0-8bBcCeEfF]|1[0-9a-fA-F])/)
  })
})
