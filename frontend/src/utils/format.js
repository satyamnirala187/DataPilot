// All number, label and date formatting for KPIs and charts lives here.
// The rules are conservative: ₹ only for money-like column names, % only for percentage names.

const MONEY_WORDS = new Set(['revenue', 'profit', 'sales', 'spending', 'spend', 'spent', 'amount', 'aov', 'price', 'cost'])
// A margin is a percentage: profit_margin 23.4 reads 23.4%. The value is shown as returned, never
// multiplied or divided (0.234 reads 0.23%). Percent words are checked before money words, so
// profit_margin is a percent while profit and gross_profit stay currency.
const PERCENT_WORDS = new Set(['pct', 'percent', 'percentage', 'margin'])
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

// ISO dates are read as UTC so the shown day never shifts with the viewer's time zone. A date that
// does not exist (2026-13-45, 2026-02-30) is not a date: it is shown as returned, never rolled over.
function parseIsoDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value))
  if (!match) return null
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const date = new Date(Date.UTC(year, month - 1, day))
  const exists = date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  return exists ? date : null
}

/**
 * 'Apr 2026' when every point is the first of a month, otherwise '12 Apr 2026'. September is
 * always 'Sep' (newer ICU versions write 'Sept' for en-GB).
 */
export function dateFormatter(values) {
  const monthly = values.every((value) => /^\d{4}-\d{2}-01/.test(String(value)))
  const options = monthly
    ? { month: 'short', year: 'numeric', timeZone: 'UTC' }
    : { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }
  return (value) => {
    const date = parseIsoDate(value)
    return date ? date.toLocaleDateString('en-GB', options).replace('Sept', 'Sep') : String(value)
  }
}

/** Truncate long category labels on an axis; the tooltip still shows the full name. */
export function shortenLabel(value, maxLength = 14) {
  const text = String(value)
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text
}

// --- UI V2: Indian number presentation -------------------------------------------------------
// New screens use these; the formatters above stay as they are until each screen moves to V2.
// Indian grouping (1,30,100), and compact values in K (thousand), L (lakh) and Cr (crore):
// ₹45.4 Cr, ₹7.4 L, 1.2 K. ₹ and % still come only from the column name (valueKind).

const COMPACT_UNITS = [
  [1e7, 'Cr'],
  [1e5, 'L'],
  [1e3, 'K'],
]

function toFiniteNumber(value) {
  const number = toNumber(value)
  return number !== null && Number.isFinite(number) ? number : null
}

// The text for anything that is not a number: a dash for missing values, otherwise as given.
function notANumber(value) {
  return value === null || value === undefined ? '—' : String(value)
}

// A minus sign only when the shown digits are not all zero, so -0.001 never reads "-0.00".
function signed(number, digits, text) {
  return number < 0 && /[1-9]/.test(digits) ? `-${text}` : text
}

/** Indian digit grouping, up to two decimals: 130100 -> '1,30,100'. */
export function formatIndianNumber(value, maximumFractionDigits = 2) {
  const number = toFiniteNumber(value)
  if (number === null) return notANumber(value)
  const digits = Math.abs(number).toLocaleString('en-IN', { maximumFractionDigits })
  return signed(number, digits, digits)
}

/** Rupees, Indian grouping, two decimals: 21304631.99 -> '₹2,13,04,631.99'. */
export function formatInr(value) {
  const number = toFiniteNumber(value)
  if (number === null) return notANumber(value)
  const digits = Math.abs(number).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return signed(number, digits, `₹${digits}`)
}

/**
 * Compact Indian notation with one decimal at most: 454000000 -> '45.4 Cr', 740000 -> '7.4 L',
 * 1200 -> '1.2 K', 950 -> '950'. A value that rounds up to the next unit uses it (99,960 -> '1 L').
 */
export function formatIndianCompact(value) {
  const number = toFiniteNumber(value)
  if (number === null) return notANumber(value)
  const size = Math.abs(number)
  let index = COMPACT_UNITS.findIndex(([unit]) => size >= unit)
  if (index > 0 && round1(size / COMPACT_UNITS[index][0]) >= 100) index -= 1
  // Values just below 1,000 that round up to 1,000 (999.96) also move to K.
  if (index === -1 && round1(size) >= 1000) index = COMPACT_UNITS.length - 1
  const [unit, label] = index === -1 ? [1, ''] : COMPACT_UNITS[index]
  const digits = round1(size / unit).toLocaleString('en-IN', { maximumFractionDigits: 1 })
  return signed(number, digits, label ? `${digits} ${label}` : digits)
}

function round1(number) {
  return Math.round(number * 10) / 10
}

/** Full value for a column, V2 style: '₹2,13,04,631.99', '34.56%', '1,30,100'. */
export function formatIndianValue(column, value) {
  const kind = valueKind(column)
  if (kind === 'currency') return formatInr(value)
  const text = formatIndianNumber(value)
  return kind === 'percent' && toFiniteNumber(value) !== null ? `${text}%` : text
}

/** Compact value for a column, V2 style (summary tiles, chart axes): '₹45.4 Cr', '56.9%', '1.2 K'. */
export function formatIndianCompactValue(column, value) {
  const kind = valueKind(column)
  if (kind === 'percent') {
    const text = formatIndianNumber(value, 1)
    return toFiniteNumber(value) !== null ? `${text}%` : text
  }
  const text = formatIndianCompact(value)
  if (kind !== 'currency' || toFiniteNumber(value) === null) return text
  return text.startsWith('-') ? `-₹${text.slice(1)}` : `₹${text}`
}
