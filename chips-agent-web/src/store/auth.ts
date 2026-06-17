import { create } from 'zustand'

interface AuthState {
  token: string | null
  username: string | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (username: string, password: string) => Promise<boolean>
  logout: () => void
  getAuthHeader: () => Record<string, string>
}

const TOKEN_KEY = 'chips_agent_token'
const USER_KEY = 'chips_agent_user'

function loadPersistedState() {
  if (typeof window === 'undefined') {
    return { token: null, username: null }
  }
  try {
    const token = localStorage.getItem(TOKEN_KEY)
    const username = localStorage.getItem(USER_KEY)
    return { token, username }
  } catch {
    return { token: null, username: null }
  }
}

function persistState(token: string | null, username: string | null) {
  if (typeof window === 'undefined') return
  try {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.setItem(USER_KEY, username ?? '')
    } else {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    }
  } catch {
    // localStorage 不可用时静默失败
  }
}

const persisted = loadPersistedState()

export const useAuthStore = create<AuthState>()((set, get) => ({
  token: persisted.token,
  username: persisted.username,
  isAuthenticated: !!persisted.token,
  isLoading: false,

  login: async (username: string, password: string) => {
    set({ isLoading: true })
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })

      if (!res.ok) {
        set({ isLoading: false })
        return false
      }

      const data = await res.json()
      const token = data.access_token

      persistState(token, username)
      set({ token, username, isAuthenticated: true, isLoading: false })
      return true
    } catch {
      set({ isLoading: false })
      return false
    }
  },

  logout: () => {
    persistState(null, null)
    set({ token: null, username: null, isAuthenticated: false })
  },

  getAuthHeader: (): Record<string, string> => {
    const { token } = get()
    return token ? { Authorization: `Bearer ${token}` } : {} as Record<string, string>
  },
}))
