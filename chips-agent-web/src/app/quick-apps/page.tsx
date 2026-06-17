'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/store/auth'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'

interface QuickApp {
  name: string
  description: string
  source_type: string
  venv_level: number
  created_at: string
}

const SOURCE_LABELS: Record<string, string> = {
  cli_tool: 'CLI 工具',
  pip_package: 'pip 包',
  github: 'GitHub 项目',
  write_code: '自写代码',
}

const VENV_LABELS: Record<string, string> = {
  '0': '系统环境',
  '1': '共享环境',
  '2': '独立环境',
}

interface LogEntry {
  timestamp: string
  tool_name: string
  args_summary: string
  duration_ms: number
  success: boolean
  reason: string
}

export default function QuickAppsPage() {
  const router = useRouter()
  const { isAuthenticated, token, getAuthHeader } = useAuthStore()
  const [apps, setApps] = useState<QuickApp[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [runTarget, setRunTarget] = useState<QuickApp | null>(null)
  const [runArgs, setRunArgs] = useState('{}')
  const [runResult, setRunResult] = useState('')
  const [runLoading, setRunLoading] = useState(false)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [showLogs, setShowLogs] = useState(false)

  useEffect(() => {
    if (!isAuthenticated || !token) {
      router.push('/')
      return
    }
    fetchApps()
    fetchLogs()
  }, [isAuthenticated, token])

  const fetchLogs = async () => {
    try {
      const res = await fetch('/api/quick_apps/logs?limit=20', {
        headers: { ...getAuthHeader() },
      })
      if (res.ok) {
        setLogs(await res.json())
      }
    } catch {
      // silent
    }
  }

  const fetchApps = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/quick_apps', {
        headers: { ...getAuthHeader() },
      })
      if (res.ok) {
        setApps(await res.json())
      } else {
        setError('获取快应用列表失败')
      }
    } catch {
      setError('网络错误')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (name: string) => {
    if (!confirm(`确定要删除快应用「${name}」吗？此操作不可恢复。`)) return
    try {
      const res = await fetch(`/api/quick_apps/${name}`, {
        method: 'DELETE',
        headers: { ...getAuthHeader() },
      })
      if (res.ok) {
        setApps(apps.filter((a) => a.name !== name))
      } else {
        const data = await res.json()
        alert(data.error || '删除失败')
      }
    } catch {
      alert('删除失败')
    }
  }

  const handleRun = async () => {
    if (!runTarget) return
    setRunLoading(true)
    setRunResult('')
    try {
      let parsed: Record<string, unknown>
      try {
        parsed = JSON.parse(runArgs)
      } catch {
        setRunResult('❌ 参数不是有效的 JSON 格式')
        setRunLoading(false)
        return
      }

      const res = await fetch('/api/chat/sync', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeader(),
        },
        body: JSON.stringify({
          message: `请帮我运行快应用「${runTarget.name}」，参数：${JSON.stringify(parsed)}`,
        }),
      })
      const data = await res.json()
      setRunResult(data.reply || '✅ 执行完毕')
    } catch {
      setRunResult('❌ 执行失败')
    } finally {
      setRunLoading(false)
    }
  }

  if (!isAuthenticated) return null

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      {/* Header */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold gradient-text">快应用管理</h1>
          <p className="mt-1 text-sm text-muted">
            管理和使用已创建的快应用工具
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => router.push('/')}>
            返回聊天
          </Button>
          <Button onClick={() => router.push('/quick-apps/create')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mr-1">
              <path d="M5 12h14M12 5v14" />
            </svg>
            创建快应用
          </Button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-6 rounded-xl bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-16">
          <div className="text-sm text-muted">加载中...</div>
        </div>
      )}

      {/* Empty state */}
      {!loading && apps.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-primary/10 bg-white/50 px-6 py-16 backdrop-blur-sm">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#A0AEC0" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
          </svg>
          <p className="mt-4 text-sm text-muted">还没有创建任何快应用</p>
          <Button className="mt-4" onClick={() => router.push('/quick-apps/create')}>
            创建第一个
          </Button>
        </div>
      )}

      {/* List */}
      {!loading && apps.length > 0 && (
        <div className="space-y-3">
          {apps.map((app) => (
            <div
              key={app.name}
              className="flex items-start justify-between rounded-xl border border-primary/10 bg-white/50 px-5 py-4 backdrop-blur-sm transition-colors hover:border-primary/20"
            >
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-semibold text-secondary">{app.name}</h3>
                <p className="mt-0.5 text-xs text-muted truncate">{app.description}</p>
                <div className="mt-2 flex items-center gap-3">
                  <span className="inline-flex items-center rounded-md bg-primary/5 px-2 py-0.5 text-xs text-primary">
                    {SOURCE_LABELS[app.source_type] || app.source_type}
                  </span>
                  <span className="inline-flex items-center rounded-md bg-positive/5 px-2 py-0.5 text-xs text-positive">
                    {VENV_LABELS[String(app.venv_level)] || `级别 ${app.venv_level}`}
                  </span>
                  {app.created_at && (
                    <span className="text-xs text-muted">{app.created_at.slice(0, 10)}</span>
                  )}
                </div>
              </div>
              <div className="ml-4 flex shrink-0 gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setRunTarget(app)
                    setRunArgs(JSON.stringify({ _: app.name }, null, 2))
                    setRunResult('')
                  }}
                >
                  运行
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-destructive hover:bg-destructive/10"
                  onClick={() => handleDelete(app.name)}
                >
                  删除
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 执行日志 */}
      {apps.length > 0 && (
        <div className="mt-8">
          <button
            onClick={() => setShowLogs(!showLogs)}
            className="flex items-center gap-2 text-sm font-medium text-muted hover:text-secondary transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              className={`transform transition-transform ${showLogs ? 'rotate-90' : ''}`}
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
            执行日志 {logs.length > 0 && `（${logs.length} 条）`}
          </button>

          {showLogs && (
            <div className="mt-3 space-y-1.5">
              {logs.length === 0 && (
                <p className="text-xs text-muted py-4 text-center">暂无执行记录</p>
              )}
              {logs.map((log, i) => (
                <div
                  key={`${log.timestamp}-${i}`}
                  className="flex items-center gap-3 rounded-xl border border-primary/5 bg-white/30 px-4 py-2.5 text-xs"
                >
                  {/* 状态图标 */}
                  <span className={`flex size-5 shrink-0 items-center justify-center rounded-full ${log.success ? 'bg-positive/10' : 'bg-destructive/10'}`}>
                    {log.success ? (
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#38A169" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : (
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#E53E3E" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18" />
                        <line x1="6" y1="6" x2="18" y2="18" />
                      </svg>
                    )}
                  </span>

                  {/* 工具名 */}
                  <span className="font-medium text-secondary w-28 truncate">{log.tool_name}</span>

                  {/* 参数摘要 */}
                  <span className="text-muted flex-1 truncate">{log.args_summary || '—'}</span>

                  {/* 时长 */}
                  <span className="text-muted w-16 text-right shrink-0">
                    {log.duration_ms < 1000
                      ? `${log.duration_ms}ms`
                      : `${(log.duration_ms / 1000).toFixed(1)}s`}
                  </span>

                  {/* 时间 */}
                  <span className="text-muted/60 w-16 text-right shrink-0">
                    {log.timestamp ? log.timestamp.slice(11, 19) : ''}
                  </span>
                </div>
              ))}
              {logs.length > 0 && (
                <button
                  onClick={fetchLogs}
                  className="w-full text-center text-xs text-muted/40 hover:text-muted py-1 transition-colors"
                >
                  刷新
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Run Dialog */}
      <Dialog open={!!runTarget} onOpenChange={(open) => !open && setRunTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>运行 {runTarget?.name}</DialogTitle>
            <DialogDescription>
              输入 JSON 参数并点击运行
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <textarea
              className="w-full rounded-xl border border-primary/20 bg-white/80 px-3 py-2.5 text-xs font-mono text-secondary backdrop-blur-sm focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/10"
              rows={6}
              value={runArgs}
              onChange={(e) => setRunArgs(e.target.value)}
              placeholder='{"key": "value"}'
            />
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setRunTarget(null)}>
                取消
              </Button>
              <Button onClick={handleRun} disabled={runLoading}>
                {runLoading ? '运行中...' : '运行'}
              </Button>
            </div>
            {runResult && (
              <div className="max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-secondary/5 px-3 py-2.5 text-xs text-secondary">
                {runResult}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
