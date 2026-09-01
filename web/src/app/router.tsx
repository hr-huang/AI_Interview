import { Navigate, Route, Routes } from 'react-router-dom'
import { AnalyzingPage } from '../features/assessments/AnalyzingPage'
import { NewAssessmentPage } from '../features/assessments/NewAssessmentPage'
import { InterviewPage } from '../features/interview/InterviewPage'
import { EnterprisePlanReviewPage } from '../features/plans/EnterprisePlanReviewPage'
import { DemoReportPage } from '../features/report/DemoReportPage'
import { ReportPage } from '../features/report/ReportPage'

export function AppRouter() {
  return (
    <Routes>
      <Route path="/assessments/new" element={<NewAssessmentPage />} />
      <Route path="/assessments/:assessmentId/analyzing" element={<AnalyzingPage />} />
      <Route path="/assessments/:assessmentId/plan" element={<EnterprisePlanReviewPage />} />
      <Route path="/assessments/:assessmentId/report" element={<ReportPage />} />
      <Route path="/interviews/:candidateToken" element={<InterviewPage />} />
      <Route path="/demo/assessment" element={<DemoReportPage />} />
      <Route path="*" element={<Navigate replace to="/assessments/new" />} />
    </Routes>
  )
}
