import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { AssessmentStatus, AssessmentStatusView } from '../../api/types'
import { PlanReviewPage } from './PlanReviewPage'
import './enterprise-plan-review.css'

const TERMINAL_STATUSES = new Set<AssessmentStatus>(['COMPLETE', 'FAILED'])
const VISIBLE_STATUSES = new Set<AssessmentStatus>([
  'READY',
  'IN_PROGRESS',
  'REPORTING',
  'COMPLETE',
  'FAILED',
])

const STATUS_COPY: Record<
  Extract<AssessmentStatus, 'READY' | 'IN_PROGRESS' | 'REPORTING' | 'COMPLETE' | 'FAILED'>,
  { label: string; detail: string; step: number }
> = {
  READY: {
    label: '等待候选人开始',
    detail: '计划已冻结。候选人通过专属链接进入面试后，这里会自动更新。',
    step: 1,
  },
  IN_PROGRESS: {
    label: '候选人面试中',
    detail: '系统正在接收回答、提取 Evidence，并由 Supervisor 动态决定后续验证。',
    step: 2,
  },
  REPORTING: {
    label: '正在生成评估报告',
    detail: '面试已经结束，系统正在汇总 Evidence、Claim 核验与确定性评分。',
    step: 3,
  },
  COMPLETE: {
    label: '面试已完成',
    detail: '候选人的回答与评估结果已经回到本次 Assessment，可进入企业报告查看。',
    step: 4,
  },
  FAILED: {
    label: '评估流程异常',
    detail: '系统未能完成本次评估，请根据错误信息检查后重试。',
    step: 0,
  },
}

function statusClass(status: AssessmentStatus): string {
  return `status-${status.toLowerCase().replace('_', '-')}`
}

export function EnterprisePlanReviewPage() {
  const { assessmentId = '' } = useParams()
  const [statusView, setStatusView] = useState<AssessmentStatusView | null>(null)
  const [syncError, setSyncError] = useState('')

  useEffect(() => {
    if (!assessmentId) return

    let active = true
    let timer: number | undefined

    const refresh = async () => {
      try {
        const next = await api.getAssessment(assessmentId)
        if (!active) return
        setStatusView(next)
        setSyncError('')
        if (!TERMINAL_STATUSES.has(next.status)) {
          timer = window.setTimeout(() => void refresh(), 3000)
        }
      } catch {
        if (!active) return
        setSyncError('企业端状态同步暂时失败，系统会自动重试。')
        timer = window.setTimeout(() => void refresh(), 5000)
      }
    }

    void refresh()

    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [assessmentId])

  const status = statusView?.status
  const visible = status ? VISIBLE_STATUSES.has(status) : false
  const copy = visible && status ? STATUS_COPY[status as keyof typeof STATUS_COPY] : null

  return (
    <>
      {visible && status && copy ? (
        <aside
          className={`enterprise-interview-monitor ${statusClass(status)}`}
          aria-label="候选人面试进度"
          aria-live="polite"
        >
          <div className="enterprise-monitor-heading">
            <div>
              <span className="enterprise-monitor-eyebrow">ENTERPRISE LIVE STATUS</span>
              <strong>{copy.label}</strong>
            </div>
            <span className="enterprise-monitor-dot" aria-hidden="true" />
          </div>
          <p>{status === 'FAILED' && statusView?.error_message ? statusView.error_message : copy.detail}</p>

          {status !== 'FAILED' ? (
            <ol className="enterprise-monitor-steps" aria-label="评估进度">
              {['已冻结', '面试中', '生成报告', '已完成'].map((label, index) => {
                const step = index + 1
                const state = step < copy.step ? 'done' : step === copy.step ? 'current' : 'pending'
                return (
                  <li className={state} key={label}>
                    <span aria-hidden="true">{step}</span>
                    {label}
                  </li>
                )
              })}
            </ol>
          ) : null}

          {status === 'COMPLETE' ? (
            <a
              className="enterprise-report-link"
              href={`/assessments/${encodeURIComponent(assessmentId)}/report`}
            >
              查看评估报告
            </a>
          ) : null}
          {syncError ? <small className="enterprise-monitor-error">{syncError}</small> : null}
        </aside>
      ) : null}
      <PlanReviewPage />
    </>
  )
}

export default EnterprisePlanReviewPage
