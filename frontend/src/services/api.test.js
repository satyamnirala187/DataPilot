import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL, ApiError, postQuery } from './api.js'

afterEach(() => {
  vi.unstubAllGlobals()
})

function stubFetch(response) {
  const fetchMock = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('postQuery', () => {
  it('posts JSON to {API_BASE_URL}/query', async () => {
    const body = { question: 'q', sql: 'SELECT 1', columns: ['x'], rows: [[1]], row_count: 1, truncated: false }
    const fetchMock = stubFetch(new Response(JSON.stringify(body), { status: 200 }))

    await expect(postQuery('q')).resolves.toEqual(body)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${API_BASE_URL}/query`)
    expect(options.headers['Content-Type']).toBe('application/json')
    expect(API_BASE_URL).not.toMatch(/\/$/)
  })

  it('replaces the backend request-shape message with a user-facing one', async () => {
    stubFetch(new Response(JSON.stringify({ error: { code: 'invalid_request', message: 'Send JSON like {"question": "..."}' } }), { status: 400 }))
    const error = await postQuery('q').catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(400)
    expect(error.message).not.toContain('JSON')
  })

  it('rejects a 200 response that is not a query result', async () => {
    stubFetch(new Response('{"unexpected": true}', { status: 200 }))
    await expect(postQuery('q')).rejects.toMatchObject({ title: 'Unexpected response' })
  })
})
