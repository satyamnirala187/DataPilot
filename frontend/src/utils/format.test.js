import { describe, expect, it } from 'vitest'
import {
  dateFormatter,
  formatAxisValue,
  formatIndianCompact,
  formatIndianCompactValue,
  formatIndianNumber,
  formatIndianValue,
  formatInr,
  formatValue,
  humanizeLabel,
  shortenLabel,
  valueKind,
} from './format.js'

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

describe('Indian number presentation (UI V2)', () => {
  it('groups digits the Indian way', () => {
    expect(formatIndianNumber(130100)).toBe('1,30,100')
    expect(formatIndianNumber(1234567.891)).toBe('12,34,567.89')
    expect(formatIndianNumber('4500')).toBe('4,500')
    expect(formatIndianNumber(-1200)).toBe('-1,200')
    expect(formatIndianNumber(-0.001)).toBe('0')
  })

  it('formats rupees with Indian grouping and two decimals', () => {
    expect(formatInr(21304631.99)).toBe('₹2,13,04,631.99')
    expect(formatInr(5095.58)).toBe('₹5,095.58')
    expect(formatInr(-1200)).toBe('-₹1,200.00')
    expect(formatInr(0)).toBe('₹0.00')
    expect(formatInr(-0.001)).toBe('₹0.00')
  })

  it.each([
    [454000000, '45.4 Cr'],
    [21304631.99, '2.1 Cr'],
    [10000000, '1 Cr'],
    [123456789012, '12,345.7 Cr'],
    [740000, '7.4 L'],
    [100000, '1 L'],
    [1200, '1.2 K'],
    [950, '950'],
    [12.34, '12.3'],
    [0, '0'],
    // Values that round up to the next unit use it.
    [99960, '1 L'],
    [9996000, '1 Cr'],
    [999.96, '1 K'],
    [-740000, '-7.4 L'],
    [-0.01, '0'],
  ])('compacts %s as %s', (value, expected) => {
    expect(formatIndianCompact(value)).toBe(expected)
  })

  it('picks ₹ and % from the column name, as the existing formatters do', () => {
    expect(formatIndianValue('total_revenue', 21304631.99)).toBe('₹2,13,04,631.99')
    expect(formatIndianValue('order_count', 130100)).toBe('1,30,100')
    expect(formatIndianValue('cancelled_pct', 12.345)).toBe('12.35%')
    expect(formatIndianCompactValue('total_revenue', 454000000)).toBe('₹45.4 Cr')
    expect(formatIndianCompactValue('total_revenue', -740000)).toBe('-₹7.4 L')
    expect(formatIndianCompactValue('order_count', 1200)).toBe('1.2 K')
    expect(formatIndianCompactValue('revenue_percentage', 56.94)).toBe('56.9%')
  })

  it('shows a dash for missing values and other text as it is', () => {
    for (const format of [formatIndianNumber, formatInr, formatIndianCompact]) {
      expect(format(null)).toBe('—')
      expect(format(undefined)).toBe('—')
      expect(format('n/a')).toBe('n/a')
    }
    expect(formatIndianValue('revenue', null)).toBe('—')
    expect(formatIndianCompactValue('revenue', 'n/a')).toBe('n/a')
    expect(formatIndianCompactValue('cancelled_pct', null)).toBe('—')
  })

  it('does not show non-finite numbers as values', () => {
    expect(formatInr('Infinity')).toBe('Infinity')
    expect(formatIndianCompact('NaN')).toBe('NaN')
  })

  it('leaves the existing formatters unchanged', () => {
    expect(formatValue('total_revenue', 21304631.99)).toBe('₹21,304,631.99')
    expect(formatAxisValue('revenue', 7371769.52)).toBe('₹7.4M')
  })
})
