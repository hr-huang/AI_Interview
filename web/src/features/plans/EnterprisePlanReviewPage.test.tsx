import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { api } from '../../api/client'
import type { AssessmentStatusView } from '../../api/types'
import { EnterprisePlanReviewPage } from './EnterprisePlanReviewPage'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      ...actual.api,
      getAssessment: vi.fn(),
    },
  }
})

vi.mock('./PlanReviewPage', () => ({
  PlanReviewPage: () => <div>plan review body</div>,
}))

function status(status: AssessmentStatusView['status']): AssessmentStatusView {
  return {
    assessment_id: 'ast_001',
    status,
    target_role: 'AI Agent应用工程师（校招/初级）',
    retryable: false,
    failed_stage: null,
    error_message: null,
    has_plan: true,
    has_final_plan: true,
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/assessments/ast_001/plan']}>
      <Routes>
        <Route path="/assessments/:assessmentId/plan" element={<EnterprisePlanReviewPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('EnterprisePlanReviewPage', () => {
  test('polls candidate progress from ready to complete and exposes the report', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getAssessment)
      .mockResolvedValueOnce(status('READY'))
      .mockResolvedValueOnce(status('COMPLETE'))

    renderPage()

    expect(await screen.findByText('等待候选人开始')).toBeVisible()
    expect(screen.queryByRole('link', { name: '查看评估报告' })).not.toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText('面试已完成')).toBeVisible()
    expect(screen.getByRole('link', { name: '查看评估报告' })).toHaveAttribute(
      'href',
      '/assessments/ast_001/report',
    )
    expect(api.getAssessment).toHaveBeenCalledTimes(2)
  })

  test('keeps plan review uncluttered before freeze', async () => {
    vi.mocked(api.getAssessment).mockResolvedValue(status('PLAN_REVIEW'))

    renderPage()

    expect(await screen.findByText('plan review body')).toBeVisible()
    expect(screen.queryByLabelText('候选人面试进度')).not.toBeInTheDocument()
  })
})
