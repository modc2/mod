/** Typed client for the tdot GIS API. */

const BASE = process.env.NEXT_PUBLIC_API_URL || '/tdot/api'

export type LayerDef = {
  id: string
  title: string
  category: string
  kind: 'choropleth' | 'point' | 'line' | 'polygon' | 'outline' | 'heatmap' | 'area'
  geometry: 'point' | 'line' | 'polygon'
  default_on: boolean
  description: string
  endpoint: string
  style?: Record<string, any>
  controls?: Record<string, any>
  /** True for a dataset somebody added from the portal — removable. */
  custom?: boolean
  source: { name: string; dataset: string; url: string; portal: string }
}

/** A dataset on the city's portal, as returned by the search. */
export type DataHit = {
  package: string
  title: string
  notes: string
  topics: string
  refreshed: string
  formats: string[]
  url: string
}

export type AddedDataset = {
  ok: boolean
  dataset: string
  title: string
  mode: string
  features?: number
  error?: string
}

/** One event from the agent's chat stream. */
export type ChatEvent =
  | { type: 'start'; model?: string; session?: string; tools?: number }
  | { type: 'text'; text: string }
  | { type: 'tool'; name: string; args: Record<string, any> }
  | { type: 'tool_done'; error?: boolean }
  | { type: 'map'; action: MapAction }
  | { type: 'done'; answer: string; session?: string; turns?: number; ms?: number }
  | { type: 'error'; error: string }

/** What a `map` event asks the live map to do. */
export type MapAction = {
  show?: string[]
  hide?: string[]
  only?: boolean
  fly_to?: { lng: number; lat: number; zoom?: number }
  crime?: Partial<CrimeQuery>
  catalog_changed?: boolean
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
  categories: Record<string, { label: string }>
}

export type TrendPoint = {
  year: number
  incidents: number
  assault?: number
  auto_theft?: number
  break_enter?: number
  robbery?: number
  theft_over?: number
}

export type CrimeQuery = {
  metric: string
  geography: string
  since: string
  until?: string
  category: string
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

export const api = {
  catalog: () => get<Catalog>('/layers'),
  options: () => get<Options>('/options'),
  view: () => get<any>('/view'),
  layer: (id: string) => get<GeoJSON.FeatureCollection>(`/layers/${id}`),
  crime: (q: CrimeQuery) => get<Choropleth>('/layers/crime', q),
  incidents: (q: Partial<CrimeQuery> & { limit?: number }) =>
    get<GeoJSON.FeatureCollection>('/layers/incidents', q),
  summary: (q: { since: string; until?: string; category: string }) =>
    get<any>('/summary', q),
  trend: (q: { area?: string; category?: string }) =>
    get<{ series: TrendPoint[]; name?: string; area?: string; partial_year?: number | null }>('/trend', q),
  where: (q: string) =>
    get<{ name: string; lat: number; lng: number; type: string }[]>('/where', { q }),

  // ── open-data registry ──────────────────────────────────────────────────
  findData: (q: string, rows = 10) =>
    get<{ results: DataHit[] }>('/datasets/search', { q, rows }),
  addData: (body: { package: string; title?: string; category?: string }) =>
    post<AddedDataset>('/datasets', body),
  removeData: (id: string) => del<{ ok: boolean }>(`/datasets/${id}`),

  // ── housing & the score model ───────────────────────────────────────────
  housing: (role?: string) =>
    get<HousingInventory>('/housing', role ? { role } : {}),
  scoreModel: () => get<ScoreReport>('/score/model'),
  outliers: (direction: 'under' | 'over' = 'under', limit = 20) =>
    get<{ direction: string; reading: string; typical_error: number; buildings: OutlierBuilding[] }>(
      '/score/outliers', { direction, limit }),
  building: (rsn: string) => get<BuildingDetail>(`/score/building/${rsn}`),

  agent: () => get<AgentCard>('/agent/card'),
}

/** One dataset in the open-housing inventory, and what tdot does with it. */
export type HousingDataset = {
  package: string | null
  title: string
  what: string
  /** layer = on the map · feature = feeds the model · table = nothing to draw · closed = not open */
  role: 'layer' | 'feature' | 'table' | 'closed'
  layer: string | null
  note: string | null
  portal: string
  url: string | null
  portal_status?: {
    available: boolean
    refreshed?: string
    formats?: string[]
    queryable?: boolean
    error?: string
  }
}

export type HousingInventory = {
  datasets: HousingDataset[]
  count: number
  by_role: Record<string, number>
  roles: Record<string, string>
  unavailable: string[]
  checked: string | null
}

export type ScoreReport = {
  target: { name: string; buildings_scored: number; mean: number; sd: number; min: number; max: number }
  accuracy: {
    model: { mae: number; rmse: number; r2: number; within_5: number; within_10: number }
    baseline: { mae: number; rmse: number; r2: number; within_5: number; within_10: number }
    validation: string
    reading: string
  }
  drivers: { feature: string; label: string; importance: number }[]
  features: { used: number; dropped_constant: string[]; excluded_by_design: string }
  coverage: {
    registered_buildings: number
    ever_evaluated: number
    never_evaluated: number
    geocoded: number
  }
  sources: { name: string; package: string; role: string }[]
  caveats: string[]
  fitted_at: string
}

export type OutlierBuilding = {
  rsn: string
  address: string
  ward: string
  manager: string | null
  units: number | null
  score: number | null
  predicted: number
  residual: number
  lng: number
  lat: number
}

export type BuildingDetail = {
  rsn: string
  address: string
  ward: string
  manager: string | null
  score: number | null
  predicted: number
  residual: number | null
  typical_error: number
  drivers: { feature: string; label: string; value: any; effect: number }[]
  note: string
}

export type AgentCard = {
  title: string
  description: string
  ready: boolean
  hint?: string | null
  model?: string
  tool_count: number
  skills: { group: string; tools: string[] }[]
}

async function post<T>(path: string, body: any): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await detail(res, path))
  return res.json()
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await detail(res, path))
  return res.json()
}

async function detail(res: Response, path: string): Promise<string> {
  try {
    const d = (await res.json()).detail
    return typeof d === 'string' ? d : JSON.stringify(d)
  } catch {
    return `${path} → ${res.status}: ${res.statusText}`
  }
}

/**
 * Stream a chat turn. The response is SSE over POST, which `EventSource`
 * cannot do, so the body is read and split on the blank-line frame boundary
 * by hand. `signal` lets the caller stop a run mid-answer.
 */
export async function chat(
  body: { message: string; session?: string },
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    onEvent({ type: 'error', error: await detail(res, '/agent/chat') })
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)) as ChatEvent)
      } catch {
        // a truncated frame is not worth taking the stream down for
      }
    }
  }
}
