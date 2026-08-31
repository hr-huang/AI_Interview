import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, api } from '../../api/client'
import type { InterviewSession, InterviewTurnView } from '../../api/types'
import './interview.css'

function makeIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `answer-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function messageFor(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '请求失败，请稍后重试。'
}

export function InterviewPage() {
  const { candidateToken = '' } = useParams()
  const [session, setSession] = useState<InterviewSession | null>(null)
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const answerKeyRef = useRef<{ turnId: string; answer: string; key: string } | null>(null)

  const currentTurn = useMemo<InterviewTurnView | null>(() => {
    const turn = session?.turn
    return turn && typeof turn === 'object' ? (turn as InterviewTurnView) : null
  }, [session])

  async function refresh() {
    if (!candidateToken) return
    setLoading(true)
    setError('')
    try {
      setSession(await api.getInterview(candidateToken))
    } catch (requestError) {
      setError(messageFor(requestError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    // candidateToken uniquely identifies this interview session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateToken])

  async function startInterview() {
    if (!candidateToken) return
    setSubmitting(true)
    setError('')
    try {
      setSession(await api.startInterview(candidateToken))
    } catch (requestError) {
      setError(messageFor(requestError))
    } finally {
      setSubmitting(false)
    }
  }

  async function submitAnswer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!candidateToken || !currentTurn) return
    const normalized = answer.trim()
    if (!normalized) {
      setError('请先填写回答。')
      return
    }

    const cached = answerKeyRef.current
    const idempotencyKey =
      cached?.turnId === currentTurn.id && cached.answer === normalized
        ? cached.key
        : makeIdempotencyKey()
    answerKeyRef.current = { turnId: currentTurn.id, answer: normalized, key: idempotencyKey }

    setSubmitting(true)
    setError('')
    try {
      const next = await api.submitAnswer(candidateToken, {
        turn_id: currentTurn.id,
        answer: normalized,
        idempotency_key: idempotencyKey,
      })
      setSession(next)
      setAnswer('')
      answerKeyRef.current = null
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 409) {
        const payload = requestError.payload
        if (payload && typeof payload === 'object' && 'session' in payload) {
          const latest = (payload as { session?: unknown }).session
          if (latest && typeof latest === 'object') {
            setSession(latest as InterviewSession)
            setAnswer('')
            answerKeyRef.current = null
          }
        }
      }
      setError(messageFor(requestError))
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return <main className="candidate-interview"><p role="status">正在读取面试状态…</p></main>
  }

  if (!session) {
    return (
      <main className="candidate-interview">
        <h1>无法打开面试</h1>
        <p>{error || '候选人面试链接不可用。'}</p>
        <button type="button" onClick={() => void refresh()}>重新加载</button>
      </main>
    )
  }

  if (session.state === 'ready') {
    return (
      <main className="candidate-interview">
        <p className="eyebrow">候选人面试</p>
        <h1>{session.target_role || '岗位胜任力面试'}</h1>
        <p className="candidate-lead">系统会逐题展示问题，并根据你的回答决定后续追问。每次只需要回答当前问题。</p>
        {error ? <p className="candidate-error" role="alert">{error}</p> : null}
        <button className="candidate-primary" type="button" onClick={startInterview} disabled={submitting}>
          {submitting ? '正在开始…' : '开始面试'}
        </button>
      </main>
    )
  }

  if (session.state === 'complete') {
    return (
      <main className="candidate-interview">
        <p className="eyebrow">面试完成</p>
        <h1>感谢完成本次面试</h1>
        <p className="candidate-lead">你的回答已经提交。评估结果将由企业侧查看。</p>
      </main>
    )
  }

  if (session.state === 'reporting') {
    return (
      <main className="candidate-interview">
        <p className="eyebrow">面试完成</p>
        <h1>正在生成评估报告</h1>
        <p className="candidate-lead">无需继续作答。</p>
      </main>
    )
  }

  return (
    <main className="candidate-interview">
      <header className="candidate-header">
        <div>
          <p className="eyebrow">候选人面试</p>
          <h1>{session.target_role || '岗位胜任力面试'}</h1>
        </div>
        <span className="candidate-meta">
          已用 {Math.max(0, Math.floor((session.elapsed_seconds || 0) / 60))} 分钟
        </span>
      </header>

      {currentTurn ? (
        <section className="question-card" aria-labelledby="current-question">
          <p className="question-index">第 {currentTurn.sequence_number} 题</p>
          <h2 id="current-question">{currentTurn.question}</h2>
          <form onSubmit={submitAnswer}>
            <label htmlFor="candidate-answer">你的回答</label>
            <textarea
              id="candidate-answer"
              rows={9}
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder="请直接说明你的做法、判断依据和关键取舍。"
              disabled={submitting}
            />
            {error ? <p className="candidate-error" role="alert">{error}</p> : null}
            <button className="candidate-primary" type="submit" disabled={submitting}>
              {submitting ? '正在提交…' : '提交回答'}
            </button>
          </form>
        </section>
      ) : (
        <section className="question-card">
          <h2>正在准备下一题</h2>
          <p>如果页面长时间没有更新，可以重新加载当前状态。</p>
          {error ? <p className="candidate-error" role="alert">{error}</p> : null}
          <button type="button" onClick={() => void refresh()} disabled={submitting}>重新加载</button>
        </section>
      )}
    </main>
  )
}

export default InterviewPage
