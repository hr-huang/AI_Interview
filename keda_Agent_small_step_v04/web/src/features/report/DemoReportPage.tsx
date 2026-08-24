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
        <p className="eyebrow">DEMO / READ ONLY</p>
        <h1 id="demo-report-loading-title">正在读取演示示例</h1>
        <p className="report-state-copy" role="status" aria-live="polite">正在读取演示示例，请稍候…</p>
      </section>
    )
  }

  return <ReportView report={report} demo />
}

export default DemoReportPage
