import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'
import { EvidenceDrawer, type EvidenceDrawerGroup } from './EvidenceDrawer'

const groups: EvidenceDrawerGroup[] = [{
  dimensionName: 'Agent 编排',
  reasons: [{
    reason_type: 'strength',
    text: '能够解释状态流转。',
    sources: [{
      turn_id: 'turn_001',
      conclusion: '回答支持当前能力判断。',
      quote: '节点只返回增量更新',
      interpretation: '回答包含可复核的状态边界。',
      limitation: '尚未证明更大规模场景下的迁移能力。',
    }],
  }],
}]

function DrawerHarness() {
  const [open, setOpen] = useState(true)
  return open ? (
    <EvidenceDrawer
      groups={groups}
      onClose={() => setOpen(false)}
      onTurnSelect={() => undefined}
    />
  ) : null
}

describe('EvidenceDrawer', () => {
  test('traps Tab focus and restores the opening trigger after Escape closes', async () => {
    const user = userEvent.setup()
    const trigger = document.createElement('button')
    trigger.type = 'button'
    trigger.textContent = '打开依据'
    document.body.appendChild(trigger)
    trigger.focus()

    render(<DrawerHarness />)
    const closeButton = screen.getByRole('button', { name: '关闭' })
    const jumpButton = screen.getByRole('button', { name: /查看完整面试记录中的本轮/ })
    expect(closeButton).toHaveFocus()

    await user.tab()
    expect(jumpButton).toHaveFocus()
    await user.tab()
    expect(closeButton).toHaveFocus()
    await user.tab({ shift: true })
    expect(jumpButton).toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
    trigger.remove()
  })
})
