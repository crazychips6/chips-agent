import { Component, createSignal, For } from "solid-js"

interface ChatProps {
  sessionId: string
  onSend: (text: string) => Promise<string>
  onBack: () => void
}

interface Message {
  role: "user" | "assistant"
  content: string
}

export const Chat: Component<ChatProps> = (props) => {
  const [messages, setMessages] = createSignal<Message[]>([])
  const [input, setInput] = createSignal("")
  const [isLoading, setIsLoading] = createSignal(false)

  const handleSend = async () => {
    const text = input()
    if (!text.trim() || isLoading()) return

    // 添加用户消息
    setMessages([...messages(), { role: "user", content: text }])
    setInput("")
    setIsLoading(true)

    try {
      // 调用后端 API（流式）
      const reply = await props.onSend(text)
      
      // 添加助手消息
      setMessages([...messages(), { role: "assistant", content: reply }])
    } catch (error) {
      console.error("发送消息失败:", error)
      setMessages([...messages(), { role: "assistant", content: "发送消息失败" }])
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <box flexDirection="column" height="100%">
      {/* 标题栏 */}
      <box flexDirection="row" justifyContent="space-between" padding={1}>
        <text color="#808080">Session: {props.sessionId.slice(0, 8)}...</text>
        <text 
          color="#808080" 
          clickable
          onClick={props.onBack}
        >
          [返回]
        </text>
      </box>

      {/* 消息列表 */}
      <scrollbox flexGrow={1} padding={1}>
        <For each={messages()}>
          {(msg) => (
            <box marginBottom={1}>
              {msg.role === "user" ? (
                <box borderLeft="tall #FF6A00" paddingLeft={2} background="#141414">
                  <text color="#eeeeee">{msg.content}</text>
                </box>
              ) : (
                <box paddingLeft={3}>
                  <text>{msg.content}</text>
                </box>
              )}
            </box>
          )}
        </For>
        
        {isLoading() && (
          <box paddingLeft={3}>
            <text color="#808080">思考中...</text>
          </box>
        )}
      </scrollbox>

      {/* 输入框 */}
      <box padding={1}>
        <textarea
          value={input()}
          onInput={(e) => setInput(e.target.value)}
          placeholder="输入消息... (Ctrl+Enter 发送)"
          height={3}
          onKeydown={(e) => {
            if (e.key === "Enter" && e.ctrl) {
              handleSend()
            }
          }}
        />
      </box>
    </box>
  )
}
