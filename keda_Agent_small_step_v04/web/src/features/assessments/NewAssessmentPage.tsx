import { useId, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import './assessment.css'

export const SUPPORTED_ROLE = 'AI Agent应用工程师（校招/初级）'
export const MAX_RESUME_BYTES = 5 * 1024 * 1024

const SAMPLE_JD = `AI Agent 应用工程师（校招/初级）｜2026-H2

岗位职责
负责 Agent Workflow、Context/Memory 状态管理、MCP 或 Skills 工具 Schema 的应用设计与迭代；参与从需求拆解到评测上线的完整链路。

岗位要求
熟悉 Python 或 TypeScript，理解大模型应用开发、RAG、函数调用和基础服务工程；能够用评测集、Trace/Metric/Log 定位质量问题，设计故障恢复路径，并以可复现验收记录结果。

加分项
有 LangGraph、工作流编排、AI 协作审查、工具安全边界或生产系统稳定性实践。`

const ALLOWED_EXTENSIONS = new Set(['pdf', 'docx', 'txt'])

type ResumeSource = 'text' | 'file'

function makeIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `assessment-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function submissionFingerprint({
  jdText,
  resumeSource,
  resumeText,
  resumeFile,
}: {
  jdText: string
  resumeSource: ResumeSource
  resumeText: string
  resumeFile: File | null
}): string {
  return JSON.stringify({
    targetRole: SUPPORTED_ROLE,
    interviewDurationMinutes: 45,
    jdText,
    resumeSource,
    resumeText: resumeSource === 'text' ? resumeText : '',
    resumeFile:
      resumeSource === 'file' && resumeFile
        ? {
            name: resumeFile.name,
            size: resumeFile.size,
            type: resumeFile.type,
            lastModified: resumeFile.lastModified,
            relativePath: resumeFile.webkitRelativePath,
          }
        : null,
  })
}

function extensionOf(file: File): string {
  return file.name.split('.').pop()?.toLowerCase() ?? ''
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '评估材料提交失败，请检查网络后重试。'
}

export function NewAssessmentPage() {
  const navigate = useNavigate()
  const roleId = useId()
  const jdId = useId()
  const resumeTextId = useId()
  const resumeFileId = useId()

  const [jdText, setJdText] = useState('')
  const [resumeSource, setResumeSource] = useState<ResumeSource>('text')
  const [resumeText, setResumeText] = useState('')
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [fileInputVersion, setFileInputVersion] = useState(0)
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [submitError, setSubmitError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const idempotencyKeyRef = useRef<{ fingerprint: string; key: string } | null>(null)

  function switchResumeSource(source: ResumeSource) {
    setResumeSource(source)
    setValidationErrors([])
    if (source === 'text') {
      setResumeFile(null)
      setFileInputVersion((version) => version + 1)
    } else {
      setResumeText('')
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null
    if (!file) {
      setResumeFile(null)
      return
    }

    if (file.size > MAX_RESUME_BYTES) {
      event.target.value = ''
      setResumeFile(null)
      setValidationErrors(['简历文件不能超过 5 MiB。'])
      return
    }

    if (!ALLOWED_EXTENSIONS.has(extensionOf(file))) {
      event.target.value = ''
      setResumeFile(null)
      setValidationErrors(['简历文件仅支持 PDF、DOCX 或 TXT。'])
      return
    }

    setValidationErrors([])
    setResumeFile(file)
  }

  function validate(): string[] {
    const errors: string[] = []
    if (!SUPPORTED_ROLE.trim()) errors.push('请选择支持的岗位族。')
    if (!jdText.trim()) errors.push('请填写岗位描述 JD。')

    if (resumeSource === 'text' && !resumeText.trim()) {
      errors.push('请粘贴简历文本，或切换为上传简历文件。')
    }
    if (resumeSource === 'file' && !resumeFile) {
      errors.push('请选择一份 PDF、DOCX 或 TXT 简历文件。')
    }
    if (resumeFile && resumeFile.size > MAX_RESUME_BYTES) {
      errors.push('简历文件不能超过 5 MiB。')
    }
    return errors
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitError('')
    const errors = validate()
    setValidationErrors(errors)
    if (errors.length > 0) return

    const normalizedJdText = jdText.trim()
    const normalizedResumeText = resumeText.trim()
    const fingerprint = submissionFingerprint({
      jdText: normalizedJdText,
      resumeSource,
      resumeText: normalizedResumeText,
      resumeFile,
    })
    const cachedKey = idempotencyKeyRef.current
    const idempotencyKey =
      cachedKey?.fingerprint === fingerprint
        ? cachedKey.key
        : makeIdempotencyKey()
    idempotencyKeyRef.current = { fingerprint, key: idempotencyKey }

    const form = new FormData()
    form.set('target_role', SUPPORTED_ROLE)
    form.set('jd_text', normalizedJdText)
    if (resumeSource === 'text') {
      form.set('resume_text', normalizedResumeText)
    } else if (resumeFile) {
      form.set('resume_file', resumeFile)
    }
    form.set('idempotency_key', idempotencyKey)
    form.set('interview_duration_minutes', '45')

    setIsSubmitting(true)
    try {
      const result = await api.createAssessment(form)
      navigate(`/assessments/${encodeURIComponent(result.assessment_id)}/analyzing`)
    } catch (error) {
      setSubmitError(errorMessage(error))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <section className="assessment-page new-assessment-page" aria-labelledby="new-assessment-title">
      <div className="assessment-page-heading">
        <div>
          <p className="eyebrow">评估准备 / 01</p>
          <h1 id="new-assessment-title">创建岗位胜任力评估</h1>
          <p className="page-lead">
            从岗位要求与候选人材料出发，建立一条可审计、可复核的验证路径。
          </p>
        </div>
        <div className="audit-stamp" aria-label="评估输入审计信息">
          <span>岗位标准</span>
          <strong>AI Agent应用工程师（校招/初级）</strong>
          <small>岗位标准 · 2026-H2</small>
        </div>
      </div>

      <div className="assessment-rule" aria-hidden="true" />

      <form className="assessment-form" onSubmit={handleSubmit} aria-busy={isSubmitting} noValidate>
        <div className="form-main-column">
          <fieldset className="form-section">
            <legend><span className="section-index">01</span> 岗位范围</legend>
            <p className="section-note">第一阶段只开放一套已经校准的岗位标准。</p>
            <label className="field-label" htmlFor={roleId}>支持岗位族</label>
            <select id={roleId} aria-label="目标岗位" value={SUPPORTED_ROLE} disabled>
              <option value={SUPPORTED_ROLE}>{SUPPORTED_ROLE}</option>
            </select>
            <p className="field-hint">岗位标准会决定能力维度、证据要求与面试计划护栏。</p>
          </fieldset>

          <fieldset className="form-section">
            <legend><span className="section-index">02</span> 岗位描述</legend>
            <div className="field-heading-row">
              <label className="field-label" htmlFor={jdId}>岗位描述 JD</label>
              <button
                type="button"
                className="text-action"
                onClick={() => {
                  setJdText(SAMPLE_JD)
                  setValidationErrors([])
                }}
              >
                填入 2026-H2 示例 JD
              </button>
            </div>
            <textarea
              id={jdId}
              value={jdText}
              onChange={(event) => setJdText(event.target.value)}
              placeholder="粘贴真实岗位描述，或使用右侧示例后继续编辑。"
              rows={10}
              spellCheck={false}
            />
            <p className="field-hint">示例只是起点；提交前请按本次招聘的真实职责进行编辑。</p>
          </fieldset>

          <fieldset className="form-section">
            <legend><span className="section-index">03</span> 候选人材料</legend>
            <p className="section-note">简历文本与文件只能选择一种来源，原始文件不会进入评估记录。</p>
            <div className="source-switch" role="radiogroup" aria-label="简历材料来源">
              <label className={`source-option ${resumeSource === 'text' ? 'is-selected' : ''}`}>
                <input
                  type="radio"
                  name="resume-source"
                  value="text"
                  checked={resumeSource === 'text'}
                  onChange={() => switchResumeSource('text')}
                />
                <span>粘贴文本</span>
              </label>
              <label className={`source-option ${resumeSource === 'file' ? 'is-selected' : ''}`}>
                <input
                  type="radio"
                  name="resume-source"
                  value="file"
                  checked={resumeSource === 'file'}
                  onChange={() => switchResumeSource('file')}
                />
                <span>上传简历文件</span>
              </label>
            </div>

            {resumeSource === 'text' ? (
              <div className="resume-text-field">
                <label className="field-label" htmlFor={resumeTextId}>粘贴简历文本</label>
                <textarea
                  id={resumeTextId}
                  value={resumeText}
                  onChange={(event) => setResumeText(event.target.value)}
                  placeholder="粘贴候选人的简历正文、项目经历或公开履历。"
                  rows={10}
                  spellCheck={false}
                />
              </div>
            ) : (
              <div className="resume-file-field">
                <label className="field-label" htmlFor={resumeFileId}>简历文件</label>
                <input
                  key={fileInputVersion}
                  id={resumeFileId}
                  type="file"
                  accept=".pdf,.docx,.txt"
                  onChange={handleFileChange}
                />
                <p className="field-hint">支持 PDF、DOCX、TXT，单个文件不超过 5 MiB。</p>
                {resumeFile ? <p className="selected-file">已选择：{resumeFile.name}</p> : null}
              </div>
            )}
          </fieldset>
        </div>

        <aside className="submission-panel" aria-label="提交评估">
          <div className="submission-panel-header">
            <span className="panel-code">材料控制</span>
            <span className="panel-status">准备就绪</span>
          </div>
          <h2>提交材料</h2>
          <p>服务器将依次执行文件提取、简历画像、岗位理解、能力建模与计划生成。</p>
          <dl className="submission-facts">
            <div><dt>面试预算</dt><dd>45 min</dd></div>
            <div><dt>岗位标准</dt><dd>2026-H2</dd></div>
            <div><dt>结果状态</dt><dd>可审核</dd></div>
          </dl>

          {validationErrors.length > 0 ? (
            <div className="form-feedback error-feedback" role="alert" aria-live="assertive">
              {validationErrors.map((error) => <p key={error}>{error}</p>)}
            </div>
          ) : null}
          {submitError ? (
            <div className="form-feedback error-feedback" role="alert" aria-live="assertive">
              <p>{submitError}</p>
            </div>
          ) : null}
          {isSubmitting ? (
            <p className="form-feedback loading-feedback" role="status" aria-live="polite">
              正在提交评估材料…
            </p>
          ) : null}

          <button className="primary-action" type="submit" disabled={isSubmitting}>
            {isSubmitting ? '提交中…' : '创建评估'}
          </button>
          <p className="demo-route-copy">
            暂时没有材料？{' '}
            <Link to="/demo/assessment">查看演示示例 →</Link>
          </p>
          <p className="privacy-note">评估用于辅助决策，系统不会自动输出录用或淘汰结论。</p>
        </aside>
      </form>
    </section>
  )
}

export default NewAssessmentPage
