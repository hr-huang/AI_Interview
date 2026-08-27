import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { ReportViewModel } from '../../api/types'
import { ReportView } from './ReportPage'

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '演示示例暂时无法读取，请稍后重试。'
}

export function DemoReportPage() {
  const [report, setReport] = useState<ReportViewModel | null>(null)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoadError('')
    void api.getDemoAssessment()
      .then((payload) => {
        if (!cancelled) setReport(payload)
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(errorMessage(error))
      })
    return () => { cancelled = true }
  }, [])

  if (loadError) {
    return (
      <section className="report-page report-state-page" aria-labelledby="demo-report-error-title">
        <p className="eyebrow">DEMO / READ ONLY</p>
        <h1 id="demo-report-error-title">演示示例暂时不可用</h1>
        <p className="report-state-copy" role="alert" aria-live="assertive">{loadError}</p>
      </section>
    )
  }

  if (!report) {
    return (
      <section className="report-page report-state-page" aria-labelledby="demo-report-loading-title">
        <p className="eyebrow">DEMO / FROZEN DATA</p>
        <h1 id="demo-report-loading-title">正在读取学生候选人演示</h1>
        <p className="report-state-copy" role="status" aria-live="polite">正在读取冻结演示数据，请稍候…</p>
      </section>
    )
  }

  return (
    <div className="demo-report-route">
      <aside className="demo-case-switcher" aria-label="演示说明">
        <div>
          <p className="eyebrow">演示示例 · 零 API 费用</p>
          <strong>学生候选人完整评估</strong>
          <span>展示从简历画像、动态追问到岗位胜任力报告的完整结果；数据已冻结，不调用模型。</span>
        </div>
        <span className="demo-case-badge">六维能力 · 真实问答 · 可追溯证据</span>
      </aside>
      <ReportView report={report} demo />
    </div>
  )
}

export default DemoReportPage
