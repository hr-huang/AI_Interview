import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { api } from '../../api/client'
import { NewAssessmentPage } from './NewAssessmentPage'

vi.mock('../../api/client', () => ({
  api: {
    createAssessment: vi.fn(),
  },
}))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/assessments/new']}>
      <NewAssessmentPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('NewAssessmentPage', () => {
  test('keeps demo as a text link and submits real materials', async () => {
    const user = userEvent.setup()
    vi.mocked(api.createAssessment).mockResolvedValue({
      assessment_id: 'ast_001',
      status: 'ANALYZING',
    })
    renderPage()

    expect(screen.getByRole('button', { name: '创建评估' })).toBeVisible()
    expect(screen.getByRole('combobox', { name: '目标岗位' })).toHaveValue('AI Agent / AI应用工程师')
    expect(screen.getByRole('option')).toHaveTextContent('AI Agent / AI应用工程师')
    expect(screen.getByRole('link', { name: /查看已完成的演示评估/ })).toHaveAttribute(
      'href',
      '/demo/assessment',
    )

    await user.type(screen.getByLabelText('岗位描述 JD'), '负责 Agent Workflow')
    await user.type(screen.getByLabelText('粘贴简历文本'), '候选人有 LangGraph 项目')
    await user.click(screen.getByRole('button', { name: '创建评估' }))

    await waitFor(() => expect(api.createAssessment).toHaveBeenCalledTimes(1))
    const form = vi.mocked(api.createAssessment).mock.calls[0][0]
    expect(form).toBeInstanceOf(FormData)
    expect(form.get('target_role')).toBe('AI Agent / AI应用工程师')
    expect(form.get('jd_text')).toBe('负责 Agent Workflow')
    expect(form.get('resume_text')).toBe('候选人有 LangGraph 项目')
    expect(form.get('resume_file')).toBeNull()
    expect(form.get('idempotency_key')).toEqual(expect.any(String))
    expect(form.get('interview_duration_minutes')).toBe('45')
  })

  test('inserts an editable versioned JD sample with a secondary action', async () => {
    const user = userEvent.setup()
    renderPage()

    const sampleAction = screen.getByRole('button', { name: '填入 2026-H2 示例 JD' })
    expect(sampleAction).not.toHaveClass('primary-action')
    await user.click(sampleAction)

    const jd = screen.getByLabelText('岗位描述 JD')
    expect((jd as HTMLTextAreaElement).value.length).toBeGreaterThan(0)
    expect((jd as HTMLTextAreaElement).value).toContain('AI Agent')
    expect(jd).toBeEnabled()
    await user.clear(jd)
    await user.type(jd, '改写后的 JD')
    expect(jd).toHaveValue('改写后的 JD')
  })

  test('validates required content and rejects files over 5 MiB before network', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '创建评估' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('请填写岗位描述 JD')
    expect(api.createAssessment).not.toHaveBeenCalled()

    await user.click(screen.getByRole('radio', { name: '上传简历文件' }))
    const oversized = new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'resume.pdf', {
      type: 'application/pdf',
    })
    await user.upload(screen.getByLabelText('简历文件'), oversized)
    expect(await screen.findByRole('alert')).toHaveTextContent('5 MiB')
    expect(api.createAssessment).not.toHaveBeenCalled()
  })

  test('reuses the idempotency key when the same payload is retried after a failed request', async () => {
    const user = userEvent.setup()
    vi.mocked(api.createAssessment).mockRejectedValue(new Error('网络连接中断'))
    renderPage()

    await user.type(screen.getByLabelText('岗位描述 JD'), '负责 Agent Workflow')
    await user.type(screen.getByLabelText('粘贴简历文本'), '候选人有 LangGraph 项目')
    await user.click(screen.getByRole('button', { name: '创建评估' }))
    await screen.findByText('网络连接中断')

    await user.click(screen.getByRole('button', { name: '创建评估' }))
    await waitFor(() => expect(api.createAssessment).toHaveBeenCalledTimes(2))

    const [firstForm, secondForm] = vi.mocked(api.createAssessment).mock.calls
    expect(firstForm[0].get('idempotency_key')).toBe(secondForm[0].get('idempotency_key'))
  })

  test('creates a new idempotency key after a substantive JD change', async () => {
    const user = userEvent.setup()
    vi.mocked(api.createAssessment).mockRejectedValue(new Error('网络连接中断'))
    renderPage()

    const jd = screen.getByLabelText('岗位描述 JD')
    await user.type(jd, '负责 Agent Workflow')
    await user.type(screen.getByLabelText('粘贴简历文本'), '候选人有 LangGraph 项目')
    await user.click(screen.getByRole('button', { name: '创建评估' }))
    await screen.findByText('网络连接中断')
    const firstKey = vi.mocked(api.createAssessment).mock.calls[0][0].get('idempotency_key')

    await user.type(jd, '，并负责线上评测')
    await user.click(screen.getByRole('button', { name: '创建评估' }))
    await waitFor(() => expect(api.createAssessment).toHaveBeenCalledTimes(2))
    const secondKey = vi.mocked(api.createAssessment).mock.calls[1][0].get('idempotency_key')

    expect(secondKey).not.toBe(firstKey)
  })
})
