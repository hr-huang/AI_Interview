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
  test('renders every public source field without repeating the full answer or exposing ids', () => {
    render(<EvidenceDrawer
      groups={[{
        dimensionName: 'Agent 编排',
        reasons: [{
          reason_type: 'strength',
          text: '该能力得到支持。',
          sources: [{
            turn_id: 'turn_public_001',
            conclusion: '该回答证明了状态边界意识。',
            quote: '关键回答片段',
            interpretation: '片段说明候选人知道状态由谁合并。',
            limitation: '尚未证明异常恢复能力。',
          }],
        }],
      }]}
      onClose={() => undefined}
      onTurnSelect={() => undefined}
    />)

    expect(screen.getByText('该回答证明了状态边界意识。')).toBeVisible()
    expect(screen.getByText('关键回答片段')).toBeVisible()
    expect(screen.getByText('片段说明候选人知道状态由谁合并。')).toBeVisible()
    expect(screen.getByText('尚未证明异常恢复能力。')).toBeVisible()
    expect(screen.queryByText('turn_public_001')).not.toBeInTheDocument()
    expect(screen.queryByText('候选人完整回答')).not.toBeInTheDocument()
  })

  test('translates critical and unknown reason types into safe enterprise labels', () => {
    render(<EvidenceDrawer
      groups={[{
        dimensionName: '可靠性',
        reasons: [
          { reason_type: 'critical_error', text: '需要重点核验。', sources: [] },
          { reason_type: 'internal_reason_kind', text: '仍需核验。', sources: [] },
        ],
      }]}
      onClose={() => undefined}
      onTurnSelect={() => undefined}
    />)

    expect(screen.getAllByText('关键限制').length).toBeGreaterThan(0)
    expect(screen.getAllByText('决策依据').length).toBeGreaterThan(0)
    expect(screen.queryByText('critical_error')).not.toBeInTheDocument()
    expect(screen.queryByText('internal_reason_kind')).not.toBeInTheDocument()
  })

  test('uses a neutral Chinese explanation label for unknown reason types', () => {
    render(<EvidenceDrawer
      groups={[{
        dimensionName: '可靠性',
        reasons: [{
          reason_type: 'internal_reason_kind',
          text: '仍需核验。',
          sources: [{
            turn_id: 'turn_unknown_001',
            conclusion: '当前判断需要保留。',
            quote: '尚未覆盖的片段',
            interpretation: '该片段尚不足以支持明确结论。',
            limitation: '仍需独立复核。',
          }],
        }],
      }]}
      onClose={() => undefined}
      onTurnSelect={() => undefined}
    />)

    expect(screen.getByText('相关判断依据')).toBeVisible()
    expect(screen.queryByText('为什么支持该判断')).not.toBeInTheDocument()
    expect(screen.queryByText('internal_reason_kind')).not.toBeInTheDocument()
  })

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
