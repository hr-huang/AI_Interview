import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { api } from '../../api/client'
import { ModelSettings } from './ModelSettings'

vi.mock('../../api/client', () => ({
  api: {
    createModelSession: vi.fn(),
  },
}))

describe('ModelSettings', () => {
  test('tests structured-output compatibility and returns only the session handle', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    vi.mocked(api.createModelSession).mockResolvedValue({
      model_session_id: 'ms_test',
      provider: 'qwen',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      model: 'qwen3.8-max',
    })

    render(<ModelSettings value={null} onChange={onChange} />)
    await user.click(screen.getByRole('button', { name: '模型设置' }))
    await user.type(screen.getByLabelText('API Key'), 'sk-secret')
    await user.click(screen.getByRole('button', { name: '测试连接并使用' }))

    await waitFor(() => expect(api.createModelSession).toHaveBeenCalledTimes(1))
    expect(api.createModelSession).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: 'qwen',
        api_key: 'sk-secret',
        model: 'qwen3.8-max',
      }),
    )
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ model_session_id: 'ms_test' }),
    )
    expect(screen.queryByDisplayValue('sk-secret')).not.toBeInTheDocument()
  })
})
