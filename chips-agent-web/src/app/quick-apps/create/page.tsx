'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/store/auth'
import { Button } from '@/components/ui/button'

interface DraftParam {
  name: string
  type: string
  description: string
}

interface Draft {
  name: string
  description: string
  source_type: string
  source_name: string
  source_reason: string
  params: DraftParam[]
  install_hint: string
  output_description: string
  venv_level: number
}

const SOURCE_OPTIONS = [
  { value: 'cli_tool', label: 'CLI 工具' },
  { value: 'pip_package', label: 'pip 包' },
  { value: 'github', label: 'GitHub 项目' },
  { value: 'write_code', label: '自写代码' },
]

const TYPE_OPTIONS = [
  { value: 'string', label: '字符串' },
  { value: 'integer', label: '整数' },
  { value: 'boolean', label: '布尔值' },
  { value: 'number', label: '浮点数' },
  { value: 'array', label: '数组' },
  { value: 'object', label: '对象' },
]

const CREATE_STEPS = [
  { id: 'validate', label: '🔍 校验方案',     detail: '检查 source 有效性' },
  { id: 'codegen',  label: '📝 LLM 生成代码', detail: '调用 LLM 生成 handler 代码' },
  { id: 'schema',   label: '📋 构建 Schema',  detail: '构建参数定义' },
  { id: 'create',   label: '💾 写入文件',     detail: '写入 app.py 和 meta.json' },
  { id: 'verify',   label: '🧪 验证',         detail: '运行样例参数并检查输出' },
  { id: 'register', label: '📝 注册工具',     detail: '注册到 ToolRegistry' },
  { id: 'done',     label: '🎉 创建完成',     detail: '可以开始使用了' },
]

export default function CreateQuickAppPage() {
  const router = useRouter()
  const { isAuthenticated, getAuthHeader } = useAuthStore()

  // Step tracking
  const [step, setStep] = useState<'input' | 'preview' | 'result'>('input')

  // Input state
  const [description, setDescription] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  // Draft state
  const [draft, setDraft] = useState<Draft | null>(null)

  // Create state
  const [creating, setCreating] = useState(false)
  const [progressStep, setProgressStep] = useState(0)
  const [currentDetail, setCurrentDetail] = useState('')
  const [streamLogs, setStreamLogs] = useState<Array<{ message: string; data: string; lang: string }>>([])
  const [createResult, setCreateResult] = useState<{
    success: boolean
    name?: string
    message?: string
    error?: string
  } | null>(null)
  const [showTechLogs, setShowTechLogs] = useState(false)

  if (!isAuthenticated) {
    router.push('/')
    return null
  }

  // ── Step 1: Input description → search draft ──

  const handleSearch = async () => {
    if (!description.trim()) return
    setSearching(true)
    setSearchError('')
    try {
      const res = await fetch('/api/quick_apps/draft', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeader(),
        },
        body: JSON.stringify({ description: description.trim() }),
      })
      if (!res.ok) throw new Error('API 错误')
      const data: Draft = await res.json()
      setDraft(data)
      setStep('preview')
    } catch {
      setSearchError('检索失败，请重试')
    } finally {
      setSearching(false)
    }
  }

  // ── Step 2: Preview & confirm draft ──

  const updateDraftField = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    if (!draft) return
    setDraft({ ...draft, [key]: value })
  }

  const updateParam = (index: number, field: keyof DraftParam, value: string) => {
    if (!draft) return
    const params = [...draft.params]
    params[index] = { ...params[index], [field]: value }
    setDraft({ ...draft, params })
  }

  const addParam = () => {
    if (!draft) return
    setDraft({
      ...draft,
      params: [...draft.params, { name: '', type: 'string', description: '' }],
    })
  }

  const removeParam = (index: number) => {
    if (!draft) return
    setDraft({
      ...draft,
      params: draft.params.filter((_, i) => i !== index),
    })
  }

  // ── Step 3: Create（SSE 流式） ──

  const handleCreate = async () => {
    if (!draft) return
    setCreating(true)
    setCreateResult(null)
    setProgressStep(0)
    setCurrentDetail('准备中...')
    setStreamLogs([])

    const stepMap: Record<string, number> = {}
    CREATE_STEPS.forEach((s, i) => { stepMap[s.id] = i })

    try {
      const res = await fetch('/api/quick_apps/create/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeader(),
        },
        body: JSON.stringify({ draft }),
      })

      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''  // 保留不完整的行

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6)

          // [DONE] 标记
          if (raw === '[DONE]') continue

          try {
            const event = JSON.parse(raw)

            if (event.type === 'step') {
              const idx = stepMap[event.id]
              if (idx !== undefined) {
                setProgressStep(idx)
              }
              if (event.detail) {
                setCurrentDetail(event.detail)
              }
            } else if (event.type === 'log') {
              setStreamLogs((prev) => [...prev, {
                message: event.message || '',
                data: event.data || '',
                lang: event.lang || 'text',
              }])
              // 日志内容也显示在浮动状态上
              if (event.message) {
                setCurrentDetail(event.message + (event.data?.length < 100 ? `: ${event.data}` : ''))
              }
            } else if (event.type === 'result') {
              setCreateResult({ success: event.success, name: event.name, message: event.message, error: event.error })
              setStep('result')
            }
          } catch {
            // 跳过解析失败的事件
          }
        }
      }
    } catch (e) {
      setCreateResult({ success: false, error: `创建请求失败: ${e}` })
      setStep('result')
    } finally {
      setCreating(false)
      setCurrentDetail('')
    }
  }

  // ── Re-search ──

  const handleBackToInput = () => {
    setStep('input')
    setDraft(null)
    setCreateResult(null)
    setProgressStep(0)
    setCurrentDetail('')
    setStreamLogs([])
    setShowTechLogs(false)
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      {/* Header */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold gradient-text">创建快应用</h1>
          <p className="mt-1 text-sm text-muted">
            描述你需要的工具，系统会自动匹配最佳方案
          </p>
        </div>
        <Button variant="outline" onClick={() => router.push('/quick-apps')}>
          返回列表
        </Button>
      </div>

      {/* Step 1: Input */}
      {step === 'input' && (
        <div className="rounded-2xl border border-primary/10 bg-white/50 px-6 py-8 backdrop-blur-sm">
          <label className="block text-sm font-medium text-secondary">
            功能描述
          </label>
          <textarea
            className="mt-2 w-full rounded-xl border border-primary/20 bg-white/80 px-4 py-3 text-sm text-secondary placeholder:text-muted/60 backdrop-blur-sm transition-colors focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
            rows={4}
            placeholder="例如：做一个视频转 GIF 的工具"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          {searchError && (
            <p className="mt-2 text-sm text-destructive">{searchError}</p>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="outline" onClick={() => router.push('/quick-apps')}>
              取消
            </Button>
            <Button onClick={handleSearch} disabled={searching || !description.trim()}>
              {searching ? '检索中...' : '检索方案'}
            </Button>
          </div>
        </div>
      )}

      {/* Step 2: Preview */}
      {step === 'preview' && draft && (
        <div className="space-y-6">
          <div className="rounded-2xl border border-primary/10 bg-white/50 px-6 py-6 backdrop-blur-sm">
            <h2 className="mb-4 text-sm font-semibold text-secondary">草案卡片</h2>

            {/* Name */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-muted">工具名称</label>
              <input
                className="mt-1 w-full rounded-xl border border-primary/20 bg-white/80 px-3 py-2 text-sm text-secondary backdrop-blur-sm focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
                value={draft.name}
                onChange={(e) => updateDraftField('name', e.target.value)}
              />
            </div>

            {/* Description */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-muted">描述</label>
              <input
                className="mt-1 w-full rounded-xl border border-primary/20 bg-white/80 px-3 py-2 text-sm text-secondary backdrop-blur-sm focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
                value={draft.description}
                onChange={(e) => updateDraftField('description', e.target.value)}
              />
            </div>

            {/* Source */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-muted">选用方案</label>
              <div className="mt-1 flex items-center gap-3">
                <select
                  className="rounded-xl border border-primary/20 bg-white/80 px-3 py-2 text-sm text-secondary backdrop-blur-sm focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
                  value={draft.source_type}
                  onChange={(e) => updateDraftField('source_type', e.target.value)}
                >
                  {SOURCE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
                <span className="text-xs text-muted">{draft.source_name}</span>
              </div>
              {draft.source_reason && (
                <p className="mt-1 text-xs text-muted">{draft.source_reason}</p>
              )}
            </div>

            {/* Install hint */}
            {draft.install_hint && (
              <div className="mb-4 rounded-xl bg-warning/5 px-3 py-2 text-xs text-muted">
                ⚠ {draft.install_hint}
              </div>
            )}

            {/* Params */}
            <div className="mb-4">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-muted">参数定义</label>
                <button
                  onClick={addParam}
                  className="text-xs text-primary hover:text-primary/80"
                >
                  + 添加参数
                </button>
              </div>
              <div className="mt-2 space-y-2">
                {draft.params.map((param, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <input
                      className="flex-1 rounded-xl border border-primary/20 bg-white/80 px-2.5 py-1.5 text-xs text-secondary backdrop-blur-sm focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
                      placeholder="参数名"
                      value={param.name}
                      onChange={(e) => updateParam(i, 'name', e.target.value)}
                    />
                    <select
                      className="rounded-xl border border-primary/20 bg-white/80 px-2 py-1.5 text-xs text-secondary backdrop-blur-sm focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
                      value={param.type}
                      onChange={(e) => updateParam(i, 'type', e.target.value)}
                    >
                      {TYPE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                    <input
                      className="flex-[2] rounded-xl border border-primary/20 bg-white/80 px-2.5 py-1.5 text-xs text-secondary backdrop-blur-sm focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
                      placeholder="说明"
                      value={param.description}
                      onChange={(e) => updateParam(i, 'description', e.target.value)}
                    />
                    <button
                      onClick={() => removeParam(i)}
                      className="text-muted hover:text-destructive"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex justify-between gap-2">
            <Button variant="outline" onClick={handleBackToInput}>
              重新描述
            </Button>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => router.push('/quick-apps')}>
                取消
              </Button>
              <Button onClick={handleCreate} disabled={creating}>
                {creating ? '创建中...' : '确认创建'}
              </Button>
            </div>
          </div>

          {/* 创建进度 */}
          {creating && (
            <div className="mt-4 rounded-xl border border-primary/10 bg-white/50 px-5 py-4 backdrop-blur-sm">
              <p className="mb-3 text-xs font-medium text-muted">创建进度</p>
              <div className="space-y-2.5">
                {CREATE_STEPS.map((step, i) => {
                  const isActive = i === progressStep
                  const isDone = i < progressStep
                  return (
                    <div key={step.id} className="flex items-start gap-2.5">
                      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center">
                        {isDone ? (
                          <span className="flex size-5 items-center justify-center rounded-full bg-positive/10">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#38A169" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          </span>
                        ) : isActive ? (
                          <svg className="size-4 animate-spin text-primary" viewBox="0 0 24 24" fill="none">
                            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
                            <path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" strokeLinecap="round" className="opacity-75" />
                          </svg>
                        ) : (
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#CBD5E0" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="12" cy="12" r="10" />
                          </svg>
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <span className={`text-xs font-medium ${isDone || isActive ? 'text-secondary' : 'text-muted/30'}`}>
                          {step.label}
                        </span>
                        {isActive && (
                          <span className="ml-2 text-[10px] text-primary animate-pulse">{step.detail}</span>
                        )}
                        {isDone && (
                          <span className="ml-2 text-[10px] text-muted/50">✓ 完成</span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Step 3: Result */}
      {step === 'result' && createResult && (
        <div className="rounded-2xl border border-primary/10 bg-white/50 px-6 py-6 backdrop-blur-sm">
          {/* 进度总览 — 详细步骤 */}
          <div className="mb-5 rounded-xl bg-secondary/5 px-5 py-4">
            <p className="mb-3 text-xs font-medium text-muted">
              创建过程
              {createResult.success ? (
                <span className="ml-2 text-positive">✅ 全部完成</span>
              ) : (
                <span className="ml-2 text-destructive">❌ 失败</span>
              )}
            </p>
            <div className="space-y-2.5">
              {CREATE_STEPS.map((s) => (
                <div key={s.id} className="flex items-start gap-2.5">
                  <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-positive/10">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#38A169" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </span>
                  <div className="min-w-0 flex-1">
                    <span className="text-xs text-secondary font-medium">{s.label}</span>
                    <span className="ml-2 text-[10px] text-muted/50">{s.detail}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 工具信息 */}
          <div className="mb-5 rounded-xl border border-primary/10 bg-white/50 px-5 py-4 backdrop-blur-sm">
            <p className="mb-2 text-xs font-medium text-muted">工具信息</p>
            <div className="space-y-1.5 text-xs">
              <div className="flex gap-2">
                <span className="w-16 shrink-0 text-muted">名称</span>
                <span className="text-secondary">{createResult.name || draft?.name || '—'}</span>
              </div>
              <div className="flex gap-2">
                <span className="w-16 shrink-0 text-muted">来源</span>
                <span className="text-secondary">{draft?.source_type || '—'} / {draft?.source_name || '—'}</span>
              </div>
              <div className="flex gap-2">
                <span className="w-16 shrink-0 text-muted">参数</span>
                <span className="text-secondary">{draft?.params?.length ? draft.params.map(p => p.name).join(', ') : '无'}</span>
              </div>
            </div>
          </div>

          {/* 结果状态 */}
          <div className="text-center">
            {createResult.success ? (
              <>
                <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full bg-positive/10">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#38A169" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </div>
                <p className="text-sm font-medium text-positive">创建成功</p>
                <p className="mt-1 text-xs text-muted">
                  {createResult.message || `快应用「${createResult.name}」已就绪`}
                </p>
                <div className="mt-4 flex justify-center gap-3">
                  <Button onClick={() => router.push('/quick-apps')}>返回列表</Button>
                  <Button variant="outline" onClick={handleBackToInput}>再创建一个</Button>
                </div>
              </>
            ) : (
              <>
                <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full bg-destructive/10">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#E53E3E" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="15" y1="9" x2="9" y2="15" />
                    <line x1="9" y1="9" x2="15" y2="15" />
                  </svg>
                </div>
                <p className="text-sm font-medium text-destructive">创建失败</p>
                <p className="mt-1 text-xs text-muted">
                  {createResult.error || '未知错误'}
                </p>
                <div className="mt-4 flex justify-center gap-3">
                  <Button onClick={handleBackToInput}>修改后重试</Button>
                  <Button variant="outline" onClick={() => router.push('/quick-apps')}>返回列表</Button>
                </div>
              </>
            )}

            {/* 技术详情（LLM prompt/响应/验证日志） */}
            {streamLogs.length > 0 && (
              <div className="mt-6 border-t border-primary/10 pt-4">
                <button
                  onClick={() => setShowTechLogs(!showTechLogs)}
                  className="flex items-center gap-1.5 text-xs text-muted hover:text-secondary transition-colors mx-auto"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                    className={`transform transition-transform ${showTechLogs ? 'rotate-90' : ''}`}
                  >
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                  技术详情（{streamLogs.length} 条日志）
                </button>

                {showTechLogs && (
                  <div className="mt-3 space-y-2 text-left max-h-96 overflow-y-auto">
                    {streamLogs.map((log, i) => (
                      <div key={i} className="rounded-xl border border-primary/5 bg-secondary/5 px-3 py-2">
                        <p className="text-[10px] font-medium text-muted mb-1">{log.message}</p>
                        {log.lang === 'python' && log.data ? (
                          <pre className="text-[10px] font-mono text-secondary leading-relaxed whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto">
                            {log.data}
                          </pre>
                        ) : log.data && log.data.length > 200 ? (
                          <>
                            <pre className="text-[10px] font-mono text-secondary leading-relaxed whitespace-pre-wrap overflow-x-auto max-h-32 overflow-y-auto">
                              {log.data.slice(0, 1000)}
                            </pre>
                            {log.data.length > 1000 && (
                              <p className="text-[9px] text-muted/50 mt-1">...（截断，共 {log.data.length} 字符）</p>
                            )}
                          </>
                        ) : (
                          <p className="text-[10px] text-secondary whitespace-pre-wrap">{log.data}</p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 浮动状态栏 — 创建中在底部显示当前操作 */}
      {creating && currentDetail && (
        <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-xl border border-primary/20 bg-white/90 px-5 py-2.5 text-xs text-secondary shadow-lg backdrop-blur-md flex items-center gap-2 max-w-lg">
          <svg className="size-3.5 animate-spin text-primary shrink-0" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
            <path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="3" strokeLinecap="round" className="opacity-75" />
          </svg>
          <span className="truncate">{currentDetail}</span>
        </div>
      )}
    </div>
  )
}
