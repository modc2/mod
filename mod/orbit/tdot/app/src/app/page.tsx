'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import {
  api, type Catalog, type Choropleth, type HousingQuery, type Options,
} from '@/lib/api'
import { usd } from '@/lib/format'
import HousingControls from './components/HousingControls'
import Inspector, { type Selection } from './components/Inspector'
import LayerPanel from './components/LayerPanel'
import Legend from './components/Legend'
import SearchBar from './components/SearchBar'
import type { Basemap } from './components/MapView'

// MapLibre touches `window` at import time, so it can't be server-rendered.
const MapView = dynamic(() => import('./components/MapView'), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 grid place-items-center bg-[#0b0e14] text-[13px] text-[#898781]">
      Loading map…
    </div>
  ),
})

const BASEMAPS: { id: Basemap; label: string }[] = [
  { id: 'dark', label: 'Dark' },
  { id: 'light', label: 'Light' },
  { id: 'streets', label: 'Streets' },
]

export default function Page() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [options, setOptions] = useState<Options | null>(null)
  const [active, setActive] = useState<string[]>([])
  const [opacity, setOpacity] = useState<Record<string, number>>({})
  const [layerData, setLayerData] = useState<Record<string, GeoJSON.FeatureCollection>>({})
  const [loading, setLoading] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [housing, setHousing] = useState<Choropleth | null>(null)
  const [housingBusy, setHousingBusy] = useState(false)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [basemap, setBasemap] = useState<Basemap>('dark')
  const [panelOpen, setPanelOpen] = useState(true)
  const [flyTo, setFlyTo] = useState<{ lng: number; lat: number; zoom?: number; nonce: number } | null>(null)
  const [boot, setBoot] = useState<string | null>(null)

  const [query, setQuery] = useState<HousingQuery>({
    metric: 'median_price',
    geography: 'nta',
    since: '2024-01-01',
    property_type: 'residential',
  })

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

  // ── housing choropleth ──────────────────────────────────────────────────
  const queryKey = JSON.stringify(query)
  useEffect(() => {
    if (!active.includes('housing_prices')) return
    let alive = true
    setHousingBusy(true)
    api.housing(query)
      .then((fc) => { if (alive) { setHousing(fc); setErrors((e) => omit(e, 'housing_prices')) } })
      .catch((e) => { if (alive) setErrors((er) => ({ ...er, housing_prices: msg(e) })) })
      .finally(() => { if (alive) setHousingBusy(false) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, active.includes('housing_prices')])

  // ── overlay fetching, one request per layer, cached in state ────────────
  const inflight = useRef<Set<string>>(new Set())
  useEffect(() => {
    for (const id of active) {
      if (id === 'housing_prices' || layerData[id] || inflight.current.has(id)) continue
      inflight.current.add(id)
      setLoading((l) => [...l, id])
      const fetcher = id === 'sales'
        ? api.sales({ since: query.since, property_type: query.property_type, limit: 12000 })
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

  // The sales layer depends on the housing window, so drop it when that moves.
  useEffect(() => {
    setLayerData((d) => omit(d, 'sales'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.since, query.property_type])

  const toggle = useCallback((id: string) => {
    setActive((a) => (a.includes(id) ? a.filter((x) => x !== id) : [...a, id]))
    setSelection((s) => (s?.layerId === id ? null : s))
  }, [])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const [id, fc] of Object.entries(layerData)) c[id] = fc.features?.length ?? 0
    if (housing) c.housing_prices = housing.meta?.areas_with_data ?? housing.features.length
    return c
  }, [layerData, housing])

  if (boot) {
    return (
      <main className="grid h-screen place-items-center bg-[#0b0e14] px-6 text-center">
        <div className="max-w-md">
          <h1 className="text-[15px] font-medium text-[#e6e8ee]">The map can’t reach its API</h1>
          <p className="mt-2 text-[12.5px] leading-relaxed text-[#898781]">{boot}</p>
          <p className="mt-3 text-[12px] text-[#898781]">
            Start it with <code className="rounded bg-white/10 px-1.5 py-0.5">m tdot/serve_api</code>.
          </p>
        </div>
      </main>
    )
  }

  return (
    <main className="relative h-screen w-screen overflow-hidden bg-[#0b0e14]">
      <MapView
        catalog={catalog}
        active={active}
        opacity={opacity}
        housing={housing}
        housingMetric={query.metric}
        layerData={layerData}
        basemap={basemap}
        flyTo={flyTo}
        onFeatureClick={setSelection}
        onMapReady={() => {}}
      />

      {/* ── top bar ─────────────────────────────────────────────────── */}
      <header className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start justify-between gap-3 p-3">
        <div className="pointer-events-auto flex items-center gap-2.5 rounded-lg border border-white/10 bg-[#121722]/95 px-3 py-2 shadow-xl backdrop-blur">
          <button
            onClick={() => setPanelOpen((v) => !v)}
            aria-label={panelOpen ? 'Hide layers' : 'Show layers'}
            className="rounded p-1 text-[#898781] hover:bg-white/10 hover:text-[#e6e8ee]"
          >
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M8 1.8 14.5 5 8 8.2 1.5 5 8 1.8Z" stroke="currentColor"
                    strokeWidth="1.3" strokeLinejoin="round" />
              <path d="M2.4 8.2 8 11l5.6-2.8M2.4 11.2 8 14l5.6-2.8" stroke="currentColor"
                    strokeWidth="1.3" strokeLinejoin="round" />
            </svg>
          </button>
          <div>
            <h1 className="text-[13.5px] font-semibold leading-tight text-[#e6e8ee]">
              NYC Atlas
            </h1>
            <p className="text-[9.5px] leading-tight text-[#898781]">
              Open-data GIS · {catalog?.count ?? '—'} layers
            </p>
          </div>
        </div>

        <div className="pointer-events-auto flex items-center gap-2">
          <SearchBar onPick={(h) => setFlyTo({ ...h, zoom: 15, nonce: Date.now() })} />
          <div className="flex gap-0.5 rounded-md border border-white/10 bg-[#121722]/95 p-0.5 shadow-xl backdrop-blur">
            {BASEMAPS.map((b) => (
              <button
                key={b.id}
                onClick={() => setBasemap(b.id)}
                className={`rounded px-2 py-1 text-[11px] transition-colors ${
                  basemap === b.id ? 'bg-[#3987e5] text-white' : 'text-[#898781] hover:text-[#e6e8ee]'
                }`}
              >
                {b.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* ── left rail ───────────────────────────────────────────────── */}
      {panelOpen && (
        <div className="absolute bottom-3 left-3 top-[68px] z-10 flex w-[268px] flex-col overflow-hidden rounded-lg border border-white/10 bg-[#121722]/95 shadow-2xl backdrop-blur">
          <div className="flex-1 overflow-y-auto">
            {active.includes('housing_prices') && (
              <div className="border-b border-white/10">
                <HousingControls
                  options={options}
                  query={query}
                  onChange={(patch) => setQuery((q) => ({ ...q, ...patch }))}
                  busy={housingBusy}
                />
                {housing && <Headline housing={housing} metric={query.metric} />}
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
            />
          </div>
        </div>
      )}

      {/* ── legend ──────────────────────────────────────────────────── */}
      <div className={`pointer-events-none absolute bottom-3 z-10 ${panelOpen ? 'left-[288px]' : 'left-3'}`}>
        <Legend
          breaks={housing?.breaks ?? null}
          metric={query.metric}
          options={options}
          active={active}
          areasWithData={housing?.meta?.areas_with_data}
          totalAreas={housing?.meta?.areas}
        />
      </div>

      {/* ── inspector ───────────────────────────────────────────────── */}
      <div className="pointer-events-none absolute right-3 top-[68px] z-20">
        <Inspector
          selection={selection}
          catalog={catalog}
          propertyType={query.property_type}
          onClose={() => setSelection(null)}
        />
      </div>
    </main>
  )
}

/** A one-line read of the current choropleth, above the layer list. */
function Headline({ housing, metric }: { housing: Choropleth; metric: string }) {
  const vals = housing.features
    .map((f) => (f.properties as any)?.[metric])
    .filter((v): v is number => typeof v === 'number')
  if (!vals.length) return null
  const sorted = [...vals].sort((a, b) => a - b)
  const median = sorted[Math.floor(sorted.length / 2)]
  const sales = housing.features.reduce((n, f) => n + (((f.properties as any)?.sales) || 0), 0)

  return (
    <div className="flex items-baseline justify-between gap-2 px-4 pb-3 text-[11px]">
      <span className="text-[#898781]">
        {sales.toLocaleString()} sales
      </span>
      <span className="tabular-nums text-[#c3c2b7]">
        typical area:{' '}
        <span className="font-medium text-[#e6e8ee]">
          {metric === 'price_change'
            ? `${median > 0 ? '+' : ''}${median.toFixed(1)}%`
            : metric === 'sales'
            ? median.toLocaleString()
            : metric === 'median_ppsf'
            ? `$${Math.round(median)}/ft²`
            : usd(median)}
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
