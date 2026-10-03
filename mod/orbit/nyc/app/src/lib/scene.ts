/**
 * The agent's hands on the map.
 *
 * The chat agent calls `nyc_map` / `nyc_infographic`; the API validates the
 * call and forwards the result on the chat stream as a `display` directive.
 * This hook owns the state only the agent sets (an overlay of any open
 * dataset, highlighted areas, a value filter, a caption, the pinned card) and
 * applies the rest of a directive through the page's own setters — so the map
 * the agent builds is the same map the controls build, and the user can keep
 * editing it by hand.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { BASE, type HousingQuery } from './api'
import type { Basemap } from '@/app/components/MapView'

export type OverlaySpec = {
  dataset: string
  domain: string
  mode: 'points' | 'heat' | 'areas'
  where?: string | null
  title: string
  by?: 'zip' | 'borough'
  value?: string
  per_capita?: boolean
}

export type AgentOverlay = {
  spec: OverlaySpec
  data: (GeoJSON.FeatureCollection & { breaks?: { stops: number[]; min: number | null; max: number | null }; meta?: Record<string, any> }) | null
  error?: string
}

export type Infographic = {
  kind: 'infographic'
  title: string
  subtitle?: string
  stats?: { label: string; value: string; note?: string }[]
  bars?: { title: string; unit: string; items: { label: string; value: number }[] }
  series?: { title: string; unit: string; points: { x: string; y: number }[] }
  bullets?: string[]
  sources?: { name: string; url?: string }[]
}

export type MapDirective = {
  kind: 'map'
  reset?: boolean
  layers?: string[]
  add?: string[]
  remove?: string[]
  basemap?: Basemap
  housing?: Partial<HousingQuery>
  filter?: { min: number | null; max: number | null }
  highlight?: string[]
  only?: string[]
  focus?: { lat: number; lng: number; zoom: number; label?: string }
  overlay?: { spec: OverlaySpec; url: string; title: string; mode: string } | null
  caption?: string
}

export type Directive = MapDirective | Infographic

export type ValueFilter = { min: number | null; max: number | null } | null

type Setters = {
  defaults: { layers: string[]; query: HousingQuery; basemap: Basemap }
  setActive: (fn: (a: string[]) => string[]) => void
  setQuery: (fn: (q: HousingQuery) => HousingQuery) => void
  setBasemap: (b: Basemap) => void
  setFlyTo: (f: { lng: number; lat: number; zoom?: number; nonce: number }) => void
}

export function useAgentScene(setters: Setters) {
  // Directives arrive from a stream that outlives any one render; reading the
  // setters (and the catalog-derived defaults) through a ref means `apply`
  // never acts on the first render's empty defaults.
  const ref = useRef(setters)
  ref.current = setters
  const [overlay, setOverlay] = useState<AgentOverlay | null>(null)
  const [overlayUrl, setOverlayUrl] = useState<string | null>(null)
  const [highlight, setHighlight] = useState<{ names: string[]; nonce: number }>({ names: [], nonce: 0 })
  const [filter, setFilter] = useState<ValueFilter>(null)
  const [only, setOnly] = useState<string[]>([])
  // Areas the camera should frame: what was just outlined or kept.
  const [frame, setFrame] = useState<{ names: string[]; nonce: number }>({ names: [], nonce: 0 })
  const [caption, setCaption] = useState<string | null>(null)
  const [card, setCard] = useState<Infographic | null>(null)

  // The overlay is fetched from the same cache the tool just filled, so this
  // is normally instant; a stale URL never overwrites a newer one.
  useEffect(() => {
    if (!overlayUrl) return
    let alive = true
    fetch(`${BASE}${overlayUrl}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`overlay ${r.status}: ${(await r.text()).slice(0, 160)}`)
        return r.json()
      })
      .then((fc) => { if (alive) setOverlay((o) => (o ? { ...o, data: fc, error: undefined } : o)) })
      .catch((e) => { if (alive) setOverlay((o) => (o ? { ...o, error: String(e.message ?? e) } : o)) })
    return () => { alive = false }
  }, [overlayUrl])

  const clearAgent = useCallback(() => {
    setOverlay(null)
    setOverlayUrl(null)
    setHighlight((h) => ({ names: [], nonce: h.nonce + 1 }))
    setFilter(null)
    setOnly([])
    setCaption(null)
  }, [])

  const apply = useCallback((d: Directive) => {
    if (d.kind === 'infographic') {
      setCard(d)
      return
    }
    const s = ref.current
    const { defaults } = s
    if (d.reset) {
      clearAgent()
      s.setActive(() => defaults.layers)
      s.setQuery(() => defaults.query)
      s.setBasemap(defaults.basemap)
    }
    if (d.layers) s.setActive(() => d.layers!)
    if (d.add?.length || d.remove?.length) {
      s.setActive((a) => {
        const next = a.filter((id) => !d.remove?.includes(id))
        for (const id of d.add ?? []) if (!next.includes(id)) next.push(id)
        return next
      })
    }
    if (d.basemap) s.setBasemap(d.basemap)
    if (d.housing) {
      // A new metric means a new scale: an old value range would dim the
      // wrong areas, so it goes with the metric unless set in the same call.
      if (d.housing.metric && !d.filter) setFilter(null)
      s.setQuery((q) => {
        const next = { ...q, ...d.housing }
        if (next.until == null) delete next.until   // null = "through today"
        return next
      })
    }
    if (d.filter) setFilter(d.filter.min === null && d.filter.max === null ? null : d.filter)
    if (d.highlight) setHighlight((h) => ({ names: d.highlight!, nonce: h.nonce + 1 }))
    if (d.only) setOnly(d.only)
    const toFrame = d.highlight?.length ? d.highlight : d.only?.length ? d.only : null
    if (toFrame && !d.focus) setFrame((f) => ({ names: toFrame, nonce: f.nonce + 1 }))
    if (d.focus) s.setFlyTo({ lng: d.focus.lng, lat: d.focus.lat, zoom: d.focus.zoom, nonce: Date.now() })
    if (d.overlay === null) {
      setOverlay(null)
      setOverlayUrl(null)
    } else if (d.overlay) {
      setOverlay({ spec: d.overlay.spec, data: null })
      setOverlayUrl(d.overlay.url)
    }
    if (d.caption !== undefined) setCaption(d.caption || null)
  }, [clearAgent])

  return {
    overlay, highlight, filter, only, frame, caption, card, apply,
    closeCard: () => setCard(null),
    clearAgent,
  }
}

/** A compact read of what the map shows, sent with each chat turn. */
export function describeMap(x: {
  active: string[]
  query: HousingQuery
  basemap: Basemap
  overlay: AgentOverlay | null
  highlight: string[]
  filter: ValueFilter
  only?: string[]
}): Record<string, any> {
  const out: Record<string, any> = { layers: x.active, basemap: x.basemap }
  if (x.active.includes('housing_prices')) out.housing = x.query
  if (x.filter) out.value_filter = x.filter
  if (x.highlight.length) out.highlight = x.highlight
  if (x.only?.length) out.only = x.only
  if (x.overlay) {
    out.overlay = {
      title: x.overlay.spec.title, dataset: x.overlay.spec.dataset,
      mode: x.overlay.spec.mode, where: x.overlay.spec.where, by: x.overlay.spec.by,
    }
  }
  return out
}

/** Does a feature's properties match any highlighted name, code or borough? */
export function matchesHighlight(props: Record<string, any>, names: string[]): boolean {
  if (!names.length) return false
  const fields = [props.area, props.name, props.borough, props.boroname, props.ntaname, props.label]
    .filter((v) => v !== undefined && v !== null)
    .map((v) => String(v).toLowerCase())
  return names.some((n) => {
    const q = n.toLowerCase().trim()
    return !!q && fields.some((f) => f === q || (q.length > 3 && f.includes(q)))
  })
}

/** The same match as `matchesHighlight`, as a MapLibre filter expression. */
export function matchExpression(names: string[]): any[] {
  const fields = ['area', 'name', 'borough', 'boroname', 'ntaname', 'label']
  const anyOf: any[] = ['any']
  for (const n of names) {
    const q = n.toLowerCase().trim()
    if (!q) continue
    for (const f of fields) {
      const v = ['downcase', ['to-string', ['coalesce', ['get', f], '']]]
      anyOf.push(q.length > 3 ? ['in', q, v] : ['==', v, q])
    }
  }
  return anyOf
}
