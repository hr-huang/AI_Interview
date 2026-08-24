import { Navigate, Route, Routes, useParams } from 'react-router-dom'

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

function NewAssessmentPage() {
  return (
    <RouteFrame
      eyebrow="新建评估 / 01"
      title="创建岗位胜任力评估"
      detail="从岗位要求与候选人材料出发，建立一条可审计的验证路径。"
    />
  )
}

function AnalyzingPage() {
  const { assessmentId } = useParams()
  return (
    <RouteFrame
      eyebrow="材料分析 / 02"
      title="正在分析评估材料"
      detail={`评估 ${assessmentId ?? '待识别'} 的岗位理解与能力建模正在准备中。`}
    />
  )
}

function PlanReviewPage() {
  const { assessmentId } = useParams()
  return (
    <RouteFrame
      eyebrow="计划审核 / 03"
      title="审核评估计划"
      detail={`评估 ${assessmentId ?? '待识别'} 的验证目标、方式与预算将在此处共同冻结。`}
    />
  )
}

function ReportPage() {
  const { assessmentId } = useParams()
  return (
    <RouteFrame
      eyebrow="评估报告 / 05"
      title="岗位胜任力报告"
      detail={`评估 ${assessmentId ?? '待识别'} 的证据链与决策摘要。`}
    />
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

function DemoReportPage() {
  return (
    <RouteFrame
      eyebrow="离线演示 / 只读"
      title="演示评估报告"
      detail="这是固定校准案例，仅用于查看报告结构，不调用模型。"
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
