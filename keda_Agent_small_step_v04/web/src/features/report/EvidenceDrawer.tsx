import { useEffect, useRef } from 'react'
import type { ReasonView } from '../../api/types'

type EvidenceDrawerProps = {
  reason: ReasonView | null
  selectedEvidenceId?: string | null
  onClose: () => void
}

function reasonLabel(reasonType: string): string {
  const labels: Record<string, string> = {
    strength: '支持原因',
    risk: '限制原因',
    limiting: '限制原因',
    error: '关键错误',
    unverified: '待核验原因',
  }
  return labels[reasonType] ?? (reasonType || '评分原因')
}

export function EvidenceDrawer({ reason, selectedEvidenceId, onClose }: EvidenceDrawerProps) {
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
      <button className="evidence-drawer-backdrop" type="button" aria-label="关闭证据详情" onClick={onClose} />
      <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title">
        <div className="evidence-drawer-header">
          <div>
            <p className="eyebrow">TRACE / SCORE REASON</p>
            <h2 id="evidence-drawer-title">{reasonLabel(reason.reason_type)}</h2>
          </div>
          <button ref={closeButtonRef} className="evidence-drawer-close" type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <p className="evidence-drawer-reason">{reason.text}</p>

        {reason.rubric_signal_ids.length > 0 ? (
          <div className="evidence-rubric-line">
            <span>规则信号</span>
            <div>
              {reason.rubric_signal_ids.map((signalId) => <code key={signalId}>{signalId}</code>)}
            </div>
          </div>
        ) : null}

        <div className="evidence-source-list">
          <div className="evidence-section-heading">
            <h3>原始问答与证据</h3>
            <span>{reason.sources.length} 条来源</span>
          </div>
          {reason.sources.length > 0 ? reason.sources.map((source) => (
            <article
              className={`evidence-source ${source.evidence_id === selectedEvidenceId ? 'is-selected' : ''}`}
              key={source.evidence_id}
            >
              <div className="evidence-source-meta">
                <code>{source.evidence_id}</code>
                <span>{source.turn_id}</span>
              </div>
              <dl>
                <div>
                  <dt>原题</dt>
                  <dd>{source.question}</dd>
                </div>
                <div>
                  <dt>原回答</dt>
                  <dd>{source.answer || '候选人未提供回答。'}</dd>
                </div>
                <div>
                  <dt>证据摘录</dt>
                  <dd
                    className="evidence-excerpt"
                    data-excerpt={source.source_excerpt || '未提供摘录。'}
                    aria-label={`证据摘录：${source.source_excerpt || '未提供摘录。'}`}
                  />
                </div>
                <div>
                  <dt>冻结观察</dt>
                  <dd>{source.observation || '未提供观察。'}</dd>
                </div>
              </dl>
            </article>
          )) : (
            <p className="report-empty">该原因没有可展开的原始问答证据，保持为待核验状态。</p>
          )}
        </div>
      </aside>
    </div>
  )
}

export default EvidenceDrawer
