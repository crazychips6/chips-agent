import { APIRoutes } from './routes'

/**
 * Check if chips-agent server is reachable.
 */
export async function checkHealth(base: string): Promise<boolean> {
  try {
    const res = await fetch(APIRoutes.Health(base), { method: 'GET' })
    return res.ok
  } catch {
    return false
  }
}

/**
 * Login to chips-agent server.
 */
export async function loginAPI(
  base: string,
  username: string,
  password: string
): Promise<string | null> {
  try {
    const res = await fetch(APIRoutes.Login(base), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!res.ok) return null
    const data = await res.json()
    return data.access_token as string
  } catch {
    return null
  }
}
