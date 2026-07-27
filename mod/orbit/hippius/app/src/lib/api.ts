// Hippius BYOK client — credentials live in this browser's localStorage only
// and are attached per-request as x-hip-* headers. The API never stores them.

export type Creds = {
  key: string
  secret: string
  endpoint: string
  region: string
}

export const DEFAULT_ENDPOINT = 'https://s3.hippius.com'
export const DEFAULT_REGION = 'decentralized'
export const ENDPOINTS = [
  'https://s3.hippius.com',
  'https://eu-central-1.hippius.com',
  'https://us-east-1.hippius.com',
]

const LS_CREDS = 'hippius:creds'
const LS_API = 'hippius:apiBase'

export function apiBase(): string {
  if (typeof window === 'undefined') return ''
  try {
    const override = localStorage.getItem(LS_API)
    if (override) return override
  } catch {}
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL
  // Behind the mod gateway the app lives at /hippius and the API at /hippius/api.
  if (window.location.pathname.startsWith('/hippius')) return '/hippius/api'
  return 'http://localhost:50142'
}

export function loadCreds(): Creds | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(LS_CREDS)
    if (!raw) return null
    const c = JSON.parse(raw)
    if (!c.key || !c.secret) return null
    return {
      key: c.key,
      secret: c.secret,
      endpoint: c.endpoint || DEFAULT_ENDPOINT,
      region: c.region || DEFAULT_REGION,
    }
  } catch {
    return null
  }
}

export function saveCreds(c: Creds): boolean {
  try {
    localStorage.setItem(LS_CREDS, JSON.stringify(c))
    return true
  } catch {
    return false // storage quota / private mode — session-only keys still work in memory
  }
}

export function clearCreds() {
  try {
    localStorage.removeItem(LS_CREDS)
  } catch {}
}

export class ApiError extends Error {
  status: number
  code?: string
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

function headers(c: Creds): Record<string, string> {
  return {
    'x-hip-key': c.key,
    'x-hip-secret': c.secret,
    'x-hip-endpoint': c.endpoint,
    'x-hip-region': c.region,
  }
}

async function parseError(res: Response): Promise<never> {
  let msg = `HTTP ${res.status}`
  let code: string | undefined
  try {
    const body = await res.json()
    const d = body.detail ?? body
    if (typeof d === 'string') msg = d
    else {
      code = d.error
      msg = d.message || d.error || msg
    }
  } catch {}
  throw new ApiError(res.status, msg, code)
}

export async function api<T = any>(
  c: Creds | null,
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: {
      ...(c ? headers(c) : {}),
      ...(init.headers as Record<string, string> | undefined),
    },
  })
  if (!res.ok) return parseError(res)
  return res.json()
}

export async function upload(
  c: Creds,
  bucket: string,
  file: File,
  key?: string
): Promise<any> {
  const form = new FormData()
  form.append('file', file)
  if (key) form.append('key', key)
  const res = await fetch(`${apiBase()}/buckets/${encodeURIComponent(bucket)}/objects`, {
    method: 'POST',
    headers: headers(c),
    body: form,
  })
  if (!res.ok) return parseError(res)
  return res.json()
}

export async function downloadObject(c: Creds, bucket: string, key: string) {
  // Presign, then let the browser fetch straight from Hippius — no proxy hop.
  const { url } = await api<{ url: string }>(
    c,
    `/buckets/${encodeURIComponent(bucket)}/presign?key=${encodeURIComponent(key)}&op=get`
  )
  window.open(url, '_blank')
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n
  let i = -1
  do {
    v /= 1024
    i++
  } while (v >= 1024 && i < units.length - 1)
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}
