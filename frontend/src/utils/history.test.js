import { describe, expect, it } from 'vitest'
import {
  describeHistoryItem,
  filterHistory,
  formatHistoryTime,
  groupHistory,
  groupOf,
  historyKind,
  parseTimestamp,
} from './history.js'

// Dates are built with the local-time constructor, so these tests hold in any time zone.
const local = (...parts) => new Date(...parts)
const iso = (date) => date.toISOString()

describe('parseTimestamp', () => {
  it('reads backend timestamps with offsets, fractions and microseconds', () => {
    expect(parseTimestamp('2026-10-15T09:00:00+05:30').getTime()).toBe(Date.UTC(2026, 9, 15, 3, 30))
    expect(parseTimestamp('2026-10-15T03:30:00Z').getTime()).toBe(Date.UTC(2026, 9, 15, 3, 30))
    expect(parseTimestamp('2026-10-15T03:30:00.123456+00:00').getTime()).toBe(Date.UTC(2026, 9, 15, 3, 30, 0, 123))
    expect(parseTimestamp('2026-10-15T09:42:00').getTime()).toBe(local(2026, 9, 15, 9, 42).getTime())
  })

  it.each(['2026-13-45T10:00:00Z', '2026-02-30T10:00:00Z', '2026-10-15T25:00:00Z', 'yesterday', '', null, undefined, 1760000000])(
    'refuses %s instead of rolling it into another date',
    (value) => {
      expect(parseTimestamp(value)).toBeNull()
    },
  )

  it('accepts 29 February in a leap year', () => {
    expect(parseTimestamp('2028-02-29T12:00:00Z')).not.toBeNull()
  })
})

describe('groupOf (local calendar, weeks start on Monday)', () => {
  const thursday = local(2026, 9, 15, 10, 0) // Thu 15 Oct 2026

  it.each([
    [local(2026, 9, 15, 0, 0), 'Today'],
    [local(2026, 9, 15, 9, 59), 'Today'],
    [local(2026, 9, 15, 10, 5), 'Today'], // a little ahead of now (clock difference)
    [local(2026, 9, 14, 23, 59), 'Yesterday'],
    [local(2026, 9, 14, 0, 0), 'Yesterday'],
    [local(2026, 9, 13, 23, 59), 'Earlier this week'],
    [local(2026, 9, 12, 0, 0), 'Earlier this week'], // Monday
    [local(2026, 9, 11, 23, 59), 'Earlier'], // Sunday, last week
    [null, 'Earlier'],
  ])('%s is %s', (date, group) => {
    expect(groupOf(date, thursday)).toBe(group)
  })

  it('handles a month boundary', () => {
    const now = local(2026, 9, 1, 9, 0) // Thu 1 Oct
    expect(groupOf(local(2026, 8, 30, 18, 0), now)).toBe('Yesterday')
    expect(groupOf(local(2026, 8, 28, 9, 0), now)).toBe('Earlier this week') // Mon 28 Sep
    expect(groupOf(local(2026, 8, 27, 9, 0), now)).toBe('Earlier')
  })

  it('handles a year boundary', () => {
    const now = local(2027, 0, 1, 9, 0) // Fri 1 Jan 2027
    expect(groupOf(local(2026, 11, 31, 22, 0), now)).toBe('Yesterday')
    expect(groupOf(local(2026, 11, 28, 9, 0), now)).toBe('Earlier this week') // Mon 28 Dec
    expect(groupOf(local(2026, 11, 27, 9, 0), now)).toBe('Earlier')
  })

  it('on a Monday, yesterday is still Yesterday and nothing else is this week', () => {
    const monday = local(2026, 9, 12, 9, 0)
    expect(groupOf(local(2026, 9, 11, 9, 0), monday)).toBe('Yesterday')
    expect(groupOf(local(2026, 9, 10, 9, 0), monday)).toBe('Earlier')
  })
})

describe('formatHistoryTime', () => {
  const date = local(2026, 9, 12, 14, 48) // Mon 12 Oct 2026, 14:48 local
  it('shows the time, the weekday and time, or the date, by group', () => {
    expect(formatHistoryTime(date, 'Today')).toBe('14:48')
    expect(formatHistoryTime(date, 'Yesterday')).toBe('14:48')
    expect(formatHistoryTime(date, 'Earlier this week')).toBe('Mon, 14:48')
    expect(formatHistoryTime(local(2026, 8, 8, 9, 5), 'Earlier')).toBe('8 Sep 2026')
    expect(formatHistoryTime(null, 'Earlier')).toBe('Time unavailable')
  })
})

describe('groupHistory', () => {
  it('groups in display order and keeps the backend order inside each group, without changing the list', () => {
    const now = local(2026, 9, 15, 10, 0)
    const items = Object.freeze([
      { id: 'a', question: 'A', created_at: iso(local(2026, 9, 15, 9, 42)) },
      { id: 'b', question: 'B', created_at: iso(local(2026, 9, 15, 9, 15)) },
      { id: 'c', question: 'C', created_at: iso(local(2026, 9, 14, 16, 20)) },
      { id: 'd', question: 'D', created_at: iso(local(2026, 9, 12, 10, 12)) },
      { id: 'e', question: 'E', created_at: 'not a time' },
      { id: 'f', question: 'F', created_at: iso(local(2026, 8, 8, 9, 0)) },
    ])
    const groups = groupHistory(items, now)
    expect(groups.map((group) => [group.label, group.items.map(({ item, time }) => `${item.id} ${time}`)])).toEqual([
      ['Today', ['a 09:42', 'b 09:15']],
      ['Yesterday', ['c 16:20']],
      ['Earlier this week', ['d Mon, 10:12']],
      ['Earlier', ['e Time unavailable', 'f 8 Sep 2026']],
    ])
    expect(items.map((item) => item.id)).toEqual(['a', 'b', 'c', 'd', 'e', 'f'])
  })

  it('leaves out empty groups', () => {
    expect(groupHistory([], new Date())).toEqual([])
  })
})

describe('row description', () => {
  it.each([
    [{ visualization_type: 'line', row_count: 12 }, 'Line chart · 12 rows'],
    [{ visualization_type: 'kpi', row_count: 1 }, 'KPI · 1 row'],
    [{ visualization_type: 'bar', row_count: 0 }, 'Bar chart · 0 rows'],
    [{ visualization_type: 'table', row_count: 1000, truncated: true }, 'Table · 1,000 rows · Limited result'],
    [{ visualization_type: 'scatter', row_count: 3 }, 'Result · 3 rows'],
    [{}, 'Result'],
  ])('%o reads %s', (item, text) => {
    expect(describeHistoryItem(item)).toBe(text)
  })

  it('maps unknown visualization types to a neutral kind', () => {
    expect(['kpi', 'line', 'bar', 'table', 'pie', undefined, 'constructor'].map(historyKind)).toEqual([
      'kpi', 'line', 'bar', 'table', 'other', 'other', 'other',
    ])
  })
})

describe('filterHistory', () => {
  const items = [{ question: 'Show monthly revenue trends.' }, { question: 'Who are our top customers?' }]
  it('matches the question ignoring case and outer spaces, and returns everything for an empty search', () => {
    expect(filterHistory(items, '  REVENUE ')).toEqual([items[0]])
    expect(filterHistory(items, 'zzz')).toEqual([])
    expect(filterHistory(items, '   ')).toBe(items)
  })
})
