const API_URL = process.env.NEXT_PUBLIC_API_URL || '/openplay/api'

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
}

export type Sport = { key: string; label: string; emoji: string; color: string }
export type Venue = { id: string; builtin: boolean; name: string; city: string; neighborhood: string; lat: number; lng: number; sports: string[] }
export type City = { key: string; label: string; country: string; lat: number | null; lng: number | null; zoom: number; venues: number; default: boolean }
