import { useCallback } from 'react'
import { toast } from 'sonner'

import { useStore } from '../store'

import { type ChatMessage } from '@/types/os'
import { checkHealth } from '@/api/os'

const useChatActions = () => {
  const { chatInputRef } = useStore()
  const setMessages = useStore((state) => state.setMessages)
  const setIsEndpointActive = useStore((state) => state.setIsEndpointActive)

  const getStatus = useCallback(async () => {
    const endpoint = useStore.getState().selectedEndpoint
    try {
      return await checkHealth(endpoint)
    } catch {
      return false
    }
  }, [])

  const clearChat = useCallback(() => {
    setMessages([])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const focusChatInput = useCallback(() => {
    setTimeout(() => {
      requestAnimationFrame(() => chatInputRef?.current?.focus())
    }, 0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const addMessage = useCallback(
    (message: ChatMessage) => {
      setMessages((prevMessages) => [...prevMessages, message])
    },
    [setMessages]
  )

  const initialize = useCallback(async () => {
    try {
      const isActive = await getStatus()
      setIsEndpointActive(isActive)
      return isActive
    } catch {
      setIsEndpointActive(false)
      return false
    }
  }, [getStatus, setIsEndpointActive])

  return {
    clearChat,
    addMessage,
    focusChatInput,
    initialize,
  }
}

export default useChatActions
