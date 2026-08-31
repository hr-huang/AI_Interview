import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { api } from '../../api/client'
import type { AssessmentStatusView } from '../../api/types'
import { AnalyzingPage } from './AnalyzingPage'

vi.mock('../../api/client', () => ({
  api: {
    getAssessment: vi.fn(),
    retryAssessment: vi.fn(),
  },
}))

const status = (overrides: Partial<AssessmentStatusView>): AssessmentStatusView => ({
  assessment_id: 'ast_001',
  status: 'ANALYZING',
  target_role: 'AI Agent / AI应用工程师',
  retryable: false,
  failed_stage: null,
  error_message: null,
  has_plan: false,
  has_final_plan: false,
  ...overrides,
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/assessments/ast_001/analyzing']}>
      <Routes>
        <Route path="/assessments/:assessmentId/analyzing" element={<AnalyzingPage />} />
        <Route path="/assessments/:assessmentId/plan" element={<h1>审核评估计划</h1>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('AnalyzingPage', () => {
  test('polls real status and navigates only when the server reaches PLAN_REVIEW', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getAssessment)
      .mockResolvedValueOnce(status({ status: 'ANALYZING' }))
      .mockResolvedValueOnce(status({ status: 'PLAN_REVIEW', has_plan: true }))
    renderPage()

    await act(async () => {
      await Promise.resolve()
    })
    expect(api.getAssessment).toHaveBeenCalledTimes(1)
    expect(screen.getByText('文件提取')).toBeInTheDocument()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '审核评估计划' })).not.toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600)
    })
    expect(screen.getByRole('heading', { name: '审核评估计划' })).toBeInTheDocument()
  })

  test('shows a retry action on FAILED while keeping the assessment id', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getAssessment).mockResolvedValue(
      status({
        status: 'FAILED',
        retryable: true,
        failed_stage: 'ANALYZING',
        error_message: '面试计划分析失败，请稍后重试。',
      }),
    )
    vi.mocked(api.retryAssessment).mockResolvedValue({
      assessment_id: 'ast_001',
      status: 'ANALYZING',
    })
    renderPage()

    expect(await screen.findByRole('button', { name: '重新分析' })).toBeVisible()
    expect(screen.getByText('ast_001')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新分析' }))
    await waitFor(() => expect(api.retryAssessment).toHaveBeenCalledWith('ast_001'))
    expect(screen.getByText('ast_001')).toBeInTheDocument()
  })

  test('waits for an in-flight status request before polling again', async () => {
    vi.useFakeTimers()
    let resolvePending!: (value: AssessmentStatusView) => void
    const pending = new Promise<AssessmentStatusView>((resolve) => {
      resolvePending = resolve
    })
    vi.mocked(api.getAssessment)
      .mockReturnValueOnce(pending)
      .mockResolvedValue(status({ status: 'ANALYZING' }))
    renderPage()

    expect(api.getAssessment).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(api.getAssessment).toHaveBeenCalledTimes(1)

    resolvePending(status({ status: 'ANALYZING' }))
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(api.getAssessment).toHaveBeenCalledTimes(2)
  })
})
