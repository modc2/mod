const API_URL = process.env.NEXT_PUBLIC_API_URL || '/openbnb/api'

/** Owner calls carry the secret in a header, so anything the console does is a
 *  one-liner with curl too. */
export async function api(path: string, opts?: { method?: string; body?: any; ownerKey?: string }) {
  const headers: Record<string, string> = {}
  if (opts?.body) headers['Content-Type'] = 'application/json'
  if (opts?.ownerKey) headers['X-Owner-Key'] = opts.ownerKey
  const res = await fetch(`${API_URL}/${path}`, {
    method: opts?.method || (opts?.body ? 'POST' : 'GET'),
    headers,
    body: opts?.body ? JSON.stringify(opts.body) : undefined,
    cache: 'no-store',
  })
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Request failed')
  return data
}

export type Listing = {
  id: string
  host: string
  host_wallet: string
  title: string
  kind: string
  city: string
  address: string
  guests: number
  bedrooms: number
  beds: number
  baths: number
  amenities: string[]
  photos: string[]
  notes: string
  price: number
  cleaning_fee: number
  min_nights: number
  max_nights: number
  instant_book: boolean
  blocked: string[]
  status: string
  created_at: number
  booked?: string[]
  stays?: number
  host_key?: string
}

export type QuoteLine = { label: string; amount: number; rules?: string[] }

export type Quote = {
  listing_id: string
  checkin: string
  checkout: string
  nights: number
  guests: number
  currency: string
  subtotal: number
  rule_adjustment: number
  cleaning_fee: number
  service_fee: number
  total: number
  host_payout: number
  lines: QuoteLine[]
  tags: string[]
  instant: boolean
  needs_approval: boolean
  matched_rules: string[]
  trace?: RuleTrace[]
  facts?: Record<string, any>
}

export type RuleTrace = {
  rule: string
  id: string
  when?: string
  matched: boolean
  then?: Record<string, any> | null
  error?: string
}

export type Booking = {
  id: string
  listing_id: string
  listing_title: string
  city: string
  host: string
  guest: string
  checkin: string
  checkout: string
  nights: number
  guests: number
  note: string
  quote: Quote
  status: string
  created_at: number
  refund?: { amount: number; tier: string; days_out: number }
  history: { at: number; event: string; by?: string; reason?: string }[]
}

export type Rule = {
  id: string
  name: string
  when: string
  then: Record<string, any>
  enabled: boolean
  created_at: number
  hits: number
}

export type Hook = { id: string; url: string; events: string[]; secret: string; created_at: number }

export type PolicyField = { type: string; note: string; default: any }

export type Status = {
  market_name: string
  tagline: string
  currency: string
  chain: string
  fee_bps: number
  listings: number
  live_listings: number
  cities: string[]
  bookings: number
  bookings_by_status: Record<string, number>
  nights_booked: number
  volume: number
  rules: number
  active_rules: number
  hooks: number
}

export type Kind = { key: string; label: string; emoji: string; allowed: boolean }

/** Nights between two YYYY-MM-DD dates (check-out is not a night). */
export function nightsBetween(a: string, b: string): number {
  if (!a || !b) return 0
  const d = (Date.parse(b) - Date.parse(a)) / 86400000
  return Number.isFinite(d) ? Math.max(0, Math.round(d)) : 0
}

export function addDays(iso: string, n: number): string {
  const d = new Date(iso + 'T00:00:00Z')
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

export function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export function money(n: number, currency = 'USDC'): string {
  const v = Math.round((n + Number.EPSILON) * 100) / 100
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency}`
}
