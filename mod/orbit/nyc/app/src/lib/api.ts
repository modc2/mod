/** Typed client for the nyc GIS API. */

const BASE = process.env.NEXT_PUBLIC_API_URL || '/nyc/api'

export type LayerDef = {
  id: string
  title: string
  category: string
  kind: 'choropleth' | 'point' | 'line' | 'polygon' | 'outline' | 'heatmap'
  geometry: 'point' | 'line' | 'polygon'
  default_on: boolean
  description: string
  endpoint: string
  style?: Record<string, any>
  controls?: Record<string, any>
  source: { name: string; dataset: string; url: string; portal: string }
}

export type Catalog = {
  layers: LayerDef[]
  categories: { name: string; layers: string[] }[]
  count: number
  attribution: { name: string; url: string }[]
}

export type Breaks = {
  metric: string
  stops: number[]
  /** Domain ends. For a diverging metric these are the clipped, symmetric ends. */
  min: number | null
  max: number | null
  /** Unclipped extremes, present only on a clipped (diverging) scale. */
  true_min?: number
  true_max?: number
  count?: number
  diverging?: boolean
}

export type Choropleth = GeoJSON.FeatureCollection & {
  breaks: Breaks
  meta: Record<string, any>
}

export type Options = {
  metrics: Record<string, { label: string; unit: string; format: string }>
  geographies: Record<string, { label: string }>
  property_types: Record<string, { label: string }>
}

export type TrendPoint = {
  year: number
  sales: number
  median_price: number | null
  median_ppsf: number | null
  total_value: number | null
}

export type HousingQuery = {
  metric: string
  geography: string
  since: string
  until?: string
  property_type: string
}

async function get<T>(path: string, params?: Record<string, any>): Promise<T> {
  const qs = params
    ? '?' + new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null && v !== '')
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  const res = await fetch(`${BASE}${path}${qs}`)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = JSON.stringify((await res.json()).detail ?? detail)
    } catch {}
    throw new Error(`${path} → ${res.status}: ${detail}`)
  }
  return res.json()
}

export type ChatEvent =
  | { type: 'session'; id: string }
  | { type: 'tool'; name: string; input: Record<string, any> }
  | { type: 'text'; text: string }
  | { type: 'done'; ms?: number; session_id?: string }
  | { type: 'error'; error: string }

/**
 * Ask the NYC agent a question, yielding SSE events as they stream in.
 * Pass the session id from a previous turn's `done` event to continue a
 * conversation.
 */
export async function* chatStream(
  message: string,
  sessionId?: string,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId ?? null }),
  })
  if (!res.ok || !res.body) {
    let detail = res.statusText
    try {
      detail = JSON.stringify((await res.json()).detail ?? detail)
    } catch {}
    throw new Error(`chat → ${res.status}: ${detail}`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let cut
    while ((cut = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, cut)
      buf = buf.slice(cut + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data: ')) continue
        try {
          yield JSON.parse(line.slice(6)) as ChatEvent
        } catch {}
      }
    }
  }
}

export const api = {
  catalog: () => get<Catalog>('/layers'),
  options: () => get<Options>('/options'),
  view: () => get<any>('/view'),
  layer: (id: string) => get<GeoJSON.FeatureCollection>(`/layers/${id}`),
  housing: (q: HousingQuery) => get<Choropleth>('/layers/housing_prices', q),
  sales: (q: Partial<HousingQuery> & { limit?: number }) =>
    get<GeoJSON.FeatureCollection>('/layers/sales', q),
  prices: (q: { since: string; until?: string; property_type: string }) =>
    get<any>('/prices', q),
  trend: (q: { area?: string; property_type?: string }) =>
    get<{ series: TrendPoint[]; name?: string; area?: string }>('/trend', q),
  where: (q: string) =>
    get<{ name: string; lat: number; lng: number; type: string }[]>('/where', { q }),
}
