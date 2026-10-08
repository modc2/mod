/** Typed client for the nyc GIS API. */

export const BASE = process.env.NEXT_PUBLIC_API_URL || '/nyc/api'

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
  /** Set on layers that read live instruments; the UI re-polls on this cadence. */
  live?: boolean
  refresh_seconds?: number
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
  /** A validated nyc_map / nyc_infographic call, for the page to apply. */
  | { type: 'display'; directive: import('./scene').Directive }

/**
 * Ask the NYC agent a question, yielding SSE events as they stream in.
 * Pass the session id from a previous turn's `done` event to continue a
 * conversation.
 */
export async function* chatStream(
  message: string,
  sessionId?: string,
  mapState?: Record<string, any>,
  token?: string | null,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    // The token is how the agent knows it may save datasets for the owner;
    // without one it gets the read-only toolset.
    body: JSON.stringify({ message, session_id: sessionId ?? null,
                           map_state: mapState ?? null, token: token ?? null }),
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

/** The MCP surface, as served by `/tools` — what the docs page renders from. */
export type ToolDef = {
  name: string
  title?: string
  description: string
  inputSchema: {
    type: 'object'
    properties: Record<string, { type: string; description: string; default?: any }>
    required?: string[]
  }
  annotations?: Record<string, any>
}

export type McpSurface = {
  count: number
  groups: Record<string, string[]>
  tools: ToolDef[]
  prompts: {
    name: string
    title?: string
    description: string
    arguments?: { name: string; description: string; required?: boolean }[]
  }[]
  resources: {
    uri: string
    name: string
    title?: string
    description: string
    mimeType: string
  }[]
  server: { name: string; title?: string; version: string }
  instructions: string
  mcp: {
    http: string
    stdio: string
    protocol: string
    supported: string[]
    capabilities: Record<string, any>
  }
}

/** The population layer: which census statistic, at which grain. */
export type PopulationQuery = { metric: string; geography: string }

/** The full brief — one self-contained HTML page, safe to save and send. */
export const REPORT_URL = `${BASE}/report`
export const reportCsv = (geography: string) => `${BASE}/report.csv?geography=${geography}`

export const api = {
  catalog: () => get<Catalog>('/layers'),
  tools: () => get<McpSurface>('/tools'),
  options: () => get<Options>('/options'),
  view: () => get<any>('/view'),
  layer: (id: string) => get<GeoJSON.FeatureCollection>(`/layers/${id}`),
  housing: (q: HousingQuery) => get<Choropleth>('/layers/housing_prices', q),
  population: (q: PopulationQuery) => get<Choropleth>('/layers/population', q),
  sales: (q: Partial<HousingQuery> & { limit?: number }) =>
    get<GeoJSON.FeatureCollection>('/layers/sales', q),
  prices: (q: { since: string; until?: string; property_type: string }) =>
    get<any>('/prices', q),
  trend: (q: { area?: string; property_type?: string }) =>
    get<{ series: TrendPoint[]; name?: string; area?: string }>('/trend', q),
  where: (q: string) =>
    get<{ name: string; lat: number; lng: number; type: string }[]>('/where', { q }),
  news: (q: { topic?: string; limit?: number }) => get<NewsFeed>('/news', q),
  crime: () => get<CrimeSummary>('/crime'),
  market: () => get<MarketSummary>('/market'),
}

// ── the owner's saved datasets ────────────────────────────────────────────

export type SavedDataset = {
  slug: string
  title: string
  description: string
  kind: 'geojson' | 'overlay' | 'url'
  geometry: 'point' | 'line' | 'polygon'
  features?: number
  added_at: string
  added_by?: string
  source?: { name: string; dataset: string; url: string; portal: string }
}

export type SavedList = {
  count: number; max: number; owner: string | null
  writable: boolean; datasets: SavedDataset[]
}

/** What POST /data accepts: a title plus exactly one source. */
export type AddDataBody = {
  title: string
  description?: string
  geojson?: GeoJSON.FeatureCollection
  dataset?: string
  url?: string
  mode?: 'points' | 'heat' | 'areas'
  where?: string
  by?: 'zip' | 'borough'
  value?: string
  per_capita?: boolean
}

async function dataFetch<T>(path: string, token: string | null,
                            init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'content-type': 'application/json' }
  if (token) headers.authorization = `Bearer ${token}`
  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = String((await res.json()).detail ?? detail) } catch {}
    throw new Error(detail)
  }
  return res.json()
}

export const userData = {
  list: (token: string | null) => dataFetch<SavedList>('/data', token),
  add: (body: AddDataBody, token: string) =>
    dataFetch<SavedDataset>('/data', token, { method: 'POST', body: JSON.stringify(body) }),
  remove: (slug: string, token: string) =>
    dataFetch<{ removed: string }>(`/data/${slug}`, token, { method: 'DELETE' }),
  refresh: (slug: string, token: string) =>
    dataFetch<{ slug: string; features: number }>(`/data/${slug}/refresh`, token, { method: 'POST' }),
}

/** One headline from the newsroom feeds, tagged with a crude topic. */
export type NewsItem = {
  title: string; url: string; source: string
  published: string | null; summary: string; topic: string
}
export type NewsFeed = {
  fetched: string; sources: string[]; topic: string
  count: number; total: number; items: NewsItem[]
}

/** The safety picture: this year vs the same window last year. */
export type CrimeSummary = {
  window: { since: string; until: string }
  complaints: {
    total: number; felony: number; misdemeanor: number; violation: number
    prior_total: number; change_pct: number | null; per_1k_residents: number
  }
  shootings: {
    this_year: { incidents: number }
    last_year_same_window: { incidents: number }
    change_pct: number | null
  }
  by_borough: Record<string, any>[]
  top_offenses: { offense: string; level: string; count: number; change_pct: number | null }[]
  monthly_trend: { month: string; total: number; felony: number }[]
}

/** The listing market: asking rent / price / inventory with YoY change. */
export type MarketSnap = { month: string | null; value: number | null; yoy_pct: number | null }
export type MarketSummary = {
  as_of: string | null
  city: { asking_rent: MarketSnap; asking_price: MarketSnap; rental_inventory: MarketSnap }
  boroughs: Record<string, { asking_rent: MarketSnap; asking_price: MarketSnap; rental_inventory: MarketSnap }>
  rent_rising_fastest: { area: string; borough: string; asking_rent: number; yoy_pct: number }[]
  rent_falling_fastest: { area: string; borough: string; asking_rent: number; yoy_pct: number }[]
  zillow_ny_metro?: Record<string, any>
}
