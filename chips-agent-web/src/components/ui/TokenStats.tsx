'use client'

import { useEffect, useState, useCallback } from 'react'
import { useAuthStore } from '@/store/auth'

interface TokenData {
  call_count: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_cost: number
  avg_latency_ms: number
}

export default function TokenStats() {
  const { isAuthenticated, getAuthHeader } = useAuthStore()
  const [data, setData] = useState<TokenData | null>(null)

  const fetchStats = useCallback(async () => {
    if (!isAuthenticated) return
    try {
      const res = await fetch('/api/stats/tokens', {
        headers: { ...getAuthHeader() },
      })
      if (res.ok) {
        setData(await res.json())
      }
    } catch {
      // silent fail
    }
  }, [isAuthenticated, getAuthHeader])

  useEffect(() => {
    if (!isAuthenticated) return
    fetchStats()
    const interval = setInterval(fetchStats, 5000)
    return () => clearInterval(interval)
  }, [isAuthenticated, fetchStats])

  if (!data || data.call_count === 0) return null

  return (
    <div className="fixed bottom-3 right-3 z-50 flex items-center gap-3 rounded-xl border border-primary/10 bg-white/80 px-3 py-1.5 text-xs text-muted shadow-sm backdrop-blur-sm">
      <span className="flex items-center gap-1">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
        {data.total_tokens.toLocaleString()} tokens
      </span>
      <span className="text-muted/50">|</span>
      <span className="flex items-center gap-1">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
          <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
          <line x1="6" y1="6" x2="6.01" y2="6" />
          <line x1="6" y1="18" x2="6.01" y2="18" />
        </svg>
        {data.call_count} calls
      </span>
      <span className="text-muted/50">|</span>
      <span>
        ${data.total_cost.toFixed(4)}
      </span>
    </div>
  )
}
