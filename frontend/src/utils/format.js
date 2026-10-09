// All number, label and date formatting for KPIs and charts lives here.
// The rules are conservative: ₹ only for money-like column names, % only for percentage names.

const MONEY_WORDS = new Set(['revenue', 'profit', 'sales', 'spending', 'spend', 'spent', 'amount', 'aov', 'price', 'cost'])
const PERCENT_WORDS = new Set(['pct', 'percent', 'percentage'])
// Counts and ratios are never money, even when the name also mentions revenue (revenue_growth_rate).
const NOT_MONEY_WORDS = new Set(['count', 'quantity', 'units', 'rate', 'ratio', 'share', 'growth'])

function words(column) {
  return String(column).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)
}

/** 'percent' | 'currency' | 'number', judged from the column name only. */
export function valueKind(column) {
  const parts = words(column)
  if (parts.some((part) => PERCENT_WORDS.has(part))) return 'percent'
  if (parts.some((part) => NOT_MONEY_WORDS.has(part))) return 'number'
  const name = parts.join('_')
  if (parts.some((part) => MONEY_WORDS.has(part)) || name.includes('order_value')) return 'currency'
  return 'number'
}

/** 'total_revenue' -> 'Total Revenue', 'aov' -> 'AOV'. */
export function humanizeLabel(column) {
  return words(column)
    .map((part) => (part === 'aov' ? 'AOV' : part.charAt(0).toUpperCase() + part.slice(1)))
    .join(' ')
}

function toNumber(value) {
  if (typeof value === 'number') return value
  if (typeof value === 'string' && value.trim() !== '' && !Number.isNaN(Number(value))) return Number(value)
  return null
}

/** Full value for KPIs and tooltips: ₹21,304,631.99, 34.56%, 1,000. */
export function formatValue(column, value) {
  const number = toNumber(value)
  if (number === null) return value === null || value === undefined ? '—' : String(value)

  const kind = valueKind(column)
  if (kind === 'currency') {
    const formatted = Math.abs(number).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    return `${number < 0 ? '-' : ''}₹${formatted}`
  }
  const formatted = number.toLocaleString('en-US', { maximumFractionDigits: 2 })
  return kind === 'percent' ? `${formatted}%` : formatted
}

/** Short value for chart axes: ₹7.4M, 1.2K, 35%. */
export function formatAxisValue(column, value) {
  const number = toNumber(value)
  if (number === null) return ''
  const kind = valueKind(column)
  const compact = Math.abs(number).toLocaleString('en-US', { notation: 'compact', maximumFractionDigits: 1 })
  const sign = number < 0 ? '-' : ''
  if (kind === 'currency') return `${sign}₹${compact}`
  if (kind === 'percent') return `${sign}${compact}%`
  return `${sign}${compact}`
}

// ISO dates are read as UTC so the shown day never shifts with the viewer's time zone.
function parseIsoDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value))
  if (!match) return null
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
  return Number.isNaN(date.getTime()) ? null : date
}

/** 'Apr 2026' when every point is the first of a month, otherwise '12 Apr 2026'. */
export function dateFormatter(values) {
  const monthly = values.every((value) => /^\d{4}-\d{2}-01/.test(String(value)))
  const options = monthly
    ? { month: 'short', year: 'numeric', timeZone: 'UTC' }
    : { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }
  return (value) => {
    const date = parseIsoDate(value)
    return date ? date.toLocaleDateString('en-GB', options) : String(value)
  }
}

/** Truncate long category labels on an axis; the tooltip still shows the full name. */
export function shortenLabel(value, maxLength = 14) {
  const text = String(value)
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text
}
