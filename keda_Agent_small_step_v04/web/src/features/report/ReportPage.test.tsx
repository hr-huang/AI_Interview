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
  candidate_overview: {
    candidate_name: '候选人 A',
    target_role: 'AI Agent / AI应用工程师',
    education_summary: '计算机相关专业本科',
    experience_summary: '具备 Agent 工作流与生产系统实践。',
    jd_focus: ['Agent 编排', '生产稳定性'],
    interview_rounds: 1,
    generated_at: '2026-08-25T10:00:00Z',
  },
  enterprise_assessment: {
    decision: 'CONDITIONAL_PROCEED',
    decision_label: '有条件进入结构化复试',
    provisional_score: 86,
    confidence: 'high',
    conditions: ['复核生产场景下的故障边界。'],
    decision_reasons: ['核心能力已形成初步支持证据。'],
    overall_assessment: '候选人能够清晰拆分 Agent 状态与人工介入边界，适合进入下一轮验证。',
    strengths: [{
      title: '状态边界清楚',
      text: '能够说明状态流转与可复核节点。',
      dimension_names: ['Agent 编排'],
      confidence: 'high',
    }],
    risks: [],
    unknowns: [{
      title: '生产迁移待确认',
      text: '尚未证明大规模场景下的迁移能力。',
      dimension_names: ['生产稳定性'],
      confidence: 'low',
    }],
    reinterview_plan: [{
      priority: 1,
      dimension_name: '生产稳定性',
      reason: '需要确认复杂故障下的排查与恢复边界。',
      question: '请设计一次生产故障演练，并说明如何判断恢复完成。',
      follow_ups: ['如果指标恢复但用户仍受影响，你会如何继续定位？'],
      positive_signals: ['能给出可观测指标与回滚边界。'],
      risk_signals: ['只描述工具，不说明失败条件。'],
      pass_criteria: ['明确输入、输出、失败条件与验证方式。'],
      suggested_minutes: 15,
    }],
    evidence_excerpts: [{
      turn_id: 'turn_003',
      conclusion: '回答支持当前能力判断。',
      quote: '节点只返回增量更新',
      interpretation: '能够说明状态更新与合并职责。',
      limitation: '尚未证明更大规模场景下的迁移能力。',
    }],
  },
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
      name: 'Agent 编排',
      score: 86,
      level: 'L3',
      coverage: 0.8,
      confidence: 'high',
      reasons: [
        {
          reason_type: 'strength',
          text: '能够解释状态流转。',
          sources: [
            {
              turn_id: 'turn_003',
              conclusion: '回答支持当前能力判断。',
              quote: '节点只返回增量更新',
              interpretation: '回答包含可复核的状态边界。',
              limitation: '尚未证明更大规模场景下的迁移能力。',
            },
          ],
        },
      ],
    },
    {
      name: '待核验能力',
      score: null,
      level: 'UNVERIFIED',
      coverage: 0,
      confidence: 'low',
      reasons: [
        {
          reason_type: 'unverified',
          text: '当前没有足够证据。',
          sources: [],
        },
      ],
    },
  ],
  narrative: {
    executive_summary: '当前证据覆盖有限。',
    strengths: [{ text: '状态边界清楚。', dimension_names: ['Agent 编排'] }],
    risks: [],
    unverified_areas: [{ text: '业务场景待核验。', dimension_names: ['生产稳定性'] }],
    fit_contexts: [],
  },
  interview_path: [
    {
      turn_id: 'turn_003',
      question_mode: 'scenario',
      outcome: 'supporting',
    },
  ],
  interview_transcript: [{
    turn_id: 'turn_003',
    sequence_number: 3,
    question: '你如何设计 State？',
    answer: '节点只返回增量更新，状态机负责合并。',
    question_mode: 'scenario',
    requirement_label: 'Agent 编排',
    asked_at: '2026-08-25T10:02:00Z',
    answered_at: '2026-08-25T10:03:00Z',
    evidence_status: 'supporting',
    evidence_cta: '查看本轮依据',
  }],
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
  test('presents an enterprise decision and opens grounded excerpts from a radar dimension', async () => {
    vi.mocked(api.getReport).mockResolvedValue(report())
    const user = userEvent.setup()
    renderEnterprisePage()

    expect(await screen.findByRole('heading', { name: '岗位胜任力报告' })).toBeVisible()
    expect(api.getReport).toHaveBeenCalledWith('ast_001')
    expect(screen.getByText('有条件进入结构化复试')).toBeVisible()
    expect(screen.getByRole('heading', { name: '候选人总评' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '企业复试计划' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '能力雷达' })).toBeVisible()
    expect(screen.getByText('当前证据覆盖有限。')).toBeVisible()
    expect(screen.queryByText('展开查看为什么得到这个评价')).not.toBeInTheDocument()
    expect(screen.queryByText('候选人成长建议')).not.toBeInTheDocument()
    expect(screen.queryByText(/RubricMatch|Requirement|d03_min_02|ev_/)).not.toBeInTheDocument()

    const radarTable = screen.getByRole('table')
    await user.click(within(radarTable).getByRole('button', { name: /Agent 编排/ }))
    expect(screen.getByRole('dialog')).toBeVisible()
    expect(screen.getByText('关键回答摘录')).toBeVisible()
    expect(screen.getByText('为什么支持该判断')).toBeVisible()
    expect(screen.getByText('尚未证明')).toBeVisible()
    expect(screen.getByText('节点只返回增量更新')).toBeVisible()
    expect(within(screen.getByRole('dialog')).queryByText('节点只返回增量更新，状态机负责合并。')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /查看完整面试记录中的本轮/ })).toBeVisible()

    await user.click(screen.getByRole('button', { name: /查看完整面试记录中的本轮/ }))
    expect(screen.getByText('节点只返回增量更新，状态机负责合并。')).toBeVisible()
  })

  test('groups every dimension linked to a transcript turn in the shared evidence drawer', async () => {
    vi.mocked(api.getReport).mockResolvedValue(report({
      radar_dimensions: [
        report().radar_dimensions[0],
        {
          name: '生产稳定性',
          score: 72,
          level: 'L2',
          coverage: 0.7,
          confidence: 'medium',
          reasons: [{
            reason_type: 'risk',
            text: '生产故障恢复边界仍需验证。',
            sources: [{
              turn_id: 'turn_003',
              conclusion: '回答覆盖了局部恢复流程。',
              quote: '先隔离故障，再观察恢复指标',
              interpretation: '能够描述故障处理的先后顺序。',
              limitation: '尚未证明真实生产压力下的恢复能力。',
            }],
          }],
        },
      ],
    }))
    const user = userEvent.setup()
    renderEnterprisePage()

    expect(await screen.findByRole('heading', { name: '岗位胜任力报告' })).toBeVisible()
    await user.click(screen.getByText('展开查看面试过程回顾'))
    const transcript = screen.getByRole('heading', { name: '问题、回答与追问依据' }).closest('section')
    expect(transcript).not.toBeNull()
    const firstTurn = (transcript as HTMLElement).querySelector('.interview-transcript-list > li')
    expect(firstTurn).not.toBeNull()
    await user.click(within(firstTurn as HTMLElement).getByRole('button', { name: /查看本轮依据/ }))

    const drawer = screen.getByRole('dialog')
    expect(within(drawer).getByRole('heading', { name: 'Agent 编排' })).toBeVisible()
    expect(within(drawer).getByRole('heading', { name: '生产稳定性' })).toBeVisible()
    expect(within(drawer).getByText('能够解释状态流转。')).toBeVisible()
    expect(within(drawer).getByText('生产故障恢复边界仍需验证。')).toBeVisible()
    expect(within(drawer).getByText('节点只返回增量更新')).toBeVisible()
    expect(within(drawer).getByText('先隔离故障，再观察恢复指标')).toBeVisible()
  })

  test('renders usable empty states when optional report sections are missing', async () => {
    vi.mocked(api.getReport).mockResolvedValue(report({
      candidate_overview: undefined as unknown as ReportViewModel['candidate_overview'],
      enterprise_assessment: undefined as unknown as ReportViewModel['enterprise_assessment'],
      job_match: undefined as unknown as ReportViewModel['job_match'],
      narrative: undefined as unknown as ReportViewModel['narrative'],
      radar_dimensions: undefined as unknown as ReportViewModel['radar_dimensions'],
      interview_path: undefined as unknown as ReportViewModel['interview_path'],
      interview_transcript: undefined as unknown as ReportViewModel['interview_transcript'],
      assessment_limitations: undefined as unknown as ReportViewModel['assessment_limitations'],
    }))
    renderEnterprisePage()

    expect(await screen.findByRole('heading', { name: '岗位胜任力报告' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '候选人概览' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '候选人总评' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '企业复试计划' })).toBeVisible()
    expect(screen.getByText('服务端没有返回能力维度。')).toBeVisible()
    expect(screen.getByText('当前没有需要额外安排的结构化复试重点。')).toBeVisible()
    await userEvent.setup().click(screen.getByText('展开查看面试过程回顾'))
    expect(screen.getByText('当前没有返回面试记录。')).toBeVisible()
    await userEvent.setup().click(screen.getByText('展开查看报告说明'))
    expect(screen.getByText('服务端没有返回评估限制。')).toBeVisible()
  })

  test('renders ordered transcript turns without exposing internal identifiers', async () => {
    vi.mocked(api.getReport).mockResolvedValue(report({
      interview_transcript: [
        {
          turn_id: 'turn_transcript_002',
          sequence_number: 2,
          question: '第二个问题：请说明边界？',
          answer: null,
          question_mode: 'follow_up',
          requirement_label: '边界验证',
          asked_at: '2026-08-24T10:02:00Z',
          answered_at: null,
          evidence_status: 'none',
          evidence_cta: '查看本轮依据',
        },
        {
          turn_id: 'turn_transcript_001',
          sequence_number: 1,
          question: '第一个问题：请介绍项目？',
          answer: '第一个回答。',
          question_mode: 'project_deep_dive',
          requirement_label: '项目经验',
          asked_at: '2026-08-24T10:00:00Z',
          answered_at: '2026-08-24T10:01:00Z',
          evidence_status: 'supporting',
          evidence_cta: '查看本轮依据',
        },
        {
          turn_id: 'turn_transcript_003',
          sequence_number: 3,
          question: '第三个问题：请说明迁移？',
          answer: '第三个回答。',
          question_mode: 'scenario',
          requirement_label: '迁移场景',
          asked_at: '2026-08-24T10:04:00Z',
          answered_at: '2026-08-24T10:05:00Z',
          evidence_status: 'supporting',
          evidence_cta: '查看本轮依据',
        },
      ] satisfies InterviewTranscriptTurnView[],
    }))
    const user = userEvent.setup()
    renderEnterprisePage()

    expect(await screen.findByRole('heading', { name: '岗位胜任力报告' })).toBeVisible()
    await user.click(screen.getByText('展开查看面试过程回顾'))

    const transcriptPanel = screen.getByRole('heading', { name: '问题、回答与追问依据' }).closest('section')
    expect(transcriptPanel).not.toBeNull()
    const rows = (transcriptPanel as HTMLElement).querySelectorAll('.interview-transcript-list > li')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('第一个问题：请介绍项目？')
    expect(rows[1]).toHaveTextContent('第二个问题：请说明边界？')
    expect(rows[1]).toHaveTextContent('未提交回答')
    expect(rows[2]).toHaveTextContent('第三个问题：请说明迁移？')

    expect(screen.queryByText(/req_|ev_|E003/)).not.toBeInTheDocument()
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
