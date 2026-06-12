import { useCallback } from 'react'
import { useStore } from '../store'

// Simple session loader for chips-agent
const useSessionLoader = () => {
  const setMessages = useStore((state) => state.setMessages)

  const getSession = useCallback(
    async (_sessionId: string) => {
      // TODO: implement session loading when chips-agent supports it
      return null
    },
    [setMessages]
  )

  const getSessions = useCallback(async () => {
    // TODO: implement sessions listing when chips-agent supports it
    return []
  }, [])

  return { getSession, getSessions }
}

export default useSessionLoader
