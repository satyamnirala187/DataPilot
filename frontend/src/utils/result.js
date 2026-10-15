// How a result is presented: which view, the headline sentence, the summary tiles, and how table
// cells read. Everything here comes from the result's own rows and column names. Nothing is
// estimated or invented: anything that cannot be worked out reliably is left out, never guessed.
// The result object is only read, never changed.

import {
  dateFormatter,
  formatIndianCompactValue,
  formatIndianNumber,
  formatIndianValue,
  humanizeLabel,
  valueKind,
} from './format.js'

// Columns whose values must not be added up: rates, shares, averages, prices and the like.
// Their total over some rows is not a meaningful number.
const NOT_ADDITIVE_WORDS = new Set([
  'pct', 'percent', 'percentage', 'rate', 'ratio', 'share', 'growth', 'margin',
  'avg', 'average', 'mean', 'median', 'aov', 'price', 'min', 'max', 'minimum', 'maximum',
])

// Columns that hold codes rather than amounts: shown exactly as returned (2026, not 2,026).
const IDENTIFIER_WORDS = new Set(['id', 'year', 'yr', 'zip', 'pin', 'pincode', 'postcode', 'phone'])

function words(column) {
  return String(column).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)
}

function isNumber(value) {
  return typeof value === 'number' && Number.isFinite(value)
}

/** 'total_revenue' -> 'Total revenue', 'aov' -> 'AOV'. */
export function sentenceLabel(column) {
  return humanizeLabel(column)
    .split(' ')
    .map((word, index) => (index === 0 || word === 'AOV' ? word : word.toLowerCase()))
    .join(' ')
}

// 'Total revenue' -> 'total revenue', for use inside a sentence.
function inlineLabel(column) {
  const label = sentenceLabel(column)
  return label.startsWith('AOV') ? label : label.charAt(0).toLowerCase() + label.slice(1)
}

export function isIdentifierColumn(column) {
  return words(column).some((word) => IDENTIFIER_WORDS.has(word))
}

function isAdditive(column) {
  return valueKind(column) !== 'percent' && !words(column).some((word) => NOT_ADDITIVE_WORDS.has(word))
}

/** A summary value: compact Indian notation ('₹45.4 Cr', '1.2 K', '34.5%'); codes as returned. */
export function formatSummaryValue(column, value) {
  return isIdentifierColumn(column) ? String(value) : formatIndianCompactValue(column, value)
}

/** An exact value: Indian grouping ('₹2,13,04,631.99', '1,30,100', '34.56%'); codes as returned. */
export function formatExactValue(column, value) {
  if (value === null || value === undefined) return '—'
  return isIdentifierColumn(column) || typeof value !== 'number' ? String(value) : formatIndianValue(column, value)
}

/** '+8.1%', '-12.5%' or '0%': a change in percent, to one decimal. */
export function formatChange(percent) {
  const rounded = Math.round(percent * 10) / 10
  if (rounded === 0) return '0%'
  return `${rounded > 0 ? '+' : '-'}${formatIndianNumber(Math.abs(rounded), 1)}%`
}

// A line runs oldest to newest. Rows already in that order are used as they are; otherwise a
// sorted copy is used (the table keeps the backend's order).
function inDateOrder(points) {
  const ascending = points.every((point, i) => i === 0 || String(points[i - 1].x) <= String(point.x))
  return ascending ? points : [...points].sort((a, b) => String(a.x).localeCompare(String(b.x)))
}

function plural(count, word) {
  return `${formatIndianNumber(count)} ${word}${count === 1 ? '' : 's'}`
}

/**
 * The presentation of a result:
 *   { kind: 'empty' }                       no rows
 *   { kind: 'table' }                       only the table (also when chart details are missing)
 *   { kind: 'kpi', yKey, label, value, headline }
 *   { kind: 'line' | 'bar', xKey, yKey, title, points, ..., headline, tiles }
 * The view type and columns are the backend's choice (visualization); this never picks a chart.
 * headline is null whenever a sentence could not be stated with certainty.
 */
export function describeResult(result) {
  const columns = Array.isArray(result?.columns) ? result.columns : []
  const rows = Array.isArray(result?.rows) ? result.rows : []
  if (rows.length === 0) return { kind: 'empty' }

  const visualization = result.visualization ?? {}
  const yIndex = columns.indexOf(visualization.y_key)
  const xIndex = columns.indexOf(visualization.x_key)
  const yKey = visualization.y_key

  if (visualization.type === 'kpi' && yIndex !== -1 && rows.length === 1 && isNumber(rows[0]?.[yIndex])) {
    const value = rows[0][yIndex]
    const label = sentenceLabel(yKey)
    return { kind: 'kpi', yKey, label, value, headline: `${label} is ${formatSummaryValue(yKey, value)}.` }
  }

  const charted = visualization.type === 'line' || visualization.type === 'bar'
  if (!charted || xIndex === -1 || yIndex === -1 || xIndex === yIndex) return { kind: 'table' }

  const points = rows.map((row) => ({ x: row?.[xIndex], y: isNumber(row?.[yIndex]) ? row[yIndex] : null }))
  const numeric = points.filter((point) => point.y !== null)
  if (numeric.length === 0) return { kind: 'table' }

  const base = {
    xKey: visualization.x_key,
    yKey,
    title: `${sentenceLabel(yKey)} by ${inlineLabel(visualization.x_key)}`,
    truncated: result.truncated === true,
  }
  return visualization.type === 'line' ? describeLine(base, inDateOrder(points)) : describeBar(base, points, numeric)
}

function describeLine(base, points) {
  const { yKey, truncated } = base
  const formatX = dateFormatter(points.map((point) => point.x))
  const numeric = points.filter((point) => point.y !== null)
  const latest = numeric[numeric.length - 1]
  const previous = numeric.length > 1 ? numeric[numeric.length - 2] : null
  const value = (number) => formatSummaryValue(yKey, number)

  // Percent change only from a positive previous value, and not for values that are already
  // percentages (a change of a rate in percent reads ambiguously).
  const change =
    previous && previous.y > 0 && valueKind(yKey) !== 'percent'
      ? { text: formatChange(((latest.y - previous.y) / previous.y) * 100), against: formatX(previous.x) }
      : null

  // With a truncated result the newest row may be missing, so "latest" is only "latest shown".
  const shown = truncated ? ' shown' : ''
  const tiles = [{ label: `Latest${shown}`, value: value(latest.y), note: formatX(latest.x) }]
  if (previous) tiles.push({ label: `Previous${shown}`, value: value(previous.y), note: formatX(previous.x) })
  if (change) tiles.push({ label: 'Change vs previous', value: change.text, note: `${formatX(latest.x)} vs ${change.against}` })
  if (numeric.length > 1) {
    const average = numeric.reduce((sum, point) => sum + point.y, 0) / numeric.length
    tiles.push({ label: 'Average', value: value(average), note: `Of ${plural(numeric.length, 'shown row')}` })
  }

  return {
    ...base,
    kind: 'line',
    points,
    formatX,
    range: points.length > 1 ? `${formatX(points[0].x)} – ${formatX(points[points.length - 1].x)}` : formatX(points[0].x),
    latest: { label: formatX(latest.x), value: value(latest.y) },
    change,
    tiles,
    headline: truncated ? null : `${sentenceLabel(yKey)} for ${formatX(latest.x)} is ${value(latest.y)}.`,
  }
}

function describeBar(base, points, numeric) {
  const { yKey } = base
  const value = (number) => formatSummaryValue(yKey, number)
  // The first row with the highest value, in the backend's order: always the same bar.
  const top = numeric.reduce((best, point) => (point.y > best.y ? point : best))
  const bottom = numeric.reduce((worst, point) => (point.y < worst.y ? point : worst))
  const count = numeric.length

  const tiles = [{ label: 'Highest shown', value: value(top.y), note: String(top.x) }]
  if (count > 1) {
    tiles.push({ label: 'Lowest shown', value: value(bottom.y), note: String(bottom.x) })
    if (isAdditive(yKey)) {
      const total = numeric.reduce((sum, point) => sum + point.y, 0)
      tiles.push({ label: 'Total of shown', value: value(total), note: `Across ${plural(count, 'row')}` })
    }
    const average = numeric.reduce((sum, point) => sum + point.y, 0) / count
    tiles.push({ label: 'Average of shown', value: value(average), note: `Across ${plural(count, 'row')}` })
  }

  return {
    ...base,
    kind: 'bar',
    points,
    topIndex: points.indexOf(top),
    top: { label: String(top.x), value: value(top.y) },
    count,
    tiles,
    headline:
      count > 1
        ? `${top.x} has the highest ${inlineLabel(yKey)} of the ${formatIndianNumber(count)} shown: ${value(top.y)}.`
        : null,
  }
}

// --- Table cells ---------------------------------------------------------------------------------

// A date, or a date at midnight (with any offset): shown as a date, since there is no time to lose.
const PLAIN_DATE = /^\d{4}-\d{2}-\d{2}(T00:00(:00(\.0+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/

/**
 * How each column is shown in the Data table: { numeric, format(value) }. Numbers get Indian
 * grouping (₹ and % only from the column name), columns of plain dates read '8 Oct 2026' (or
 * 'Oct 2026' for month starts), and everything else is shown as returned. Missing values are null.
 */
export function describeColumns(columns, rows) {
  return columns.map((column, index) => {
    const values = rows.map((row) => row?.[index]).filter((value) => value !== null && value !== undefined)
    const numeric = values.length > 0 && values.every((value) => typeof value === 'number')
    const dates = values.length > 0 && values.every((value) => typeof value === 'string' && PLAIN_DATE.test(value))
    const formatDate = dates ? dateFormatter(values) : null
    return {
      label: sentenceLabel(column) || String(column),
      numeric,
      format(value) {
        if (value === null || value === undefined) return null
        if (typeof value === 'number') return formatExactValue(column, value)
        if (formatDate && typeof value === 'string') return formatDate(value)
        if (typeof value === 'object') return JSON.stringify(value)
        return String(value)
      },
    }
  })
}
