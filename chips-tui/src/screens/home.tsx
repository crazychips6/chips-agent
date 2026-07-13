import { Component } from "solid-js"
import { Logo } from "../components/logo"
import { StarryBackground } from "../components/starry-background"

interface HomeProps {
  onSend: (text: string) => Promise<void>
}

export const Home: Component<HomeProps> = (props) => {
  return (
    <>
      <StarryBackground />
      <box flexGrow={1} alignItems="center" paddingLeft={8} paddingRight={8}>
        <box flexGrow={1} minHeight={0} />
        <box height={4} minHeight={0} flexShrink={1} />
        <box flexShrink={0}>
          <Logo />
        </box>
        <box height={1} minHeight={0} flexShrink={1} />
        <box
          width="100%"
          maxWidth={75}
          zIndex={1000}
          paddingTop={1}
          flexShrink={0}
        >
          {/* 输入框组件 */}
          <textarea
            placeholder="给 chips 发消息..."
            height={3}
            onKeydown={(e) => {
              if (e.key === "Enter" && e.ctrl) {
                const text = e.target.value
                if (text.trim()) {
                  props.onSend(text.trim())
                }
              }
            }}
          />
        </box>
        <box flexGrow={1} minHeight={0} />
      </box>
    </>
  )
}
