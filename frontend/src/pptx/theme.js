// The look of an exported deck. Colours (hex without #) are the app's palette, as used for the PDF
// in src/pdf/renderReport.js. Fonts are Arial and Courier New: PowerPoint cannot embed our PDF
// fonts, and these two exist on Windows, macOS and in Google Slides. Sizes are in inches.

export const COLORS = Object.freeze({
  ink: '0E1A36',
  ink2: '1A2547',
  secondary: '414C69',
  muted: '5F6983',
  accent: '2A59E6',
  accentSoft: 'A9BCF5',
  mist: 'EEF2FB',
  border: 'E3E8F2',
  zebra: 'F7F9FD',
  code: 'F6F8FC',
  white: 'FFFFFF',
})

export const FONTS = Object.freeze({ text: 'Arial', mono: 'Courier New' })

// PptxGenJS "LAYOUT_WIDE": 16:9, 13.33 × 7.5 in.
export const SLIDE = Object.freeze({
  layout: 'LAYOUT_WIDE',
  width: 13.33,
  height: 7.5,
  margin: 0.6,
  contentWidth: 12.13,
  contentTop: 1.15, // below the header band
  contentBottom: 6.8, // above the footer band
})
