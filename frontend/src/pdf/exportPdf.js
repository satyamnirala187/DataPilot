// Export one analysis as a PDF download. Loaded only when Export PDF is pressed (a dynamic import),
// so jsPDF and the embedded fonts never weigh on the app's first load. Nothing here calls the
// backend or an AI provider: the PDF is made in the browser from the analysis already on screen
// (for History and Saved Reports, the stored snapshot).

import { jsPDF } from 'jspdf'
import logoUrl from '../assets/brand/datapilot-logo-color.svg'
import { downloadBlob, loadLogoImage } from '../utils/exportFile.js'
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

// Kept importable from here: they moved to utils/exportFile.js so the PPTX export can share them.
export { downloadBlob, loadLogoImage }
