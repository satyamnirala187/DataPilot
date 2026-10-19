// Turns a report (from buildReportContent) into a slide plan: plain objects saying what each slide
// holds, with every page break decided here. Nothing is drawn and PptxGenJS is not used, so the
// layout rules can be tested without making a file. renderDeck.js draws the plan.
//
// Slides, in order: Summary, Chart (bar and line results), an Insight slide only when the insight
// is too long to sit beside the summary or chart, Data (as many slides as the rows need) and
// Generated SQL (more than one only for a very long query).

import { SLIDE } from './theme.js'

// Insight longer than this is not put beside the summary or chart; it gets its own slide.
export const MAX_INLINE_INSIGHT = 420
// Even on its own slide an insight is cut here, so one slide can always hold it.
export const MAX_INSIGHT = 1800
const MAX_TITLE = 200
const MAX_QUESTION = 240
const MAX_NOTE = 44
const MAX_TILE_VALUE = 20
const MAX_BAR_LABEL = 40

// Table layout, in inches and points. The numbers are deliberately conservative: a row taller than
// planned grows in PowerPoint, so every row is budgeted for its likely wrapped height.
const TABLE = Object.freeze({ top: 1.65, bottom: SLIDE.contentBottom, cellPad: 0.14, maxLines: 3 })
const AVG_CHAR_EM = 0.55 // an Arial character is ~0.5 em wide; wider keeps wrapping estimates safe
const WRAP_SAFETY = 0.85 // words break early, so a line holds fewer characters than width allows

const SQL_COLUMNS = 100
const SQL_LINES_PER_SLIDE = 24

/**
 * Text that is valid inside PowerPoint's XML: control characters the format forbids and unpaired
 * surrogates are removed, tabs become spaces and line breaks become \n. PptxGenJS escapes < > &
 * itself; it does not remove these.
 */
export function xmlSafeText(value) {
  return String(value ?? '')
    .normalize('NFC')
    .replace(/\r\n?/g, '\n')
    .replace(/\t/g, '    ')
    // oxlint-disable-next-line no-control-regex -- removing control characters is the point
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\uFFFE\uFFFF]/g, '')
    .replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g, '')
}

/** value cut with "…" so it is at most max characters. */
export function clip(value, max) {
  const text = xmlSafeText(value)
  if (text.length <= max) return text
  let cut = text.slice(0, max - 1)
  if (/[\uD800-\uDBFF]$/.test(cut)) cut = cut.slice(0, -1) // not in the middle of an emoji
  return `${cut.trimEnd()}…`
}

/** The deck for one report: { title, footer, slides }. Each slide has a `type`. */
export function buildSlides(content) {
  const insight = xmlSafeText(content.insight).trim() || null
  const hasChart = Boolean(content.chart) && !content.empty
  const slides = []
  let insightPlaced = !insight

  // Beside the summary only when it is short and the summary still has room for it.
  let summary = summarySlide(content, null)
  if (!insightPlaced && !hasChart && insight.length <= MAX_INLINE_INSIGHT) {
    const withInsight = summarySlide(content, insight)
    if (withInsight.layout.bottom <= SLIDE.contentBottom) {
      summary = withInsight
      insightPlaced = true
    }
  }
  slides.push(summary)
  if (hasChart) {
    const inline = !insightPlaced && insight.length <= MAX_INLINE_INSIGHT
    slides.push(chartSlide(content.chart, inline ? insight : null))
    insightPlaced = insightPlaced || inline
  }
  if (!insightPlaced) slides.push({ type: 'insight', title: 'Insight', text: clip(insight, MAX_INSIGHT) })
  if (!content.empty) slides.push(...tableSlides(content))
  if (content.sql) slides.push(...sqlSlides(content.sql))

  return {
    title: clip(content.title, MAX_TITLE),
    footer: `DataPilot · Generated ${xmlSafeText(content.generated)}`,
    slides,
  }
}

function summarySlide(content, insight) {
  const title = clip(content.title, MAX_TITLE)
  const slide = {
    type: 'summary',
    kicker: xmlSafeText(content.kicker),
    title,
    titleSize: title.length <= 70 ? 28 : title.length <= 130 ? 24 : 20,
    question: content.question ? clip(content.question, MAX_QUESTION) : null,
    meta: xmlSafeText([`Generated ${content.generated}`, ...content.meta].join('  ·  ')),
    headline: content.headline ? clip(content.headline, 240) : null,
    empty: Boolean(content.empty),
    kpi: content.kpi ? { label: clip(content.kpi.label, 80), value: clip(content.kpi.value, 40) } : null,
    tiles: content.kpi || content.empty ? [] : content.tiles.slice(0, 4).map((tile) => ({
      label: clip(tile.label, 28),
      value: clip(tile.value, MAX_TILE_VALUE),
      note: clip(tile.note, MAX_NOTE),
    })),
    insight,
  }
  return { ...slide, layout: layoutSummary(slide) }
}

// --- Summary layout ------------------------------------------------------------------------------

/** Estimated height (inches) of text wrapped in a box width inches wide. */
export function textHeight(text, fontSize, width) {
  const perLine = Math.max(1, Math.floor(((width * 72) / (AVG_CHAR_EM * fontSize)) * WRAP_SAFETY))
  const lines = String(text).split('\n').reduce((sum, line) => sum + Math.max(1, Math.ceil(line.length / perLine)), 0)
  return (lines * fontSize * 1.25) / 72
}

/** Where each part of the summary slide goes, top to bottom; `bottom` is where its last part ends. */
function layoutSummary(slide) {
  const width = SLIDE.contentWidth
  const layout = {}
  let y = 1.1
  layout.kicker = { y, h: 0.3 }
  y += 0.38
  layout.title = { y, h: textHeight(slide.title, slide.titleSize, width) }
  y += layout.title.h + 0.12
  if (slide.question) {
    layout.question = { y, h: textHeight(`Question: ${slide.question}`, 12, width) }
    y += layout.question.h + 0.08
  }
  layout.meta = { y, h: textHeight(slide.meta, 11, width) }
  y += layout.meta.h + 0.22
  if (slide.headline) {
    layout.headline = { y, h: textHeight(slide.headline, 16, width) }
    y += layout.headline.h + 0.25
  }
  const bodyHeight = slide.empty ? 1.3 : slide.kpi ? 1.6 : slide.tiles.length > 0 ? 1.35 : 0
  layout.body = { y, h: bodyHeight }
  if (bodyHeight > 0) y += bodyHeight + 0.3
  if (slide.insight) {
    const text = textHeight(slide.insight, 13, width - 0.7)
    layout.insight = { y, h: text + 0.75, textHeight: text }
    y += layout.insight.h
  }
  layout.bottom = y
  return layout
}

function chartSlide(chart, insight) {
  const bar = chart.kind === 'bar'
  return {
    type: 'chart',
    title: clip(chart.title, 120),
    subtitle: xmlSafeText(bar ? chart.note ?? '' : chart.range),
    chart: {
      kind: chart.kind,
      valueFormat: chart.valueFormat,
      seriesName: clip(chart.title, 60),
      labels: bar ? chart.bars.map((point) => clip(point.label, MAX_BAR_LABEL)) : chart.points.map((point) => xmlSafeText(point.label)),
      values: (bar ? chart.bars : chart.points).map((point) => point.value),
    },
    insight,
  }
}

// --- Data table ----------------------------------------------------------------------------------

/** Font size for the table: the more columns, the smaller. */
function tableFontSize(columnCount) {
  if (columnCount <= 4) return 12
  if (columnCount <= 6) return 11
  if (columnCount <= 8) return 10
  if (columnCount <= 10) return 9
  return 8
}

function charsPerLine(width, fontSize, em = AVG_CHAR_EM) {
  const usable = width - 0.2 // the cell's left and right padding
  return Math.max(4, Math.floor((usable / ((em * fontSize) / 72)) * WRAP_SAFETY))
}

function tableSlides(content) {
  const { columns, rows } = content.table
  if (columns.length === 0) return []

  const fontSize = tableFontSize(columns.length)
  const widths = columnWidths(columns, rows)
  const lineHeight = (fontSize * 1.2) / 72
  const heightOf = (lines) => lines * lineHeight + TABLE.cellPad

  const headers = columns.map((column, index) => clip(column.label, 2 * charsPerLine(widths[index], fontSize, 0.62)))
  const headerLines = headers.map((label, index) => lineCount(label, charsPerLine(widths[index], fontSize, 0.62)))
  const headerHeight = heightOf(Math.max(...headerLines))

  // Cells are cut to a few lines, so one very long value can never make a row taller than a slide.
  const planned = rows.map((row) => {
    const cells = columns.map((_, index) => {
      const perLine = charsPerLine(widths[index], fontSize)
      return clip(String(row[index] ?? '').replace(/\s*\n\s*/g, ' '), TABLE.maxLines * perLine)
    })
    const lines = Math.max(...cells.map((cell, index) => lineCount(cell, charsPerLine(widths[index], fontSize))))
    return { cells, height: heightOf(lines) }
  })

  const available = TABLE.bottom - TABLE.top - headerHeight
  const pages = []
  let current = []
  let used = 0
  planned.forEach((row, index) => {
    if (current.length > 0 && used + row.height > available) {
      pages.push(current)
      current = []
      used = 0
    }
    current.push({ ...row, number: index + 1 })
    used += row.height
  })
  if (current.length > 0) pages.push(current)
  if (pages.length === 0) pages.push([]) // columns but no rows: the header alone

  const subtitle = content.truncated
    ? `Only the first ${content.rowCount} rows are shown. The full result has more rows than DataPilot displays.`
    : `${content.rowCount} ${content.rowCount === 1 ? 'row' : 'rows'}`
  return pages.map((page) => {
    const from = page[0]?.number ?? 0
    const to = page[page.length - 1]?.number ?? 0
    return {
      type: 'table',
      title: pages.length > 1 ? `Data · rows ${from}–${to} of ${rows.length}` : 'Data',
      subtitle,
      columns: columns.map((column, index) => ({ label: headers[index], numeric: column.numeric, width: widths[index] })),
      headerHeight,
      fontSize,
      from,
      to,
      total: rows.length,
      rows: page.map((row) => row.cells),
      rowHeights: page.map((row) => row.height),
    }
  })
}

function lineCount(text, perLine) {
  return Math.max(1, Math.ceil(text.length / perLine))
}

/** Column widths (inches) filling the slide: each in proportion to its longest value, none tiny. */
function columnWidths(columns, rows) {
  const sample = rows.slice(0, 500)
  const weights = columns.map((column, index) => {
    let longest = column.label.length
    for (const row of sample) longest = Math.max(longest, String(row[index] ?? '').length)
    return Math.min(40, Math.max(6, longest))
  })
  const total = weights.reduce((sum, weight) => sum + weight, 0)
  const widths = weights.map((weight) => (weight / total) * SLIDE.contentWidth)
  const minimum = Math.min(0.9, SLIDE.contentWidth / columns.length)
  const short = widths.reduce((sum, width) => sum + Math.max(0, minimum - width), 0)
  if (short === 0) return widths
  // Lift the narrow columns to the minimum and pay for it from the wide ones, in proportion.
  const spare = widths.reduce((sum, width) => sum + Math.max(0, width - minimum), 0)
  return widths.map((width) => (width < minimum ? minimum : width - (short * (width - minimum)) / spare))
}

// --- Generated SQL -------------------------------------------------------------------------------

/** The query as lines of at most SQL_COLUMNS characters, split at spaces where it can be. */
export function wrapSql(sql) {
  const lines = []
  for (const source of xmlSafeText(String(sql ?? '').replace(/\t/g, '  ')).split('\n')) {
    let rest = source.trimEnd()
    while (rest.length > SQL_COLUMNS) {
      const space = rest.lastIndexOf(' ', SQL_COLUMNS)
      const cut = space > SQL_COLUMNS / 4 ? space : SQL_COLUMNS
      lines.push(rest.slice(0, cut).trimEnd())
      rest = rest.slice(cut).trimStart()
    }
    lines.push(rest)
  }
  return lines
}

function sqlSlides(sql) {
  const lines = wrapSql(sql)
  const parts = Math.ceil(lines.length / SQL_LINES_PER_SLIDE)
  return Array.from({ length: parts }, (_, index) => ({
    type: 'sql',
    title: parts > 1 ? `Generated SQL (${index + 1} of ${parts})` : 'Generated SQL',
    part: index + 1,
    parts,
    lines: lines.slice(index * SQL_LINES_PER_SLIDE, (index + 1) * SQL_LINES_PER_SLIDE),
  }))
}
