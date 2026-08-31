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

export interface EvidenceExcerptView {
  turn_id: string
  conclusion: string
  quote: string
  interpretation: string
  limitation: string
}

export interface ReasonView {
  reason_type: string
  text: string
  sources: EvidenceExcerptView[]
}

export interface RadarDimensionView {
  name: string
  score: number | null
  level: string
  coverage: number
  confidence: string
  reasons: ReasonView[]
}

export interface CandidateOverviewView {
  candidate_name: string | null
  target_role: string
  education_summary: string | null
  experience_summary: string | null
  jd_focus: string[]
  interview_rounds: number
  generated_at: string
}

export interface DecisionSignalView {
  title: string
  text: string
  dimension_names: string[]
  confidence: string
}

export interface ReinterviewFocusView {
  priority: number
  dimension_name: string
  reason: string
  question: string
  follow_ups: string[]
  positive_signals: string[]
  risk_signals: string[]
  pass_criteria: string[]
  suggested_minutes: number
}

export interface EnterpriseAssessmentView {
  decision: string
  decision_label: string
  provisional_score: number | null
  confidence: string
  conditions: string[]
  decision_reasons: string[]
  overall_assessment: string
  strengths: DecisionSignalView[]
  risks: DecisionSignalView[]
  unknowns: DecisionSignalView[]
  reinterview_plan: ReinterviewFocusView[]
  evidence_excerpts: EvidenceExcerptView[]
}

export interface JobMatchView {
  raw_score: number | null
  published: boolean
  fit_level: string | null
  coverage: number
  confidence: string
  limiting_reasons: ReasonView[]
}

export interface InterviewPathView {
  turn_id: string
  question_mode: string
  outcome: string
}

export interface InterviewTranscriptTurnView {
  turn_id: string
  sequence_number: number
  question: string
  answer: string | null
  question_mode: string
  requirement_label: string
  asked_at: string
  answered_at: string | null
  evidence_status: 'supporting' | 'limiting' | 'mixed' | 'none'
  evidence_cta: string
}

export interface ClaimVerificationView {
  status: string
  explanation: string
}

export interface NarrativeItemView {
  text: string
  dimension_names: string[]
}

export interface NarrativeView {
  executive_summary: string
  strengths: NarrativeItemView[]
  risks: NarrativeItemView[]
  unverified_areas: NarrativeItemView[]
  fit_contexts: NarrativeItemView[]
}

export interface ReportViewModel {
  demo: boolean
  target_role: string
  role_profile_version: string
  scoring_engine_version: string
  candidate_overview: CandidateOverviewView
  enterprise_assessment: EnterpriseAssessmentView
  job_match: JobMatchView
  radar_dimensions: RadarDimensionView[]
  narrative: NarrativeView
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

export interface InterviewTurnView {
  id: string
  sequence_number: number
  question: string
  answer: string | null
}

export interface InterviewSession {
  state: 'ready' | 'waiting_for_answer' | 'reporting' | 'complete'
  target_role?: string
  phase?: 'waiting' | 'question'
  elapsed_seconds?: number
  turns?: InterviewTurnView[]
  turn?: InterviewTurnView
}
