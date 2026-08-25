import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import type { InterviewTranscriptTurnView } from '../../api/types'
import { InterviewTranscript } from './InterviewTranscript'

const turns: InterviewTranscriptTurnView[] = [
  {
    turn_id: 'turn_002',
    sequence_number: 7,
    question: '请说明你如何验证边界？',
    answer: '我会先定义失败条件，再补充一个可复现的验证场景。',
    question_mode: 'follow_up',
    requirement_id: 'req_002',
    requirement_label: '验证边界条件',
    asked_at: '2026-08-24T10:02:00Z',
    answered_at: '2026-08-24T10:03:00Z',
    evidence_ids: [],
    evidence_status: 'none',
  },
  {
    turn_id: 'turn_001',
    sequence_number: 3,
    question: '请介绍一个你负责的项目？',
    answer: '我负责把状态流转拆成可复核的节点，并记录每次变更。',
    question_mode: 'scenario',
    requirement_id: 'req_001',
    requirement_label: '项目落地经验',
    asked_at: '2026-08-24T10:00:00Z',
    answered_at: '2026-08-24T10:01:00Z',
    evidence_ids: ['ev_001'],
    evidence_status: 'supporting',
  },
]

describe('InterviewTranscript', () => {
  test('expands the complete answers and opens a real evidence source', async () => {
    const user = userEvent.setup()
    const onEvidenceSelect = vi.fn()

    render(<InterviewTranscript turns={turns} onEvidenceSelect={onEvidenceSelect} />)

    expect(screen.getByText('展开查看完整面试记录')).toBeVisible()
    expect(screen.queryByText('我负责把状态流转拆成可复核的节点，并记录每次变更。')).not.toBeVisible()

    await user.click(screen.getByText('展开查看完整面试记录'))

    expect(screen.getByText('候选人的完整回答')).toBeVisible()
    expect(screen.getByText('2 轮记录')).toBeVisible()
    expect(screen.getByText('我负责把状态流转拆成可复核的节点，并记录每次变更。')).toBeVisible()
    expect(screen.getByText('我会先定义失败条件，再补充一个可复现的验证场景。')).toBeVisible()
    expect(screen.getByText('未形成评分证据')).toBeVisible()

    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('项目落地经验')
    expect(rows[1]).toHaveTextContent('验证边界条件')
    expect(within(rows[0]).getByText('03')).toBeVisible()
    expect(within(rows[1]).getByText('07')).toBeVisible()

    await user.click(screen.getByRole('button', { name: /查看证据 ev_001/ }))

    expect(onEvidenceSelect).toHaveBeenCalledWith('ev_001')
  })
})
