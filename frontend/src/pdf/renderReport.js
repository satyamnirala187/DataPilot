// Draws a report (from buildReportContent) onto a jsPDF document: A4 portrait, in millimetres.
// Everything is drawn as text and vectors from the stored values; nothing is a screenshot of the
// page, so no sidebar, buttons or other app chrome can appear. Every page gets the DataPilot header
// and a "Page n of N" footer once the content is laid out.

import { FONTS } from './fonts.js'
import { pdfSafeText } from './reportContent.js'

export const PAGE = Object.freeze({ width: 210, height: 297, margin: 18, top: 30, bottom: 276 })

// The app's palette (src/index.css), for print on white.
export const COLORS = Object.freeze({
  ink: '#0e1a36',
  ink2: '#1a2547',
  secondary: '#414c69',
  muted: '#5f6983',
  subtle: '#7f89a3',
  accent: '#2a59e6',
  accentSoft: '#a9bcf5',
  mist: '#eef2fb',
  border: '#e3e8f2',
  zebra: '#f7f9fd',
  code: '#f6f8fc',
})

const MM_PER_PT = 0.3528
const MAX_CELL_LINES = 8 // a longer cell is cut with "…" so one row can never outgrow a page

/** Lay the report out from the first page on, then add the header and footer to every page. */
export function renderReport(doc, content, { logo = null } = {}) {
  const page = new PageWriter(doc)
  page.kicker(content.kicker)
  page.paragraph(content.title, { size: 17, style: 'bold', color: COLORS.ink, gap: 1.5 })
  if (content.question) page.paragraph(`Question: ${content.question}`, { size: 10, color: COLORS.secondary, gap: 1 })
  page.paragraph([`Generated ${content.generated}`, ...content.meta].join('  ·  '), { size: 8.5, color: COLORS.muted, gap: 5 })
  if (content.headline) page.paragraph(content.headline, { size: 11, color: COLORS.ink2, gap: 5 })

  if (content.empty) {
    page.emptyResult()
  } else {
    if (content.kpi) page.kpi(content.kpi)
    else if (content.tiles.length > 0) page.tiles(content.tiles)
    if (content.chart?.kind === 'bar') page.barChart(content.chart)
    if (content.chart?.kind === 'line') page.lineChart(content.chart)
    page.insight(content.insight, content.rowCount)
    page.dataTable(content.table, content.rowCount, content.truncated)
  }
  if (content.sql) page.sql(content.sql)

  decoratePages(doc, content, logo)
  return doc
}

class PageWriter {
  constructor(doc) {
    this.doc = doc
    this.x = PAGE.margin
    this.width = PAGE.width - 2 * PAGE.margin
    this.y = PAGE.top
  }

  font(family, style, size, color) {
    this.doc.setFont(family, style)
    this.doc.setFontSize(size)
    this.doc.setTextColor(color)
  }

  /** Start a new page unless height still fits on this one. */
  ensure(height) {
    if (this.y + height <= PAGE.bottom) return false
    this.doc.addPage()
    this.y = PAGE.top
    return true
  }

  text(value, x, y, options = {}) {
    this.doc.text(pdfSafeText(value), x, y, { baseline: 'top', ...options })
  }

  /** value cut with "…" to fit width in the current font. */
  fit(value, width) {
    let text = pdfSafeText(value)
    if (this.doc.getTextWidth(text) <= width) return text
    while (text.length > 1 && this.doc.getTextWidth(`${text}…`) > width) text = text.slice(0, -1)
    return `${text.trimEnd()}…`
  }

  kicker(label) {
    this.font(FONTS.text, 'bold', 8, COLORS.accent)
    this.text(label.toUpperCase(), this.x, this.y, { charSpace: 0.4 })
    this.y += 5
  }

  paragraph(value, { size, style = 'normal', family = FONTS.text, color = COLORS.ink2, gap = 0 }) {
    this.font(family, style, size, color)
    const lineHeight = size * MM_PER_PT * 1.35
    for (const line of this.doc.splitTextToSize(pdfSafeText(value), this.width)) {
      this.ensure(lineHeight)
      this.text(line, this.x, this.y)
      this.y += lineHeight
    }
    this.y += gap
  }

  heading(label, aside = null) {
    this.ensure(22) // a heading never ends a page on its own
    this.font(FONTS.brand, 'bold', 12, COLORS.ink)
    this.text(label, this.x, this.y)
    if (aside) {
      this.font(FONTS.text, 'normal', 8.5, COLORS.muted)
      this.text(aside, this.x + this.width, this.y + 0.8, { align: 'right' })
    }
    this.y += 7
  }

  card(height, { fill = null } = {}) {
    this.ensure(height)
    this.doc.setLineWidth(0.3)
    this.doc.setDrawColor(COLORS.border)
    if (fill) this.doc.setFillColor(fill)
    this.doc.roundedRect(this.x, this.y, this.width, height, 2.5, 2.5, fill ? 'FD' : 'S')
    return this.y
  }

  emptyResult() {
    const top = this.card(20)
    this.font(FONTS.text, 'bold', 11, COLORS.ink)
    this.text('No matching data was found for this question.', this.x + 6, top + 5)
    this.font(FONTS.text, 'normal', 9.5, COLORS.muted)
    this.text('The query ran successfully and returned 0 rows.', this.x + 6, top + 11.5)
    this.y = top + 26
  }

  kpi({ label, value }) {
    const top = this.card(26, { fill: COLORS.mist })
    this.font(FONTS.text, 'normal', 9, COLORS.muted)
    this.text(this.fit(label, this.width - 12), this.x + 6, top + 5)
    this.font(FONTS.text, 'bold', 22, COLORS.accent)
    this.text(this.fit(value, this.width - 12), this.x + 6, top + 11)
    this.y = top + 32
  }

  tiles(tiles) {
    const shown = tiles.slice(0, 4)
    const gap = 4
    const width = (this.width - gap * (shown.length - 1)) / shown.length
    const height = 22
    this.ensure(height)
    const top = this.y
    shown.forEach((tile, index) => {
      const left = this.x + index * (width + gap)
      this.doc.setLineWidth(0.3)
      this.doc.setDrawColor(COLORS.border)
      this.doc.roundedRect(left, top, width, height, 2.5, 2.5, 'S')
      this.font(FONTS.text, 'normal', 7.5, COLORS.muted)
      this.text(this.fit(tile.label, width - 8), left + 4, top + 3.5)
      this.font(FONTS.text, 'bold', 12.5, COLORS.ink)
      this.text(this.fit(tile.value, width - 8), left + 4, top + 8.5)
      this.font(FONTS.text, 'normal', 7.5, COLORS.muted)
      this.text(this.fit(tile.note, width - 8), left + 4, top + 15.5)
    })
    this.y = top + height + 6
  }

  barChart(chart) {
    const rowHeight = 6.2
    const height = 13 + chart.bars.length * rowHeight + (chart.note ? 6 : 0) + 3
    const top = this.card(height)
    this.font(FONTS.text, 'bold', 10.5, COLORS.ink)
    this.text(this.fit(chart.title, this.width - 10), this.x + 5, top + 4)

    const labelWidth = Math.min(55, this.width * 0.32)
    const valueWidth = 28
    const barLeft = this.x + 5 + labelWidth + 3
    const barSpace = this.width - 10 - labelWidth - 3 - valueWidth
    const largest = Math.max(0, ...chart.bars.map((bar) => bar.value ?? 0))
    chart.bars.forEach((bar, index) => {
      const rowTop = top + 12 + index * rowHeight
      this.font(FONTS.text, 'normal', 8.5, COLORS.secondary)
      this.text(this.fit(bar.label, labelWidth), this.x + 5, rowTop + 0.6)
      const length = largest > 0 && bar.value > 0 ? Math.max(0.6, (bar.value / largest) * barSpace) : 0
      if (length > 0) {
        this.doc.setFillColor(bar.highlight ? COLORS.accent : COLORS.accentSoft)
        this.doc.roundedRect(barLeft, rowTop, length, rowHeight - 2.2, 0.8, 0.8, 'F')
      }
      this.font(FONTS.text, bar.highlight ? 'bold' : 'normal', 8, COLORS.ink)
      this.text(bar.text, barLeft + length + 2, rowTop + 0.6)
    })
    if (chart.note) {
      this.font(FONTS.text, 'normal', 7.5, COLORS.muted)
      this.text(chart.note, this.x + 5, top + 12 + chart.bars.length * rowHeight + 1.5)
    }
    this.y = top + height + 6
  }

  lineChart(chart) {
    const height = 86
    const top = this.card(height)
    this.font(FONTS.text, 'bold', 10.5, COLORS.ink)
    this.text(this.fit(chart.title, this.width * 0.6), this.x + 5, top + 4)
    this.font(FONTS.text, 'normal', 8, COLORS.muted)
    this.text(chart.range, this.x + this.width - 5, top + 4.6, { align: 'right' })

    this.font(FONTS.text, 'normal', 7.5, COLORS.muted)
    const labelWidth = Math.max(...chart.ticks.map((tick) => this.doc.getTextWidth(pdfSafeText(tick.text)))) + 3
    const plot = { left: this.x + 5 + labelWidth, top: top + 15, width: this.width - 10 - labelWidth - 3, height: 54 }
    const low = chart.ticks[0].value
    const high = chart.ticks[chart.ticks.length - 1].value
    const yFor = (value) => plot.top + plot.height - ((value - low) / (high - low || 1)) * plot.height
    const count = chart.points.length
    const xFor = (index) => (count === 1 ? plot.left + plot.width / 2 : plot.left + (index * plot.width) / (count - 1))

    this.doc.setLineWidth(0.2)
    this.doc.setDrawColor(COLORS.border)
    for (const tick of chart.ticks) {
      const y = yFor(tick.value)
      this.doc.line(plot.left, y, plot.left + plot.width, y)
      this.text(tick.text, plot.left - 2, y - 1.3, { align: 'right' })
    }

    // The line breaks where a value is missing, as on screen.
    this.doc.setDrawColor(COLORS.accent)
    this.doc.setLineWidth(0.6)
    chart.points.forEach((point, index) => {
      const next = chart.points[index + 1]
      if (point.value !== null && next && next.value !== null) {
        this.doc.line(xFor(index), yFor(point.value), xFor(index + 1), yFor(next.value))
      }
    })
    this.doc.setFillColor(COLORS.accent)
    chart.points.forEach((point, index) => {
      if (point.value !== null) this.doc.circle(xFor(index), yFor(point.value), 0.8, 'F')
    })

    // At most about eight date labels, always including the last.
    const step = Math.max(1, Math.ceil(count / 8))
    const labelSpace = (plot.width / Math.min(count, 8)) * 0.95
    this.font(FONTS.text, 'normal', 7.5, COLORS.muted)
    chart.points.forEach((point, index) => {
      if (index % step !== 0 && index !== count - 1) return
      if (index !== count - 1 && count - 1 - index < step) return // too close to the last label
      this.text(this.fit(point.label, labelSpace), xFor(index), plot.top + plot.height + 2.5, { align: 'center' })
    })
    this.y = top + height + 6
  }

  insight(insight, rowCount) {
    this.heading('Insight')
    if (insight) this.paragraph(insight, { size: 10, color: COLORS.ink2, gap: 1.5 })
    else this.paragraph('AI insight is unavailable for this result.', { size: 10, color: COLORS.muted, gap: 1.5 })
    this.paragraph(`Answered from the sales database · ${rowCount} ${rowCount === 1 ? 'row' : 'rows'}`, {
      size: 8,
      color: COLORS.muted,
      gap: 6,
    })
  }

  dataTable(table, rowCount, truncated) {
    this.heading('Data', `${rowCount} ${rowCount === 1 ? 'row' : 'rows'}`)
    if (truncated) {
      this.paragraph(`Only the first ${rowCount} rows are shown. The full result has more rows than DataPilot displays.`, {
        size: 8.5,
        color: COLORS.muted,
        gap: 2,
      })
    }
    if (table.columns.length === 0) return

    const size = table.columns.length > 8 ? 7 : 8.5
    const pad = 1.8
    const lineHeight = size * MM_PER_PT * 1.3
    const widths = this.columnWidths(table, size, pad)
    const drawHeader = () => this.tableRow(table.columns.map((column) => column.label), table.columns, widths, {
      size: size - 0.5, pad, lineHeight, header: true,
    })

    drawHeader()
    table.rows.forEach((cells, index) => {
      const lines = this.cellLines(cells, widths, size, 'normal', pad)
      const height = Math.max(...lines.map((cell) => cell.length)) * lineHeight + 2 * pad
      if (this.ensure(height)) drawHeader() // the header repeats at the top of every page
      this.tableRow(cells, table.columns, widths, { size, pad, lineHeight, zebra: index % 2 === 1, lines })
    })
    this.y += 6
  }

  /** Column widths that fill the page: each in proportion to its widest cell, none too narrow. */
  columnWidths(table, size, pad) {
    const sample = table.rows.slice(0, 500)
    const natural = table.columns.map((column, index) => {
      this.font(FONTS.text, 'bold', size - 0.5, COLORS.secondary)
      let widest = this.doc.getTextWidth(pdfSafeText(column.label))
      this.font(FONTS.text, 'normal', size, COLORS.ink2)
      for (const row of sample) widest = Math.max(widest, this.doc.getTextWidth(pdfSafeText(row[index])))
      return Math.min(widest, 90) + 2 * pad
    })
    const minimum = Math.min(16, this.width / table.columns.length)
    const total = natural.reduce((sum, width) => sum + width, 0)
    let widths = natural.map((width) => Math.max(minimum, (width / total) * this.width))
    const over = widths.reduce((sum, width) => sum + width, 0) - this.width
    if (over > 0) {
      // Take the excess from the columns that are wider than the minimum, in proportion.
      const flexible = widths.reduce((sum, width) => sum + (width - minimum), 0)
      widths = widths.map((width) => width - (over * (width - minimum)) / flexible)
    }
    return widths
  }

  cellLines(cells, widths, size, style, pad) {
    this.font(FONTS.text, style, size, COLORS.ink2)
    return cells.map((cell, index) => {
      const lines = this.doc.splitTextToSize(pdfSafeText(cell), widths[index] - 2 * pad)
      if (lines.length <= MAX_CELL_LINES) return lines
      return [...lines.slice(0, MAX_CELL_LINES - 1), this.fit(`${lines[MAX_CELL_LINES - 1]}…`, widths[index] - 2 * pad)]
    })
  }

  tableRow(cells, columns, widths, { size, pad, lineHeight, header = false, zebra = false, lines = null }) {
    const cellLines = lines ?? this.cellLines(cells, widths, size, header ? 'bold' : 'normal', pad)
    const height = Math.max(...cellLines.map((cell) => cell.length)) * lineHeight + 2 * pad
    if (header) this.ensure(height + lineHeight + 2 * pad) // never a header alone at the foot of a page
    if (header || zebra) {
      this.doc.setFillColor(header ? COLORS.mist : COLORS.zebra)
      this.doc.rect(this.x, this.y, this.width, height, 'F')
    }
    this.font(FONTS.text, header ? 'bold' : 'normal', size, header ? COLORS.secondary : COLORS.ink2)
    let left = this.x
    cellLines.forEach((cell, index) => {
      const numeric = columns[index].numeric
      cell.forEach((line, lineIndex) => {
        const y = this.y + pad + lineIndex * lineHeight
        if (numeric) this.doc.text(line, left + widths[index] - pad, y, { baseline: 'top', align: 'right' })
        else this.doc.text(line, left + pad, y, { baseline: 'top' })
      })
      left += widths[index]
    })
    this.doc.setDrawColor(COLORS.border)
    this.doc.setLineWidth(0.2)
    this.doc.line(this.x, this.y + height, this.x + this.width, this.y + height)
    this.y += height
  }

  sql(sql) {
    this.heading('Generated SQL')
    this.font(FONTS.mono, 'normal', 8, COLORS.ink2)
    const lineHeight = 8 * MM_PER_PT * 1.45
    const lines = this.doc.splitTextToSize(pdfSafeText(sql), this.width - 8)
    // A long query continues in a new box on the next page.
    while (lines.length > 0) {
      const fits = Math.floor((PAGE.bottom - this.y - 6) / lineHeight)
      if (fits < 2) {
        this.doc.addPage()
        this.y = PAGE.top
        continue
      }
      const chunk = lines.splice(0, fits)
      const height = chunk.length * lineHeight + 6
      this.doc.setFillColor(COLORS.code)
      this.doc.setDrawColor(COLORS.border)
      this.doc.setLineWidth(0.3)
      this.doc.roundedRect(this.x, this.y, this.width, height, 2, 2, 'FD')
      this.font(FONTS.mono, 'normal', 8, COLORS.ink2)
      chunk.forEach((line, index) => this.doc.text(line, this.x + 4, this.y + 3 + index * lineHeight, { baseline: 'top' }))
      this.y += height + 2
    }
  }
}

function decoratePages(doc, content, logo) {
  const total = doc.getNumberOfPages()
  const right = PAGE.width - PAGE.margin
  for (let number = 1; number <= total; number += 1) {
    doc.setPage(number)
    if (logo) {
      doc.addImage(logo.dataUrl, 'PNG', PAGE.margin, 12, 7 * logo.ratio, 7)
    } else {
      doc.setFont(FONTS.brand, 'bold')
      doc.setFontSize(15)
      doc.setTextColor(COLORS.ink)
      doc.text('DataPilot', PAGE.margin, 12, { baseline: 'top' })
    }
    doc.setFont(FONTS.text, 'normal')
    doc.setFontSize(8)
    doc.setTextColor(COLORS.muted)
    doc.text('Analysis report', right, 14, { baseline: 'top', align: 'right' })
    doc.setDrawColor(COLORS.border)
    doc.setLineWidth(0.3)
    doc.line(PAGE.margin, 23, right, 23)

    doc.line(PAGE.margin, 282, right, 282)
    doc.setFontSize(7.5)
    doc.text(pdfSafeText(`DataPilot · Generated ${content.generated}`), PAGE.margin, 285, { baseline: 'top' })
    doc.text(`Page ${number} of ${total}`, right, 285, { baseline: 'top', align: 'right' })
  }
}
