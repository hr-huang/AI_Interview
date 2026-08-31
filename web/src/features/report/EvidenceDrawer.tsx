import { useEffect, useRef } from 'react'
import type { ReasonView } from '../../api/types'

export type EvidenceDrawerGroup = {
  dimensionName: string
  reasons: ReasonView[]
}

type EvidenceDrawerProps = {
  groups: EvidenceDrawerGroup[] | null
  onClose: () => void
  onTurnSelect: (turnId: string) => void
}

function reasonLabel(reasonType: string): string {
  const labels: Record<string, string> = {
    strength: '支持信号',
    risk: '限制信号',
    limiting: '限制信号',
    error: '关键限制',
    critical_error: '关键限制',
    unverified: '待核验信号',
  }
  return labels[reasonType] ?? '决策依据'
}

function interpretationLabel(reasonType: string): string {
  if (reasonType === 'risk' || reasonType === 'limiting' || reasonType === 'error' || reasonType === 'critical_error') {
    return '为什么形成限制'
  }
  if (reasonType === 'unverified') return '为什么仍待核验'
  if (reasonType === 'strength') return '为什么支持该判断'
  return '相关判断依据'
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true')
}

function groupKey(group: EvidenceDrawerGroup): string {
  return `${group.dimensionName}-${group.reasons.map((reason) => `${reason.reason_type}-${reason.text}`).join('|')}`
}

export function EvidenceDrawer({ groups, onClose, onTurnSelect }: EvidenceDrawerProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const wasOpenRef = useRef(false)
  const activeElementBeforeOpenRef = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const safeGroups = groups ?? []
  const isOpen = safeGroups.length > 0

  useEffect(() => {
    if (!isOpen) {
      if (wasOpenRef.current) {
        wasOpenRef.current = false
        activeElementBeforeOpenRef.current?.focus()
        activeElementBeforeOpenRef.current = null
      }
      return
    }

    if (!wasOpenRef.current) {
      const activeElement = document.activeElement
      activeElementBeforeOpenRef.current = activeElement instanceof HTMLElement ? activeElement : null
      wasOpenRef.current = true
    }

    closeButtonRef.current?.focus()

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const drawer = drawerRef.current
      if (!drawer) return
      const focusable = focusableElements(drawer)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const activeElement = document.activeElement
      if (event.shiftKey && activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (wasOpenRef.current) {
        wasOpenRef.current = false
        activeElementBeforeOpenRef.current?.focus()
        activeElementBeforeOpenRef.current = null
      }
    }
  }, [isOpen])

  if (!isOpen) return null

  const excerptCount = safeGroups.reduce(
    (count, group) => count + group.reasons.reduce((reasonCount, reason) => reasonCount + (reason.sources ?? []).length, 0),
    0,
  )
  const firstReason = safeGroups[0].reasons[0]

  return (
    <div className="evidence-drawer-layer">
      <button
        className="evidence-drawer-backdrop"
        type="button"
        tabIndex={-1}
        aria-label="关闭依据详情"
        onClick={onClose}
      />
      <aside
        ref={drawerRef}
        className="evidence-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-drawer-title"
      >
        <div className="evidence-drawer-header">
          <div>
            <p className="eyebrow">企业决策依据</p>
            <h2 id="evidence-drawer-title">{safeGroups.length === 1 && firstReason ? reasonLabel(firstReason.reason_type) : '本轮关联能力依据'}</h2>
          </div>
          <button ref={closeButtonRef} className="evidence-drawer-close" type="button" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="evidence-source-list">
          <div className="evidence-section-heading">
            <h3>关键回答摘录</h3>
            <span>{excerptCount} 条依据</span>
          </div>
          {safeGroups.map((group) => (
            <section className="evidence-drawer-group" key={groupKey(group)}>
              <div className="evidence-group-heading">
                <h3>{group.dimensionName}</h3>
                <span>{group.reasons.length} 个判断</span>
              </div>
              {group.reasons.map((reason) => {
                const sources = reason.sources ?? []
                return (
                  <div className="evidence-drawer-reason-group" key={`${reason.reason_type}-${reason.text}`}>
                    <div className="evidence-reason-label">{reasonLabel(reason.reason_type)}</div>
                    <p className="evidence-drawer-reason">{reason.text}</p>
                    {sources.length > 0 ? sources.map((source) => (
                      <article className="evidence-source evidence-excerpt-card" key={`${source.turn_id}-${source.quote}`}>
                        <div className="evidence-excerpt-quote">
                          <span className="evidence-excerpt-label">候选人原话摘录</span>
                          <blockquote>{source.quote}</blockquote>
                        </div>
                        <dl>
                          <div>
                            <dt>该证据的结论</dt>
                            <dd>{source.conclusion || reason.text || '当前判断依据待核验。'}</dd>
                          </div>
                          <div>
                            <dt>{interpretationLabel(reason.reason_type)}</dt>
                            <dd>{source.interpretation || '当前没有更多解释，需结合完整面试记录复核。'}</dd>
                          </div>
                          <div>
                            <dt>尚未证明</dt>
                            <dd>{source.limitation || '该片段只覆盖当前问答场景，仍需独立复核。'}</dd>
                          </div>
                        </dl>
                        <button
                          type="button"
                          className="evidence-link evidence-jump-link"
                          onClick={() => {
                            onTurnSelect(source.turn_id)
                            onClose()
                          }}
                        >
                          查看完整面试记录中的本轮 →
                        </button>
                      </article>
                    )) : (
                      <p className="report-empty">当前没有可公开的短摘录，保持为待核验状态。</p>
                    )}
                  </div>
                )
              })}
            </section>
          ))}
        </div>
      </aside>
    </div>
  )
}

export default EvidenceDrawer
