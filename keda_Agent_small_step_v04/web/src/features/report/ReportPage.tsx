import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../../api/client'
import type {
  CandidateOverviewView,
  DecisionSignalView,
  EnterpriseAssessmentView,
  RadarDimensionView,
  ReasonView,
  ReportViewModel,
  ReinterviewFocusView,
} from '../../api/types'
import { EvidenceDrawer } from './EvidenceDrawer'
import { InterviewTranscript } from './InterviewTranscript'
import { RadarChart } from './RadarChart'
import './report.css'

type SelectedReason = ReasonView | null

function textValue(value: string | null | undefined, fallback = '—'): string {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function percentage(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`
}

function confidenceLabel(value: string | null | undefined): string {
  const labels: Record<string, string> = { high: '高', medium: '中', low: '低' }
  return labels[value ?? ''] ?? textValue(value, '未标注')
}

function scoreLabel(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value)) : '待核验'
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '报告暂时无法读取，请稍后重试。'
}

function ReportLoadingState() {
  return (
    <section className="report-page report-state-page" aria-labelledby="report-loading-title">
      <p className="eyebrow">评估报告 / 05</p>
      <h1 id="report-loading-title">正在读取岗位胜任力报告</h1>
      <p className="report-state-copy" role="status" aria-live="polite">正在读取报告，请稍候…</p>
    </section>
  )
}

function ReportErrorState({ message }: { message: string }) {
  return (
    <section className="report-page report-state-page" aria-labelledby="report-error-title">
      <p className="eyebrow">评估报告 / 暂不可用</p>
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

function CandidateOverview({ overview }: { overview: CandidateOverviewView }) {
  return (
    <section className="report-panel report-candidate-panel" aria-labelledby="candidate-overview-title">
      <div className="report-section-heading">
        <div>
          <p className="eyebrow">候选人 / 岗位上下文</p>
          <h2 id="candidate-overview-title">候选人概览</h2>
        </div>
        <span>{overview.interview_rounds} 轮面试记录</span>
      </div>
      <div className="candidate-overview-grid">
        <div className="candidate-overview-identity">
          <span className="candidate-overview-label">候选人</span>
          <strong>{textValue(overview.candidate_name, '匿名候选人')}</strong>
          <span>{overview.target_role}</span>
        </div>
        <dl className="candidate-overview-facts">
          <div>
            <dt>教育背景</dt>
            <dd>{textValue(overview.education_summary, '未提供')}</dd>
          </div>
          <div>
            <dt>经历概览</dt>
            <dd>{textValue(overview.experience_summary, '未提供')}</dd>
          </div>
          <div>
            <dt>本次岗位关注</dt>
            <dd>{overview.jd_focus.length > 0 ? overview.jd_focus.join(' · ') : '按岗位标准核验'}</dd>
          </div>
        </dl>
      </div>
    </section>
  )
}

function DecisionBrief({
  assessment,
  jobMatch,
}: {
  assessment: EnterpriseAssessmentView
  jobMatch: ReportViewModel['job_match']
}) {
  const score = assessment.provisional_score ?? jobMatch.raw_score
  const hasScore = typeof score === 'number' && Number.isFinite(score)
  const confidence = confidenceLabel(assessment.confidence || jobMatch.confidence)
  return (
    <section className="report-decision-brief enterprise-decision-brief" aria-labelledby="decision-brief-title">
      <div className="report-decision-main">
        <p className="eyebrow">企业招聘结论</p>
        <h2 id="decision-brief-title">{textValue(assessment.decision_label, '待核验后决定')}</h2>
        <p>{assessment.decision_reasons[0] || '当前结论基于已保存的面试证据，仍需结合人工复试。'}</p>
      </div>
      <div className="report-decision-signals" aria-label="招聘决策摘要">
        <div>
          <span>暂定匹配分</span>
          <strong>{hasScore ? scoreLabel(score) : '待核验'}</strong>
          <small>{hasScore ? '不替代人工复试判断' : '证据覆盖不足，暂不折算为 0 分'}</small>
        </div>
        <div>
          <span>综合置信度</span>
          <strong>{confidence}</strong>
          <small>由服务端证据覆盖与一致性信号汇总</small>
        </div>
        <div>
          <span>推进条件</span>
          <strong>{assessment.conditions.length > 0 ? `${assessment.conditions.length} 项` : '无'}</strong>
          <small>{assessment.conditions[0] || '当前没有额外推进条件。'}</small>
        </div>
      </div>
      {assessment.conditions.length > 1 ? (
        <ul className="report-decision-conditions">
          {assessment.conditions.map((condition) => <li key={condition}>{condition}</li>)}
        </ul>
      ) : null}
    </section>
  )
}

function OverallAssessment({ assessment, narrativeSummary }: { assessment: EnterpriseAssessmentView; narrativeSummary: string }) {
  return (
    <section className="report-panel report-overall-panel" aria-labelledby="overall-assessment-title">
      <div className="report-section-heading">
        <div>
          <p className="eyebrow">综合判断</p>
          <h2 id="overall-assessment-title">候选人总评</h2>
        </div>
        <span>基于当前问答证据</span>
      </div>
      <p className="report-overall-copy">{assessment.overall_assessment}</p>
      {narrativeSummary && narrativeSummary !== assessment.overall_assessment ? (
        <p className="report-overall-context">{narrativeSummary}</p>
      ) : null}
    </section>
  )
}

function DecisionSignalList({
  title,
  items,
  tone,
}: {
  title: string
  items: DecisionSignalView[]
  tone: 'positive' | 'risk' | 'unverified'
}) {
  return (
    <section className={`report-subpanel narrative-subpanel is-${tone}`} aria-labelledby={`${tone}-signal-title`}>
      <div className="report-section-heading compact">
        <h3 id={`${tone}-signal-title`}>{title}</h3>
        <span>{items.length} 项</span>
      </div>
      {items.length > 0 ? (
        <ul className="narrative-list decision-signal-list">
          {items.map((item, index) => (
            <li key={`${item.title}-${index}`}>
              <strong>{item.title}</strong>
              <p>{item.text}</p>
              <div className="narrative-meta">
                <span>{item.dimension_names.join(' · ') || '岗位相关能力'}</span>
                <span>置信度 {confidenceLabel(item.confidence)}</span>
              </div>
            </li>
          ))}
        </ul>
      ) : <EmptyReportCopy>当前没有返回内容。</EmptyReportCopy>}
    </section>
  )
}

function DecisionSignals({ assessment }: { assessment: EnterpriseAssessmentView }) {
  return (
    <section className="report-panel report-signals-panel" aria-labelledby="signals-title">
      <div className="report-section-heading">
        <div>
          <p className="eyebrow">支持 / 限制 / 未知</p>
          <h2 id="signals-title">优势、风险与待确认</h2>
        </div>
        <span>只呈现与招聘判断相关的信号</span>
      </div>
      <div className="narrative-grid decision-signal-grid">
        <DecisionSignalList title="优势" items={assessment.strengths} tone="positive" />
        <DecisionSignalList title="风险" items={assessment.risks} tone="risk" />
        <DecisionSignalList title="待确认" items={assessment.unknowns} tone="unverified" />
      </div>
    </section>
  )
}

function ReinterviewPlan({ items }: { items: ReinterviewFocusView[] }) {
  const plan = items.slice(0, 3)
  return (
    <section className="report-panel development-panel enterprise-reinterview-panel" aria-labelledby="reinterview-title">
      <div className="report-section-heading">
        <div>
          <p className="eyebrow">下一轮验证</p>
          <h2 id="reinterview-title">企业复试计划</h2>
        </div>
        <span>{plan.length} 项优先核验</span>
      </div>
      {plan.length > 0 ? (
        <div className="development-grid reinterview-grid">
          {plan.map((item, index) => (
            <article key={`${item.dimension_name}-${item.priority}-${index}`} className="reinterview-card">
              <span className="development-index">优先级 {String(item.priority || index + 1).padStart(2, '0')}</span>
              <h3>{item.dimension_name}</h3>
              <p className="development-gap">{item.reason}</p>
              <div className="reinterview-question">
                <span>结构化主问题</span>
                <p>{item.question}</p>
              </div>
              {item.follow_ups.length > 0 ? (
                <div className="reinterview-followups">
                  <span>追问方向</span>
                  <ul>{item.follow_ups.map((followUp) => <li key={followUp}>{followUp}</li>)}</ul>
                </div>
              ) : null}
              {item.pass_criteria.length > 0 ? (
                <div className="development-acceptance">
                  <strong>通过标准</strong>
                  <span>{item.pass_criteria.join('；')}</span>
                </div>
              ) : null}
              <small className="reinterview-minutes">建议用时 {item.suggested_minutes} 分钟</small>
            </article>
          ))}
        </div>
      ) : <EmptyReportCopy>当前没有需要额外安排的结构化复试重点。</EmptyReportCopy>}
    </section>
  )
}

function findReasonForTurn(dimensions: RadarDimensionView[], turnId: string): ReasonView | null {
  for (const dimension of dimensions) {
    const reason = dimension.reasons.find((item) => item.sources.some((source) => source.turn_id === turnId))
    if (reason) return reason
  }
  return null
}

function ReportView({ report, demo = report.demo }: { report: ReportViewModel; demo?: boolean }) {
  const [selectedReason, setSelectedReason] = useState<SelectedReason>(null)
  const [focusTurnId, setFocusTurnId] = useState<string | null>(null)
  const enterprise = report.enterprise_assessment
  const jobMatch = report.job_match
  const dimensions = report.radar_dimensions
  const path = report.interview_path
  const transcript = report.interview_transcript ?? []
  const demoVariant = report.demo_variant === 'boundary' ? 'boundary' : 'showcase'
  const score = enterprise.provisional_score ?? jobMatch.raw_score
  const hasScore = typeof score === 'number' && Number.isFinite(score)

  const closeDrawer = useCallback(() => setSelectedReason(null), [])

  function selectDimension(dimension: RadarDimensionView) {
    const reason = dimension.reasons.find((item) => item.sources.length > 0) ?? dimension.reasons[0]
    setSelectedReason(reason ?? null)
  }

  function focusTurn(turnId: string) {
    setFocusTurnId(turnId)
  }

  function selectTranscriptTurn(turnId: string) {
    const reason = findReasonForTurn(dimensions, turnId)
    if (reason) setSelectedReason(reason)
    focusTurn(turnId)
  }

  return (
    <section className="report-page" aria-labelledby="report-title">
      {demo ? (
        <div className={`report-demo-banner is-${demoVariant}`} role="note">
          <span>演示示例 · 只读</span>
          <strong>学生候选人完整评估</strong>
          <small>冻结数据 · 只读 · 不调用模型</small>
        </div>
      ) : null}

      <header className="report-hero">
        <div className="report-hero-heading">
          <p className="eyebrow">{demo ? '演示评估 / 学生候选人 · 05' : '评估报告 / 05'}</p>
          <h1 id="report-title">岗位胜任力报告</h1>
          <p className="report-role">{report.target_role}</p>
        </div>
        <div className="report-fit-summary is-context" aria-label="候选人上下文">
          <span className="report-fit-kicker">候选人</span>
          <strong>{textValue(report.candidate_overview.candidate_name, '匿名候选人')}</strong>
          <span className="report-fit-level">{report.candidate_overview.interview_rounds} 轮面试记录</span>
          <small>{report.candidate_overview.jd_focus.length > 0 ? report.candidate_overview.jd_focus.join(' · ') : '按岗位标准核验'}</small>
        </div>
      </header>

      <CandidateOverview overview={report.candidate_overview} />
      <DecisionBrief assessment={enterprise} jobMatch={jobMatch} />
      <OverallAssessment assessment={enterprise} narrativeSummary={report.narrative.executive_summary} />

      <section className="report-metrics" aria-label="报告指标">
        <div><span>岗位覆盖率</span><strong>{percentage(jobMatch.coverage)}</strong><small>加权证据覆盖</small></div>
        <div><span>岗位置信度</span><strong>{confidenceLabel(enterprise.confidence || jobMatch.confidence)}</strong><small>服务端综合信号</small></div>
        <div><span>面试轮次</span><strong>{report.candidate_overview.interview_rounds}</strong><small>完整记录保留在下方</small></div>
        <div><span>复试重点</span><strong>{Math.min(3, enterprise.reinterview_plan.length)}</strong><small>最多三项结构化核验</small></div>
      </section>

      <div className="report-layout report-layout-primary">
        <section className="report-panel report-radar-panel" aria-labelledby="radar-title">
          <div className="report-section-heading">
            <div>
              <p className="eyebrow">能力总览</p>
              <h2 id="radar-title">能力雷达</h2>
            </div>
            <span>{dimensions.length} 个岗位维度</span>
          </div>
          {dimensions.length > 0 ? (
            <RadarChart dimensions={dimensions} onDimensionSelect={selectDimension} />
          ) : <EmptyReportCopy>服务端没有返回能力维度。</EmptyReportCopy>}
        </section>

        <aside className="report-panel report-match-panel" aria-labelledby="match-context-title">
          <div className="report-section-heading">
            <div>
              <p className="eyebrow">决策边界</p>
              <h2 id="match-context-title">匹配边界</h2>
            </div>
          </div>
          <div className="report-match-note">
            <span className="report-match-mark" aria-hidden="true">{hasScore ? '✓' : '—'}</span>
            <p>{hasScore ? '暂定匹配分已形成，但仍需按条件完成复试。' : '岗位匹配分暂不计算；这不是 0 分结论。'}</p>
          </div>
          {jobMatch.limiting_reasons.length > 0 ? (
            <ul className="limiting-reason-list">
              {jobMatch.limiting_reasons.map((reason, index) => (
                <li key={`${reason.reason_type}-${index}`}>
                  <span>{reason.reason_type === 'unverified' ? '待核验' : '决策限制'}</span>
                  <p>{reason.text}</p>
                </li>
              ))}
            </ul>
          ) : <EmptyReportCopy>当前没有额外的匹配限制说明。</EmptyReportCopy>}
          <p className="report-disclaimer">报告用于辅助决策。最终判断仍需结合人工复试、岗位语境与其他合法信息。</p>
        </aside>
      </div>

      <DecisionSignals assessment={enterprise} />
      <ReinterviewPlan items={enterprise.reinterview_plan} />

      <InterviewTranscript
        turns={transcript}
        path={path}
        focusTurnId={focusTurnId}
        onTurnSelect={selectTranscriptTurn}
      />

      <ReportCollapsible title="报告说明" eyebrow="报告说明" className="report-limitations-collapsible">
        <footer className="report-limitations" aria-labelledby="limitations-title">
          <div>
            <p className="eyebrow">报告说明</p>
            <h2 id="limitations-title">报告说明</h2>
          </div>
          {report.assessment_limitations.length > 0 ? (
            <ul>{report.assessment_limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
          ) : <EmptyReportCopy>服务端没有返回评估限制。</EmptyReportCopy>}
        </footer>
      </ReportCollapsible>

      <EvidenceDrawer
        reason={selectedReason}
        onClose={closeDrawer}
        onTurnSelect={focusTurn}
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
