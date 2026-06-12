import MarkdownRenderer from '@/components/ui/typography/MarkdownRenderer'
import { useStore } from '@/store'
import type { ChatMessage } from '@/types/os'
import { memo } from 'react'
import AgentThinkingLoader from './AgentThinkingLoader'

interface MessageProps {
  message: ChatMessage
}

const AgentMessage = ({ message }: MessageProps) => {
  const isStreaming = useStore((state) => state.isStreaming)

  if (!message.content) {
    return (
      <div className="flex flex-row items-start gap-4 font-geist">
        <div className="flex-shrink-0">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-primary/20 to-primaryAccent/20">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6B46C1" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a4 4 0 0 1 4 4v1a4 4 0 0 1-4 4 4 4 0 0 1-4-4V6a4 4 0 0 1 4-4z" />
              <path d="M18 12v1a6 6 0 0 1-6 6 6 6 0 0 1-6-6v-1" />
              <path d="M12 19v3" />
            </svg>
          </div>
        </div>
        <div className="mt-2">
          <AgentThinkingLoader />
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-row items-start gap-4 font-geist">
      <div className="flex-shrink-0">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-primary/20 to-primaryAccent/20">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6B46C1" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2a4 4 0 0 1 4 4v1a4 4 0 0 1-4 4 4 4 0 0 1-4-4V6a4 4 0 0 1 4-4z" />
            <path d="M18 12v1a6 6 0 0 1-6 6 6 6 0 0 1-6-6v-1" />
            <path d="M12 19v3" />
          </svg>
        </div>
      </div>
      <div className="flex w-full flex-col gap-4 min-w-0">
        <MarkdownRenderer>{message.content}</MarkdownRenderer>
        {isStreaming && (
          <span className="inline-block h-4 w-2 animate-pulse rounded-full bg-primary" />
        )}
      </div>
    </div>
  )
}

const UserMessage = memo(({ message }: MessageProps) => {
  return (
    <div className="flex items-start gap-4 pt-4 text-start max-md:break-words">
      <div className="flex-shrink-0">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-brand/20 to-primary/20">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ED64A6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        </div>
      </div>
      <div className="text-md rounded-lg font-geist text-secondary break-words">
        {message.content}
      </div>
    </div>
  )
})

AgentMessage.displayName = 'AgentMessage'
UserMessage.displayName = 'UserMessage'
export { AgentMessage, UserMessage }
