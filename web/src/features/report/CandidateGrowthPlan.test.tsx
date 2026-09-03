import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import type { ReinterviewFocusView } from '../../api/types'
import { CandidateGrowthPlan } from './CandidateGrowthPlan'


const focus: ReinterviewFocusView = {
  priority: 1,
  dimension_name: 'Context、RAG、Memory与工具工程',
  reason: '当前回答说明了删除流程，但版本与引用链仍缺少可核验结果。',
  question: '请设计一次知识版本更新实验，并说明如何确认回答只引用当前有效版本。',
  follow_ups: ['如何证明旧版本已经不会进入最终回答？'],
  positive_signals: ['有版本过滤和来源追溯。'],
  risk_signals: ['只删除向量但没有验证最终回答。'],
  pass_criteria: ['能展示版本切换前后结果，并保留来源引用。'],
  suggested_minutes: 10,
}


describe('CandidateGrowthPlan', () => {
  test('turns an evidence gap into a concrete practice task and acceptance criteria', () => {
    render(<CandidateGrowthPlan items={[focus]} />)

    expect(screen.getByRole('heading', { name: '候选人成长建议' })).toBeVisible()
    expect(screen.getByText('当前证据缺口')).toBeVisible()
    expect(screen.getByText(focus.reason)).toBeVisible()
    expect(screen.getByText(/用一个真实项目、课程项目或可复现实验完成这项任务/)).toBeVisible()
    expect(screen.getByText(focus.question, { exact: false })).toBeVisible()
    expect(screen.getByText('复盘重点')).toBeVisible()
    expect(screen.getByText(focus.follow_ups[0])).toBeVisible()
    expect(screen.getByText('验收标准')).toBeVisible()
    expect(screen.getByText(focus.pass_criteria[0])).toBeVisible()
    expect(screen.getByText(/不修改评分/)).toBeVisible()
  })

  test('does not turn missing evidence into a negative ability conclusion', () => {
    render(<CandidateGrowthPlan items={[]} />)

    expect(screen.getByRole('heading', { name: '候选人成长建议' })).toBeVisible()
    expect(screen.getByText('当前没有足够证据生成针对性的成长任务；未验证不等于能力不足。')).toBeVisible()
  })
})
