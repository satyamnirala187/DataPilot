import { describe, expect, it } from 'vitest'
import { describeColumns, describeResult, formatChange, formatExactValue, formatSummaryValue, sentenceLabel } from './result.js'

const line = (rows, extra = {}) => ({
  columns: ['month', 'revenue'],
  rows,
  visualization: { type: 'line', x_key: 'month', y_key: 'revenue' },
  ...extra,
})

const bar = (rows, y = 'revenue', extra = {}) => ({
  columns: ['category', y],
  rows,
  visualization: { type: 'bar', x_key: 'category', y_key: y },
  ...extra,
})

describe('labels and numbers', () => {
  it('turns column names into sentence-case labels', () => {
    expect(sentenceLabel('total_revenue')).toBe('Total revenue')
    expect(sentenceLabel('category_name')).toBe('Category name')
    expect(sentenceLabel('aov')).toBe('AOV')
  })

  it('formats a change to one decimal with a sign, and an unchanged value as 0%', () => {
    expect(formatChange(8.123)).toBe('+8.1%')
    expect(formatChange(-12.46)).toBe('-12.5%')
    expect(formatChange(0.04)).toBe('0%')
  })

  it.each([
    ['revenue', 21304631.99, '₹2,13,04,631.99', '₹2.1 Cr'],
    ['revenue', 740000, '₹7,40,000.00', '₹7.4 L'],
    ['order_count', 130100, '1,30,100', '1.3 L'],
    ['share_pct', 56.9, '56.9%', '56.9%'],
    ['revenue', 0, '₹0.00', '₹0'],
    ['revenue', -1234.5, '-₹1,234.50', '-₹1.2 K'],
    ['share_pct', -5.5, '-5.5%', '-5.5%'],
    ['order_count', 21304631, '2,13,04,631', '2.1 Cr'], // large, but not money: never ₹
    // Margins are percentages, shown as returned: never scaled, never ₹.
    ['profit_margin', 23.4, '23.4%', '23.4%'],
    ['gross_margin', 30.2, '30.2%', '30.2%'],
    ['profit_margin', 0, '0%', '0%'],
    ['profit_margin', -4.5, '-4.5%', '-4.5%'],
    ['profit_margin', 0.234, '0.23%', '0.2%'],
  ])('%s %s reads %s exactly and %s in short', (column, value, exact, compact) => {
    expect(formatExactValue(column, value)).toBe(exact)
    expect(formatSummaryValue(column, value)).toBe(compact)
  })

  it('shows missing and non-numeric values without inventing a number', () => {
    expect(formatExactValue('revenue', null)).toBe('—')
    expect(formatExactValue('revenue', undefined)).toBe('—')
    expect(formatExactValue('revenue', 'n/a')).toBe('n/a')
  })

  it('shows codes such as ids and years as returned, never grouped or shortened', () => {
    expect(formatSummaryValue('order_year', 2026)).toBe('2026')
    expect(formatExactValue('customer_id', 104201)).toBe('104201')
    expect(formatExactValue('revenue', 21304631.99)).toBe('₹2,13,04,631.99')
    expect(formatSummaryValue('revenue', 21304631.99)).toBe('₹2.1 Cr')
  })
})

describe('headlines', () => {
  const kpi = (column, value) => describeResult({ columns: [column], rows: [[value]], visualization: { type: 'kpi', y_key: column } }).headline

  it('states only the value, with the column label and its own unit', () => {
    expect(kpi('total_revenue', 21304631.99)).toBe('Total revenue is ₹2.1 Cr.')
    expect(kpi('average_order_value', 2450.5)).toBe('Average order value is ₹2.5 K.')
    expect(kpi('customer_count', 1000)).toBe('Customer count is 1 K.')
    // A margin is a percentage, never rupees.
    expect(kpi('profit_margin', 23.4)).toBe('Profit margin is 23.4%.')
  })

  it('has no headline for a table, an empty result or a truncated line', () => {
    expect(describeResult({ columns: ['a', 'b'], rows: [['x', 'y']], visualization: { type: 'table' } }).headline).toBeUndefined()
    expect(describeResult({ columns: ['a'], rows: [] }).headline).toBeUndefined()
    expect(describeResult(line([['2026-01-01', 1], ['2026-02-01', 2]], { truncated: true })).headline).toBeNull()
  })
})

describe('describeResult', () => {
  it('returns empty for no rows, and table for anything not clearly a KPI or chart', () => {
    expect(describeResult({ columns: ['a'], rows: [] }).kind).toBe('empty')
    expect(describeResult({ columns: ['a'], rows: [[1]] }).kind).toBe('table')
    expect(describeResult({ columns: ['a'], rows: [['x']], visualization: { type: 'kpi', y_key: 'a' } }).kind).toBe('table')
    expect(describeResult(line([['2026-01-01', null], ['2026-02-01', null]])).kind).toBe('table')
    expect(describeResult({ ...line([['2026-01-01', 1]]), visualization: { type: 'line', x_key: 'month', y_key: 'month' } }).kind).toBe('table')
    expect(describeResult({ ...line([['2026-01-01', 1]]), visualization: { type: 'pie', x_key: 'month', y_key: 'revenue' } }).kind).toBe('table')
  })

  it('does not change the result it is given', () => {
    const rows = [['2026-03-01', 3], ['2026-01-01', 1], ['2026-02-01', 2]]
    const result = line(rows)
    const before = JSON.stringify(result)
    const view = describeResult(result)
    expect(view.points.map((point) => point.x)).toEqual(['2026-01-01', '2026-02-01', '2026-03-01'])
    expect(JSON.stringify(result)).toBe(before)
  })

  describe('line', () => {
    it('skips missing values for latest and previous, and says which dates they are', () => {
      const view = describeResult(line([['2026-01-01', 100], ['2026-02-01', 200], ['2026-03-01', null]]))
      expect(view.tiles).toEqual([
        { label: 'Latest', value: '₹200', note: 'Feb 2026' },
        { label: 'Previous', value: '₹100', note: 'Jan 2026' },
        { label: 'Change vs previous', value: '+100%', note: 'Feb 2026 vs Jan 2026' },
        { label: 'Average', value: '₹150', note: 'Of 2 shown rows' },
      ])
    })

    it('gives no percentage change from a zero or negative previous value', () => {
      expect(describeResult(line([['2026-01-01', 0], ['2026-02-01', 50]])).change).toBeNull()
      expect(describeResult({ ...line([['2026-01-01', -20], ['2026-02-01', 50]]), columns: ['month', 'profit'], visualization: { type: 'line', x_key: 'month', y_key: 'profit' } }).change).toBeNull()
    })

    it('computes change as (latest - previous) / previous x 100, from a positive previous value', () => {
      expect(describeResult(line([['2026-01-01', 80], ['2026-02-01', 100]])).change.text).toBe('+25%')
      expect(describeResult(line([['2026-01-01', 100], ['2026-02-01', 80]])).change.text).toBe('-20%')
      expect(describeResult(line([['2026-01-01', 100], ['2026-02-01', -50]])).change.text).toBe('-150%')
    })

    it('gives no percentage change for a percentage column', () => {
      const view = describeResult({ columns: ['month', 'cancelled_pct'], rows: [['2026-01-01', 10], ['2026-02-01', 12]], visualization: { type: 'line', x_key: 'month', y_key: 'cancelled_pct' } })
      expect(view.change).toBeNull()
      expect(view.tiles.map((tile) => tile.label)).toEqual(['Latest', 'Previous', 'Average'])
    })

    it('shows only the latest value for a one-point series', () => {
      const view = describeResult(line([['2026-01-01', 5]]))
      expect(view.tiles).toEqual([{ label: 'Latest', value: '₹5', note: 'Jan 2026' }])
      expect(view.range).toBe('Jan 2026')
    })

    it('reads day-level dates in full', () => {
      const view = describeResult(line([['2026-10-07', 1], ['2026-10-08', 2]]))
      expect(view.latest.label).toBe('8 Oct 2026')
      expect(view.headline).toBe('Revenue for 8 Oct 2026 is ₹2.')
    })
  })

  describe('bar', () => {
    it('highlights the first of equal highest values, in the backend order', () => {
      const view = describeResult(bar([['A', 5], ['B', 9], ['C', 9]]))
      expect(view.topIndex).toBe(1)
      expect(view.top.label).toBe('B')
    })

    it('handles missing and negative values', () => {
      const view = describeResult(bar([['A', -5], ['B', null], ['C', 3]], 'profit'))
      expect(view.count).toBe(2)
      expect(view.tiles.map((tile) => [tile.label, tile.value])).toEqual([
        ['Highest shown', '₹3'],
        ['Lowest shown', '-₹5'],
        ['Total of shown', '-₹2'],
        ['Average of shown', '-₹1'],
      ])
    })

    it('writes no headline when only one value can be compared', () => {
      expect(describeResult(bar([['A', 5], ['B', null]])).headline).toBeNull()
    })
  })
})

describe('describeColumns', () => {
  it('formats month starts, days and midnight timestamps as dates, and leaves real times alone', () => {
    const [months] = describeColumns(['month'], [['2026-04-01'], ['2026-05-01']])
    expect(months.format('2026-04-01')).toBe('Apr 2026')
    const [days] = describeColumns(['day'], [['2026-04-07T00:00:00+05:30'], ['2026-04-08']])
    expect(days.format('2026-04-07T00:00:00+05:30')).toBe('7 Apr 2026')
    const [times] = describeColumns(['at'], [['2026-04-07T10:30:00Z']])
    expect(times.format('2026-04-07T10:30:00Z')).toBe('2026-04-07T10:30:00Z')
  })

  it('marks number columns, keeps text as returned and shows missing values as null', () => {
    const [name, count, flag] = describeColumns(['name', 'order_count', 'active'], [['A', 1200, true], [null, 3, false]])
    expect([name.numeric, count.numeric, flag.numeric]).toEqual([false, true, false])
    expect(count.format(1200)).toBe('1,200')
    expect(name.format(null)).toBeNull()
    expect(flag.format(true)).toBe('true')
    expect(name.label).toBe('Name')
  })
})
