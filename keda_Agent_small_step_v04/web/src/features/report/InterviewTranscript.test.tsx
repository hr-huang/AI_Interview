import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import type { InterviewPathView, InterviewTranscriptTurnView } from '../../api/types'
import { InterviewTranscript } from './InterviewTranscript'

const turns: InterviewTranscriptTurnView[] = [
  {
    turn_id: 'turn_002',
    sequence_number: 7,
    question: '请说明你如何验证边界？',
    answer: '我会先定义失败条件，再补充一个可复现的验证场景。',
    question_mode: 'follow_up',
    requirement_label: '验证边界条件',
    asked_at: '2026-08-24T10:02:00Z',
    answered_at: '2026-08-24T10:03:00Z',
    evidence_status: 'none',
    evidence_cta: '查看本轮依据',
  },
  {
    turn_id: 'turn_001',
    sequence_number: 3,
    question: '请介绍一个你负责的项目？',
    answer: '我负责把状态流转拆成可复核的节点，并记录每次变更。',
    question_mode: 'scenario',
    requirement_label: '项目落地经验',
    asked_at: '2026-08-24T10:00:00Z',
    answered_at: '2026-08-24T10:01:00Z',
    evidence_status: 'supporting',
    evidence_cta: '查看本轮依据',
  },
]

const path: InterviewPathView[] = [
  { turn_id: 'turn_001', question_mode: 'scenario', outcome: 'supporting' },
  { turn_id: 'turn_002', question_mode: 'follow_up', outcome: 'unverified' },
]

describe('InterviewTranscript', () => {
  test('expands complete answers once and links a transcript turn to its evidence drawer', async () => {
    const user = userEvent.setup()
    const onTurnSelect = vi.fn()

    render(<InterviewTranscript turns={turns} path={path} onTurnSelect={onTurnSelect} />)

    expect(screen.getByText('展开查看面试过程回顾')).toBeVisible()
    expect(screen.queryByText('我负责把状态流转拆成可复核的节点，并记录每次变更。')).not.toBeVisible()

    await user.click(screen.getByText('展开查看面试过程回顾'))

    expect(screen.getByText('问题、回答与追问依据')).toBeVisible()
    expect(screen.getByText('2 轮记录')).toBeVisible()
    expect(screen.getByText('我负责把状态流转拆成可复核的节点，并记录每次变更。')).toBeVisible()
    expect(screen.getByText('我会先定义失败条件，再补充一个可复现的验证场景。')).toBeVisible()
    expect(screen.getByText('未形成评分证据')).toBeVisible()
    expect(screen.getByText('形成支持证据')).toBeVisible()
    expect(screen.getByText('仍待核验')).toBeVisible()

    const rows = screen.getByRole('heading', { name: '问题、回答与追问依据' })
      .closest('section')
      ?.querySelectorAll('.interview-transcript-list > li')
    expect(rows).not.toBeNull()
    expect(rows).toHaveLength(2)
    expect(rows?.[0]).toHaveTextContent('项目落地经验')
    expect(rows?.[1]).toHaveTextContent('验证边界条件')
    expect(within(rows?.[0] as HTMLElement).getByText('03')).toBeVisible()
    expect(within(rows?.[1] as HTMLElement).getByText('07')).toBeVisible()

    await user.click(within(rows?.[0] as HTMLElement).getByRole('button', { name: /查看本轮依据/ }))

    expect(onTurnSelect).toHaveBeenCalledWith('turn_001')
    expect(screen.queryByText(/req_|ev_/)).not.toBeInTheDocument()
  })

  test('repeats scroll and focus when the same turn receives a new focus request', () => {
    const originalRequestAnimationFrame = window.requestAnimationFrame
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView
    const requestAnimationFrame = vi.fn((callback: FrameRequestCallback) => {
      callback(0)
      return 0
    })
    const scrollIntoView = vi.fn()
    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      writable: true,
      value: requestAnimationFrame,
    })
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      writable: true,
      value: scrollIntoView,
    })

    try {
      const { rerender } = render(
        <InterviewTranscript
          turns={turns}
          focusRequest={{ turnId: 'turn_001', requestId: 1 }}
        />,
      )

      expect(scrollIntoView).toHaveBeenCalledTimes(1)
      rerender(
        <InterviewTranscript
          turns={turns}
          focusRequest={{ turnId: 'turn_001', requestId: 2 }}
        />,
      )
      expect(scrollIntoView).toHaveBeenCalledTimes(2)
      expect(requestAnimationFrame).toHaveBeenCalledTimes(2)
    } finally {
      Object.defineProperty(window, 'requestAnimationFrame', {
        configurable: true,
        writable: true,
        value: originalRequestAnimationFrame,
      })
      Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        writable: true,
        value: originalScrollIntoView,
      })
    }
  })
})
