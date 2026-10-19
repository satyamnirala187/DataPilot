import PptxGenJS from 'pptxgenjs'
import { describe, expect, it } from 'vitest'
import { buildReportContent } from '../pdf/reportContent.js'
import { buildSlides } from './buildSlides.js'
import { chartNumberFormat, renderDeck } from './renderDeck.js'
import { BAR, GENERATED, KPI, LINE, manyRows, openDeck } from './testSupport.js'

// Drawing a plan into a real PptxGenJS presentation, then reading the XML back.

async function draw(result) {
  const pptx = new PptxGenJS()
  renderDeck(pptx, buildSlides(buildReportContent(result, { generatedAt: GENERATED })), { logo: null })
  return openDeck(await pptx.write({ outputType: 'blob' }))
}

describe('chartNumberFormat', () => {
  it('formats ₹ amounts, percentages and plain numbers, with decimals only when values have them', () => {
    expect(chartNumberFormat('currency', [1000, 2000])).toBe('"₹"#,##0')
    expect(chartNumberFormat('currency', [1000.5, 2000])).toBe('"₹"#,##0.00')
    expect(chartNumberFormat('percent', [10, 20])).toBe('0"%"')
    expect(chartNumberFormat('percent', [10.5, null])).toBe('0.0"%"')
    expect(chartNumberFormat('number', [1, 2, null])).toBe('#,##0')
    expect(chartNumberFormat('number', [1.5])).toBe('#,##0.00')
  })
})

describe('renderDeck', () => {
  it('uses only Arial and Courier New, so nothing depends on fonts that are not embedded', async () => {
    const deck = await draw({ ...BAR, sql: 'SELECT 1' })
    const faces = new Set(deck.xml.flatMap((xml) => [...xml.matchAll(/<a:latin typeface="([^"]+)"/g)].map((match) => match[1])))
    expect([...faces].sort()).toEqual(['Arial', 'Courier New'])
  })

  it('shows the SQL in Courier New, in one text box, as plain text', async () => {
    const deck = await draw({ ...KPI, sql: 'SELECT a\nFROM t' })
    const sqlSlide = deck.xml[deck.slideCount - 1]
    expect(sqlSlide).toContain('typeface="Courier New"')
    expect(deck.text(deck.slideCount)).toEqual(expect.arrayContaining(['SELECT a', 'FROM t']))
  })

  it('makes the data a real PowerPoint table, numbers right-aligned, one row per planned row', async () => {
    const deck = await draw(manyRows(5))
    const table = deck.xml[1]
    expect(table).toContain('<a:tbl>')
    expect((table.match(/<a:tr /g) ?? []).length).toBe(6) // header + 5 rows
    expect(table).toContain('algn="r"')
    expect(table).toContain('algn="l"')
  })

  it('draws no pictures except the logo: every chart, table and text is native', async () => {
    const deck = await draw(LINE)
    for (const xml of deck.xml) expect(xml).not.toContain('<p:pic>')
  })

  it('adds the chart slide only for charted results, and a graphic frame for it', async () => {
    expect((await draw(LINE)).xml[1]).toContain('<p:graphicFrame>')
    expect((await draw(KPI)).chartNames).toEqual([])
  })

  it('is a 16:9 widescreen deck', async () => {
    const deck = await draw(KPI)
    expect(await deck.zip.file('ppt/presentation.xml').async('string')).toContain('<p:sldSz cx="12192000" cy="6858000"/>')
  })
})
