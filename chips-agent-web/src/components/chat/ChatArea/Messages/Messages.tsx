import type { ChatMessage } from '@/types/os'

import { AgentMessage, UserMessage } from './MessageItem'
import { memo } from 'react'
import ChatBlankState from './ChatBlankState'

interface MessageListProps {
  messages: ChatMessage[]
}

const Messages = ({ messages }: MessageListProps) => {
  if (messages.length === 0) {
    return <ChatBlankState />
  }

  return (
    <>
      {messages.map((message, index) => {
        const key = `${message.role}-${message.created_at}-${index}`

        if (message.role === 'agent') {
          return <AgentMessage key={key} message={message} />
        }
        return <UserMessage key={key} message={message} />
      })}
    </>
  )
}

export default Messages
