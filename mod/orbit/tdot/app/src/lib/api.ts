/** Typed client for the tdot GIS API. */

const BASE = process.env.NEXT_PUBLIC_API_URL || '/tdot/api'

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
}
