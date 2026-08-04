const API_URL = process.env.NEXT_PUBLIC_API_URL || '/openplay/api'

export async function api(path: string, opts?: { method?: string; body?: any }) {
  const res = await fetch(`${API_URL}/${path}`, {
    method: opts?.method || 'GET',
    headers: opts?.body ? { 'Content-Type': 'application/json' } : undefined,
    body: opts?.body ? JSON.stringify(opts.body) : undefined,
    cache: 'no-store',
  })
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) {
    // A 400 can carry structure (which games you'd clash with) — keep it on
    // the error so the caller can show more than a sentence.
    const detail = data?.detail
    const err = new Error((typeof detail === 'string' ? detail : detail?.error) || 'Request failed') as ApiError
    err.status = res.status
    err.body = typeof detail === 'object' && detail ? detail : data
    throw err
  }
  return data
}

export type ApiError = Error & { status?: number; body?: any }

export type Conflict = {
  kind: 'venue' | 'pool' | 'organizer'
  severity: 'blocking' | 'warning'
  game_id: string
  title: string
  sport: string
  venue: string
  admin: string
  occ: string
  starts_at: number
  overlap_min: number
  distance_m: number | null
  detail: string
}

export type AgentRequest = {
  request_id: string
  status: string
  agent: string
  reason: string
  proposal: Record<string, any>
  warnings: Conflict[]
  created_at: number
  expires_at: number
  game_id?: string
  conflicts_now?: { ok: boolean; blocking: Conflict[]; warnings: Conflict[] }
}

export type Occurrence = {
  game_id: string
  occ: string
  occ_ts: number
  sport: string
  title: string
  city: string
  venue: string
  neighborhood: string
  lat: number | null
  lng: number | null
  starts_at: number
  duration_min: number
  capacity: number
  going: number
  spots_left: number | null
  waitlist: number
  recurring: boolean
  recurrence: string
  free: boolean
  cost: { amount: number; currency: string } | null
  admin: string
  notes: string
  status: string
}

export type GameDetail = Occurrence & {
  players: { handle: string; wallet: string; paid: any; at: number }[]
  waitlist_players?: any[]
  invited: string[]
  upcoming: string[]
  links: GameLink[]
  chat_count: number
}

export type GameLink = { label: string; url: string }
export type ChatMessage = { id: string; handle: string; text: string; ts: number }

export type Sport = { key: string; label: string; emoji: string; color: string }
export type Venue = { id: string; builtin: boolean; name: string; city: string; neighborhood: string; lat: number; lng: number; sports: string[] }
export type City = { key: string; label: string; country: string; lat: number | null; lng: number | null; zoom: number; venues: number; default: boolean }
