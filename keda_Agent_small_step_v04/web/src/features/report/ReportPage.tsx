import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { RadarDimensionView, ReasonView, ReportViewModel } from '../../api/types'
import { EvidenceDrawer } from './EvidenceDrawer'
import { InterviewTranscript } from './InterviewTranscript'
import { RadarChart } from './RadarChart'
import './report.css'

type ReportRecord = Record<string, unknown>

type SelectedReason = {
  reason: ReasonView
  evidenceId: string | null
}

function asRecord(value: unknown): ReportRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as ReportRecord
    : {}
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function textValue(value: unknown, fallback = '—'): string {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringList(value: unknown): string[] {
  return asArray(value).filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function percentage(value: unknown): string {
  const numeric = numberValue(value)
  if (numeric === null) return '—'
  return `${Math.round(Math.max(0, Math.min(1, numeric)) * 100)}%`
}

function confidenceLabel(value: unknown): string {
  const labels: Record<string, string> = { high: '高', medium: '中', low: '低' }
  const normalized = textValue(value, '')
  return labels[normalized] ?? (normalized || '未标注')
}

function scoreLabel(value: number | null): string {
  return value === null || !Number.isFinite(value) ? '未验证' : String(Math.round(value))
}

function reasonTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    strength: '支持',
    risk: '限制',
    limiting: '限制',
    error: '关键错误',
    unverified: '待核验',
  }
  return labels[value] ?? (value || '评分原因')
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '报告暂时无法读取，请稍后重试。'
}

function ReportLoadingState() {
  return (
    <section className="report-page report-state-page" aria-labelledby="report-loading-title">
      <p className="eyebrow">REPORT / 05</p>
      <h1 id="report-loading-title">正在读取岗位胜任力报告</h1>
      <p className="report-state-copy" role="status" aria-live="polite">正在读取报告，请稍候…</p>
    </section>
  )
}

function ReportErrorState({ message }: { message: string }) {
  return (
    <section className="report-page report-state-page" aria-labelledby="report-error-title">
      <p className="eyebrow">REPORT / UNAVAILABLE</p>
      <h1 id="report-error-title">报告暂时不可用</h1>
      <p className="report-state-copy" role="alert" aria-live="assertive">{message}</p>
      <p className="report-state-hint">报告只读取服务端已保存的评估结果；刷新页面可以重新尝试。</p>
    </section>
  )
}

function EmptyReportCopy({ children }: { children: string }) {
  return <p className="report-empty">{children}</p>
}

function ReportCollapsible({
  title,
  eyebrow,
  className = '',
  children,
}: {
  title: string
  eyebrow: string
  className?: string
  children: ReactNode
}) {
  return (
    <details className={`report-collapsible ${className}`}>
      <summary>
        <span className="report-collapsible-eyebrow">{eyebrow}</span>
        <strong>展开查看{title}</strong>
        <span className="report-collapsible-action">查看详情 →</span>
      </summary>
      {children}
    </details>
  )
}

function findReasonForEvidence(dimensions: RadarDimensionView[], evidenceId: string): ReasonView | null {
  for (const dimension of dimensions) {
    const reason = dimension.reasons.find((item) => item.evidence_ids.includes(evidenceId))
    if (reason) return reason
  }
  return null
}

function EvidenceIdList({ ids }: { ids: string[] }) {
  if (ids.length === 0) return <span className="report-muted">无可追溯证据</span>
  return (
    <span className="report-id-list">
      {ids.map((id) => <code key={id}>{id}</code>)}
    </span>
  )
}

function NarrativeList({
  title,
  items,
  tone,
}: {
  title: string
  items: unknown[]
  tone: 'positive' | 'risk' | 'unverified'
}) {
  return (
    <section className={`report-subpanel narrative-subpanel is-${tone}`} aria-labelledby={`${tone}-narrative-title`}>
      <div className="report-section-heading compact">
        <h3 id={`${tone}-narrative-title`}>{title}</h3>
        <span>{items.length} 项</span>
      </div>
      {items.length > 0 ? (
        <ul className="narrative-list">
          {items.map((item, index) => {
            const value = asRecord(item)
            return (
              <li key={`${title}-${index}`}>
                <p>{textValue(value.text)}</p>
                <div className="narrative-meta">
                  <span>{stringList(value.dimension_ids).join(' · ') || '服务端未返回维度'}</span>
                  <EvidenceIdList ids={stringList(value.evidence_ids)} />
                </div>
              </li>
            )
          })}
        </ul>
      ) : <EmptyReportCopy>当前没有返回内容。</EmptyReportCopy>}
    </section>
  )
}

function ReportView({ report, demo = report.demo }: { report: ReportViewModel; demo?: boolean }) {
  const [selected, setSelected] = useState<SelectedReason | null>(null)
  const jobMatch = asRecord(report.job_match)
  const narrative = asRecord(report.narrative)
  const rawScore = numberValue(jobMatch.raw_score)
  const isPublished = jobMatch.published === true && rawScore !== null
  const dimensions = report.radar_dimensions
  const path = report.interview_path
  const transcript = report.interview_transcript ?? []
  const claims = report.claim_verifications
  const developmentActions = asArray(narrative.development_actions)
  const limitingReasons = asArray(jobMatch.limiting_reasons)
  const strengths = asArray(narrative.strengths)
  const risks = asArray(narrative.risks)
  const verifiedDimensions = dimensions.filter((item) => item.score !== null && item.level !== 'UNVERIFIED')
  const dimensionNames = new Map(dimensions.map((dimension) => [dimension.dimension_id, dimension.name]))
  const demoVariant = report.demo_variant === 'boundary' ? 'boundary' : 'showcase'
  const isBoundaryDemo = demo && demoVariant === 'boundary'
  const decisionTitle = isPublished
    ? '建议进入结构化复试'
    : '先补齐证据，再做岗位判断'
  const decisionCopy = isPublished
    ? `六项核心能力均已形成证据，当前岗位匹配度为 ${Math.round(rawScore ?? 0)} 分。建议把复试时间放在关键场景复核与岗位团队沟通。`
    : '当前结论不是 0 分，而是证据覆盖或关键边界尚未满足发布条件。请先完成对应追问，再决定是否推进。'

  const closeDrawer = useCallback(() => setSelected(null), [])

  function selectReason(reason: ReasonView, evidenceId: string | null = null) {
    setSelected({ reason, evidenceId })
  }

  function selectEvidence(evidenceId: string) {
    const reason = findReasonForEvidence(dimensions, evidenceId)
    if (reason) selectReason(reason, evidenceId)
  }

  function selectDimension(dimension: RadarDimensionView) {
    const reason = dimension.reasons.find((item) => item.evidence_ids.length > 0) ?? dimension.reasons[0]
    if (reason) selectReason(reason, reason.evidence_ids[0] ?? null)
  }

  return (
    <section className="report-page" aria-labelledby="report-title">
      {demo ? (
        <div className={`report-demo-banner is-${demoVariant}`} role="note">
          <span>演示示例 · 只读</span>
          <strong>{isBoundaryDemo ? '评分边界案例' : '企业完整演示'}</strong>
          <small>冻结数据 · 只读 · 不调用模型</small>
        </div>
      ) : null}

      <header className="report-hero">
        <div className="report-hero-heading">
          <p className="eyebrow">{demo ? `DEMO / ${isBoundaryDemo ? 'BOUNDARY' : 'SHOWCASE'} · 05` : 'ASSESSMENT REPORT / 05'}</p>
          <h1 id="report-title">岗位胜任力报告</h1>
          <p className="report-role">{report.target_role}</p>
          <p className="report-summary">{textValue(narrative.executive_summary, '服务端已完成报告组合。')}</p>
          {demo && report.demo_case_description ? <p className="report-demo-description">{report.demo_case_description}</p> : null}
        </div>
        <div className={`report-fit-summary ${isPublished ? 'is-published' : 'is-pending'}`} aria-label="岗位匹配摘要">
          <span className="report-fit-kicker">岗位匹配</span>
          <strong>{isPublished ? Math.round(rawScore) : '暂不计算'}</strong>
          <span className="report-fit-level">{isPublished ? textValue(jobMatch.fit_level, '已计算') : '待核验'}</span>
          <small>{isPublished ? '服务端发布分数' : '核心覆盖或门槛证据不足'}</small>
        </div>
      </header>

      <section className="report-decision-brief" aria-labelledby="decision-brief-title">
        <div className="report-decision-main">
          <p className="eyebrow">RECRUITING DECISION</p>
          <h2 id="decision-brief-title">{decisionTitle}</h2>
          <p>{decisionCopy}</p>
        </div>
        <div className="report-decision-signals" aria-label="招聘决策摘要">
          <div>
            <span>已验证优势</span>
            <strong>{strengths.length > 0 ? `${strengths.length} 项` : '待核验'}</strong>
            <small>{strengths.length > 0 ? textValue(asRecord(strengths[0]).text, '已形成支持证据。') : '暂无可发布优势。'}</small>
          </div>
          <div>
            <span>主要风险</span>
            <strong>{risks.length > 0 ? `${risks.length} 项` : '未发现'}</strong>
            <small>{risks.length > 0 ? textValue(asRecord(risks[0]).text, '存在待复核限制。') : '当前证据中未形成限制项。'}</small>
          </div>
          <div>
            <span>证据覆盖</span>
            <strong>{verifiedDimensions.length} / {dimensions.length}</strong>
            <small>{isPublished ? '覆盖足以发布匹配结论。' : '覆盖不足，不折算为 0 分。'}</small>
          </div>
        </div>
      </section>

      <details className="report-audit-details">
        <summary>查看评估版本、数据边界与审计信息</summary>
        <div className="report-audit-line">
          <span>Role Pack <strong>{report.role_profile_version}</strong></span>
          <span>评分引擎 <strong>{report.scoring_engine_version}</strong></span>
          <span>数据边界 <strong>{demo ? (isBoundaryDemo ? '固定 C03 边界案例' : '固定 C01 黄金演示') : '本次评估已保存证据'}</strong></span>
        </div>
      </details>

      <section className="report-metrics" aria-label="报告指标">
        <div><span>岗位覆盖率</span><strong>{percentage(jobMatch.coverage)}</strong><small>加权证据覆盖</small></div>
        <div><span>岗位置信度</span><strong>{confidenceLabel(jobMatch.confidence)}</strong><small>服务端综合信号</small></div>
        <div><span>已验证维度</span><strong>{verifiedDimensions.length} / {dimensions.length}</strong><small>未验证不折算为 0</small></div>
        <div><span>追问节点</span><strong>{path.length}</strong><small>来自实际 InterviewPath</small></div>
      </section>

      <div className="report-layout report-layout-primary">
        <section className="report-panel report-radar-panel" aria-labelledby="radar-title">
          <div className="report-section-heading">
            <div>
              <p className="eyebrow">CAPABILITY MAP</p>
              <h2 id="radar-title">能力雷达</h2>
            </div>
            <span>{dimensions.length} 个服务端维度</span>
          </div>
          {dimensions.length > 0 ? (
            <RadarChart dimensions={dimensions} onDimensionSelect={selectDimension} />
          ) : <EmptyReportCopy>服务端没有返回能力维度。</EmptyReportCopy>}
        </section>

        <aside className="report-panel report-match-panel" aria-labelledby="match-context-title">
          <div className="report-section-heading">
            <div>
              <p className="eyebrow">DECISION CONTEXT</p>
              <h2 id="match-context-title">匹配边界</h2>
            </div>
          </div>
          <div className="report-match-note">
            <span className="report-match-mark" aria-hidden="true">{isPublished ? '✓' : '—'}</span>
            <p>{isPublished ? '岗位匹配分已满足发布条件。' : '岗位匹配分暂不计算；这不是 0 分结论。'}</p>
          </div>
          {limitingReasons.length > 0 ? (
            <ul className="limiting-reason-list">
              {limitingReasons.map((item, index) => {
                const reason = asRecord(item)
                return <li key={`limiting-${index}`}><span>{textValue(reason.reason_type, '边界')}</span><p>{textValue(reason.text)}</p></li>
              })}
            </ul>
          ) : <EmptyReportCopy>当前没有额外的匹配限制说明。</EmptyReportCopy>}
          <p className="report-disclaimer">报告用于辅助决策。最终判断仍需结合人工复试、岗位语境与其他合法信息。</p>
        </aside>
      </div>

      <InterviewTranscript turns={transcript} onEvidenceSelect={selectEvidence} />

      <ReportCollapsible title="评分原因与证据" eyebrow="EVIDENCE REGISTER" className="report-reasons-collapsible">
        <section className="report-panel report-reasons-panel" aria-labelledby="reasons-title">
          <div className="report-section-heading">
            <div>
              <p className="eyebrow">EVIDENCE REGISTER</p>
              <h2 id="reasons-title">评分原因与证据</h2>
            </div>
            <span>点击证据编号查看原始问答</span>
          </div>
          <div className="reason-grid">
            {dimensions.flatMap((dimension) => dimension.reasons.map((reason, index) => ({ dimension, reason, index }))).length > 0 ? (
              dimensions.flatMap((dimension) => dimension.reasons.map((reason, index) => ({ dimension, reason, index }))).map(({ dimension, reason, index }) => (
                <article className={`reason-card reason-${reason.reason_type}`} key={`${dimension.dimension_id}-${index}`}>
                  <div className="reason-card-meta"><span>{dimension.name} · 评分原因</span><strong>{reasonTypeLabel(reason.reason_type)}</strong></div>
                  <p>{reason.text}</p>
                  <div className="reason-card-evidence">
                    {reason.evidence_ids.length > 0 ? reason.evidence_ids.map((evidenceId) => (
                      <button
                        type="button"
                        key={evidenceId}
                        className="evidence-link"
                        aria-label={`查看证据 ${evidenceId}`}
                        onClick={() => selectReason(reason, evidenceId)}
                      >
                        查看证据 {evidenceId} →
                      </button>
                    )) : <span className="report-muted">暂无证据编号 · 保持待核验</span>}
                  </div>
                </article>
              ))
            ) : <EmptyReportCopy>服务端没有返回评分原因。</EmptyReportCopy>}
          </div>
        </section>
      </ReportCollapsible>

      <ReportCollapsible title="动态追问路径" eyebrow="REQUIREMENT TRACE" className="report-requirement-collapsible">
        <section className="report-panel requirement-panel" aria-labelledby="requirements-title">
          <div className="report-section-heading">
            <div>
              <p className="eyebrow">REQUIREMENT TRACE</p>
              <h2 id="requirements-title">动态追问路径</h2>
            </div>
            <span>Requirement 分解 · 问题顺序由 InterviewGraph 决定</span>
          </div>
          {path.length > 0 ? (
            <ol className="path-timeline">
              {path.map((item, index) => (
                <li key={`${item.turn_id}-${index}`}>
                  <div className="path-step-index">{String(index + 1).padStart(2, '0')}</div>
                  <div className="path-step-copy">
                    <div className="path-step-head"><strong>{textValue(item.requirement_id, '未标注 Requirement')}</strong><span>{textValue(item.question_mode, '未标注方式')}</span></div>
                    <p>{textValue(item.outcome, '结果待核验')}</p>
                    <EvidenceIdList ids={item.evidence_ids} />
                  </div>
                </li>
              ))}
            </ol>
          ) : <EmptyReportCopy>当前报告没有返回动态追问路径。</EmptyReportCopy>}
        </section>
      </ReportCollapsible>

      <ReportCollapsible title="结论分层与声明核验" eyebrow="REVIEW REGISTER" className="report-secondary-collapsible">
        <div className="report-layout report-layout-secondary">
          <section className="report-panel narrative-panel" aria-labelledby="narrative-title">
            <div className="report-section-heading">
              <div>
                <p className="eyebrow">NARRATIVE / REVIEW</p>
                <h2 id="narrative-title">结论分层</h2>
              </div>
            </div>
            <div className="narrative-grid">
              <NarrativeList title="已验证优势" items={asArray(narrative.strengths)} tone="positive" />
              <NarrativeList title="限制与风险" items={asArray(narrative.risks)} tone="risk" />
              <NarrativeList title="待核验区域" items={asArray(narrative.unverified_areas)} tone="unverified" />
            </div>
          </section>

          <section className="report-panel claims-panel" aria-labelledby="claims-title">
            <div className="report-section-heading">
              <div>
                <p className="eyebrow">CLAIM REGISTER</p>
                <h2 id="claims-title">声明核验</h2>
              </div>
              <span>{claims.length} 条</span>
            </div>
            {claims.length > 0 ? (
              <ul className="claim-verification-list">
                {claims.map((claim, index) => {
                  const value = asRecord(claim)
                  return (
                    <li key={`claim-${index}`}>
                      <div className="claim-verification-head"><code>{textValue(value.claim_id ?? value.id, `CLAIM-${index + 1}`)}</code><strong>{textValue(value.status ?? value.outcome, '未标注')}</strong></div>
                      <p>{textValue(value.claim ?? value.claim_text ?? value.text, '声明文本未返回。')}</p>
                      <EvidenceIdList ids={stringList(value.supporting_evidence_ids ?? value.contradicting_evidence_ids ?? value.evidence_ids)} />
                    </li>
                  )
                })}
              </ul>
            ) : <EmptyReportCopy>当前没有返回声明核验结果。</EmptyReportCopy>}
          </section>
        </div>
      </ReportCollapsible>

      <ReportCollapsible title="发展建议与复试验证" eyebrow="NEXT VERIFICATION" className="report-development-collapsible">
        <section className="report-panel development-panel" aria-labelledby="development-title">
          <div className="report-section-heading">
            <div>
              <p className="eyebrow">NEXT VERIFICATION</p>
              <h2 id="development-title">发展建议与复试验证</h2>
            </div>
            <span>{developmentActions.length} 个维度</span>
          </div>
          {developmentActions.length > 0 ? (
            <div className="development-grid">
              {developmentActions.map((action, index) => {
                const value = asRecord(action)
                return (
                  <article key={`development-${index}`}>
                    <span className="development-index">{String(index + 1).padStart(2, '0')}</span>
                    <h3>{dimensionNames.get(textValue(value.dimension_id, '')) ?? `复试重点 ${String(index + 1).padStart(2, '0')}`}</h3>
                    <p className="development-gap">{textValue(value.current_gap, '当前差距未返回。')}</p>
                    {stringList(value.actions).length > 0 ? <ul>{stringList(value.actions).map((item) => <li key={item}>{item}</li>)}</ul> : null}
                    {stringList(value.acceptance_criteria).length > 0 ? <p className="development-acceptance"><strong>验收标准</strong>{stringList(value.acceptance_criteria).join('；')}</p> : null}
                  </article>
                )
              })}
            </div>
          ) : <EmptyReportCopy>当前没有返回发展建议。</EmptyReportCopy>}
        </section>
      </ReportCollapsible>

      <ReportCollapsible title="评估限制" eyebrow="AUDIT NOTES" className="report-limitations-collapsible">
        <footer className="report-limitations" aria-labelledby="limitations-title">
          <div>
            <p className="eyebrow">AUDIT NOTES</p>
            <h2 id="limitations-title">评估限制</h2>
          </div>
          {report.assessment_limitations.length > 0 ? (
            <ul>{report.assessment_limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
          ) : <EmptyReportCopy>服务端没有返回评估限制。</EmptyReportCopy>}
        </footer>
      </ReportCollapsible>

      <EvidenceDrawer
        reason={selected?.reason ?? null}
        selectedEvidenceId={selected?.evidenceId}
        onClose={closeDrawer}
      />
    </section>
  )
}

export function ReportPage() {
  const { assessmentId = '' } = useParams()
  const [report, setReport] = useState<ReportViewModel | null>(null)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    setReport(null)
    setLoadError('')
    if (!assessmentId) {
      setLoadError('缺少评估编号，无法读取报告。')
      return () => { cancelled = true }
    }
    void api.getReport(assessmentId)
      .then((payload) => {
        if (!cancelled) setReport(payload)
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(errorMessage(error))
      })
    return () => { cancelled = true }
  }, [assessmentId])

  if (loadError) return <ReportErrorState message={loadError} />
  if (!report) return <ReportLoadingState />
  return <ReportView report={report} demo={false} />
}

export { ReportView }
export default ReportPage
