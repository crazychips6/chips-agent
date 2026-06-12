import { APIRoutes } from './routes'

/**
 * Check if chips-agent server is reachable.
 */
export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(APIRoutes.Health, { method: 'GET' })
    return res.ok
  } catch {
    return false
  }
}
