import { afterEach, describe, expect, test, vi } from 'vitest'
import { ApiError, api, request } from './client'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('typed API client', () => {
  test('keeps FormData unmodified and targets the configured API base', async () => {
    const response = new Response(
      JSON.stringify({ assessment_id: 'ast_001', status: 'ANALYZING' }),
      { status: 202, headers: { 'content-type': 'application/json' } },
    )
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)
    const form = new FormData()
    form.set('target_role', 'AI 应用工程师')

    await expect(api.createAssessment(form)).resolves.toEqual({
      assessment_id: 'ast_001',
      status: 'ANALYZING',
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/assessments')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(form)
    expect(new Headers(init?.headers).has('Content-Type')).toBe(false)
  })

  test('preserves structured server errors as ApiError details', async () => {
    const response = new Response(
      JSON.stringify({ detail: [{ msg: '岗位不能为空' }] }),
      { status: 422, headers: { 'content-type': 'application/json' } },
    )
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)

    await expect(request('/assessments')).rejects.toMatchObject({
      name: 'ApiError',
      status: 422,
      message: '岗位不能为空',
    } satisfies Partial<ApiError>)
  })

  test('encodes path identifiers before sending JSON requests', async () => {
    const response = new Response(
      JSON.stringify({
        assessment_id: 'ast/001',
        status: 'PLAN_REVIEW',
        target_role: 'AI 应用工程师',
        retryable: false,
        failed_stage: null,
        error_message: null,
        has_plan: true,
        has_final_plan: false,
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    )
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)

    await api.getAssessment('ast/001')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/assessments/ast%2F001')
    expect(init?.method).toBeUndefined()
  })
})
