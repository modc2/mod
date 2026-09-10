'use client'

// The module is served by the mod protocol: POST /{fn} with a JSON body,
// answering {"result": ...}. Through the fleet gateway that lives under
// /api/monero; standalone it is the module's own API port.
const BASE = process.env.NEXT_PUBLIC_API_URL
  || `${process.env.NEXT_PUBLIC_BASE_PATH || ''}/api`

export type Json = Record<string, any>

export class ApiError extends Error {}

const TOKEN_KEY = 'monero_module_token'

export const getToken = () =>
  (typeof window === 'undefined' ? '' : localStorage.getItem(TOKEN_KEY) || '')

export const setToken = (t: string) => {
  if (typeof window === 'undefined') return
  if (t) localStorage.setItem(TOKEN_KEY, t.trim())
  else localStorage.removeItem(TOKEN_KEY)
}

export async function call(fn: string, args: Json = {}, timeoutMs = 180000): Promise<any> {
  let res: Response
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  // A scan can legitimately run for minutes, so the abort budget is generous
  // rather than the browser default.
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    res = await fetch(`${BASE}/${fn}`, {
      method: 'POST', headers, body: JSON.stringify(args), signal: controller.signal,
    })
  } catch (e: any) {
    throw new ApiError(e?.name === 'AbortError'
      ? `${fn} took longer than ${Math.round(timeoutMs / 1000)}s and was cancelled`
      : `cannot reach the monero API: ${e?.message || e}`)
  } finally {
    clearTimeout(timer)
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

export const ATOMIC = 1e12

export const xmr = (n: number | undefined | null, dp = 12) =>
  n == null ? '—' : `${trim(Number(n).toFixed(dp))} XMR`

export const atomicToXmr = (a: number | undefined | null, dp = 12) =>
  a == null ? '—' : xmr(a / ATOMIC, dp)

const trim = (s: string) => s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s

export const num = (n: number | undefined | null) =>
  n == null ? '—' : Number(n).toLocaleString()

export const usd = (n: number | string | undefined | null) => {
  if (n == null) return '—'
  const v = Number(n)
  if (!isFinite(v)) return '—'
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
}

export const hashrate = (h: number | undefined | null) => {
  if (!h) return '—'
  const units = ['H/s', 'kH/s', 'MH/s', 'GH/s', 'TH/s']
  let i = 0, v = h
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++ }
  return `${v.toFixed(2)} ${units[i]}`
}

export const bytes = (b: number | undefined | null) => {
  if (b == null) return '—'
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} kB`
  return `${(b / 1024 / 1024).toFixed(2)} MB`
}

export function timeAgo(unix: number | undefined | null) {
  if (!unix) return '—'
  const mins = Math.floor((Date.now() / 1000 - unix) / 60)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export const short = (s: string | undefined | null, n = 10) =>
  !s ? '—' : s.length <= n * 2 ? s : `${s.slice(0, n)}…${s.slice(-n)}`
