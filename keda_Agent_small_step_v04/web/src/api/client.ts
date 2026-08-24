import type {
  AssessmentPlanView,
  AssessmentStatusView,
  CreateAssessmentResponse,
  FreezePlanResponse,
  InterviewAnswerRequest,
  InterviewSession,
  PlanOverrideSet,
  ReportViewModel,
} from './types'

const configuredApiBase = import.meta.env.VITE_API_BASE_URL || '/api'
const API_BASE_URL = configuredApiBase.replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly payload: unknown

  constructor(status: number, message: string, payload: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

function errorMessage(payload: unknown, status: number): string {
  if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === 'object' && item !== null && 'msg' in item) {
            return String((item as { msg: unknown }).msg)
          }
          return String(item)
        })
        .join('；')
    }
    if (detail !== undefined) return JSON.stringify(detail)
  }
  return `请求失败（${status}）`
}

function endpoint(path: string): string {
  return `${API_BASE_URL}/${path.replace(/^\//, '')}`
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(endpoint(path), { ...init, headers })
  const contentType = response.headers.get('content-type') || ''
  let payload: unknown = null

  if (response.status !== 204) {
    payload = contentType.includes('application/json')
      ? await response.json()
      : await response.text()
  }

  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(payload, response.status), payload)
  }

  return payload as T
}

export const api = {
  createAssessment(form: FormData) {
    return request<CreateAssessmentResponse>('/assessments', {
      method: 'POST',
      body: form,
    })
  },

  getAssessment(assessmentId: string) {
    return request<AssessmentStatusView>(`/assessments/${encodeURIComponent(assessmentId)}`)
  },

  retryAnalysis(assessmentId: string) {
    return request<CreateAssessmentResponse>(
      `/assessments/${encodeURIComponent(assessmentId)}/retry`,
      { method: 'POST' },
    )
  },

  retryAssessment(assessmentId: string) {
    return this.retryAnalysis(assessmentId)
  },

  getPlan(assessmentId: string) {
    return request<AssessmentPlanView>(`/assessments/${encodeURIComponent(assessmentId)}/plan`)
  },

  updatePlanOverrides(assessmentId: string, overrides: PlanOverrideSet) {
    return request<AssessmentPlanView>(
      `/assessments/${encodeURIComponent(assessmentId)}/plan-overrides`,
      {
        method: 'PUT',
        body: JSON.stringify(overrides),
      },
    )
  },

  freezePlan(assessmentId: string) {
    return request<FreezePlanResponse>(
      `/assessments/${encodeURIComponent(assessmentId)}/freeze`,
      { method: 'POST' },
    )
  },

  getReport(assessmentId: string) {
    return request<ReportViewModel>(`/assessments/${encodeURIComponent(assessmentId)}/report`)
  },

  getInterview(candidateToken: string) {
    return request<InterviewSession>(`/interviews/${encodeURIComponent(candidateToken)}`)
  },

  startInterview(candidateToken: string) {
    return request<InterviewSession>(
      `/interviews/${encodeURIComponent(candidateToken)}/start`,
      { method: 'POST' },
    )
  },

  submitAnswer(candidateToken: string, answer: InterviewAnswerRequest) {
    return request<InterviewSession>(
      `/interviews/${encodeURIComponent(candidateToken)}/answers`,
      {
        method: 'POST',
        body: JSON.stringify(answer),
      },
    )
  },

  getDemoAssessment() {
    return request<ReportViewModel>('/demo/assessment')
  },

  getDemoBoundaryAssessment() {
    return request<ReportViewModel>('/demo/assessment/boundary')
  },
}
