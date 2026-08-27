import { useEffect, useMemo, useRef } from 'react'
import type { InterviewPathView, InterviewTranscriptTurnView } from '../../api/types'

export type TranscriptFocusRequest = {
  turnId: string
  requestId: number
}

type InterviewTranscriptProps = {
  turns: InterviewTranscriptTurnView[]
  path?: InterviewPathView[]
  focusRequest?: TranscriptFocusRequest | null
  onTurnSelect?: (turnId: string) => void
}

const questionModeLabels: Record<string, string> = {
  foundation: '基础知识',
  project_deep_dive: '项目深挖',
  scenario: '场景题',
  system_design: '系统设计',
  coding: '编码实现',
  follow_up: '深入追问',
}

const evidenceStatusLabels: Record<InterviewTranscriptTurnView['evidence_status'], string> = {
  supporting: '支持证据',
  limiting: '限制证据',
  mixed: '支持与限制并存',
  none: '未形成评分证据',
}

const pathOutcomeLabels: Record<string, string> = {
  supporting: '形成支持证据',
  limiting: '形成限制证据',
  contradicting: '形成限制证据',
  mixed: '形成支持与限制证据',
  unverified: '仍待核验',
  none: '仍待核验',
}

function questionModeLabel(value: string): string {
  return questionModeLabels[value] ?? '面试提问'
}

function evidenceStatusLabel(value: InterviewTranscriptTurnView['evidence_status']): string {
  return evidenceStatusLabels[value] ?? evidenceStatusLabels.none
}

function pathOutcomeLabel(value: string): string {
  return pathOutcomeLabels[value] ?? '仍待核验'
}

function turnDomId(turn: InterviewTranscriptTurnView): string {
  return `interview-turn-${turn.sequence_number}`
}

export function InterviewTranscript({ turns, path = [], focusRequest, onTurnSelect }: InterviewTranscriptProps) {
  const detailsRef = useRef<HTMLDetailsElement>(null)
  const orderedTurns = useMemo(
    () => [...turns].sort((left, right) => left.sequence_number - right.sequence_number),
    [turns],
  )

  useEffect(() => {
    if (!focusRequest) return
    const targetTurn = orderedTurns.find((turn) => turn.turn_id === focusRequest.turnId)
    if (!targetTurn) return
    detailsRef.current?.setAttribute('open', '')
    window.requestAnimationFrame(() => {
      const row = document.getElementById(turnDomId(targetTurn))
      if (!row) return
      row.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
      row.focus()
    })
  }, [focusRequest, orderedTurns])

  return (
    <details ref={detailsRef} className="report-collapsible report-transcript-collapsible">
      <summary>
        <span className="report-collapsible-eyebrow">面试过程</span>
        <strong>展开查看面试过程回顾</strong>
        <span className="report-collapsible-action">查看详情 →</span>
      </summary>

      <section className="report-panel report-transcript-panel" aria-labelledby="transcript-title">
        <div className="report-section-heading">
          <div>
            <p className="eyebrow">面试过程</p>
            <h2 id="transcript-title">问题、回答与追问依据</h2>
          </div>
          <span>{orderedTurns.length} 轮记录</span>
        </div>

        {orderedTurns.length > 0 ? (
          <ol className="interview-transcript-list">
            {orderedTurns.map((turn) => (
              <li
                className="interview-transcript-row"
                id={turnDomId(turn)}
                key={turn.turn_id}
                tabIndex={-1}
              >
                <div className="interview-transcript-row-head">
                  <span className="interview-transcript-index">{String(turn.sequence_number).padStart(2, '0')}</span>
                  <div className="interview-transcript-meta">
                    <strong>{turn.requirement_label}</strong>
                    <span>{questionModeLabel(turn.question_mode)}</span>
                  </div>
                </div>

                <div className="interview-transcript-copy">
                  <div>
                    <span className="interview-transcript-label">面试问题</span>
                    <p className="interview-transcript-question">{turn.question}</p>
                  </div>
                  <div>
                    <span className="interview-transcript-label">回答</span>
                    <p className="interview-transcript-answer">{turn.answer || '未提交回答'}</p>
                  </div>
                </div>

                <div className="interview-transcript-evidence">
                  <span className={`interview-transcript-evidence-badge is-${turn.evidence_status}`}>
                    {evidenceStatusLabel(turn.evidence_status)}
                  </span>
                  {turn.evidence_status !== 'none' && onTurnSelect ? (
                    <div className="interview-transcript-evidence-actions">
                      <button
                        type="button"
                        className="evidence-link"
                        onClick={() => onTurnSelect(turn.turn_id)}
                      >
                        {turn.evidence_cta || '查看本轮依据'}
                      </button>
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        ) : <p className="report-empty">当前没有返回面试记录。</p>}

        {path.length > 0 ? (
          <div className="interview-path-review" aria-label="面试追问结果">
            <h3>本次追问结果</h3>
            <ol className="path-timeline">
              {path.map((item, index) => {
                const turn = orderedTurns.find((candidate) => candidate.turn_id === item.turn_id)
                return (
                  <li key={`${item.turn_id}-${item.question_mode}-${item.outcome}`}>
                    <div className="path-step-index">{String(index + 1).padStart(2, '0')}</div>
                    <div className="path-step-copy">
                      <div className="path-step-head">
                        <strong>{turn?.requirement_label || '本轮面试'}</strong>
                        <span>{questionModeLabel(item.question_mode)}</span>
                      </div>
                      <p>{pathOutcomeLabel(item.outcome)}</p>
                      {turn && onTurnSelect && turn.evidence_status !== 'none' ? (
                        <button
                          type="button"
                          className="evidence-link"
                          onClick={() => onTurnSelect(turn.turn_id)}
                        >
                          查看本轮依据
                        </button>
                      ) : null}
                    </div>
                  </li>
                )
              })}
            </ol>
          </div>
        ) : null}
      </section>
    </details>
  )
}

export default InterviewTranscript
