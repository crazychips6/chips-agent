// ── chips-agent 数据类型 ──

export interface ChatMessage {
  role: 'user' | 'agent'
  content: string
  created_at: number
}

export interface SessionEntry {
  session_id: string
  session_name: string
  created_at: number
  updated_at?: number
}

export interface Sessions {
  data: SessionEntry[]
}

export interface ToolCall {
  name: string
  arguments: string
  result?: string
}

// ── API 响应 ──

export interface ChatChunk {
  token: string
}

export interface HealthResponse {
  status: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
}
