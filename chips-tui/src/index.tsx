import { render } from "@opentui/solid"
import { createCliRenderer } from "@opentui/core"
import { Home } from "./screens/home"
import { Chat } from "./screens/chat"
import { createSignal } from "solid-js"

// 配置
const CONFIG = {
  backendUrl: process.env.CHIPS_BACKEND_URL || "http://localhost:8648",
}

// 路由状态
type Route = 
  | { type: "home" }
  | { type: "chat"; sessionId: string }

const [route, setRoute] = createSignal<Route>({ type: "home" })

// API 客户端
async function createSession(): Promise<string> {
  const res = await fetch(`${CONFIG.backendUrl}/api/sessions`, {
    method: "POST",
  })
  const data = await res.json()
  return data.session_id
}

async function sendMessage(sessionId: string, text: string): Promise<string> {
  const res = await fetch(`${CONFIG.backendUrl}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: text }),
  })
  const data = await res.json()
  return data.reply
}

// 流式输出
async function* streamMessage(sessionId: string, text: string): AsyncGenerator<string> {
  const res = await fetch(`${CONFIG.backendUrl}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message: text }),
  })
  
  const reader = res.body?.getReader()
  if (!reader) return
  
  const decoder = new TextDecoder()
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    
    const chunk = decoder.decode(value)
    const lines = chunk.split("\n")
    
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const data = line.slice(6)
        if (data === "[DONE]") return
        
        try {
          const parsed = JSON.parse(data)
          if (parsed.token) {
            yield parsed.token
          }
        } catch (e) {
          // 忽略解析错误
        }
      }
    }
  }
}

// 主应用
function App() {
  return (
    <>
      {route().type === "home" && (
        <Home onSend={async (text) => {
          const sessionId = await createSession()
          setRoute({ type: "chat", sessionId })
        }} />
      )}
      {route().type === "chat" && (
        <Chat 
          sessionId={(route() as { type: "chat"; sessionId: string }).sessionId}
          onSend={async (text) => {
            let result = ""
            const sessionId = (route() as { type: "chat"; sessionId: string }).sessionId
            for await (const chunk of streamMessage(sessionId, text)) {
              result += chunk
            }
            return result
          }}
          onBack={() => setRoute({ type: "home" })}
        />
      )}
    </>
  )
}

// 启动
async function main() {
  const renderer = await createCliRenderer({
    targetFps: 60,
    exitOnCtrlC: false,
  })

  await render(() => <App />, renderer)
}

main().catch(console.error)
