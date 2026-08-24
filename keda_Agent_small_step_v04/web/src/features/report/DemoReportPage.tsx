import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { ReportViewModel } from '../../api/types'
import { ReportView } from './ReportPage'

type DemoCase = 'showcase' | 'boundary'

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '演示示例暂时无法读取，请稍后重试。'
}

export function DemoReportPage() {
  const [activeCase, setActiveCase] = useState<DemoCase>('showcase')
  const [reports, setReports] = useState<Record<DemoCase, ReportViewModel | null>>({
    showcase: null,
    boundary: null,
  })
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoadError('')
    if (reports[activeCase]) return () => { cancelled = true }

    const request = activeCase === 'showcase'
      ? api.getDemoAssessment()
      : api.getDemoBoundaryAssessment()
    void request
      .then((payload) => {
        if (!cancelled) {
          setReports((current) => ({ ...current, [activeCase]: payload }))
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(errorMessage(error))
      })
    return () => { cancelled = true }
  }, [activeCase, reports])

  const report = reports[activeCase]

  function switchCase(nextCase: DemoCase) {
    if (nextCase === activeCase) return
    setActiveCase(nextCase)
  }

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
        <h1 id="demo-report-loading-title">正在读取{activeCase === 'showcase' ? '企业完整演示' : '评分边界案例'}</h1>
        <p className="report-state-copy" role="status" aria-live="polite">正在读取冻结演示数据，请稍候…</p>
      </section>
    )
  }

  return (
    <div className="demo-report-route">
      <nav className="demo-case-switcher" aria-label="选择冻结演示案例">
        <div>
          <p className="eyebrow">FROZEN DEMO / NO API COST</p>
          <strong>选择要查看的演示</strong>
          <span>两组数据都不调用模型；主演示用于路演，边界案例用于解释评分。</span>
        </div>
        <div className="demo-case-tabs" role="tablist" aria-label="演示案例">
          <button
            className={activeCase === 'showcase' ? 'is-active' : ''}
            type="button"
            role="tab"
            aria-selected={activeCase === 'showcase'}
            onClick={() => switchCase('showcase')}
          >
            <span>企业完整演示</span>
            <small>六维 · 可发布结论</small>
          </button>
          <button
            className={activeCase === 'boundary' ? 'is-active' : ''}
            type="button"
            role="tab"
            aria-selected={activeCase === 'boundary'}
            onClick={() => switchCase('boundary')}
          >
            <span>评分边界案例</span>
            <small>C03 · 解释限制性证据</small>
          </button>
        </div>
      </nav>
      <ReportView report={report} demo />
    </div>
  )
}

export default DemoReportPage
