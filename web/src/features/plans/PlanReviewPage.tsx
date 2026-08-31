import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
} from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, api } from '../../api/client'
import type {
  AssessmentPlanView,
  PlanOverrideSet,
  TargetUpdate,
} from '../../api/types'
import './plan-review.css'

type Priority = 'high' | 'medium' | 'low'

interface PlanRecord {
  [key: string]: unknown
}

interface EvidenceRequirementView {
  id: string
  description: string
  planned_role_dimension_id: string | null
  requires_transfer_validation: boolean
}

interface TargetView {
  id: string
  objective: string
  target_type: string
  competency_ids: string[]
  related_claim_ids: string[]
  priority: Priority
  must_cover: boolean
  time_budget_minutes: number
  preferred_modes: string[]
  evidence_requirements: EvidenceRequirementView[]
}

interface DimensionView {
  id: string
  name: string
  description: string
  is_gating: boolean
}

interface ClaimView {
  id: string
  text: string
  status: string
}

interface CandidateProfileView {
  summary: string
  skills: string[]
  projects: string[]
  uncertainties: string[]
}

interface PlanSnapshot {
  duration_minutes: number
  max_questions: number | null
  closing_buffer_minutes: number | null
  targets: TargetView[]
  candidate_profile: CandidateProfileView
  claims: ClaimView[]
}

interface EditableTarget {
  priority: Priority
  objective: string
  time_budget_minutes: number
}

const DEFAULT_DURATIONS = [30, 45, 60]
const TRANSFER_OPTIONS = [1, 2, 3]

const MODE_LABELS: Record<string, string> = {
  foundation: '基础理解',
  project_deep_dive: '项目深挖',
  scenario: '情景验证',
  system_design: '系统设计',
  coding: '实现验证',
  follow_up: '动态追问',
}

const TARGET_TYPE_LABELS: Record<string, string> = {
  knowledge: '知识理解',
  implementation: '实现能力',
  debugging: '故障排查',
  system_design: '系统设计',
  problem_solving: '问题建模',
  experience_verification: '经历核验',
}

function asRecord(value: unknown): PlanRecord {
  return typeof value === 'object' && value !== null
    ? (value as PlanRecord)
    : {}
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function asPriority(value: unknown, fallback: Priority = 'medium'): Priority {
  return value === 'high' || value === 'medium' || value === 'low'
    ? value
    : fallback
}

function parseRequirement(value: unknown, index: number): EvidenceRequirementView {
  const item = asRecord(value)
  return {
    id: asString(item.id, `requirement_${index + 1}`),
    description: asString(item.description, '需要获得可复核的行为证据'),
    planned_role_dimension_id:
      typeof item.planned_role_dimension_id === 'string'
        ? item.planned_role_dimension_id
        : null,
    requires_transfer_validation: asBoolean(
      item.requires_transfer_validation,
    ),
  }
}

function parseTarget(value: unknown, index: number): TargetView {
  const item = asRecord(value)
  return {
    id: asString(item.id, `target_${String(index + 1).padStart(2, '0')}`),
    objective: asString(item.objective, '验证岗位相关能力'),
    target_type: asString(item.target_type, 'problem_solving'),
    competency_ids: asArray(item.competency_ids).filter(
      (id): id is string => typeof id === 'string',
    ),
    related_claim_ids: asArray(item.related_claim_ids).filter(
      (id): id is string => typeof id === 'string',
    ),
    priority: asPriority(item.priority),
    must_cover: asBoolean(item.must_cover),
    time_budget_minutes: Math.max(
      1,
      asNumber(item.time_budget_minutes, 10),
    ),
    preferred_modes: asArray(item.preferred_modes).filter(
      (mode): mode is string => typeof mode === 'string',
    ),
    evidence_requirements: asArray(item.evidence_requirements).map(
      parseRequirement,
    ),
  }
}

function parseDimensions(value: unknown): DimensionView[] {
  return asArray(asRecord(value).dimensions).map((item, index) => {
    const dimension = asRecord(item)
    return {
      id: asString(dimension.id, `dimension_${index + 1}`),
      name: asString(dimension.name, '未命名能力维度'),
      description: asString(dimension.description),
      is_gating: asBoolean(dimension.is_gating),
    }
  })
}

function parseCandidateProfile(value: unknown): CandidateProfileView {
  const profile = asRecord(value)
  return {
    summary: asString(profile.summary, '候选人画像将在分析完成后提供。'),
    skills: asArray(profile.skills).filter(
      (skill): skill is string => typeof skill === 'string',
    ),
    projects: asArray(profile.projects).filter(
      (project): project is string => typeof project === 'string',
    ),
    uncertainties: asArray(profile.uncertainties).filter(
      (item): item is string => typeof item === 'string',
    ),
  }
}

function parseClaims(value: unknown): ClaimView[] {
  return asArray(value).map((item, index) => {
    const claim = asRecord(item)
    return {
      id: asString(claim.id, `claim_${index + 1}`),
      text: asString(claim.text, '待核验声明'),
      status: asString(claim.status, '待核验'),
    }
  })
}

function parseSnapshot(value: unknown, fallback?: PlanSnapshot): PlanSnapshot {
  const snapshot = asRecord(value)
  return {
    duration_minutes: asNumber(
      snapshot.duration_minutes,
      fallback?.duration_minutes ?? 45,
    ),
    max_questions:
      typeof snapshot.max_questions === 'number'
        ? snapshot.max_questions
        : fallback?.max_questions ?? null,
    closing_buffer_minutes:
      typeof snapshot.closing_buffer_minutes === 'number'
        ? snapshot.closing_buffer_minutes
        : fallback?.closing_buffer_minutes ?? null,
    targets: asArray(snapshot.targets).map(parseTarget),
    candidate_profile: parseCandidateProfile(
      snapshot.candidate_profile ?? fallback?.candidate_profile,
    ),
    claims: parseClaims(snapshot.claims ?? fallback?.claims),
  }
}

function planSnapshots(payload: AssessmentPlanView): {
  original: PlanSnapshot
  preview: PlanSnapshot
} {
  const original = parseSnapshot(payload.original_plan)
  const preview = parseSnapshot(payload.preview_plan, original)
  return {
    original: {
      ...original,
      candidate_profile:
        original.candidate_profile.summary ||
        original.candidate_profile.skills.length > 0
          ? original.candidate_profile
          : preview.candidate_profile,
      claims: original.claims.length > 0 ? original.claims : preview.claims,
    },
    preview: {
      ...preview,
      candidate_profile:
        preview.candidate_profile.summary ||
        preview.candidate_profile.skills.length > 0
          ? preview.candidate_profile
          : original.candidate_profile,
      claims: preview.claims.length > 0 ? preview.claims : original.claims,
    },
  }
}

function parseEditableTargets(
  original: TargetView[],
  preview: TargetView[],
): Record<string, EditableTarget> {
  const originalById = new Map(original.map((target) => [target.id, target]))
  return Object.fromEntries(
    preview.map((target) => {
      const base = originalById.get(target.id) ?? target
      return [
        target.id,
        {
          priority: target.priority,
          objective: target.objective,
          time_budget_minutes: target.time_budget_minutes || base.time_budget_minutes,
        },
      ] satisfies [string, EditableTarget]
    }),
  )
}

function parseDurationOptions(guardrails: Record<string, string[]>): number[] {
  const configured = asArray(guardrails.allowed_duration_minutes)
    .map((value) => Number(value))
    .filter((value) => DEFAULT_DURATIONS.includes(value))
  return configured.length > 0 ? configured : DEFAULT_DURATIONS
}

function readError(error: unknown): string {
  if (error instanceof ApiError && error.message) return error.message
  if (error instanceof Error && error.message) return error.message
  return '计划操作失败，请检查网络后重试。'
}

function isSettingsError(message: string): boolean {
  return /时长|迁移|预算|分钟/.test(message)
}

function dimensionName(
  dimensions: DimensionView[],
  dimensionId: string | null,
): string {
  if (!dimensionId) return '待岗位维度映射'
  return dimensions.find((dimension) => dimension.id === dimensionId)?.name ?? dimensionId
}

function statusLabel(status: AssessmentPlanView['status']): string {
  if (status === 'READY') return '计划已冻结'
  if (status === 'FAILED') return '计划需要修复'
  return '尚未冻结'
}

function getInitialTransfer(payload: AssessmentPlanView): number {
  const value = payload.overrides?.minimum_transfer_validations
  return typeof value === 'number' && value >= 1 ? value : 1
}

export function PlanReviewPage() {
  const { assessmentId = '' } = useParams()
  const [payload, setPayload] = useState<AssessmentPlanView | null>(null)
  const [loadError, setLoadError] = useState('')
  const [operationError, setOperationError] = useState('')
  const [operation, setOperation] = useState<'idle' | 'saving' | 'freezing'>('idle')
  const [duration, setDuration] = useState(45)
  const [minimumTransferValidations, setMinimumTransferValidations] = useState(1)
  const [targetDrafts, setTargetDrafts] = useState<Record<string, EditableTarget>>({})
  const [candidateUrl, setCandidateUrl] = useState('')
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')

  useEffect(() => {
    let active = true
    if (!assessmentId) {
      setLoadError('缺少评估编号，无法读取计划审核。')
      return () => {
        active = false
      }
    }

    setLoadError('')
    void api
      .getPlan(assessmentId)
      .then((nextPayload) => {
        if (!active) return
        const snapshots = planSnapshots(nextPayload)
        setPayload(nextPayload)
        setDuration(snapshots.preview.duration_minutes)
        setMinimumTransferValidations(getInitialTransfer(nextPayload))
        setTargetDrafts(parseEditableTargets(snapshots.original.targets, snapshots.preview.targets))
        setCandidateUrl('')
        setCopied(false)
      })
      .catch((error: unknown) => {
        if (!active) return
        setLoadError(readError(error))
      })

    return () => {
      active = false
    }
  }, [assessmentId])

  const snapshots = useMemo(
    () => (payload ? planSnapshots(payload) : null),
    [payload],
  )
  const dimensions = useMemo(
    () => (payload ? parseDimensions(payload.role_profile) : []),
    [payload],
  )
  const durationOptions = useMemo(
    () => (payload ? parseDurationOptions(payload.guardrails) : DEFAULT_DURATIONS),
    [payload],
  )
  const frozen = Boolean(candidateUrl) || payload?.status === 'READY'
  const isBusy = operation !== 'idle'

  function setTargetDraft(
    targetId: string,
    change: Partial<EditableTarget>,
  ) {
    setTargetDrafts((current) => ({
      ...current,
      [targetId]: {
        ...(current[targetId] ?? {
          priority: 'medium',
          objective: '',
          time_budget_minutes: 10,
        }),
        ...change,
      },
    }))
    setOperationError('')
  }

  function buildOverrides(): PlanOverrideSet {
    const originalById = new Map(
      (snapshots?.original.targets ?? []).map((target) => [target.id, target]),
    )
    const updates: TargetUpdate[] = []

    for (const target of snapshots?.preview.targets ?? []) {
      const base = originalById.get(target.id) ?? target
      const draft = targetDrafts[target.id] ?? {
        priority: target.priority,
        objective: target.objective,
        time_budget_minutes: target.time_budget_minutes,
      }
      const update: TargetUpdate = { target_id: target.id }
      if (draft.priority !== base.priority) update.priority = draft.priority
      if (draft.objective.trim() !== base.objective.trim()) {
        update.objective = draft.objective.trim()
      }
      if (draft.time_budget_minutes !== base.time_budget_minutes) {
        update.time_budget_minutes = draft.time_budget_minutes
      }
      if (Object.keys(update).length > 1) updates.push(update)
    }

    return {
      duration_minutes: duration as PlanOverrideSet['duration_minutes'],
      minimum_transfer_validations: minimumTransferValidations,
      target_updates: updates,
    }
  }

  function saveChanges() {
    if (!assessmentId || !payload || isBusy || frozen) return
    setOperation('saving')
    setOperationError('')
    void api
      .updatePlanOverrides(assessmentId, buildOverrides())
      .then((nextPayload) => {
        if (nextPayload) setPayload(nextPayload)
      })
      .catch((error: unknown) => {
        setOperationError(readError(error))
      })
      .finally(() => setOperation('idle'))
  }

  function freezePlan() {
    if (!assessmentId || !payload || isBusy || frozen) return
    setOperation('freezing')
    setOperationError('')
    // Freeze the server-side preview. Edits are persisted explicitly through
    // “保存计划修改” so this action has one deterministic API contract.
    void api
      .freezePlan(assessmentId)
      .then((result) => {
        setCandidateUrl(result.candidate_url)
        setCopied(false)
      })
      .catch((error: unknown) => {
        setOperationError(readError(error))
      })
      .finally(() => setOperation('idle'))
  }

  async function copyCandidateUrl() {
    if (!candidateUrl) return
    setCopyError('')
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(candidateUrl)
      } else {
        const input = document.createElement('textarea')
        input.value = candidateUrl
        input.setAttribute('readonly', '')
        input.style.position = 'fixed'
        input.style.opacity = '0'
        document.body.appendChild(input)
        input.select()
        document.execCommand('copy')
        document.body.removeChild(input)
      }
      setCopied(true)
    } catch {
      setCopyError('复制失败，请直接选择并复制链接。')
    }
  }

  function renderOperationFeedback() {
    if (operation === 'saving') return '正在保存计划修改…'
    if (operation === 'freezing') return '正在运行计划护栏并冻结…'
    if (copied) return '候选人链接已复制。'
    return ''
  }

  if (loadError) {
    return (
      <section className="assessment-page plan-review-page" aria-labelledby="plan-review-title">
        <div className="assessment-page-heading">
          <div>
            <p className="eyebrow">PLAN REVIEW / 03</p>
            <h1 id="plan-review-title">调整本次验证计划</h1>
          </div>
        </div>
        <div className="plan-feedback plan-feedback-error" role="alert" aria-live="assertive">
          {loadError}
        </div>
      </section>
    )
  }

  if (!payload || !snapshots) {
    return (
      <section className="assessment-page plan-review-page" aria-labelledby="plan-review-title">
        <div className="assessment-page-heading">
          <div>
            <p className="eyebrow">PLAN REVIEW / 03</p>
            <h1 id="plan-review-title">调整本次验证计划</h1>
            <p className="page-lead">企业审核验证目标、证据要求与面试预算后，计划才会进入冻结。</p>
          </div>
        </div>
        <p className="plan-feedback plan-feedback-loading" role="status" aria-live="polite">
          正在读取计划审核…
        </p>
      </section>
    )
  }

  const { original, preview } = snapshots
  const candidateProfile = preview.candidate_profile
  const claims = preview.claims
  const errorInSettings = operationError && isSettingsError(operationError)
  const errorInTargets = operationError && !errorInSettings

  return (
    <section
      className="assessment-page plan-review-page"
      aria-labelledby="plan-review-title"
      aria-busy={isBusy}
    >
      <div className="assessment-page-heading plan-review-heading">
        <div>
          <p className="eyebrow">PLAN REVIEW / EDIT MODE · 03</p>
          <h1 id="plan-review-title">调整本次验证计划</h1>
          <p className="page-lead">
            企业可以表达真实用人重点，但不能删除岗位的基础公平线。原始 Planner 结果与本次修改都会保留。
          </p>
        </div>
        <div className={`plan-state ${frozen ? 'is-frozen' : ''}`} aria-label="计划冻结状态">
          <span>ASSESSMENT / {assessmentId || 'UNKNOWN'}</span>
          <strong>{statusLabel(frozen ? 'READY' : payload.status)}</strong>
          <small>Role Pack · AI Application Engineering / 2026-H2</small>
        </div>
      </div>

      <div className="assessment-rule" aria-hidden="true" />

      <section className="plan-responsibilities" aria-labelledby="responsibilities-title">
        <div className="plan-section-heading">
          <div>
            <p className="eyebrow">RESPONSIBILITY LAYERS</p>
            <h2 id="responsibilities-title">三层职责与证据边界</h2>
          </div>
          <span>决定什么可改、什么必须保留</span>
        </div>
        <div className="responsibility-grid">
          <article className="responsibility-card">
            <span className="responsibility-index">01 / 岗位职责</span>
            <strong>Role Pack 标准</strong>
            <p>岗位核心与 Gating 能力由已校准的岗位标准定义，不随企业编辑改变。</p>
          </article>
          <article className="responsibility-card">
            <span className="responsibility-index">02 / 企业关注</span>
            <strong>验证目标与业务场景</strong>
            <p>企业可以调整优先级、业务关注点和预算，让本次面试贴近真实用人语境。</p>
          </article>
          <article className="responsibility-card">
            <span className="responsibility-index">03 / 系统核验</span>
            <strong>Evidence Requirement</strong>
            <p>每条证据要求保留维度映射、迁移验证意图与可追溯边界，不提前固定题目文本。</p>
          </article>
        </div>
      </section>

      <section className="plan-context-grid" aria-label="候选人与岗位上下文">
        <article className="context-panel">
          <div className="plan-section-heading compact">
            <div>
              <p className="eyebrow">CANDIDATE SIGNAL</p>
              <h2>候选人画像</h2>
            </div>
          </div>
          <p className="context-summary">{candidateProfile.summary}</p>
          {candidateProfile.skills.length > 0 ? (
            <div className="signal-list" aria-label="候选人技能">
              {candidateProfile.skills.map((skill) => <span key={skill}>{skill}</span>)}
            </div>
          ) : null}
          {candidateProfile.uncertainties.length > 0 ? (
            <p className="context-note">待核验：{candidateProfile.uncertainties.join('、')}</p>
          ) : null}
        </article>
        <article className="context-panel">
          <div className="plan-section-heading compact">
            <div>
              <p className="eyebrow">CLAIM REGISTER</p>
              <h2>待核验 Claim</h2>
            </div>
            <span>{claims.length} 条</span>
          </div>
          {claims.length > 0 ? (
            <ul className="claim-list">
              {claims.map((claim) => (
                <li key={claim.id}>
                  <span className="claim-id">{claim.id}</span>
                  <span>{claim.text}</span>
                  <small>{claim.status}</small>
                </li>
              ))}
            </ul>
          ) : <p className="empty-copy">当前计划没有额外声明，面试仍会按证据要求核验。</p>}
        </article>
        <article className="context-panel dimension-panel">
          <div className="plan-section-heading compact">
            <div>
              <p className="eyebrow">ROLE PACK / DIMENSIONS</p>
              <h2>岗位能力维度</h2>
            </div>
            <span>{dimensions.length} 个标准维度</span>
          </div>
          {dimensions.length > 0 ? (
            <ul className="dimension-list">
              {dimensions.map((dimension) => (
                <li key={dimension.id}>
                  <span className="dimension-marker" aria-hidden="true" />
                  <span>
                    <strong>{dimension.name}</strong>
                    <small>{dimension.description || dimension.id}</small>
                  </span>
                  {dimension.is_gating ? <em>GATING</em> : null}
                </li>
              ))}
            </ul>
          ) : <p className="empty-copy">能力维度由服务端 Role Pack 返回。</p>}
        </article>
      </section>

      <div className="plan-layout">
        <section className="targets-panel" aria-labelledby="targets-title">
          <div className="plan-section-heading">
            <div>
              <p className="eyebrow">INTERVIEW TARGETS</p>
              <h2 id="targets-title">验证目标</h2>
            </div>
            <span>{preview.targets.length} 个目标 · {preview.max_questions ?? '动态'} 题以内</span>
          </div>

          {errorInTargets ? (
            <div className="plan-feedback plan-feedback-error" role="alert" aria-live="assertive">
              {operationError}
            </div>
          ) : null}

          <div className="target-list">
            {preview.targets.map((target, index) => {
              const draft = targetDrafts[target.id] ?? {
                priority: target.priority,
                objective: target.objective,
                time_budget_minutes: target.time_budget_minutes,
              }
              const linkedClaims = claims.filter((claim) => target.related_claim_ids.includes(claim.id))
              return (
                <article className={`target-card ${target.must_cover ? 'is-locked' : ''}`} key={target.id}>
                  <div className="target-number" aria-hidden="true">
                    {String(index + 1).padStart(2, '0')}
                  </div>
                  <div className="target-copy">
                    <div className="target-title-row">
                      <div>
                        <h3>{target.objective}</h3>
                        <p className="target-type">{TARGET_TYPE_LABELS[target.target_type] ?? target.target_type}</p>
                      </div>
                      <span className="target-id">{target.id}</span>
                    </div>
                    <div className="target-tags">
                      <span className={target.must_cover ? 'tag tag-locked' : 'tag tag-custom'}>
                        {target.must_cover ? '岗位核心 · 不可删除' : '企业补充目标'}
                      </span>
                      {target.must_cover && target.priority === 'high' ? <span className="tag tag-locked">Gating / 必须覆盖</span> : null}
                      {target.competency_ids.map((id) => <span className="tag" key={id}>能力 {id}</span>)}
                      {target.preferred_modes.map((mode) => (
                        <span className="tag tag-mode" key={mode}>
                          验证方式 · {MODE_LABELS[mode] ?? mode}
                        </span>
                      ))}
                    </div>

                    <div className="target-evidence" aria-label={`${target.id} 证据要求`}>
                      <div className="target-subheading">
                        <strong>Evidence Requirements</strong>
                        <span>{target.evidence_requirements.length} 条证据要求</span>
                      </div>
                      {target.evidence_requirements.length > 0 ? (
                        <ul>
                          {target.evidence_requirements.map((requirement) => (
                            <li key={requirement.id}>
                              <span className="requirement-id">{requirement.id}</span>
                              <span>{requirement.description}</span>
                              <span className="requirement-meta">
                                {dimensionName(dimensions, requirement.planned_role_dimension_id)}
                                {requirement.requires_transfer_validation ? ' · 迁移验证' : ''}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : <p className="empty-copy">该目标尚未返回证据要求。</p>}
                    </div>

                    {linkedClaims.length > 0 ? (
                      <p className="linked-claims">关联 Claim：{linkedClaims.map((claim) => claim.id).join('、')}</p>
                    ) : null}

                    {!frozen ? (
                      <div className="target-edit-fields">
                        <div>
                          <label htmlFor={`objective-${target.id}`} className="field-label">
                            业务关注点{preview.targets.length > 1 ? ` · ${target.id}` : ''}
                          </label>
                          <textarea
                            id={`objective-${target.id}`}
                            aria-label={preview.targets.length > 1 ? `业务关注点 · ${target.id}` : '业务关注点'}
                            value={draft.objective}
                            rows={3}
                            onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
                              setTargetDraft(target.id, { objective: event.target.value })}
                          />
                        </div>
                        <div className="target-number-field">
                          <label htmlFor={`budget-${target.id}`} className="field-label">目标预算（分钟）</label>
                          <input
                            id={`budget-${target.id}`}
                            aria-label={`${target.id} 目标预算（分钟）`}
                            type="number"
                            min={1}
                            value={draft.time_budget_minutes}
                            onChange={(event) => setTargetDraft(target.id, {
                              time_budget_minutes: Math.max(1, Number(event.target.value) || 1),
                            })}
                          />
                        </div>
                      </div>
                    ) : null}
                  </div>

                  <div className="target-controls">
                    <label htmlFor={`priority-${target.id}`} className="field-label">优先级</label>
                    <select
                      id={`priority-${target.id}`}
                      aria-label={`${target.id} 优先级`}
                      value={draft.priority}
                      disabled={target.must_cover || frozen}
                      onChange={(event) => setTargetDraft(target.id, {
                        priority: asPriority(event.target.value, draft.priority),
                      })}
                    >
                      <option value="high">高</option>
                      <option value="medium">中</option>
                      <option value="low">低</option>
                    </select>
                    <span className="control-hint">
                      {target.must_cover ? '核心目标保持高优先级' : '可调整本次关注顺序'}
                    </span>
                  </div>
                </article>
              )
            })}
          </div>
        </section>

        <aside className="guardrails-panel" aria-labelledby="guardrails-title">
          <div className="plan-section-heading">
            <div>
              <p className="eyebrow">DETERMINISTIC CONTROL</p>
              <h2 id="guardrails-title">编辑规则</h2>
            </div>
          </div>
          <div className="guardrail-rule">
            <strong>可以修改</strong>
            <p>目标优先级、业务关注点、目标预算、面试时长与最低迁移验证次数。</p>
          </div>
          <div className="guardrail-rule">
            <strong>不能修改</strong>
            <p>Role Pack 能力维度、核心 / Gating 目标、Evidence Requirement、Rubric 规则和评分权重。</p>
          </div>
          <div className="guardrail-rule">
            <strong>企业补充边界</strong>
            <p>补充目标必须映射到现有能力维度；不能映射的关注点只进入补充观察，不改变雷达分数。</p>
          </div>

          <div className="plan-settings" aria-label="面试设置">
            <h3>面试设置</h3>
            <label htmlFor="duration-select">目标时长</label>
            <select
              id="duration-select"
              aria-label="目标时长"
              value={duration}
              disabled={frozen || isBusy}
              onChange={(event) => {
                setDuration(Number(event.target.value))
                setOperationError('')
              }}
            >
              {durationOptions.map((value) => <option key={value} value={value}>{value} 分钟</option>)}
            </select>
            <label htmlFor="transfer-select">至少迁移验证</label>
            <select
              id="transfer-select"
              aria-label="至少迁移验证"
              value={minimumTransferValidations}
              disabled={frozen || isBusy}
              onChange={(event) => {
                setMinimumTransferValidations(Number(event.target.value))
                setOperationError('')
              }}
            >
              {TRANSFER_OPTIONS.map((value) => <option key={value} value={value}>{value} 次</option>)}
            </select>
          </div>

          {errorInSettings ? (
            <div className="plan-feedback plan-feedback-error" role="alert" aria-live="assertive">
              {operationError}
            </div>
          ) : null}

          <div className="audit-note">
            计划确认后，服务端会重新运行确定性计划护栏并校验 Scoring Blueprint，再共同冻结本次版本。候选人开始后不允许修改。
          </div>
        </aside>
      </div>

      <div className="plan-footer">
        <div>
          <p>具体题目由 QuestionGenerator 在面试过程中根据当前 Evidence 动态生成。</p>
          <p className="plan-audit-line">原始计划：{original.targets.length} 个目标 · 当前版本：{preview.targets.length} 个目标 · 版本由服务端保存。</p>
          {renderOperationFeedback() ? (
            <p className="plan-live-message" role="status" aria-live="polite">{renderOperationFeedback()}</p>
          ) : null}
          {copyError ? <p className="plan-feedback plan-feedback-error" role="alert">{copyError}</p> : null}
        </div>
        <div className="plan-actions">
          {!frozen ? (
            <>
              <button className="secondary-action" type="button" onClick={() => void saveChanges()} disabled={isBusy}>
                {operation === 'saving' ? '保存中…' : '保存计划修改'}
              </button>
              <button className="primary-action plan-freeze-action" type="button" onClick={() => void freezePlan()} disabled={isBusy}>
                {operation === 'freezing' ? '冻结中…' : '校验并冻结计划'}
              </button>
            </>
          ) : null}
        </div>
      </div>

      {candidateUrl ? (
        <section className="candidate-link-panel" aria-labelledby="candidate-link-title">
          <div>
            <p className="eyebrow">CANDIDATE ACCESS / ONE-TIME URL</p>
            <h2 id="candidate-link-title">候选人面试链接已生成</h2>
            <p>链接只在本次冻结响应中返回；请复制并安全发送给候选人。</p>
          </div>
          <div className="candidate-link-controls">
            <label htmlFor="candidate-url" className="sr-only">候选人链接</label>
            <input id="candidate-url" aria-label="候选人链接" value={candidateUrl} readOnly />
            <div>
              <a href={candidateUrl} target="_blank" rel="noreferrer" className="secondary-action candidate-open-link">打开候选人链接</a>
              <button type="button" className="secondary-action" onClick={() => void copyCandidateUrl()}>复制链接</button>
            </div>
          </div>
        </section>
      ) : null}
    </section>
  )
}

export default PlanReviewPage
