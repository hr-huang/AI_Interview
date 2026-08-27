import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { ApiError, api } from '../../api/client'
import type { AssessmentPlanView } from '../../api/types'
import { PlanReviewPage } from './PlanReviewPage'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      getPlan: vi.fn(),
      updatePlanOverrides: vi.fn(),
      freezePlan: vi.fn(),
    },
  }
})

const planPayload = (overrides: Partial<AssessmentPlanView> = {}): AssessmentPlanView => {
  const target = {
    id: 'target_01',
    objective: '验证候选人能把 Agent 状态流落地为可靠服务',
    target_type: 'implementation',
    competency_ids: ['competency_01'],
    related_claim_ids: ['claim_01'],
    priority: 'high',
    must_cover: true,
    time_budget_minutes: 12,
    preferred_modes: ['implementation', 'scenario'],
    evidence_requirements: [
      {
        id: 'target_01_req_01',
        description: '能解释状态、错误恢复和验证证据之间的因果链',
        planned_role_dimension_id: 'role_dim_01',
        requires_transfer_validation: true,
      },
    ],
  }
  return {
    assessment_id: 'ast_001',
    status: 'PLAN_REVIEW',
    original_plan: {
      duration_minutes: 45,
      max_questions: 12,
      closing_buffer_minutes: 5,
      targets: [target],
      candidate_profile: {
        summary: '有 LangGraph 和失败恢复项目经验',
        skills: ['Python', 'LangGraph'],
      },
      claims: [
        {
          id: 'claim_01',
          text: '主导过 Agent Workflow 的线上稳定性改造',
          status: '待核验',
        },
      ],
    },
    preview_plan: {
      duration_minutes: 45,
      max_questions: 12,
      closing_buffer_minutes: 5,
      targets: [target],
    },
    overrides: null,
    role_profile: {
      role_family: 'ai_application_engineering',
      display_name: 'AI Agent / AI 应用工程师',
      version: '2026-H2',
      dimensions: [
        {
          id: 'role_dim_01',
          name: 'Agent 系统设计',
          description: '把模型、工具和状态组织成可验证系统',
        },
      ],
    },
    guardrails: {
      allowed_duration_minutes: ['30', '45', '60'],
      editable_target_fields: ['priority', 'objective', 'time_budget_minutes'],
      immutable_fields: ['must_cover', 'evidence_requirements'],
    },
    ...overrides,
  }
}

function renderPlan(payload = planPayload()) {
  vi.mocked(api.getPlan).mockResolvedValue(payload)
  return render(
    <MemoryRouter initialEntries={['/assessments/ast_001/plan']}>
      <Routes>
        <Route path="/assessments/:assessmentId/plan" element={<PlanReviewPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('PlanReviewPage', () => {
  test('locks core targets but allows business focus edits', async () => {
    renderPlan()

    expect(await screen.findByText('岗位核心 · 不可删除')).toBeVisible()
    expect(screen.queryByRole('button', { name: '删除目标' })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '业务关注点' })).toBeEnabled()
    expect(screen.getByText('Agent 系统设计')).toBeVisible()
    expect(screen.getByText(/能解释状态、错误恢复/)).toBeVisible()
  })

  test('freezes plan and shows candidate link only after server success', async () => {
    const user = userEvent.setup()
    vi.mocked(api.freezePlan).mockResolvedValue({
      assessment_id: 'ast_001',
      status: 'READY',
      candidate_url: '/interviews/candidate_once',
    })
    renderPlan()

    await user.click(await screen.findByRole('button', { name: '校验并冻结计划' }))
    expect(await screen.findByLabelText('候选人链接')).toHaveValue(
      '/interviews/candidate_once',
    )
    expect(screen.getByRole('link', { name: '打开候选人链接' })).toHaveAttribute(
      'href',
      '/interviews/candidate_once',
    )
    expect(api.freezePlan).toHaveBeenCalledWith('ast_001')
  })

  test('sends allowed edits and renders a server guardrail error beside the plan', async () => {
    const user = userEvent.setup()
    vi.mocked(api.updatePlanOverrides).mockRejectedValue(
      new ApiError(422, '核心目标不能降级或删除', {
        detail: '核心目标不能降级或删除',
      }),
    )
    renderPlan()

    const focus = await screen.findByRole('textbox', { name: '业务关注点' })
    await user.clear(focus)
    await user.type(focus, '重点核验线上故障恢复')
    await user.click(screen.getByRole('button', { name: '保存计划修改' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('核心目标不能降级或删除')
    expect(api.updatePlanOverrides).toHaveBeenCalledWith(
      'ast_001',
      expect.objectContaining({
        duration_minutes: 45,
        minimum_transfer_validations: 1,
        target_updates: [
          expect.objectContaining({
            target_id: 'target_01',
            objective: '重点核验线上故障恢复',
          }),
        ],
      }),
    )
  })

  test('prevents duplicate freeze submissions while saving', async () => {
    const user = userEvent.setup()
    let resolveFreeze!: (value: { assessment_id: string; status: 'READY'; candidate_url: string }) => void
    vi.mocked(api.freezePlan).mockReturnValue(
      new Promise((resolve) => {
        resolveFreeze = resolve
      }),
    )
    renderPlan()
    const freeze = await screen.findByRole('button', { name: '校验并冻结计划' })

    await user.click(freeze)
    expect(freeze).toBeDisabled()
    await user.click(freeze)
    expect(api.freezePlan).toHaveBeenCalledTimes(1)

    resolveFreeze({
      assessment_id: 'ast_001',
      status: 'READY',
      candidate_url: '/interviews/candidate_once',
    })
    await waitFor(() => expect(screen.getByLabelText('候选人链接')).toHaveValue('/interviews/candidate_once'))
  })

  test('announces loading and keeps the plan error accessible', async () => {
    vi.mocked(api.getPlan).mockRejectedValue(new Error('计划暂时不可用'))
    render(
      <MemoryRouter initialEntries={['/assessments/ast_001/plan']}>
        <Routes>
          <Route path="/assessments/:assessmentId/plan" element={<PlanReviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByRole('status')).toHaveTextContent('正在读取计划审核')
    expect(await screen.findByRole('alert')).toHaveTextContent('计划暂时不可用')
  })

  test('does not render concrete question text from plan data', async () => {
    const payload = planPayload()
    payload.preview_plan = {
      ...payload.preview_plan,
      questions: ['你如何设计 StateGraph？'],
    }
    renderPlan(payload)

    await screen.findByText('岗位核心 · 不可删除')
    expect(screen.queryByText('你如何设计 StateGraph？')).not.toBeInTheDocument()
  })
})
