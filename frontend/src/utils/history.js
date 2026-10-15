// History list helpers: reading created_at, grouping by the viewer's local calendar, the time shown
// on each row, its one-line description and the search filter. Nothing here changes the list.

import { formatIndianNumber } from './format.js'

export const GROUPS = ['Today', 'Yesterday', 'Earlier this week', 'Earlier']

// 2026-10-15T09:42:00, with optional seconds, fraction and offset (Z, +05:30, -0400).
const TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/

/**
 * created_at as a Date, or null when it is not a real timestamp. Impossible parts (month 13,
 * 30 February, hour 25) are refused rather than rolled over into another date.
 */
export function parseTimestamp(value) {
  const match = typeof value === 'string' ? TIMESTAMP.exec(value.trim()) : null
  if (!match) return null
  const [year, month, day, hour, minute, second = 0] = match.slice(1, 7).map(Number)
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate()
  if (month < 1 || month > 12 || day < 1 || day > daysInMonth || hour > 23 || minute > 59 || second > 59) return null
  // The backend sends microseconds; not every browser parses more than milliseconds.
  const date = new Date(value.trim().replace(' ', 'T').replace(/(\.\d{3})\d+/, '$1'))
  return Number.isNaN(date.getTime()) ? null : date
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function addDays(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days)
}

/**
 * Today, Yesterday, Earlier this week (Monday onwards, before yesterday) or Earlier, by the
 * viewer's local calendar. A time slightly ahead of now (clock difference) still counts as Today;
 * an unreadable time goes to Earlier.
 */
export function groupOf(date, now) {
  if (!date) return 'Earlier'
  const today = startOfDay(now)
  if (date >= today) return 'Today'
  if (date >= addDays(today, -1)) return 'Yesterday'
  const weekStart = addDays(today, -((today.getDay() + 6) % 7)) // Monday
  return date >= weekStart ? 'Earlier this week' : 'Earlier'
}

const TIME = { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }

/** '14:42' today and yesterday, 'Mon, 14:48' earlier this week, '8 Oct 2026' before that. */
export function formatHistoryTime(date, group) {
  if (!date) return 'Time unavailable'
  if (group === 'Today' || group === 'Yesterday') return date.toLocaleTimeString('en-GB', TIME)
  if (group === 'Earlier this week') {
    return `${date.toLocaleDateString('en-GB', { weekday: 'short' })}, ${date.toLocaleTimeString('en-GB', TIME)}`
  }
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).replace('Sept', 'Sep')
}

/**
 * The list in display groups, in GROUPS order, each item keeping the backend's (newest-first) order:
 * [{ label, items: [{ item, time }] }]. Empty groups are left out.
 */
export function groupHistory(items, now) {
  const groups = new Map(GROUPS.map((label) => [label, []]))
  for (const item of items) {
    const date = parseTimestamp(item.created_at)
    const label = groupOf(date, now)
    groups.get(label).push({ item, time: formatHistoryTime(date, label) })
  }
  return GROUPS.filter((label) => groups.get(label).length > 0).map((label) => ({ label, items: groups.get(label) }))
}

const KIND_LABELS = { kpi: 'KPI', line: 'Line chart', bar: 'Bar chart', table: 'Table' }

const KIND_ICONS = { kpi: 'target', line: 'trend', bar: 'chart', table: 'table', other: 'chat' }

/** The Icon name for a result type's chip. */
export function kindIcon(type) {
  return KIND_ICONS[historyKind(type)]
}

/** 'kpi' | 'line' | 'bar' | 'table', or 'other' for anything else. */
export function historyKind(type) {
  return Object.hasOwn(KIND_LABELS, type) ? type : 'other'
}

/** 'Line chart · 12 rows', 'KPI · 1 row', 'Table · 1,000 rows · Limited result'. */
export function describeHistoryItem(item) {
  const parts = [KIND_LABELS[historyKind(item.visualization_type)] ?? 'Result']
  if (Number.isInteger(item.row_count) && item.row_count >= 0) {
    parts.push(`${formatIndianNumber(item.row_count)} ${item.row_count === 1 ? 'row' : 'rows'}`)
  }
  if (item.truncated === true) parts.push('Limited result')
  return parts.join(' · ')
}

/** The items whose question contains the search text, ignoring case and outer spaces. */
export function filterHistory(items, search) {
  const needle = search.trim().toLocaleLowerCase()
  if (!needle) return items
  return items.filter((item) => String(item.question).toLocaleLowerCase().includes(needle))
}
