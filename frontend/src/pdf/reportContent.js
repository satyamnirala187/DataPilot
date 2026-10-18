// What a PDF report contains, worked out from one analysis without drawing anything. It uses the
// same presentation rules as the screen (describeResult, describeColumns), so the PDF shows the
// same headline, summary, ₹ formatting and table cells as ResultView. Only the given snapshot is
// read: nothing is requested, rerun or recalculated from the database.

import { parseTimestamp } from '../utils/history.js'
import { describeColumns, describeResult, formatExactValue, formatSummaryValue } from '../utils/result.js'

// Bars beyond this would not fit one readable chart; every row is still in the table.
export const MAX_PDF_BARS = 20

// Where the analysis was exported from, as the label above the title.
export const SOURCE_LABELS = Object.freeze({ query: 'Analysis', history: 'History analysis', saved: 'Saved report' })

const DATE_TIME = { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }
const DATE_ONLY = { day: 'numeric', month: 'short', year: 'numeric' }

/**
 * The report for one result-shaped object (a /query answer, or a stored History or Saved Report
 * analysis). options: source (query | history | saved), title (a saved report's title),
 * savedAt (its saved_at) and generatedAt (a Date: when the PDF is made).
 */
export function buildReportContent(result, { source = 'query', title = null, savedAt = null, generatedAt = new Date() } = {}) {
  const columns = Array.isArray(result?.columns) ? result.columns : []
  const rows = Array.isArray(result?.rows) ? result.rows : []
  const rowCount = typeof result?.row_count === 'number' ? result.row_count : rows.length
  const truncated = result?.truncated === true
  const question = cleanText(result?.question)
  const reportTitle = cleanText(title)
  const view = describeResult({ ...result, columns, rows })
  const shown = describeColumns(columns, rows)

  const meta = []
  const saved = formatDate(savedAt, DATE_ONLY)
  if (saved) meta.push(`Saved ${saved}`)
  const answered = formatDate(result?.created_at, DATE_TIME)
  if (answered) meta.push(`Answered ${answered}`)
  meta.push(`${rowCount} ${rowCount === 1 ? 'row' : 'rows'}${truncated ? ' (limited result)' : ''}`)

  return {
    source: SOURCE_LABELS[source] ? source : 'query',
    kicker: SOURCE_LABELS[source] ?? SOURCE_LABELS.query,
    title: reportTitle || question || 'DataPilot analysis',
    // A saved report's own title replaces the question as the heading, so the question follows it.
    question: reportTitle && question ? question : null,
    generated: formatDate(generatedAt, DATE_TIME) ?? '',
    meta,
    headline: view.headline ?? null,
    empty: view.kind === 'empty',
    kpi: view.kind === 'kpi' ? { label: view.label, value: formatExactValue(view.yKey, view.value) } : null,
    tiles: Array.isArray(view.tiles) ? view.tiles.map(({ label, value, note }) => ({ label, value, note: String(note ?? '') })) : [],
    chart: chartContent(view),
    insight: cleanText(result?.insight) || null,
    rowCount,
    truncated,
    table: {
      columns: shown.map((column) => ({ label: column.label, numeric: column.numeric })),
      rows: rows.map((row) => shown.map((column, index) => column.format(row?.[index]) ?? 'NULL')),
    },
    sql: typeof result?.sql === 'string' && result.sql.trim() ? result.sql.trim() : null,
  }
}

function chartContent(view) {
  if (view.kind === 'bar') {
    const bars = view.points.slice(0, MAX_PDF_BARS).map((point, index) => ({
      label: String(point.x),
      value: point.y,
      text: point.y === null ? '—' : formatSummaryValue(view.yKey, point.y),
      highlight: index === view.topIndex,
    }))
    const note = view.points.length > MAX_PDF_BARS ? `Showing the first ${MAX_PDF_BARS} of ${view.points.length} rows; every row is in the Data table.` : null
    return { kind: 'bar', title: view.title, bars, note }
  }
  if (view.kind === 'line') {
    const values = view.points.map((point) => point.y).filter((value) => value !== null)
    const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values)).map((value) => ({ value, text: formatSummaryValue(view.yKey, value) }))
    return {
      kind: 'line',
      title: view.title,
      range: view.range,
      points: view.points.map((point) => ({ label: view.formatX(point.x), value: point.y })),
      ticks,
    }
  }
  return null
}

/** About four evenly spaced round values covering min..max, e.g. 0, 2,00,000, 4,00,000 … */
export function niceTicks(min, max, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0]
  if (min === max) return min === 0 ? [0, 1] : [Math.min(0, min), Math.max(0, max)]
  const rough = (max - min) / count
  const power = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * power).find((candidate) => candidate >= rough)
  const ticks = []
  for (let value = Math.floor(min / step) * step; value < max + step / 2; value += step) {
    ticks.push(Math.round(value / step) * step)
  }
  return ticks
}

// Common question words that say nothing about the report, left out of file names.
const FILENAME_STOP_WORDS = new Set([
  'a', 'an', 'and', 'are', 'by', 'can', 'did', 'do', 'does', 'each', 'for', 'from', 'give', 'had', 'has', 'have',
  'how', 'i', 'in', 'is', 'list', 'many', 'me', 'much', 'of', 'on', 'our', 'please', 'show', 'tell', 'that', 'the',
  'their', 'to', 'us', 'was', 'we', 'were', 'what', 'whats', 'which', 'who', 'with',
])

/**
 * datapilot-<a few words of the title>-<YYYY-MM-DD>.pdf, using the local date the PDF was made:
 * "Electronics profit" -> datapilot-electronics-profit-2026-10-19.pdf. Only a-z, 0-9 and dashes.
 */
export function reportFilename(title, date = new Date()) {
  const words = String(title ?? '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '') // accents: "Café" -> "cafe"
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
  const meaningful = words.filter((word) => !FILENAME_STOP_WORDS.has(word))
  let slug = ''
  for (const word of (meaningful.length ? meaningful : words).slice(0, 6)) {
    if (slug.length + word.length + 1 > 60) break
    slug = slug ? `${slug}-${word}` : word
  }
  return `datapilot-${slug || 'analysis'}-${localDate(date)}.pdf`
}

function localDate(date) {
  const valid = date instanceof Date && !Number.isNaN(date.getTime()) ? date : new Date()
  const pad = (number) => String(number).padStart(2, '0')
  return `${valid.getFullYear()}-${pad(valid.getMonth() + 1)}-${pad(valid.getDate())}`
}

function formatDate(value, options) {
  const date = value instanceof Date ? value : parseTimestamp(value)
  if (!date || Number.isNaN(date.getTime())) return null
  return date.toLocaleString('en-GB', options).replace('Sept', 'Sep')
}

function cleanText(value) {
  return typeof value === 'string' ? value.trim() : ''
}

// The characters the PDF's embedded fonts can draw (src/pdf/fonts: Latin, Latin-1, Latin
// Extended-A, common punctuation, ₹, € and arrows). Anything else would print as an empty box.
const UNSUPPORTED = /[^\n -~ -ſ‐-‧‰‹›€₹™←-↓−]/gu

/** Text the PDF fonts can draw: tabs become spaces, other unsupported characters become "?". */
export function pdfSafeText(value) {
  return String(value ?? '')
    .normalize('NFC')
    .replace(/\r\n?/g, '\n')
    .replace(/\t/g, '    ')
    .replace(UNSUPPORTED, '?')
}
