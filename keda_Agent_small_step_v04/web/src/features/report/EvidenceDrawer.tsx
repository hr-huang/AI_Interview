import { useEffect, useRef } from 'react'
import type { ReasonView } from '../../api/types'

type EvidenceDrawerProps = {
  reason: ReasonView | null
  onClose: () => void
  onTurnSelect: (turnId: string) => void
}

function reasonLabel(reasonType: string): string {
  const labels: Record<string, string> = {
    strength: '支持信号',
    risk: '限制信号',
    limiting: '限制信号',
    error: '关键限制',
    unverified: '待核验信号',
  }
  return labels[reasonType] ?? (reasonType || '决策信号')
}

function interpretationLabel(reasonType: string): string {
  if (reasonType === 'risk' || reasonType === 'limiting' || reasonType === 'error') {
    return '为什么形成限制'
  }
  if (reasonType === 'unverified') return '为什么仍待核验'
  return '为什么支持该判断'
}

export function EvidenceDrawer({ reason, onClose, onTurnSelect }: EvidenceDrawerProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!reason) return
    closeButtonRef.current?.focus()
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose, reason])

  if (!reason) return null

  return (
    <div className="evidence-drawer-layer">
      <button className="evidence-drawer-backdrop" type="button" aria-label="关闭依据详情" onClick={onClose} />
      <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title">
        <div className="evidence-drawer-header">
          <div>
            <p className="eyebrow">企业决策依据</p>
            <h2 id="evidence-drawer-title">{reasonLabel(reason.reason_type)}</h2>
          </div>
          <button ref={closeButtonRef} className="evidence-drawer-close" type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <p className="evidence-drawer-reason">{reason.text}</p>

        <div className="evidence-source-list">
          <div className="evidence-section-heading">
            <h3>关键回答摘录</h3>
            <span>{reason.sources.length} 条依据</span>
          </div>
          {reason.sources.length > 0 ? reason.sources.map((source, index) => (
            <article className="evidence-source evidence-excerpt-card" key={`${source.turn_id}-${index}`}>
              <div className="evidence-excerpt-quote">
                <span className="evidence-excerpt-label">候选人原话摘录</span>
                <blockquote>{source.quote}</blockquote>
              </div>
              <dl>
                <div>
                  <dt>{interpretationLabel(reason.reason_type)}</dt>
                  <dd>{source.interpretation || source.conclusion}</dd>
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
      </aside>
    </div>
  )
}

export default EvidenceDrawer
