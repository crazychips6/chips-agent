import { useCallback } from 'react'

import useChatActions from '@/hooks/useChatActions'
import { useStore } from '../store'
import useAIResponseStream from './useAIResponseStream'

const useAIChatStreamHandler = () => {
  const setMessages = useStore((state) => state.setMessages)
  const { addMessage, focusChatInput } = useChatActions()
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const setIsStreaming = useStore((state) => state.setIsStreaming)
  const { streamResponse } = useAIResponseStream()

  const handleStreamResponse = useCallback(
    async (input: string) => {
      setIsStreaming(true)

      // Remove last failed pair if applicable
      setMessages((prevMessages) => {
        if (prevMessages.length >= 2) {
          const last = prevMessages[prevMessages.length - 1]
          const secondLast = prevMessages[prevMessages.length - 2]
          if (last.role === 'agent' && secondLast.role === 'user') {
            // If there's a pending empty agent message, remove the pair
            // (this happens on retry after error)
          }
        }
        return prevMessages
      })

      // Add user message
      addMessage({
        role: 'user',
        content: input,
        created_at: Math.floor(Date.now() / 1000),
      })

      // Add placeholder agent message
      addMessage({
        role: 'agent',
        content: '',
        created_at: Math.floor(Date.now() / 1000) + 1,
      })

      try {
        const apiUrl = `${selectedEndpoint}/api/chat`

        await streamResponse({
          apiUrl,
          body: { message: input },
          onToken: (token: string) => {
            setMessages((prevMessages) => {
              const newMessages = [...prevMessages]
              const lastMessage = newMessages[newMessages.length - 1]
              if (lastMessage && lastMessage.role === 'agent') {
                lastMessage.content += token
              }
              return newMessages
            })
          },
          onDone: () => {
            // streaming complete
          },
          onError: (error) => {
            setMessages((prevMessages) => {
              const newMessages = [...prevMessages]
              const last = newMessages[newMessages.length - 1]
              if (last && last.role === 'agent') {
                last.content += `\n\n[Error: ${error.message}]`
              }
              return newMessages
            })
          },
        })
      } catch (error) {
        setMessages((prevMessages) => {
          const newMessages = [...prevMessages]
          const lastMsg = newMessages[newMessages.length - 1]
          if (lastMsg && lastMsg.role === 'agent') {
            lastMsg.content += `\n\n[Error: ${error instanceof Error ? error.message : String(error)}]`
          }
          return newMessages
        })
      } finally {
        focusChatInput()
        setIsStreaming(false)
      }
    },
    [
      setMessages,
      addMessage,
      selectedEndpoint,
      setIsStreaming,
      streamResponse,
      focusChatInput,
    ]
  )

  return { handleStreamResponse }
}

export default useAIChatStreamHandler
