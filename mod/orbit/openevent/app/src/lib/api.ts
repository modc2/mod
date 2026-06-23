const API_URL = process.env.NEXT_PUBLIC_API_URL || '/openevent/api'

export async function api(path: string, opts?: { method?: string; body?: any }) {
  const res = await fetch(`${API_URL}/${path}`, {
    method: opts?.method || 'GET',
    headers: opts?.body ? { 'Content-Type': 'application/json' } : undefined,
    body: opts?.body ? JSON.stringify(opts.body) : undefined,
    cache: 'no-store',
  })
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) throw new Error(data?.detail || 'Request failed')
  return data
}

export type PlatformPill = {
  key: string
  label: string
  color: string
  url: string | null
  status?: string
}

export type EventItem = {
  uid: string
  id?: string
  source: string
  title: string
  description: string
  starts_at: number | null
  ends_at: number | null
  timezone?: string
  url: string | null
  cover_image: string | null
  venue: string
  address: string
  city: string | null
  lat: number | null
  lng: number | null
  online: boolean
  free: boolean
  price: { amount: number; currency: string } | null
  organizer: string
  mine?: boolean
  platforms: PlatformPill[]
  syndications?: Record<string, Syndication>
  edit_key?: string
}

export type Syndication = {
  platform: string
  status: string
  url?: string
  message?: string
  external_id?: string
  draft?: { title: string; when: string; where: string; text: string }
  at?: number
}

export type SourceReport = { key: string; label: string; ok: boolean; count: number; error?: string; ms: number }

export type DiscoverResult = {
  events: EventItem[]
  sources: SourceReport[]
  total: number
  raw: number
  city: string
  cached: boolean
  age: number
}

export type Platform = {
  key: string
  label: string
  color: string
  can_discover: boolean
  can_publish: boolean
  publish_mode: 'api' | 'assisted'
  connected: boolean
}

export type City = { key: string; label: string; country: string; lat: number; lng: number; zoom: number; default: boolean }
