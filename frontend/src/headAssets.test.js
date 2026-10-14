// @vitest-environment node
import { existsSync, readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// Files in public/ are served from the site root, unprocessed: a wrong name fails silently in the
// browser, so check every root path that index.html and the web manifest point to.
const frontend = new URL('../', import.meta.url)
const publicFile = (path) => new URL(`public${path}`, frontend)

describe('favicon and web manifest', () => {
  const html = readFileSync(new URL('index.html', frontend), 'utf8')
  const manifest = JSON.parse(readFileSync(publicFile('/site.webmanifest'), 'utf8'))

  it('every icon and manifest link in index.html exists in public/', () => {
    const hrefs = [...html.matchAll(/<link[^>]+href="(\/[^"]+)"/g)].map((match) => match[1])
    expect(hrefs).toEqual(
      expect.arrayContaining(['/favicon.ico', '/favicon.svg', '/apple-touch-icon.png', '/site.webmanifest']),
    )
    for (const href of hrefs) expect(existsSync(publicFile(href)), href).toBe(true)
  })

  it('the manifest names DataPilot and its icons exist', () => {
    expect(manifest.name).toBe('DataPilot')
    expect(manifest.icons.length).toBeGreaterThan(0)
    for (const icon of manifest.icons) expect(existsSync(publicFile(icon.src)), icon.src).toBe(true)
  })

  it('uses the approved light theme colour', () => {
    expect(html).toContain('<meta name="theme-color" content="#FFFFFF" />')
  })
})
