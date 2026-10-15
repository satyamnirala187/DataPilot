import { describe, expect, it } from 'vitest'
import { MAX_TITLE_LENGTH, isTitleTooLong, titleProblem } from './reportTitle.js'
import { SAVED_REPORTS_UNAVAILABLE, filterSavedReports, formatSavedDate, isSavedReportsUnavailable } from './savedReports.js'

describe('report titles (the backend rule)', () => {
  it.each([
    ['Monthly revenue', null],
    ['  Trimmed  ', null],
    ['x'.repeat(120), null],
    ['📈'.repeat(120), null], // 120 characters, 240 UTF-16 units
    ['', 'Enter a title for this report.'],
    ['   ', 'Enter a title for this report.'],
    ['x'.repeat(121), 'Titles can be up to 120 characters. This one has 121.'],
    ['a\tb', 'Titles cannot contain tabs or line breaks.'],
    ['a\nb', 'Titles cannot contain tabs or line breaks.'],
  ])('%j -> %s', (title, problem) => {
    expect(titleProblem(title)).toBe(problem)
  })

  it('measures length after trimming', () => {
    expect(MAX_TITLE_LENGTH).toBe(120)
    expect(isTitleTooLong(`  ${'x'.repeat(120)}  `)).toBe(false)
    expect(isTitleTooLong('x'.repeat(121))).toBe(true)
  })
})

describe('saved report helpers', () => {
  const reports = Object.freeze([
    { title: 'Monthly revenue trend', question: 'Show monthly revenue trends.' },
    { title: 'Top customers', question: 'Who are our top customers?' },
  ])

  it('searches title and question, ignoring case and outer spaces, without changing the list', () => {
    expect(filterSavedReports(reports, ' TREND ')).toEqual([reports[0]])
    expect(filterSavedReports(reports, 'who are')).toEqual([reports[1]])
    expect(filterSavedReports(reports, 'zzz')).toEqual([])
    expect(filterSavedReports(reports, '  ')).toBe(reports)
  })

  it('recognises the store being unavailable only by history_unavailable or 503, and words it for Saved Reports', () => {
    expect(isSavedReportsUnavailable({ status: 503, code: 'history_unavailable' })).toBe(true)
    expect(isSavedReportsUnavailable({ status: 503, code: null })).toBe(true)
    expect([429, 404, 409, null].map((status) => isSavedReportsUnavailable({ status, code: null }))).toEqual([false, false, false, false])
    expect(isSavedReportsUnavailable({ status: 409, code: 'already_saved' })).toBe(false)
    expect(SAVED_REPORTS_UNAVAILABLE).toEqual({ title: 'Saved Reports unavailable', message: 'Saved Reports are not available right now. Please try again later.' })
    expect(SAVED_REPORTS_UNAVAILABLE.message).not.toMatch(/History/)
  })

  it('shows the saved date in local time, and an unreadable one safely', () => {
    expect(formatSavedDate(new Date(2026, 9, 8, 12, 0).toISOString())).toBe('8 Oct 2026')
    expect(formatSavedDate('2026-13-45T10:00:00Z')).toBe('Date unavailable')
    expect(formatSavedDate(undefined)).toBe('Date unavailable')
  })
})
