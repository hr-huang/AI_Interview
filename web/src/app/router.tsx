import { Navigate, Route, Routes } from 'react-router-dom'
import { AnalyzingPage } from '../features/assessments/AnalyzingPage'
import { NewAssessmentPage } from '../features/assessments/NewAssessmentPage'
import { PlanReviewPage } from '../features/plans/PlanReviewPage'
import { DemoReportPage } from '../features/report/DemoReportPage'
import { ReportPage } from '../features/report/ReportPage'

function RouteFrame({
  eyebrow,
  title,
  detail,
}: {
  eyebrow: string
  title: string
  detail: string
}) {
  return (
    <section className="route-frame" aria-labelledby="route-heading">
      <p className="eyebrow">{eyebrow}</p>
      <h1 id="route-heading">{title}</h1>
      <p className="route-detail">{detail}</p>
    </section>
  )
}

function InterviewPage() {
  return (
    <RouteFrame
      eyebrow="候选人面试 / 04"
      title="候选人动态面试"
      detail="问题会根据当前回答动态推进；候选人只看到当前阶段所需信息。"
    />
  )
}

export function AppRouter() {
  return (
    <Routes>
      <Route path="/assessments/new" element={<NewAssessmentPage />} />
      <Route path="/assessments/:assessmentId/analyzing" element={<AnalyzingPage />} />
      <Route path="/assessments/:assessmentId/plan" element={<PlanReviewPage />} />
      <Route path="/assessments/:assessmentId/report" element={<ReportPage />} />
      <Route path="/interviews/:candidateToken" element={<InterviewPage />} />
      <Route path="/demo/assessment" element={<DemoReportPage />} />
      <Route path="*" element={<Navigate replace to="/assessments/new" />} />
    </Routes>
  )
}
