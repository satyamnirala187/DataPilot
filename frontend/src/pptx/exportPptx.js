// Export one analysis as a PowerPoint (.pptx) download. Loaded only when Export PPTX is pressed (a
// dynamic import), so PptxGenJS never weighs on the app's first load. Nothing here calls the
// backend or an AI provider: the deck is made in the browser from the analysis already on screen
// (for History and Saved Reports, the stored snapshot).

import PptxGenJS from 'pptxgenjs'
import logoUrl from '../assets/brand/datapilot-logo-color.svg'
import { buildReportContent, reportFilename } from '../pdf/reportContent.js'
import { downloadBlob, loadLogoImage } from '../utils/exportFile.js'
import { buildSlides } from './buildSlides.js'
import { renderDeck } from './renderDeck.js'

export const PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

/**
 * Build the deck for result and download it. options: source (query | history | saved), title and
 * savedAt (a saved report's). Resolves with the file name; any failure rejects, and nothing has
 * been downloaded then. deps lets tests replace the clock, the logo and the download.
 */
export async function exportAnalysisPptx(result, options = {}, deps = {}) {
  const { now = () => new Date(), loadLogo = loadLogoImage, download = downloadBlob } = deps
  const generatedAt = now()
  const content = buildReportContent(result, { ...options, generatedAt })
  const plan = buildSlides(content)
  const pptx = new PptxGenJS()
  renderDeck(pptx, plan, { logo: await loadLogo(logoUrl) })
  const file = await pptx.write({ outputType: 'blob', compression: true })
  // A typed blob so the download is named and opened as PowerPoint.
  const blob = new Blob([file], { type: PPTX_MIME })
  const filename = reportFilename(content.title, generatedAt, 'pptx')
  download(blob, filename)
  return filename
}
