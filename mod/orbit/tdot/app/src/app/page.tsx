'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import {
  api, type Catalog, type Choropleth, type CrimeQuery, type MapAction, type Options,
} from '@/lib/api'
import { count, percent, rate } from '@/lib/format'
import AddData from './components/AddData'
import Chat from './components/Chat'
import Housing from './components/Housing'
import CrimeControls from './components/CrimeControls'
import Inspector, { type Selection } from './components/Inspector'
import LayerPanel from './components/LayerPanel'
import Legend from './components/Legend'
import SearchBar from './components/SearchBar'
import ThemePicker from './components/ThemePicker'
import { useTheme } from './components/ThemeProvider'
import type { BasemapId } from '@/lib/theme'

// MapLibre touches `window` at import time, so it can't be server-rendered.
const MapView = dynamic(() => import('./components/MapView'), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 grid place-items-center bg-bg text-[13px] text-muted">
      Loading map…
    </div>
  ),
})

const BASEMAPS: { id: BasemapId; label: string }[] = [
  { id: 'dark', label: 'Dark' },
  { id: 'light', label: 'Light' },
  { id: 'streets', label: 'Streets' },
]

/** The right-hand dock holds one panel at a time. */
type Dock = 'chat' | 'data' | 'housing' | null

export default function Page() {
  const { theme, base } = useTheme()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [options, setOptions] = useState<Options | null>(null)
  const [active, setActive] = useState<string[]>([])
  const [opacity, setOpacity] = useState<Record<string, number>>({})
  const [layerData, setLayerData] = useState<Record<string, GeoJSON.FeatureCollection>>({})
  const [loading, setLoading] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [crime, setCrime] = useState<Choropleth | null>(null)
  const [crimeBusy, setCrimeBusy] = useState(false)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [basemap, setBasemap] = useState<BasemapId>(theme.basemap)
  const [panelOpen, setPanelOpen] = useState(true)
  const [flyTo, setFlyTo] = useState<{ lng: number; lat: number; zoom?: number; nonce: number } | null>(null)
  const [boot, setBoot] = useState<string | null>(null)
  const [dock, setDock] = useState<Dock>(null)

  const [query, setQuery] = useState<CrimeQuery>({
    metric: 'incidents',
    geography: 'neighbourhood',
    since: '2025-01-01',
    category: 'all',
  })

  // Picking a theme is one gesture: it moves the tiles under the console too,
  // so a light theme never leaves a dark basemap behind it. The buttons below
  // still override by hand until the next theme change.
  useEffect(() => {
    setBasemap(theme.basemap)
  }, [theme.basemap])

  // ── boot ────────────────────────────────────────────────────────────────
  useEffect(() => {
    Promise.all([api.catalog(), api.options()])
      .then(([c, o]) => {
        setCatalog(c)
        setOptions(o)
        setActive(c.layers.filter((l) => l.default_on).map((l) => l.id))
      })
      .catch((e) => setBoot(String(e.message || e)))
  }, [])

  /** Re-read the catalogue after a dataset is added or removed. */
  const refreshCatalog = useCallback(
    () => api.catalog().then(setCatalog).catch(() => {}), [])

  // ── crime choropleth ────────────────────────────────────────────────────
  const queryKey = JSON.stringify(query)
  useEffect(() => {
    if (!active.includes('crime')) return
    let alive = true
    setCrimeBusy(true)
    api.crime(query)
      .then((fc) => { if (alive) { setCrime(fc); setErrors((e) => omit(e, 'crime')) } })
      .catch((e) => { if (alive) setErrors((er) => ({ ...er, crime: msg(e) })) })
      .finally(() => { if (alive) setCrimeBusy(false) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, active.includes('crime')])

  // ── overlay fetching, one request per layer, cached in state ────────────
  const inflight = useRef<Set<string>>(new Set())
  useEffect(() => {
    for (const id of active) {
      if (id === 'crime' || layerData[id] || inflight.current.has(id)) continue
      inflight.current.add(id)
      setLoading((l) => [...l, id])
      const fetcher = id === 'incidents'
        ? api.incidents({ since: query.since, category: query.category, limit: 15000 })
        : api.layer(id)
      fetcher
        .then((fc) => {
          setLayerData((d) => ({ ...d, [id]: fc }))
          setErrors((e) => omit(e, id))
        })
        .catch((e) => setErrors((er) => ({ ...er, [id]: msg(e) })))
        .finally(() => {
          inflight.current.delete(id)
          setLoading((l) => l.filter((x) => x !== id))
        })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active])

  // The incidents layer depends on the crime window, so drop it when that moves.
  useEffect(() => {
    setLayerData((d) => omit(d, 'incidents'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.since, query.category])

  const toggle = useCallback((id: string) => {
    setActive((a) => (a.includes(id) ? a.filter((x) => x !== id) : [...a, id]))
    setSelection((s) => (s?.layerId === id ? null : s))
  }, [])

  /**
   * Apply what the agent just did to the map the person is looking at.
   *
   * This is the whole point of the chat panel: asking to see something
   * *changes the map*, rather than producing a paragraph about where to click.
   */
  const applyAction = useCallback((a: MapAction) => {
    if (a.catalog_changed) refreshCatalog()
    if (a.crime) setQuery((q) => ({ ...q, ...a.crime }))
    if (a.show?.length || a.hide?.length || a.only) {
      setActive((cur) => {
        const next = new Set(a.only ? [] : cur)
        // A cleared view still keeps the choropleth if the agent retuned it.
        if (a.only && a.crime) next.add('crime')
        for (const id of a.show ?? []) next.add(id)
        for (const id of a.hide ?? []) next.delete(id)
        return [...next]
      })
    }
    if (a.fly_to) setFlyTo({ ...a.fly_to, nonce: Date.now() })
  }, [refreshCatalog])

  const removeDataset = useCallback(async (id: string) => {
    await api.removeData(id).catch(() => {})
    setActive((cur) => cur.filter((x) => x !== id))
    setLayerData((d) => omit(d, id))
    setSelection((s) => (s?.layerId === id ? null : s))
    refreshCatalog()
  }, [refreshCatalog])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const [id, fc] of Object.entries(layerData)) c[id] = fc.features?.length ?? 0
    if (crime) c.crime = crime.meta?.areas_with_data ?? crime.features.length
    return c
  }, [layerData, crime])

  if (boot) {
    return (
      <main className="grid h-screen place-items-center bg-bg px-6 text-center">
        <div className="max-w-md">
          <h1 className="text-[15px] font-medium text-ink">The map can’t reach its API</h1>
          <p className="mt-2 text-[12.5px] leading-relaxed text-muted">{boot}</p>
          <p className="mt-3 text-[12px] text-muted">
            Start it with <code className="rounded-ctl bg-fill-hover px-1.5 py-0.5">m tdot/serve_api</code>.
          </p>
        </div>
      </main>
    )
  }

  return (
    <main className="relative h-screen w-screen overflow-hidden bg-bg">
      <MapView
        catalog={catalog}
        active={active}
        opacity={opacity}
        crime={crime}
        crimeMetric={query.metric}
        layerData={layerData}
        basemap={basemap}
        base={base}
        flyTo={flyTo}
        onFeatureClick={setSelection}
        onMapReady={() => {}}
      />

      {/* ── top bar ─────────────────────────────────────────────────── */}
      <header className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start justify-between gap-3 p-3">
        <div className="panel pointer-events-auto flex items-center gap-2.5 px-3 py-2">
          <button
            onClick={() => setPanelOpen((v) => !v)}
            aria-label={panelOpen ? 'Hide layers' : 'Show layers'}
            className="rounded-ctl p-1 text-muted hover:bg-fill-hover hover:text-ink"
          >
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M8 1.8 14.5 5 8 8.2 1.5 5 8 1.8Z" stroke="currentColor"
                    strokeWidth="1.3" strokeLinejoin="round" />
              <path d="M2.4 8.2 8 11l5.6-2.8M2.4 11.2 8 14l5.6-2.8" stroke="currentColor"
                    strokeWidth="1.3" strokeLinejoin="round" />
            </svg>
          </button>
          <div>
            <h1 className="text-[13.5px] font-semibold leading-tight text-ink">
              Toronto Atlas
            </h1>
            <p className="text-[9.5px] leading-tight text-muted">
              Open-data GIS · {catalog?.count ?? '—'} layers
            </p>
          </div>
        </div>

        <div className="pointer-events-auto flex items-center gap-2">
          <SearchBar onPick={(h) => setFlyTo({ ...h, zoom: 15, nonce: Date.now() })} />
          <div className="panel flex gap-0.5 p-0.5">
            {BASEMAPS.map((b) => (
              <button
                key={b.id}
                onClick={() => setBasemap(b.id)}
                aria-pressed={basemap === b.id}
                className="chip px-2 py-1 text-[11px]"
              >
                {b.label}
              </button>
            ))}
          </div>
          <div className="panel flex gap-0.5 p-0.5">
            <button
              onClick={() => setDock((d) => (d === 'housing' ? null : 'housing'))}
              aria-pressed={dock === 'housing'}
              className="chip px-2 py-1 text-[11px]"
            >
              Housing
            </button>
            <button
              onClick={() => setDock((d) => (d === 'data' ? null : 'data'))}
              aria-pressed={dock === 'data'}
              className="chip px-2 py-1 text-[11px]"
            >
              + Data
            </button>
            <button
              onClick={() => setDock((d) => (d === 'chat' ? null : 'chat'))}
              aria-pressed={dock === 'chat'}
              className="chip px-2 py-1 text-[11px]"
            >
              Ask
            </button>
          </div>
          <ThemePicker />
        </div>
      </header>

      {/* ── right dock: the chat agent and the open-data browser ────── */}
      {dock && (
        <aside className="panel absolute bottom-3 right-3 top-[68px] z-30 flex w-[340px] flex-col overflow-hidden">
          {dock === 'chat' ? (
            <Chat onAction={applyAction} onClose={() => setDock(null)} />
          ) : dock === 'housing' ? (
            <Housing
              active={active}
              onToggleLayer={toggle}
              onFlyTo={(lng, lat) => setFlyTo({ lng, lat, zoom: 16, nonce: Date.now() })}
              onClose={() => setDock(null)}
            />
          ) : (
            <AddData
              onClose={() => setDock(null)}
              onAdded={(id) => {
                refreshCatalog()
                setActive((a) => (a.includes(id) ? a : [...a, id]))
              }}
            />
          )}
        </aside>
      )}

      {/* ── left rail ───────────────────────────────────────────────── */}
      {panelOpen && (
        <div className="panel absolute bottom-3 left-3 top-[68px] z-10 flex w-[268px] flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto">
            {active.includes('crime') && (
              <div className="border-b border-line">
                <CrimeControls
                  options={options}
                  query={query}
                  onChange={(patch) => setQuery((q) => ({ ...q, ...patch }))}
                  busy={crimeBusy}
                />
                {crime && <Headline crime={crime} metric={query.metric} />}
              </div>
            )}
            <LayerPanel
              catalog={catalog}
              active={active}
              loading={loading}
              errors={errors}
              opacity={opacity}
              counts={counts}
              onToggle={toggle}
              onOpacity={(id, v) => setOpacity((o) => ({ ...o, [id]: v }))}
              onRemove={removeDataset}
            />
          </div>
        </div>
      )}

      {/* ── legend ──────────────────────────────────────────────────── */}
      <div className={`pointer-events-none absolute bottom-3 z-10 ${panelOpen ? 'left-[288px]' : 'left-3'}`}>
        <Legend
          breaks={crime?.breaks ?? null}
          metric={query.metric}
          options={options}
          active={active}
          areasWithData={crime?.meta?.areas_with_data}
          totalAreas={crime?.meta?.areas}
          catalog={catalog}
          layerData={layerData}
        />
      </div>

      {/* ── inspector ───────────────────────────────────────────────── */}
      <div className={`pointer-events-none absolute top-[68px] z-20 ${
        dock ? 'right-[356px]' : 'right-3'}`}>
        <Inspector
          selection={selection}
          catalog={catalog}
          category={query.category}
          onClose={() => setSelection(null)}
        />
      </div>
    </main>
  )
}

/** A one-line read of the current choropleth, above the layer list. */
function Headline({ crime, metric }: { crime: Choropleth; metric: string }) {
  const vals = crime.features
    .map((f) => (f.properties as any)?.[metric])
    .filter((v): v is number => typeof v === 'number')
  if (!vals.length) return null
  const sorted = [...vals].sort((a, b) => a - b)
  const median = sorted[Math.floor(sorted.length / 2)]
  const total = crime.features.reduce((n, f) => n + (((f.properties as any)?.incidents) || 0), 0)

  return (
    <div className="flex items-baseline justify-between gap-2 px-4 pb-3 text-[11px]">
      <span className="text-muted">
        {total.toLocaleString()} incidents
      </span>
      <span className="tabular-nums text-ink-2">
        typical area:{' '}
        <span className="font-medium text-ink">
          {metric === 'change'
            ? percent(median)
            : metric === 'per_km2'
            ? `${rate(median)}/km²`
            : metric === 'per_month'
            ? `${rate(median)}/mo`
            : count(median)}
        </span>
      </span>
    </div>
  )
}

function omit<T extends Record<string, any>>(obj: T, key: string): T {
  const { [key]: _, ...rest } = obj
  return rest as T
}

function msg(e: any): string {
  return String(e?.message ?? e).slice(0, 160)
}
