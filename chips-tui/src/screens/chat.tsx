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
  const [isLoading, setIsLoading] = createSignal(false)

  const handleSend = async (text: string) => {
    if (!text.trim() || isLoading()) return

    // 添加用户消息
    setMessages([...messages(), { role: "user", content: text }])
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
        <text fg="#808080">Session: {props.sessionId.slice(0, 8)}...</text>
        <text fg="#808080">[返回]</text>
      </box>

      {/* 消息列表 */}
      <scrollbox flexGrow={1} padding={1}>
        <For each={messages()}>
          {(msg) => (
            <box marginBottom={1}>
              {msg.role === "user" ? (
                <box border={["left"]} borderColor="#FF6A00" paddingLeft={2} backgroundColor="#141414">
                  <text fg="#eeeeee">{msg.content}</text>
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
            <text fg="#808080">思考中...</text>
          </box>
        )}
      </scrollbox>

      {/* 输入框 */}
      <box padding={1}>
        <textarea
          placeholder="输入消息... (Ctrl+Enter 发送)"
          height={3}
        />
      </box>
    </box>
  )
}
