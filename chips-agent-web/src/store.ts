import { create } from 'zustand'
import { type ChatMessage } from '@/types/os'

interface Store {
  isStreaming: boolean
  setIsStreaming: (isStreaming: boolean) => void
  isEndpointActive: boolean
  setIsEndpointActive: (isActive: boolean) => void
  messages: ChatMessage[]
  setMessages: (
    messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])
  ) => void
  chatInputRef: React.RefObject<HTMLTextAreaElement | null>
}

export const useStore = create<Store>()((set) => ({
  isStreaming: false,
  setIsStreaming: (isStreaming) => set(() => ({ isStreaming })),
  isEndpointActive: false,
  setIsEndpointActive: (isActive) =>
    set(() => ({ isEndpointActive: isActive })),
  messages: [],
  setMessages: (messages) =>
    set((state) => ({
      messages:
        typeof messages === 'function'
          ? messages(state.messages)
          : messages,
    })),
  chatInputRef: { current: null },
}))
