import type { InterviewTranscriptTurnView } from '../../api/types'

type InterviewTranscriptProps = {
  turns: InterviewTranscriptTurnView[]
  onEvidenceSelect: (evidenceId: string) => void
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

function questionModeLabel(value: string): string {
  return questionModeLabels[value] ?? '面试提问'
}

function evidenceStatusLabel(value: InterviewTranscriptTurnView['evidence_status']): string {
  return evidenceStatusLabels[value] ?? evidenceStatusLabels.none
}

export function InterviewTranscript({ turns, onEvidenceSelect }: InterviewTranscriptProps) {
  const orderedTurns = [...turns].sort((left, right) => left.sequence_number - right.sequence_number)

  return (
    <details className="report-collapsible report-transcript-collapsible">
      <summary>
        <span className="report-collapsible-eyebrow">INTERVIEW TRANSCRIPT</span>
        <strong>展开查看完整面试记录</strong>
        <span className="report-collapsible-action">查看详情 →</span>
      </summary>

      <section className="report-panel report-transcript-panel" aria-labelledby="transcript-title">
        <div className="report-section-heading">
          <div>
            <p className="eyebrow">INTERVIEW TRANSCRIPT</p>
            <h2 id="transcript-title">候选人的完整回答</h2>
          </div>
          <span>{orderedTurns.length} 个回答</span>
        </div>

        {orderedTurns.length > 0 ? (
          <ol className="interview-transcript-list">
            {orderedTurns.map((turn, index) => (
              <li className="interview-transcript-row" key={turn.turn_id}>
                <div className="interview-transcript-row-head">
                  <span className="interview-transcript-index">{String(index + 1).padStart(2, '0')}</span>
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
                  {turn.evidence_ids.length > 0 ? (
                    <div className="interview-transcript-evidence-actions">
                      {turn.evidence_ids.map((evidenceId) => (
                        <button
                          type="button"
                          key={evidenceId}
                          className="evidence-link"
                          aria-label={`查看证据 ${evidenceId}`}
                          onClick={() => onEvidenceSelect(evidenceId)}
                        >
                          查看证据 {evidenceId} →
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        ) : <p className="report-empty">当前没有返回面试记录。</p>}
      </section>
    </details>
  )
}

export default InterviewTranscript
