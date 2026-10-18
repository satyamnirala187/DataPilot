// The fonts embedded in exported PDFs. jsPDF's built-in fonts cannot draw ₹, so these are subsets
// (Latin, punctuation, ₹) of OFL fonts, licences beside them in ./fonts:
//   Noto Sans (regular, semibold)  all text that comes from the analysis; it has ₹
//   Noto Sans Mono                 the generated SQL
//   Outfit (semibold)              the DataPilot wordmark and fixed section headings, as in the app
//                                  (it has no ₹, so it is never used for analysis text)
// ?inline puts each file into this module as base64 text: nothing is fetched when exporting, so
// the strict connect-src of the Content Security Policy is not involved.

import notoSansRegular from './fonts/NotoSans-Regular.subset.ttf?inline'
import notoSansSemiBold from './fonts/NotoSans-SemiBold.subset.ttf?inline'
import notoSansMono from './fonts/NotoSansMono-Regular.subset.ttf?inline'
import outfitSemiBold from './fonts/Outfit-SemiBold.subset.ttf?inline'

export const FONTS = Object.freeze({
  text: 'NotoSans',
  mono: 'NotoSansMono',
  brand: 'Outfit',
})

const FILES = [
  ['NotoSans-Regular.ttf', notoSansRegular, FONTS.text, 'normal'],
  ['NotoSans-SemiBold.ttf', notoSansSemiBold, FONTS.text, 'bold'],
  ['NotoSansMono-Regular.ttf', notoSansMono, FONTS.mono, 'normal'],
  ['Outfit-SemiBold.ttf', outfitSemiBold, FONTS.brand, 'bold'],
]

/** Add the embedded fonts to a jsPDF document. */
export function registerFonts(doc) {
  for (const [file, dataUrl, family, style] of FILES) {
    doc.addFileToVFS(file, base64Of(dataUrl))
    doc.addFont(file, family, style)
  }
  doc.setFont(FONTS.text, 'normal')
}

function base64Of(dataUrl) {
  const comma = dataUrl.indexOf(',')
  if (!dataUrl.startsWith('data:') || comma === -1) throw new Error('Embedded PDF font is missing')
  return dataUrl.slice(comma + 1)
}
