import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { cloneElement, createElement } from 'react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
})

// jsdom has no layout engine: ResizeObserver is missing and every element measures 0×0,
// so Recharts' ResponsiveContainer would draw nothing. Give charts a fixed size in tests.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.mock('recharts', async (importOriginal) => {
  const recharts = await importOriginal()
  const ResponsiveContainer = ({ children }) =>
    createElement('div', { style: { width: 800, height: 320 } }, cloneElement(children, { width: 800, height: 320 }))
  return { ...recharts, ResponsiveContainer }
})
