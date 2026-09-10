'use client'

// The module is served by the mod protocol: POST /{fn} with a JSON body,
// answering {"result": ...}. Through the fleet gateway that lives under
// /api/zcash; standalone it is the module's own API port.
const BASE = process.env.NEXT_PUBLIC_API_URL
  || `${process.env.NEXT_PUBLIC_BASE_PATH || ''}/api`

export type Json = Record<string, any>

export class ApiError extends Error {}

const TOKEN_KEY = 'zcash_module_token'

export const getToken = () =>
  (typeof window === 'undefined' ? '' : localStorage.getItem(TOKEN_KEY) || '')

export const setToken = (t: string) => {
  if (typeof window === 'undefined') return
  if (t) localStorage.setItem(TOKEN_KEY, t.trim())
  else localStorage.removeItem(TOKEN_KEY)
}

export async function call(fn: string, args: Json = {}): Promise<any> {
  let res: Response
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  try {
    res = await fetch(`${BASE}/${fn}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(args),
    })
  } catch (e: any) {
    throw new ApiError(`cannot reach the zcash API: ${e?.message || e}`)
  }

  const text = await res.text()
  let body: any = text
  try { body = JSON.parse(text) } catch { /* keep raw text */ }

  if (!res.ok) {
    const detail = (body && (body.detail || body.error)) || text || res.statusText
    throw new ApiError(`${fn}: ${detail}`)
  }
  // The mod server wraps successful returns in {result: ...}.
  const out = body && typeof body === 'object' && 'result' in body ? body.result : body
  // Module functions report failures in-band rather than via HTTP status.
  if (out && typeof out === 'object' && out.error) throw new ApiError(out.error)
  return out
}

// GET, for the surfaces that are documents rather than function calls --
// GET /mcp is the tool schema, and the MCP tab renders it as-is.
export async function get(path: string): Promise<any> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  let res: Response
  try {
    res = await fetch(`${BASE}/${path}`, { headers, cache: 'no-store' })
  } catch (e: any) {
    throw new ApiError(`cannot reach the zcash API: ${e?.message || e}`)
  }
  const text = await res.text()
  let body: any = text
  try { body = JSON.parse(text) } catch { /* keep raw text */ }
  if (!res.ok) {
    throw new ApiError(`${path}: ${(body && (body.detail || body.error)) || text}`)
  }
  return body
}

export const zec = (n: number | undefined | null, dp = 8) =>
  n == null ? '—' : `${Number(n).toFixed(dp).replace(/\.?0+$/, '')} ZEC`

export const zatToZec = (z: number | undefined | null) =>
  z == null ? '—' : zec(z / 1e8)

export const num = (n: number | undefined | null) =>
  n == null ? '—' : Number(n).toLocaleString()

export const usd = (n: number | string | undefined | null) => {
  if (n == null) return '—'
  const v = Number(n)
  if (!isFinite(v)) return '—'
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
}

export function timeAgo(t: string | undefined) {
  if (!t) return '—'
  const diff = Date.now() - new Date(t.replace(' ', 'T') + 'Z').getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}
