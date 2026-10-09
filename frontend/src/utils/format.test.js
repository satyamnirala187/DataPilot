import { describe, expect, it } from 'vitest'
import { dateFormatter, formatAxisValue, formatValue, humanizeLabel, shortenLabel, valueKind } from './format.js'

describe('valueKind', () => {
  it.each([
    ['total_revenue', 'currency'],
    ['revenue', 'currency'],
    ['total_profit', 'currency'],
    ['total_sales', 'currency'],
    ['total_spending', 'currency'],
    ['amount', 'currency'],
    ['average_order_value', 'currency'],
    ['aov', 'currency'],
    ['customer_count', 'number'],
    ['order_count', 'number'],
    ['units_sold', 'number'],
    ['revenue_growth_rate', 'number'],
    ['revenue_share', 'number'],
    ['cancelled_pct', 'percent'],
    ['revenue_percentage', 'percent'],
    ['year', 'number'],
  ])('%s is %s', (column, kind) => {
    expect(valueKind(column)).toBe(kind)
  })
})

describe('formatValue', () => {
  it('formats money with ₹ and two decimals', () => {
    expect(formatValue('total_revenue', 21304631.99)).toBe('₹21,304,631.99')
    expect(formatValue('average_order_value', 5095.58)).toBe('₹5,095.58')
    expect(formatValue('profit', -1200)).toBe('-₹1,200.00')
  })

  it('formats counts and percentages without ₹', () => {
    expect(formatValue('customer_count', 1000)).toBe('1,000')
    expect(formatValue('cancelled_pct', 12.345)).toBe('12.35%')
  })

  it('handles nulls and non-numbers', () => {
    expect(formatValue('revenue', null)).toBe('—')
    expect(formatValue('revenue', 'n/a')).toBe('n/a')
  })
})

describe('formatAxisValue', () => {
  it('uses compact numbers on axes', () => {
    expect(formatAxisValue('revenue', 7371769.52)).toBe('₹7.4M')
    expect(formatAxisValue('order_count', 1200)).toBe('1.2K')
    expect(formatAxisValue('cancelled_pct', 35)).toBe('35%')
  })
})

describe('labels and dates', () => {
  it('humanizes column names', () => {
    expect(humanizeLabel('total_revenue')).toBe('Total Revenue')
    expect(humanizeLabel('aov')).toBe('AOV')
  })

  it('shows months for first-of-month dates and full dates otherwise', () => {
    expect(dateFormatter(['2026-04-01', '2026-05-01'])('2026-04-01')).toBe('Apr 2026')
    // Month abbreviations differ slightly between ICU versions (Sep / Sept).
    expect(dateFormatter(['2026-09-01', '2026-09-15'])('2026-09-15')).toMatch(/^15 Sept? 2026$/)
    expect(dateFormatter(['2026-09-01T10:00:00'])('2026-09-01T10:00:00')).toMatch(/^Sept? 2026$/)
  })

  it('shortens long category labels', () => {
    expect(shortenLabel('Home & Kitchen Appliances')).toBe('Home & Kitche…')
    expect(shortenLabel('Toys')).toBe('Toys')
  })
})
