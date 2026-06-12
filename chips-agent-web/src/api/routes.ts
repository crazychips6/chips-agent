// ── chips-agent API 路由 ──

export const APIRoutes = {
  Chat: (base: string) => `${base}/api/chat`,
  Health: (base: string) => `${base}/api/health`,
  Login: (base: string) => `${base}/api/login`,
}

export const DEFAULT_ENDPOINT = 'http://localhost:8648'
