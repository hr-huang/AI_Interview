import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { api } from '../../api/client'
import type { InterviewTranscriptTurnView, ReportViewModel } from '../../api/types'
import { DemoReportPage } from './DemoReportPage'
import { ReportPage } from './ReportPage'

vi.mock('../../api/client', () => ({
  api: {
    getReport: vi.fn(),
    getDemoAssessment: vi.fn(),
  },
}))

const report = (overrides: Partial<ReportViewModel> = {}): ReportViewModel => ({
  demo: false,
  target_role: 'AI Agent / AI应用工程师',
  role_profile_version: '2026-H2',
  scoring_engine_version: 'v1',
  job_match: {
    raw_score: 86,
    published: true,
    fit_level: 'strong',
    coverage: 0.84,
    confidence: 'high',
    limiting_reasons: [],
  },
  radar_dimensions: [
    {
      dimension_id: 'role_dim_01',
      name: 'Agent 编排',
      score: 86,
      level: 'L3',
      coverage: 0.8,
      confidence: 'high',
      reasons: [
        {
          reason_type: 'strength',
          text: '能够解释状态流转。',
          evidence_ids: ['E003'],
          rubric_signal_ids: ['rubric_01'],
          sources: [
            {
              evidence_id: 'E003',
              turn_id: 'turn_003',
              question: '你如何设计 State？',
              answer: '节点只返回增量更新，状态机负责合并。',
              observation: '回答包含可复核的状态边界。',
              source_excerpt: '节点只返回增量更新',
            },
          ],
        },
      ],
    },
    {
      dimension_id: 'role_dim_02',
      name: '待核验能力',
      score: null,
      level: 'UNVERIFIED',
      coverage: 0,
      confidence: 'low',
      reasons: [
        {
          reason_type: 'unverified',
          text: '当前没有足够证据。',
          evidence_ids: [],
          rubric_signal_ids: [],
          sources: [],
        },
      ],
    },
  ],
  narrative: {
    executive_summary: '当前证据覆盖有限。',
    strengths: [{ text: '状态边界清楚。', dimension_ids: ['role_dim_01'], evidence_ids: ['E003'] }],
    risks: [],
    unverified_areas: [{ text: '业务场景待核验。', dimension_ids: ['role_dim_02'], evidence_ids: [] }],
    fit_contexts: [],
    development_actions: [
      {
        dimension_id: 'role_dim_02',
        current_gap: '缺少场景验证。',
        actions: ['补充一个独立场景。'],
        acceptance_criteria: ['说明边界、验证方式与结果。'],
      },
    ],
  },
  interview_path: [
    {
      turn_id: 'turn_003',
      question_mode: 'scenario',
      requirement_id: 'req_01',
      outcome: 'supporting',
      evidence_ids: ['E003'],
    },
  ],
  interview_transcript: [],
  claim_verifications: [],
  assessment_limitations: ['仅基于本次面试证据。'],
  ...overrides,
})

function renderEnterprisePage() {
  return render(
    <MemoryRouter initialEntries={['/assessments/ast_001/report']}>
      <Routes>
        <Route path="/assessments/:assessmentId/report" element={<ReportPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('ReportPage', () => {
  test('loads a real assessment report and opens original question and answer for a score reason', async () => {
    vi.mocked(api.getReport).mockResolvedValue(report())
    const user = userEvent.setup()
    renderEnterprisePage()

    expect(await screen.findByRole('heading', { name: '岗位胜任力报告' })).toBeVisible()
    expect(api.getReport).toHaveBeenCalledWith('ast_001')
    expect(screen.getByText('Agent 编排')).toBeVisible()
    expect(screen.getByText('当前证据覆盖有限。')).toBeVisible()
    expect(screen.getByText('展开查看动态追问路径')).toBeVisible()

    await user.click(screen.getByRole('button', { name: /Agent 编排/ }))
    expect(screen.getByRole('dialog')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /^关闭$/ }))
    await user.click(screen.getByText('展开查看评分原因与证据'))
    await user.click(screen.getByRole('button', { name: /查看证据 E003/ }))
    expect(screen.getByRole('dialog')).toBeVisible()
    expect(screen.getByText(/你如何设计 State/)).toBeVisible()
    expect(screen.getByText(/节点只返回增量更新/)).toBeVisible()
  })

  test('renders ordered transcript turns and does not open a drawer for unlinked evidence', async () => {
    vi.mocked(api.getReport).mockResolvedValue(report({
      interview_transcript: [
        {
          turn_id: 'turn_transcript_002',
          sequence_number: 2,
          question: '第二个问题：请说明边界？',
          answer: null,
          question_mode: 'follow_up',
          requirement_id: 'req_02',
          requirement_label: '边界验证',
          asked_at: '2026-08-24T10:02:00Z',
          answered_at: null,
          evidence_ids: [],
          evidence_status: 'none',
        },
        {
          turn_id: 'turn_transcript_001',
          sequence_number: 1,
          question: '第一个问题：请介绍项目？',
          answer: '第一个回答。',
          question_mode: 'project_deep_dive',
          requirement_id: 'req_01',
          requirement_label: '项目经验',
          asked_at: '2026-08-24T10:00:00Z',
          answered_at: '2026-08-24T10:01:00Z',
          evidence_ids: ['E003'],
          evidence_status: 'supporting',
        },
        {
          turn_id: 'turn_transcript_003',
          sequence_number: 3,
          question: '第三个问题：请说明迁移？',
          answer: '第三个回答。',
          question_mode: 'scenario',
          requirement_id: 'req_03',
          requirement_label: '迁移场景',
          asked_at: '2026-08-24T10:04:00Z',
          answered_at: '2026-08-24T10:05:00Z',
          evidence_ids: ['E_UNLINKED'],
          evidence_status: 'supporting',
        },
      ] satisfies InterviewTranscriptTurnView[],
    }))
    const user = userEvent.setup()
    renderEnterprisePage()

    expect(await screen.findByRole('heading', { name: '岗位胜任力报告' })).toBeVisible()
    await user.click(screen.getByText('展开查看完整面试记录'))

    const transcriptPanel = screen.getByRole('heading', { name: '候选人的完整回答' }).closest('section')
    expect(transcriptPanel).not.toBeNull()
    const rows = within(transcriptPanel as HTMLElement).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('第一个问题：请介绍项目？')
    expect(rows[1]).toHaveTextContent('第二个问题：请说明边界？')
    expect(rows[1]).toHaveTextContent('未提交回答')
    expect(rows[2]).toHaveTextContent('第三个问题：请说明迁移？')

    await user.click(screen.getByRole('button', { name: /查看证据 E_UNLINKED/ }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('shows loading and API errors as accessible page states', async () => {
    let rejectRequest!: (error: Error) => void
    vi.mocked(api.getReport).mockReturnValueOnce(new Promise((_resolve, reject) => {
      rejectRequest = reject
    }))
    renderEnterprisePage()
    expect(screen.getByRole('status')).toHaveTextContent('正在读取报告')

    rejectRequest(new Error('报告暂时不可用'))
    expect(await screen.findByRole('alert')).toHaveTextContent('报告暂时不可用')
  })
})

describe('DemoReportPage', () => {
  test('loads the read-only demo through the demo endpoint and keeps the persistent banner', async () => {
    vi.mocked(api.getDemoAssessment).mockResolvedValue(report({ demo: true }))
    render(
      <MemoryRouter initialEntries={['/demo/assessment']}>
        <Routes>
          <Route path="/demo/assessment" element={<DemoReportPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('演示示例 · 只读')).toBeVisible()
    expect(api.getDemoAssessment).toHaveBeenCalledTimes(1)
    expect(api.getReport).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByRole('heading', { name: '岗位胜任力报告' })).toBeVisible())
  })
})
