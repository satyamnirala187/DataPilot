import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadBlob, exportAnalysisPdf, loadLogoImage } from './exportPdf.js'

// Making and downloading the PDF, with a fixed clock and a fake download: nothing leaves the
// browser. Every analysis is a test fixture.

const NOW = new Date(2026, 9, 19, 14, 5)
const STORED = {
  id: 'a1',
  question: 'What is the profit for each product in the Electronics category?',
  sql: 'SELECT product_name, profit FROM x',
  columns: ['product_name', 'profit'],
  rows: [['Orvexa Bluetooth Speaker', 560801.88], ['Kalvex Laptop', 1923.18]],
  row_count: 2,
  truncated: false,
  visualization: { type: 'bar', x_key: 'product_name', y_key: 'profit' },
  insight: null,
  created_at: '2026-10-19T08:00:00+00:00',
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function deps(overrides = {}) {
  return { now: () => NOW, loadLogo: vi.fn(async () => null), download: vi.fn(), ...overrides }
}

describe('exportAnalysisPdf', () => {
  it('makes a PDF of the analysis and downloads it under a dated name', async () => {
    const d = deps()
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    const filename = await exportAnalysisPdf(STORED, { source: 'history' }, d)
    expect(filename).toBe('datapilot-profit-product-electronics-category-2026-10-19.pdf')
    const [blob, name] = d.download.mock.calls[0]
    expect(name).toBe(filename)
    expect(blob.type).toBe('application/pdf')
    const head = new Uint8Array(await blob.arrayBuffer()).slice(0, 5)
    expect(String.fromCharCode(...head)).toBe('%PDF-')
    expect(fetchSpy).not.toHaveBeenCalled() // no backend or AI request
  })

  it('names a saved report by its title', async () => {
    const d = deps()
    const filename = await exportAnalysisPdf(STORED, { source: 'saved', title: 'Electronics profit', savedAt: '2026-10-19T08:10:00+00:00' }, d)
    expect(filename).toBe('datapilot-electronics-profit-2026-10-19.pdf')
  })

  it('never changes the analysis it is given', async () => {
    const before = JSON.stringify(STORED)
    await exportAnalysisPdf(STORED, {}, deps())
    expect(JSON.stringify(STORED)).toBe(before)
  })

  it('rejects, without downloading, when the PDF cannot be made', async () => {
    const d = deps({ loadLogo: vi.fn(async () => ({ dataUrl: 'data:image/png;base64,bm90IGEgcG5n', ratio: 5.7 })) })
    await expect(exportAnalysisPdf(STORED, {}, d)).rejects.toThrow()
    expect(d.download).not.toHaveBeenCalled()
  })

  it('rejects when the download itself fails', async () => {
    const d = deps({ download: vi.fn(() => { throw new Error('blocked') }) })
    await expect(exportAnalysisPdf(STORED, {}, d)).rejects.toThrow('blocked')
  })
})

describe('downloadBlob', () => {
  it('clicks a temporary download link and releases the object URL', () => {
    vi.useFakeTimers()
    const create = vi.fn(() => 'blob:datapilot/1')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL: create, revokeObjectURL: revoke })
    const clicked = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() {
      clicked.push({ href: this.getAttribute('href'), download: this.download, attached: document.body.contains(this) })
    })
    const blob = new Blob(['%PDF-'], { type: 'application/pdf' })
    downloadBlob(blob, 'datapilot-revenue-2026-10-19.pdf')
    expect(create).toHaveBeenCalledWith(blob)
    expect(clicked).toEqual([{ href: 'blob:datapilot/1', download: 'datapilot-revenue-2026-10-19.pdf', attached: true }])
    expect(document.querySelector('a[download]')).toBeNull() // removed again
    vi.advanceTimersByTime(1000)
    expect(revoke).toHaveBeenCalledWith('blob:datapilot/1')
  })
})

describe('loadLogoImage', () => {
  it('resolves null when the logo cannot be drawn, so the wordmark is used', async () => {
    await expect(loadLogoImage('/missing.svg', { timeoutMs: 20 })).resolves.toBeNull()
  })
})
