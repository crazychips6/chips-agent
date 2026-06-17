'use client'

import { useCallback } from 'react'
import { useStore } from '../store'
import { useAuthStore } from '@/store/auth'
import { type ChatMessage } from '@/types/os'

const useChatActions = () => {
  const { chatInputRef } = useStore()
  const setMessages = useStore((state) => state.setMessages)
  const setIsEndpointActive = useStore((state) => state.setIsEndpointActive)
  const getAuthHeader = useAuthStore((state) => state.getAuthHeader)

  const getStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/health', {
        headers: { ...getAuthHeader() },
      })
      return res.ok
    } catch {
      return false
    }
  }, [getAuthHeader])

  const clearChat = useCallback(() => {
    setMessages([])
  }, [setMessages])

  const focusChatInput = useCallback(() => {
    setTimeout(() => {
      requestAnimationFrame(() => chatInputRef?.current?.focus())
    }, 0)
  }, [chatInputRef])

  const addMessage = useCallback(
    (message: ChatMessage) => {
      setMessages((prevMessages) => [...prevMessages, message])
    },
    [setMessages],
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
