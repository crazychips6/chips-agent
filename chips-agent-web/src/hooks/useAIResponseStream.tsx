/**
 * 低层 SSE 流式读取 hook
 * 处理 chips-agent 的 SSE 格式：data: {"token": "..."}\n\n
 */
import { useCallback } from 'react'

export interface SSEMessage {
  token?: string
  [key: string]: unknown
}

export default function useAIResponseStream() {
  const streamResponse = useCallback(
    async (options: {
      apiUrl: string
      headers?: Record<string, string>
      body: Record<string, unknown>
      onToken: (token: string) => void
      onError: (error: Error) => void
      onDone: () => void
    }): Promise<void> => {
      const { apiUrl, headers = {}, body, onToken, onError, onDone } = options

      try {
        const response = await fetch(apiUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...headers,
          },
          body: JSON.stringify(body),
        })

        if (!response.ok) {
          const errData = await response.json().catch(() => ({}))
          throw new Error((errData as { detail?: string }).detail || `HTTP ${response.status}`)
        }

        if (!response.body) {
          throw new Error('No response body')
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        const processSSE = (): Promise<void> => {
          return reader.read().then(({ done, value }) => {
            if (done) {
              // process remaining buffer
              processBuffer(buffer, onToken)
              onDone()
              return
            }

            buffer += decoder.decode(value, { stream: true })
            buffer = processBuffer(buffer, onToken)
            return processSSE()
          })
        }

        await processSSE()
      } catch (error) {
        onError(error instanceof Error ? error : new Error(String(error)))
      }
    },
    []
  )

  return { streamResponse }
}

/**
 * Extract SSE data lines from buffer and call onToken for each.
 * Returns remaining incomplete buffer.
 */
function processBuffer(
  buffer: string,
  onToken: (token: string) => void
): string {
  const lines = buffer.split('\n')
  // Keep the last partial line in the buffer
  const completeLines = lines.slice(0, -1)
  const remaining = lines[lines.length - 1] ?? ''

  for (const line of completeLines) {
    // Skip empty lines and comments
    if (!line || line.startsWith(':')) continue

    // Parse SSE "data:" prefix
    if (line.startsWith('data: ')) {
      const data = line.slice(6).trim()

      // Check for done signal
      if (data === '[DONE]') continue

      try {
        const parsed = JSON.parse(data) as { token?: string }
        if (parsed.token) {
          onToken(parsed.token)
        }
      } catch {
        // Skip invalid JSON
      }
    }
  }

  return remaining
}
