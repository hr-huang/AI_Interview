import { useMemo, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { ModelProvider, ModelSessionResponse } from '../../api/types'

const PRESETS: Record<ModelProvider, { baseUrl: string; model: string; label: string }> = {
  qwen: {
    label: '阿里云百炼 / Qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen3.8-max',
  },
  deepseek: {
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
  },
  glm: {
    label: '智谱 GLM',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4.5',
  },
  openai_compatible: {
    label: 'OpenAI-compatible（高级）',
    baseUrl: '',
    model: '',
  },
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '模型连接测试失败。'
}

export function ModelSettings({
  value,
  onChange,
}: {
  value: ModelSessionResponse | null
  onChange: (session: ModelSessionResponse | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [provider, setProvider] = useState<ModelProvider>('qwen')
  const preset = PRESETS[provider]
  const [baseUrl, setBaseUrl] = useState(PRESETS.qwen.baseUrl)
  const [model, setModel] = useState(PRESETS.qwen.model)
  const [apiKey, setApiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')

  const summary = useMemo(() => {
    if (!value) return '使用服务器默认模型配置'
    return `${PRESETS[value.provider].label} · ${value.model}`
  }, [value])

  function chooseProvider(next: ModelProvider) {
    setProvider(next)
    setBaseUrl(PRESETS[next].baseUrl)
    setModel(PRESETS[next].model)
    setError('')
    onChange(null)
  }

  async function testAndUse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!apiKey.trim()) {
      setError('请填写 API Key。')
      return
    }
    setTesting(true)
    setError('')
    try {
      const session = await api.createModelSession({
        provider,
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
        model: model.trim(),
      })
      onChange(session)
      setApiKey('')
      setOpen(false)
    } catch (requestError) {
      onChange(null)
      setError(errorMessage(requestError))
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="model-settings">
      <div className="model-settings-summary">
        <div>
          <span className="panel-code">推理模型</span>
          <strong>{summary}</strong>
          <small>Embedding、Reranker 与 Qdrant 由系统固定管理。</small>
        </div>
        <button
          type="button"
          className="text-action"
          onClick={() => setOpen((current) => !current)}
        >
          {open ? '收起' : '模型设置'}
        </button>
      </div>

      {open ? (
        <form className="model-settings-form" onSubmit={testAndUse}>
          <label>
            Provider
            <select
              value={provider}
              onChange={(event) => chooseProvider(event.target.value as ModelProvider)}
            >
              {Object.entries(PRESETS).map(([key, item]) => (
                <option key={key} value={key}>{item.label}</option>
              ))}
            </select>
          </label>
          <label>
            Base URL
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://api.example.com/v1"
              spellCheck={false}
            />
          </label>
          <label>
            Model
            <input
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="model-name"
              spellCheck={false}
            />
          </label>
          <label>
            API Key
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="仅用于当前服务进程，不写入数据库"
            />
          </label>
          {error ? <p className="form-feedback error-feedback" role="alert">{error}</p> : null}
          <button type="submit" disabled={testing}>
            {testing ? '正在测试结构化输出…' : '测试连接并使用'}
          </button>
          <p className="field-hint">
            测试不仅检查网络连接，还会验证本项目依赖的 JSON Structured Output。
          </p>
        </form>
      ) : null}
    </div>
  )
}
