export type AssessmentStatus =
  | 'DRAFT'
  | 'ANALYZING'
  | 'PLAN_REVIEW'
  | 'READY'
  | 'IN_PROGRESS'
  | 'REPORTING'
  | 'COMPLETE'
  | 'FAILED'

export type JsonObject = Record<string, unknown>

export interface CreateAssessmentResponse {
  assessment_id: string
  status: AssessmentStatus
}

export interface AssessmentStatusView {
  assessment_id: string
  status: AssessmentStatus
  target_role: string
  retryable: boolean
  failed_stage: string | null
  error_message: string | null
  has_plan: boolean
  has_final_plan: boolean
}

export interface TargetUpdate {
  target_id: string
  priority?: 'high' | 'medium' | 'low' | null
  objective?: string | null
  time_budget_minutes?: number | null
}

export interface PlanOverrideSet {
  duration_minutes?: 30 | 45 | 60 | null
  minimum_transfer_validations?: number
  target_updates?: TargetUpdate[]
  custom_targets?: JsonObject[]
}

export interface AssessmentPlanView {
  assessment_id: string
  status: AssessmentStatus
  original_plan: JsonObject
  preview_plan: JsonObject
  overrides: PlanOverrideSet | null
  role_profile: JsonObject
  guardrails: Record<string, string[]>
}

export interface FreezePlanResponse {
  assessment_id: string
  status: 'READY'
  candidate_url: string
}

export interface EvidenceSourceView {
  evidence_id: string
  turn_id: string
  question: string
  answer: string
  observation: string
  source_excerpt: string
}

export interface ReasonView {
  reason_type: string
  text: string
  evidence_ids: string[]
  rubric_signal_ids: string[]
  sources: EvidenceSourceView[]
}

export interface RadarDimensionView {
  dimension_id: string
  name: string
  score: number | null
  level: string
  coverage: number
  confidence: string
  reasons: ReasonView[]
}

export interface JobMatchView extends JsonObject {
  raw_score?: number | null
  published?: boolean
  fit_level?: string | null
  coverage?: number
  confidence?: string
  limiting_reasons?: JsonObject[]
}

export interface InterviewPathView extends JsonObject {
  turn_id: string
  question_mode: string
  requirement_id: string
  outcome: string
  evidence_ids: string[]
}

export interface InterviewTranscriptTurnView {
  turn_id: string
  sequence_number: number
  question: string
  answer: string | null
  question_mode: string
  requirement_id: string
  requirement_label: string
  asked_at: string
  answered_at: string | null
  evidence_ids: string[]
  evidence_status: 'supporting' | 'limiting' | 'mixed' | 'none'
}

export interface ClaimVerificationView extends JsonObject {
  claim_id?: string
  status?: string
  outcome?: string
  supporting_evidence_ids?: string[]
  contradicting_evidence_ids?: string[]
  evidence_ids?: string[]
}

export interface ReportViewModel {
  demo: boolean
  target_role: string
  role_profile_version: string
  scoring_engine_version: string
  job_match: JobMatchView
  radar_dimensions: RadarDimensionView[]
  narrative: JsonObject
  interview_path: InterviewPathView[]
  interview_transcript: InterviewTranscriptTurnView[]
  claim_verifications: ClaimVerificationView[]
  assessment_limitations: string[]
  demo_variant?: 'showcase' | 'boundary' | string
  demo_case_title?: string | null
  demo_case_description?: string | null
}

export interface InterviewAnswerRequest {
  turn_id: string
  answer: string
  idempotency_key: string
}

export type InterviewSession = {
  state: 'ready' | 'waiting' | 'waiting_for_answer' | 'in_progress' | 'reporting' | 'complete'
  target_role?: string
  [key: string]: unknown
}
