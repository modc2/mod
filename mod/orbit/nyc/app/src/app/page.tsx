'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import {
  api, type Catalog, type Choropleth, type HousingQuery, type Options,
} from '@/lib/api'
import { useCollapse } from '@/lib/collapse'
import { usd } from '@/lib/format'
import HousingControls from './components/HousingControls'
import Inspector, { type Selection } from './components/Inspector'
import LayerPanel from './components/LayerPanel'
import Legend from './components/Legend'
import SearchBar from './components/SearchBar'
import Section from './components/Section'
import { Coin, Mushroom, QuestionBlock } from './components/Sprites'
import type { Basemap } from './components/MapView'

// MapLibre touches `window` at import time, so it can't be server-rendered.
const MapView = dynamic(() => import('./components/MapView'), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 grid place-items-center bg-nes-void">
      <span className="pixel text-[10px] text-nes-coin">LOADING...</span>
    </div>
  ),
})

// Labels are ASCII and upper case: they render in Press Start 2P, which has
// no lower case and no punctuation beyond the basics.
const BASEMAPS: { id: Basemap; label: string }[] = [
  { id: 'dark', label: 'NIGHT' },
  { id: 'light', label: 'DAY' },
  { id: 'streets', label: 'MAP' },
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
  const collapse = useCollapse()
  const [flyTo, setFlyTo] = useState<{ lng: number; lat: number; zoom?: number; nonce: number } | null>(null)
  const [boot, setBoot] = useState<string | null>(null)
  // Bumping this remounts the question block, which restarts its hit animation
  // — a CSS animation won't replay on a class that is already applied.
  const [bump, setBump] = useState(0)

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
      <main className="grid h-screen place-items-center bg-nes-void px-6">
        <div className="blk max-w-md px-7 py-7 text-center">
          <div className="flex justify-center">
            <Mushroom size={44} />
          </div>
          <h1 className="pixel pixel-shadow mt-4 text-[16px] text-nes-red">GAME OVER</h1>
          <p className="pixel mt-4 text-[8px] leading-[2.2] text-nes-ink2">
            THE MAP CANT REACH ITS API
          </p>
          <p className="mt-4 text-[12.5px] leading-relaxed text-nes-ink3">{boot}</p>
          <p className="pixel mt-5 text-[7.5px] leading-[2.2] text-nes-coin">
            CONTINUE? RUN
          </p>
          <code className="mt-1.5 inline-block border-2 border-black bg-black px-2 py-1 text-[12px] text-nes-coin">
            m nyc/serve_api
          </code>
        </div>
      </main>
    )
  }

  return (
    <main className="relative h-screen w-screen overflow-hidden bg-nes-void">
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

      {/* ── HUD ─────────────────────────────────────────────────────── */}
      <header className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start justify-between gap-3 p-3">
        <div className="blk pointer-events-auto flex items-center gap-3 px-3 py-2.5">
          <button
            onClick={() => { setPanelOpen((v) => !v); setBump((b) => b + 1) }}
            aria-label={panelOpen ? 'Hide layers' : 'Show layers'}
            className="shrink-0"
          >
            <span key={bump} className="block block-bump">
              <QuestionBlock size={22} />
            </span>
          </button>
          <div>
            <h1 className="pixel pixel-shadow text-[13px] leading-none text-white">
              NYC ATLAS
            </h1>
            <p className="pixel mt-2 text-[7.5px] leading-none text-nes-coin">
              WORLD 1-1
              <span className="mx-1.5 inline-block h-[3px] w-[3px] -translate-y-[3px] bg-nes-coin align-middle" />
              {catalog?.count ?? '--'} LAYERS
            </p>
          </div>
        </div>

        <div className="pointer-events-auto flex items-center gap-2">
          <SearchBar onPick={(h) => setFlyTo({ ...h, zoom: 15, nonce: Date.now() })} />
          <div className="flex items-center gap-1.5">
            {BASEMAPS.map((b) => (
              <button
                key={b.id}
                onClick={() => setBasemap(b.id)}
                className={`btn pixel px-2.5 py-2 text-[8px] ${basemap === b.id ? 'btn-on' : ''}`}
              >
                {b.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* ── left rail ───────────────────────────────────────────────── */}
      {panelOpen && (
        <div className="blk absolute bottom-3 left-3 top-[86px] z-10 flex w-[300px] flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto">
            {active.includes('housing_prices') && (
              <Section
                title="Housing choropleth"
                open={collapse.isOpen('housing')}
                onToggle={() => collapse.toggle('housing')}
                summary={housingBusy ? '…' : options?.metrics?.[query.metric]?.label ?? ''}
              >
                <HousingControls
                  options={options}
                  query={query}
                  onChange={(patch) => setQuery((q) => ({ ...q, ...patch }))}
                  busy={housingBusy}
                />
                {housing && <Headline housing={housing} metric={query.metric} />}
              </Section>
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
              isOpen={collapse.isOpen}
              onToggleSection={collapse.toggle}
            />
          </div>
        </div>
      )}

      {/* ── legend ──────────────────────────────────────────────────── */}
      <div className={`pointer-events-none absolute bottom-3 z-10 ${panelOpen ? 'left-[324px]' : 'left-3'}`}>
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
      <div className="pointer-events-none absolute right-3 top-[86px] z-20">
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

/**
 * A one-line read of the current choropleth, as the score line of a HUD:
 * coins collected on the left, the run's typical value on the right.
 */
function Headline({ housing, metric }: { housing: Choropleth; metric: string }) {
  const vals = housing.features
    .map((f) => (f.properties as any)?.[metric])
    .filter((v): v is number => typeof v === 'number')
  if (!vals.length) return null
  const sorted = [...vals].sort((a, b) => a - b)
  const median = sorted[Math.floor(sorted.length / 2)]
  const sales = housing.features.reduce((n, f) => n + (((f.properties as any)?.sales) || 0), 0)

  const typical = metric === 'price_change'
    ? `${median > 0 ? '+' : ''}${median.toFixed(1)}%`
    : metric === 'sales'
    ? median.toLocaleString()
    : metric === 'median_ppsf'
    ? `$${Math.round(median)}/FT2`
    : usd(median)

  return (
    <div className="flex items-center justify-between gap-2 border-t-[3px] border-black bg-black/40 px-3 py-2.5">
      <span className="pixel flex items-center gap-1.5 text-[8px] text-nes-coin">
        <Coin size={13} />
        x{sales.toLocaleString()}
      </span>
      <span className="pixel text-[8px] text-white">{typical}</span>
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
