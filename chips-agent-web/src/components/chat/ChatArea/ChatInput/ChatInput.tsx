'use client'
import { useState } from 'react'
import { toast } from 'sonner'
import { TextArea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { useStore } from '@/store'
import useAIChatStreamHandler from '@/hooks/useAIStreamHandler'

const ChatInput = () => {
  const { chatInputRef } = useStore()

  const { handleStreamResponse } = useAIChatStreamHandler()
  const [inputMessage, setInputMessage] = useState('')
  const isStreaming = useStore((state) => state.isStreaming)
  const isEndpointActive = useStore((state) => state.isEndpointActive)

  const handleSubmit = async () => {
    if (!inputMessage.trim()) return

    const currentMessage = inputMessage
    setInputMessage('')

    try {
      await handleStreamResponse(currentMessage)
    } catch (error) {
      toast.error(
        `Error: ${
          error instanceof Error ? error.message : String(error)
        }`
      )
    }
  }

  return (
    <div className="relative mx-auto mb-1 flex w-full max-w-2xl items-end justify-center gap-x-2 font-geist max-md:px-3 max-md:mb-3">
      <TextArea
        placeholder={isEndpointActive ? 'Ask chips-agent...' : 'Server not connected'}
        value={inputMessage}
        onChange={(e) => setInputMessage(e.target.value)}
        onKeyDown={(e) => {
          if (
            e.key === 'Enter' &&
            !e.nativeEvent.isComposing &&
            !e.shiftKey &&
            !isStreaming &&
            isEndpointActive
          ) {
            e.preventDefault()
            handleSubmit()
          }
        }}
        className="w-full rounded-xl border border-primary/20 bg-white px-4 py-3 text-sm text-secondary placeholder:text-muted focus:border-primary/40 focus:outline-none max-md:text-base"
        disabled={!isEndpointActive || isStreaming}
        ref={chatInputRef}
      />
      <Button
        onClick={handleSubmit}
        disabled={!isEndpointActive || !inputMessage.trim() || isStreaming}
        size="icon"
        className="rounded-xl bg-primary p-5 text-white hover:bg-primary/80 disabled:opacity-50"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M22 2L11 13" />
          <path d="M22 2L15 22L11 13L2 9L22 2Z" />
        </svg>
      </Button>
    </div>
  )
}

export default ChatInput
