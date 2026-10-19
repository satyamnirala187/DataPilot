// Browser helpers shared by the PDF and PPTX exports: the logo as a PNG and the file download.
// Nothing here calls the backend or an AI provider.

/**
 * The approved logo file, unchanged, drawn onto a canvas as a PNG for the report header. The SVG is a
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
