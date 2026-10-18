// Export one analysis as a PDF download. Loaded only when Export PDF is pressed (a dynamic import),
// so jsPDF and the embedded fonts never weigh on the app's first load. Nothing here calls the
// backend or an AI provider: the PDF is made in the browser from the analysis already on screen
// (for History and Saved Reports, the stored snapshot).

import { jsPDF } from 'jspdf'
import logoUrl from '../assets/brand/datapilot-logo-color.svg'
import { registerFonts } from './fonts.js'
import { renderReport } from './renderReport.js'
import { buildReportContent, reportFilename } from './reportContent.js'

/**
 * Build the PDF for result and download it. options: source (query | history | saved), title and
 * savedAt (a saved report's). Resolves with the file name; any failure rejects, and nothing has
 * been downloaded then. deps lets tests replace the clock, the logo and the download.
 */
export async function exportAnalysisPdf(result, options = {}, deps = {}) {
  const { now = () => new Date(), loadLogo = loadLogoImage, download = downloadBlob } = deps
  const generatedAt = now()
  const content = buildReportContent(result, { ...options, generatedAt })
  const doc = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait', compress: true })
  registerFonts(doc)
  doc.setProperties({ title: content.title, subject: 'DataPilot analysis report', creator: 'DataPilot' })
  renderReport(doc, content, { logo: await loadLogo(logoUrl) })
  const filename = reportFilename(content.title, generatedAt)
  download(doc.output('blob'), filename)
  return filename
}

/**
 * The approved logo file, unchanged, drawn onto a canvas as a PNG for the PDF header. The SVG is a
 * same-origin asset (img-src 'self'). Resolves null when it cannot be drawn (the header then
 * shows the DataPilot wordmark in text instead): exporting never fails because of the logo.
 */
export function loadLogoImage(url, { timeoutMs = 3000, scale = 4 } = {}) {
  if (typeof Image === 'undefined' || typeof document === 'undefined') return Promise.resolve(null)
  return new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(value)
    }
    const timer = setTimeout(() => finish(null), timeoutMs)
    const image = new Image()
    image.onload = () => {
      try {
        const width = image.naturalWidth || 296
        const height = image.naturalHeight || 52
        const canvas = document.createElement('canvas')
        canvas.width = Math.round(width * scale)
        canvas.height = Math.round(height * scale)
        const context = canvas.getContext('2d')
        if (!context) return finish(null)
        context.drawImage(image, 0, 0, canvas.width, canvas.height)
        finish({ dataUrl: canvas.toDataURL('image/png'), ratio: width / height })
      } catch {
        finish(null)
      }
    }
    image.onerror = () => finish(null)
    image.src = url
  })
}

/** Save blob as filename through a temporary blob: link; the object URL is released afterwards. */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.rel = 'noopener'
  link.style.display = 'none'
  document.body.appendChild(link)
  try {
    link.click()
  } finally {
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
