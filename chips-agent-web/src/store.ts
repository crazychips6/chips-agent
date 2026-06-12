import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { type ChatMessage } from '@/types/os'
import { DEFAULT_ENDPOINT } from '@/api/routes'

interface Store {
  hydrated: boolean
  setHydrated: () => void
  isStreaming: boolean
  setIsStreaming: (isStreaming: boolean) => void
  isEndpointActive: boolean
  setIsEndpointActive: (isActive: boolean) => void
  messages: ChatMessage[]
  setMessages: (
    messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])
  ) => void
  chatInputRef: React.RefObject<HTMLTextAreaElement | null>
  selectedEndpoint: string
  setSelectedEndpoint: (endpoint: string) => void
}

export const useStore = create<Store>()(
  persist(
    (set) => ({
      hydrated: false,
      setHydrated: () => set({ hydrated: true }),
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
      selectedEndpoint: DEFAULT_ENDPOINT,
      setSelectedEndpoint: (selectedEndpoint) =>
        set(() => ({ selectedEndpoint })),
    }),
    {
      name: 'chips-endpoint-storage',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        selectedEndpoint: state.selectedEndpoint,
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHydrated?.()
      },
    }
  )
)
