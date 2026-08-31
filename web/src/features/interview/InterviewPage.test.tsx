import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, test, vi } from 'vitest'
import { api } from '../../api/client'
import { InterviewPage } from './InterviewPage'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      getInterview: vi.fn(),
      startInterview: vi.fn(),
      submitAnswer: vi.fn(),
    },
  }
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/interviews/token-1']}>
      <Routes>
        <Route path="/interviews/:candidateToken" element={<InterviewPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('InterviewPage', () => {
  test('loads the current question and submits a candidate answer', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getInterview).mockResolvedValue({
      state: 'waiting_for_answer',
      target_role: 'AI Agent应用工程师（校招/初级）',
      elapsed_seconds: 60,
      turn: {
        id: 'turn_001',
        sequence_number: 1,
        question: '你会如何设计 Agent 的任务编排？',
        answer: null,
      },
    })
    vi.mocked(api.submitAnswer).mockResolvedValue({
      state: 'waiting_for_answer',
      target_role: 'AI Agent应用工程师（校招/初级）',
      elapsed_seconds: 120,
      turn: {
        id: 'turn_002',
        sequence_number: 2,
        question: '失败恢复怎么处理？',
        answer: null,
      },
    })

    renderPage()
    expect(await screen.findByText('你会如何设计 Agent 的任务编排？')).toBeVisible()
    await user.type(screen.getByLabelText('你的回答'), '先做意图路由，再进入不同工作流。')
    await user.click(screen.getByRole('button', { name: '提交回答' }))

    await waitFor(() => expect(api.submitAnswer).toHaveBeenCalledTimes(1))
    expect(api.submitAnswer).toHaveBeenCalledWith(
      'token-1',
      expect.objectContaining({
        turn_id: 'turn_001',
        answer: '先做意图路由，再进入不同工作流。',
        idempotency_key: expect.any(String),
      }),
    )
    expect(await screen.findByText('失败恢复怎么处理？')).toBeVisible()
  })
})
