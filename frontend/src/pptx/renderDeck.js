// Draws a slide plan (from buildSlides) with PptxGenJS. Everything is native PowerPoint content:
// text boxes, shapes, a real table and real charts that stay editable (right-click > Edit Data).
// Nothing is a picture of the web page, and nothing is requested: only the logo PNG, already
// drawn in the browser, is embedded. All page-break and layout decisions were made in buildSlides.

import { COLORS, FONTS, SLIDE } from './theme.js'

const MASTER = 'DATAPILOT'
const M = SLIDE.margin
const W = SLIDE.contentWidth

/** Add the plan's slides to pptx (a PptxGenJS presentation). logo: { dataUrl, ratio } or null. */
export function renderDeck(pptx, plan, { logo = null } = {}) {
  pptx.layout = SLIDE.layout
  pptx.title = plan.title
  pptx.subject = 'DataPilot analysis report'
  pptx.author = 'DataPilot'
  pptx.company = 'DataPilot'
  defineMaster(pptx, plan, logo)

  plan.slides.forEach((planned, index) => {
    const slide = pptx.addSlide({ masterName: MASTER })
    DRAW[planned.type](pptx, slide, planned)
    text(slide, `${index + 1} of ${plan.slides.length}`, {
      x: SLIDE.width - M - 1.5, y: 7.03, w: 1.5, h: 0.25, fontSize: 9, color: COLORS.muted, align: 'right',
    })
  })
  return pptx
}

// The header and footer every slide shares: logo (or wordmark), report label, thin rules, footer.
function defineMaster(pptx, plan, logo) {
  const brand = logo
    ? { image: { data: logo.dataUrl, x: M, y: 0.27, w: 0.34 * logo.ratio, h: 0.34, altText: 'DataPilot' } }
    : { text: { text: 'DataPilot', options: { x: M, y: 0.25, w: 3, h: 0.4, fontFace: FONTS.text, fontSize: 18, bold: true, color: COLORS.ink, margin: 0 } } }
  pptx.defineSlideMaster({
    title: MASTER,
    background: { color: COLORS.white },
    objects: [
      brand,
      { text: { text: 'Analysis report', options: { x: SLIDE.width - M - 3, y: 0.32, w: 3, h: 0.28, fontFace: FONTS.text, fontSize: 10, color: COLORS.muted, align: 'right', margin: 0 } } },
      { line: { x: M, y: 0.8, w: W, h: 0, line: { color: COLORS.border, width: 1 } } },
      { line: { x: M, y: 6.95, w: W, h: 0, line: { color: COLORS.border, width: 1 } } },
      { text: { text: plan.footer, options: { x: M, y: 7.03, w: 9, h: 0.25, fontFace: FONTS.text, fontSize: 9, color: COLORS.muted, margin: 0 } } },
    ],
  })
}

function text(slide, value, options) {
  slide.addText(value, { fontFace: FONTS.text, color: COLORS.ink2, margin: 0, valign: 'top', ...options })
}

function card(pptx, slide, { x, y, w, h, fill = COLORS.white }) {
  slide.addShape(pptx.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.08, fill: { color: fill }, line: { color: COLORS.border, width: 1 } })
}

function heading(slide, planned) {
  text(slide, planned.title, { x: M, y: 1.1, w: W, h: 0.4, fontSize: 20, bold: true, color: COLORS.ink })
  if (planned.subtitle) text(slide, planned.subtitle, { x: M, y: 1.52, w: W, h: 0.3, fontSize: 11, color: COLORS.muted })
}

// --- One drawer per slide type ------------------------------------------------------------------

const DRAW = {
  summary(pptx, slide, planned) {
    const { layout } = planned
    text(slide, planned.kicker.toUpperCase(), { x: M, ...layout.kicker, w: W, fontSize: 11, bold: true, color: COLORS.accent, charSpacing: 2 })
    text(slide, planned.title, { x: M, ...layout.title, w: W, fontSize: planned.titleSize, bold: true, color: COLORS.ink })
    if (planned.question) text(slide, `Question: ${planned.question}`, { x: M, ...layout.question, w: W, fontSize: 12, color: COLORS.secondary })
    text(slide, planned.meta, { x: M, ...layout.meta, w: W, fontSize: 11, color: COLORS.muted })
    if (planned.headline) text(slide, planned.headline, { x: M, ...layout.headline, w: W, fontSize: 16, color: COLORS.ink2 })

    const { y, h } = layout.body
    if (planned.empty) {
      card(pptx, slide, { x: M, y, w: W, h })
      text(slide, 'No matching data was found for this question.', { x: M + 0.35, y: y + 0.3, w: W - 0.7, h: 0.4, fontSize: 18, bold: true, color: COLORS.ink })
      text(slide, 'The query ran successfully and returned 0 rows.', { x: M + 0.35, y: y + 0.8, w: W - 0.7, h: 0.3, fontSize: 13, color: COLORS.muted })
    } else if (planned.kpi) {
      card(pptx, slide, { x: M, y, w: W, h, fill: COLORS.mist })
      text(slide, planned.kpi.label, { x: M + 0.4, y: y + 0.25, w: W - 0.8, h: 0.3, fontSize: 14, color: COLORS.muted })
      text(slide, planned.kpi.value, { x: M + 0.4, y: y + 0.7, w: W - 0.8, h: 0.75, fontSize: 40, bold: true, color: COLORS.accent })
    } else if (planned.tiles.length > 0) {
      const gap = 0.25
      const width = (W - gap * (planned.tiles.length - 1)) / planned.tiles.length
      planned.tiles.forEach((tile, index) => {
        const x = M + index * (width + gap)
        card(pptx, slide, { x, y, w: width, h })
        text(slide, tile.label, { x: x + 0.2, y: y + 0.18, w: width - 0.4, h: 0.25, fontSize: 11, color: COLORS.muted })
        text(slide, tile.value, { x: x + 0.2, y: y + 0.5, w: width - 0.4, h: 0.4, fontSize: 20, bold: true, color: COLORS.ink })
        text(slide, tile.note, { x: x + 0.2, y: y + 0.98, w: width - 0.4, h: 0.25, fontSize: 10, color: COLORS.muted })
      })
    }

    if (planned.insight) insightPanel(pptx, slide, { x: M, w: W, ...layout.insight }, planned.insight, 13)
  },

  chart(pptx, slide, planned) {
    heading(slide, planned)
    const side = Boolean(planned.insight)
    const area = { x: M, y: 1.95, w: side ? 8.1 : W, h: 4.8 }
    addChart(pptx, slide, planned, area)
    if (side) insightPanel(pptx, slide, { x: M + 8.35, y: area.y, w: W - 8.35, h: area.h }, planned.insight, 13)
  },

  insight(pptx, slide, planned) {
    heading(slide, planned)
    insightPanel(pptx, slide, { x: M, y: 1.7, w: W, h: 4.9 }, planned.text, 16, { label: false })
  },

  table(pptx, slide, planned) {
    heading(slide, planned)
    const rule = [{ type: 'none' }, { type: 'none' }, { type: 'solid', color: COLORS.border, pt: 0.75 }, { type: 'none' }]
    const common = { fontFace: FONTS.text, fontSize: planned.fontSize, valign: 'middle', border: rule, margin: [0.03, 0.1, 0.03, 0.1] }
    const header = planned.columns.map((column) => ({
      text: column.label,
      options: { ...common, bold: true, color: COLORS.secondary, fill: { color: COLORS.mist }, align: column.numeric ? 'right' : 'left' },
    }))
    const body = planned.rows.map((row, rowIndex) =>
      row.map((cell, index) => ({
        text: cell,
        options: {
          ...common,
          color: COLORS.ink2,
          fill: { color: rowIndex % 2 === 1 ? COLORS.zebra : COLORS.white },
          align: planned.columns[index].numeric ? 'right' : 'left',
        },
      })),
    )
    slide.addTable([header, ...body], {
      x: M,
      y: 1.9,
      w: W,
      colW: planned.columns.map((column) => column.width),
      rowH: [planned.headerHeight, ...planned.rowHeights],
    })
  },

  sql(pptx, slide, planned) {
    heading(slide, planned)
    card(pptx, slide, { x: M, y: 1.7, w: W, h: 5.0, fill: COLORS.code })
    text(slide, planned.lines.join('\n'), {
      x: M + 0.25, y: 1.9, w: W - 0.5, h: 4.6, fontFace: FONTS.mono, fontSize: 11, color: COLORS.ink2, wrap: false,
    })
  },
}

// The insight in a bordered panel. A "label" row (INSIGHT) is shown unless label: false.
function insightPanel(pptx, slide, area, value, fontSize, { label = true } = {}) {
  card(pptx, slide, area)
  let top = area.y + 0.25
  if (label) {
    text(slide, 'INSIGHT', { x: area.x + 0.35, y: top, w: area.w - 0.7, h: 0.25, fontSize: 10, bold: true, color: COLORS.accent, charSpacing: 2 })
    top += 0.35
  }
  text(slide, value, { x: area.x + 0.35, y: top, w: area.w - 0.7, h: area.h - (top - area.y) - 0.2, fontSize, color: COLORS.ink2 })
}

// --- Charts -------------------------------------------------------------------------------------

/** Excel number format for chart labels, from the column's kind (₹ amounts, percentages, numbers). */
export function chartNumberFormat(valueFormat, values) {
  const decimals = values.every((value) => value === null || Number.isInteger(value)) ? '' : '.00'
  if (valueFormat === 'currency') return `"₹"#,##0${decimals}`
  if (valueFormat === 'percent') return `0${decimals ? '.0' : ''}"%"`
  return `#,##0${decimals}`
}

function addChart(pptx, slide, planned, area) {
  const { chart } = planned
  const bar = chart.kind === 'bar'
  // A horizontal bar chart draws its first category at the bottom, so the data goes in reversed:
  // the first row is then the top bar, as in the app.
  const labels = bar ? [...chart.labels].reverse() : chart.labels
  const values = bar ? [...chart.values].reverse() : chart.values
  const format = chartNumberFormat(chart.valueFormat, chart.values)
  const axes = {
    ...area,
    chartColors: [COLORS.accent],
    showLegend: false,
    showTitle: false,
    altText: `${planned.title}. ${bar ? 'Bar' : 'Line'} chart of ${chart.values.length} values; the same data is in the Data slides.`,
    catAxisLabelFontFace: FONTS.text,
    catAxisLabelFontSize: 10,
    catAxisLabelColor: COLORS.secondary,
    catAxisLineShow: true,
    catAxisLineColor: COLORS.border,
    valAxisLabelFontFace: FONTS.text,
    valAxisLabelFontSize: 10,
    valAxisLabelColor: COLORS.muted,
    valAxisLabelFormatCode: format,
    valAxisLineShow: false,
    valGridLine: { color: COLORS.border, size: 0.75 },
    catGridLine: { style: 'none' },
  }
  const data = [{ name: chart.seriesName, labels, values }]

  if (bar) {
    slide.addChart(pptx.ChartType.bar, data, {
      ...axes,
      barDir: 'bar',
      barGapWidthPct: 45,
      valAxisMinVal: values.every((value) => value === null || value >= 0) ? 0 : undefined,
      showValue: true,
      dataLabelFormatCode: format,
      dataLabelFontFace: FONTS.text,
      dataLabelFontSize: 10,
      dataLabelColor: COLORS.ink,
      dataLabelPosition: 'outEnd',
    })
  } else {
    slide.addChart(pptx.ChartType.line, data, {
      ...axes,
      lineSize: 2.5,
      lineDataSymbol: 'circle',
      lineDataSymbolSize: 8,
      displayBlanksAs: 'gap', // a missing value breaks the line, as on screen
      showValue: false,
    })
  }
}
