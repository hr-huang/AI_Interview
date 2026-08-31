import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { AssessmentStatus, AssessmentStatusView } from '../../api/types'
import './assessment.css'

const POLL_INTERVAL_MS = 1500

const ANALYSIS_STAGES = [
  { code: 'A-01', label: '文件提取', description: '读取并清洗候选人材料' },
  { code: 'A-02', label: '简历画像', description: '整理经历、技能与待核验声明' },
  { code: 'A-03', label: '岗位理解', description: '解析职责、要求与业务语境' },
  { code: 'A-04', label: '能力建模', description: '对齐岗位标准与证据要求' },
  { code: 'A-05', label: '计划生成', description: '形成可供企业审核的验证计划' },
]

const STATUS_LABELS: Record<AssessmentStatus, string> = {
  DRAFT: '等待提交',
  ANALYZING: '后台分析中',
  PLAN_REVIEW: '等待企业审核',
  READY: '计划已冻结',
  IN_PROGRESS: '面试进行中',
  REPORTING: '报告生成中',
  COMPLETE: '已完成',
  FAILED: '分析未完成',
}

function safeErrorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '暂时无法获取分析状态，请稍后重试。'
}

export function AnalyzingPage() {
  const { assessmentId = '' } = useParams()
  const navigate = useNavigate()
  const [snapshot, setSnapshot] = useState<AssessmentStatusView | null>(null)
  const [pollError, setPollError] = useState('')
  const [isRetrying, setIsRetrying] = useState(false)
  const terminalRef = useRef(false)
  const pollInFlightRef = useRef(false)

  const poll = useCallback(async () => {
    if (!assessmentId || terminalRef.current || pollInFlightRef.current) return
    pollInFlightRef.current = true
    try {
      const latest = await api.getAssessment(assessmentId)
      setSnapshot(latest)
      setPollError('')
      if (latest.status === 'PLAN_REVIEW') {
        terminalRef.current = true
        navigate(`/assessments/${encodeURIComponent(assessmentId)}/plan`, { replace: true })
      } else if (latest.status === 'FAILED') {
        terminalRef.current = true
      }
    } catch (error) {
      setPollError(safeErrorMessage(error))
    } finally {
      pollInFlightRef.current = false
    }
  }, [assessmentId, navigate])

  useEffect(() => {
    terminalRef.current = false
    pollInFlightRef.current = false
    void poll()
    const interval = window.setInterval(() => {
      void poll()
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [poll])

  async function retry() {
    if (!assessmentId || isRetrying) return
    setIsRetrying(true)
    setPollError('')
    terminalRef.current = false
    setSnapshot((current) => current ? { ...current, status: 'ANALYZING', retryable: false } : current)
    try {
      await api.retryAssessment(assessmentId)
      await poll()
    } catch (error) {
      terminalRef.current = true
      setPollError(safeErrorMessage(error))
      setSnapshot((current) => current ? { ...current, status: 'FAILED', retryable: true } : current)
    } finally {
      setIsRetrying(false)
    }
  }

  const status = snapshot?.status ?? 'ANALYZING'
  const isFailed = status === 'FAILED'

  return (
    <section className="assessment-page analyzing-page" aria-labelledby="analyzing-title">
      <div className="assessment-page-heading compact-heading">
        <div>
          <p className="eyebrow">MATERIAL ANALYSIS / 02</p>
          <h1 id="analyzing-title">正在分析评估材料</h1>
          <p className="page-lead">
            服务端正在把岗位与简历整理成一份可供企业审核的验证计划。
          </p>
        </div>
        <div className="assessment-id-block" aria-label="评估编号">
          <span>ASSESSMENT ID</span>
          <strong>{assessmentId || '待识别'}</strong>
        </div>
      </div>

      <div className="assessment-rule" aria-hidden="true" />

      <div className="analysis-layout">
        <div className="analysis-stage-panel">
          <div className="panel-line-heading">
            <span className="panel-code">SERVER PIPELINE</span>
            <span className={`analysis-status status-${status.toLowerCase()}`} role="status" aria-live="polite">
              {STATUS_LABELS[status]}
            </span>
          </div>
          <ol className="analysis-stages" aria-label="分析阶段">
            {ANALYSIS_STAGES.map((stage) => (
              <li key={stage.code} className="analysis-stage">
                <span className="stage-code">{stage.code}</span>
                <span className="stage-copy">
                  <strong>{stage.label}</strong>
                  <small>{stage.description}</small>
                </span>
                <span className="stage-state">服务端状态</span>
              </li>
            ))}
          </ol>
          <p className="analysis-note">
            阶段顺序与后端分析链路一致。页面只展示服务端状态，不估算百分比或剩余时间。
          </p>
        </div>

        <aside className={`analysis-info-panel ${isFailed ? 'is-failed' : ''}`} aria-label="分析状态说明">
          {isFailed ? (
            <>
              <span className="failure-mark" aria-hidden="true">!</span>
              <p className="eyebrow">ANALYSIS INTERRUPTED</p>
              <h2>材料没有丢失</h2>
              <p>
                {snapshot?.error_message || '计划分析未完成。'} 你可以保留当前评估编号并原地重试。
              </p>
              <button
                className="primary-action"
                type="button"
                onClick={() => void retry()}
                disabled={isRetrying || snapshot?.retryable === false}
              >
                {isRetrying ? '重新提交中…' : '重新分析'}
              </button>
            </>
          ) : (
            <>
              <span className="analysis-pulse" aria-hidden="true" />
              <p className="eyebrow">ANALYSIS IN PROGRESS</p>
              <h2>请保留此页面</h2>
              <p>
                分析完成后会自动进入计划审核。你可以安全刷新页面，评估编号仍会保留。
              </p>
              <div className="live-message" role="status" aria-live="polite">
                {pollError || '正在等待服务端返回下一状态…'}
              </div>
            </>
          )}
          {pollError && isFailed ? (
            <div className="form-feedback error-feedback" role="alert" aria-live="assertive">
              <p>{pollError}</p>
            </div>
          ) : null}
        </aside>
      </div>
    </section>
  )
}

export default AnalyzingPage
