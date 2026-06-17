'use client'

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/store/auth'

export default function QuickAppDebugPage() {
  const router = useRouter()
  const { isAuthenticated, getAuthHeader } = useAuthStore()
  const [apps, setApps] = useState<any[]>([])
  const [logs, setLogs] = useState<any[]>([])
  const [tokens, setTokens] = useState<any>(null)
  const [draftInput, setDraftInput] = useState('')
  const [draftResult, setDraftResult] = useState<any>(null)
  const [draftLoading, setDraftLoading] = useState(false)

  useEffect(() => {
    if (!isAuthenticated) return
    fetchAll()
  }, [isAuthenticated])

  const fetchAll = async () => {
    const h = getAuthHeader()
    try {
      const [appsRes, logsRes, tokensRes] = await Promise.all([
        fetch('/api/quick_apps', { headers: h }),
        fetch('/api/quick_apps/logs?limit=50', { headers: h }),
        fetch('/api/stats/tokens', { headers: h }),
      ])
      if (appsRes.ok) setApps(await appsRes.json())
      if (logsRes.ok) setLogs(await logsRes.json())
      if (tokensRes.ok) setTokens(await tokensRes.json())
    } catch {}
  }

  const testDraft = async () => {
    if (!draftInput.trim()) return
    setDraftLoading(true)
    setDraftResult(null)
    try {
      const res = await fetch('/api/quick_apps/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
        body: JSON.stringify({ description: draftInput.trim() }),
      })
      setDraftResult(await res.json())
    } catch (e) {
      setDraftResult({ error: String(e) })
    } finally {
      setDraftLoading(false)
    }
  }

  if (!isAuthenticated) { router.push('/'); return null }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">🔧 QuickApp 调试面板</h1>
        <div className="flex gap-2 text-xs">
          <button onClick={fetchAll} className="rounded-lg border px-3 py-1.5 hover:bg-secondary/10">刷新</button>
          <button onClick={() => router.push('/quick-apps')} className="rounded-lg border px-3 py-1.5 hover:bg-secondary/10">返回管理页</button>
          <button onClick={() => router.push('/')} className="rounded-lg border px-3 py-1.5 hover:bg-secondary/10">聊天</button>
        </div>
      </div>

      {/* Token 统计 */}
      {tokens && (
        <section>
          <h2 className="text-sm font-semibold text-secondary mb-2">📊 Token 用量</h2>
          <pre className="rounded-xl bg-secondary/5 px-4 py-3 text-xs font-mono whitespace-pre-wrap">
{JSON.stringify(tokens, null, 2)}</pre>
        </section>
      )}

      {/* 快应用列表 */}
      <section>
        <h2 className="text-sm font-semibold text-secondary mb-2">📦 已注册快应用（{apps.length}）</h2>
        {apps.length === 0 && <p className="text-xs text-muted">无</p>}
        <div className="space-y-2">
          {apps.map((a) => (
            <details key={a.name} className="rounded-xl border border-primary/10 bg-white/50 px-4 py-2">
              <summary className="text-xs font-medium cursor-pointer">{a.name} <span className="text-muted">({a.source_type})</span></summary>
              <pre className="mt-2 text-[10px] font-mono whitespace-pre-wrap overflow-x-auto">
{JSON.stringify(a, null, 2)}</pre>
            </details>
          ))}
        </div>
      </section>

      {/* 执行日志 */}
      <section>
        <h2 className="text-sm font-semibold text-secondary mb-2">📋 执行日志（{logs.length}）</h2>
        {logs.length === 0 && <p className="text-xs text-muted">无</p>}
        {logs.length > 0 && (
          <div className="space-y-1">
            {logs.map((l, i) => (
              <div key={i} className="flex items-center gap-3 rounded-lg bg-secondary/5 px-3 py-1.5 text-[10px] font-mono">
                <span className={`shrink-0 ${l.success ? 'text-positive' : 'text-destructive'}`}>{l.success ? '✅' : '❌'}</span>
                <span className="w-20 truncate text-secondary">{l.tool_name}</span>
                <span className="w-20 truncate text-muted">{l.args_summary || '—'}</span>
                <span className="w-12 text-right text-muted">{l.duration_ms}ms</span>
                <span className="flex-1 truncate text-muted">{l.reason}</span>
                <span className="text-muted/50">{l.timestamp?.slice(11, 19)}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Draft 调试 */}
      <section>
        <h2 className="text-sm font-semibold text-secondary mb-2">🔍 Draft 检索测试</h2>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-xl border border-primary/20 bg-white/80 px-3 py-2 text-xs"
            placeholder="输入描述，如：帮我做个食谱管理工具"
            value={draftInput}
            onChange={(e) => setDraftInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && testDraft()}
          />
          <button onClick={testDraft} disabled={draftLoading}
            className="rounded-xl bg-primary px-4 py-2 text-xs text-white disabled:opacity-50">
            {draftLoading ? '检索中...' : '测试'}
          </button>
        </div>

        {draftResult && (
          <div className="mt-3 space-y-3">
            {/* 选中方案 */}
            <div className="rounded-xl border border-primary/10 bg-white/50 px-4 py-3">
              <p className="text-xs font-medium text-secondary mb-1">
                ✅ 选中：{draftResult.source_type} / {draftResult.source_name}
              </p>
              <p className="text-[10px] text-muted">{draftResult.source_reason}</p>
            </div>

            {/* 检索追踪 */}
            {draftResult._trace && (
              <div className="rounded-xl border border-primary/10 bg-white/50 px-4 py-3">
                <p className="text-xs font-medium text-secondary mb-2">🔎 检索追踪</p>
                <div className="space-y-2">
                  {draftResult._trace.steps?.map((s: any, i: number) => (
                    <div key={i} className="rounded-lg bg-secondary/5 px-3 py-2 text-[10px] font-mono">
                      <p className="font-medium text-secondary mb-1">▶ {s.source}</p>
                      <pre className="text-muted whitespace-pre-wrap">{JSON.stringify(s, null, 2)}</pre>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 展开描述 */}
            {draftResult._expanded && (
              <div className="rounded-xl border border-primary/10 bg-white/50 px-4 py-3">
                <p className="text-xs font-medium text-secondary mb-1">📝 LLM 展开描述</p>
                <pre className="text-[10px] font-mono text-muted whitespace-pre-wrap">
{JSON.stringify(draftResult._expanded, null, 2)}</pre>
              </div>
            )}

            {/* 模板信息 */}
            {draftResult.input_templates && draftResult.input_templates.length > 0 && (
              <div className="rounded-xl border border-primary/10 bg-white/50 px-4 py-3">
                <p className="text-xs font-medium text-secondary mb-1">📋 输入模板</p>
                <pre className="text-[10px] font-mono text-muted whitespace-pre-wrap">
{JSON.stringify(draftResult.input_templates, null, 2)}</pre>
              </div>
            )}

            {/* 完整 JSON */}
            <details className="rounded-xl border border-primary/10 bg-white/50 px-4 py-2">
              <summary className="text-xs font-medium cursor-pointer">📄 完整 JSON</summary>
              <pre className="mt-2 text-[10px] font-mono whitespace-pre-wrap overflow-x-auto max-h-96 overflow-y-auto">
{JSON.stringify(draftResult, null, 2)}</pre>
            </details>
          </div>
        )}
      </section>

      {/* 后端日志 */}
      <section>
        <h2 className="text-sm font-semibold text-secondary mb-2">📜 后端日志</h2>
        <button onClick={async () => {
          const r = await fetch('/tmp/chips-web.log', { headers: getAuthHeader() }).catch(() => null)
          // Can't fetch file directly - just show note
        }} className="text-[10px] text-muted">
          后端日志查看方式：<code className="bg-secondary/5 px-1">tail -50 /tmp/chips-web.log</code>
        </button>
      </section>
    </div>
  )
}
