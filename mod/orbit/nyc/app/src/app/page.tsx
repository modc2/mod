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
import Legend, { hasLegend } from './components/Legend'
import SearchBar from './components/SearchBar'
import Section from './components/Section'
import { Coin, Mushroom, QuestionBlock } from './components/Sprites'
import { NARROW } from '@/lib/layout'
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
  // The rail is a permanent fixture on a wide screen and a drawer on a phone,
  // where it starts shut: the first thing a visitor should see is the map, not
  // a wall of controls over it. Opening it on the desktop happens after mount
  // rather than as the initial value, so the server render and the first client
  // render agree — and closed-then-open is the harmless direction to flash.
  const [panelOpen, setPanelOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [legendOpen, setLegendOpen] = useState(false)
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

  useEffect(() => {
    if (window.matchMedia(`(min-width: ${NARROW}px)`).matches) setPanelOpen(true)
  }, [])

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

  // Picking a result also puts the phone's search bar away: the point of
  // searching was to look at the map, which the field is sitting on top of.
  const pick = useCallback((h: { lat: number; lng: number }) => {
    setFlyTo({ ...h, zoom: 15, nonce: Date.now() })
    setSearchOpen(false)
  }, [])

  const legendRows = hasLegend(active, housing?.breaks ?? null)

  if (boot) {
    return (
      <main className="grid h-[100dvh] place-items-center bg-nes-void px-4">
        <div className="blk w-full max-w-md px-5 py-7 text-center md:px-7">
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
    // `dvh` rather than `vh`: on a phone the browser's own chrome slides in and
    // out as you scroll, and `100vh` is the *tallest* case — the bottom of the
    // map, where the sheet and the key live, would sit under the address bar.
    <main className="relative h-[100dvh] w-full overflow-hidden bg-nes-void">
      <MapView
        catalog={catalog}
        active={active}
        opacity={opacity}
        housing={housing}
        housingMetric={query.metric}
        layerData={layerData}
        basemap={basemap}
        flyTo={flyTo}
        // Touching the map is the end of whatever the HUD was doing: the
        // phone's search field is over the map, so it stands down here rather
        // than staying up in front of the feature just tapped.
        onFeatureClick={(s) => { setSelection(s); setSearchOpen(false) }}
        onMapReady={() => {}}
      />

      {/* ── HUD ─────────────────────────────────────────────────────── */}
      <header className="safe-t safe-x pointer-events-none absolute inset-x-0 top-0 z-30 flex items-start gap-3 pb-3">
        {/* A phone gives the search field the whole bar. At 360px there is no
            room for a usable field beside the title, and a search box you can
            only half see is worse than one that is a tap away. */}
        {/* Mounted on demand rather than hidden: `autoFocus` fires once, when
            the field appears, so a field that was always in the DOM would open
            without the keyboard and cost a second tap. */}
        {searchOpen && (
          <div className="pointer-events-auto flex w-full items-center gap-2 md:hidden">
            <SearchBar autoFocus onPick={pick} />
            <button
              onClick={() => setSearchOpen(false)}
              aria-label="Close search"
              className="btn tap grid shrink-0 place-items-center px-3 py-3"
            >
              <Cross />
            </button>
          </div>
        )}

        {/* The drawer brings its own title bar, and on a phone it opens right
            under this one — two stacked headers, the lower one half covered.
            Only one of them is the current context, so only one is shown. */}
        <div className={`w-full items-start justify-between gap-2 md:gap-3
                         ${searchOpen || panelOpen ? 'hidden md:flex' : 'flex'}`}>
          <div className="blk pointer-events-auto flex items-center gap-2.5 px-2.5 py-2 md:gap-3 md:px-3 md:py-2.5">
            <button
              onClick={() => { setPanelOpen((v) => !v); setBump((b) => b + 1) }}
              aria-label={panelOpen ? 'Hide layers' : 'Show layers'}
              aria-expanded={panelOpen}
              className="tap -m-1 grid shrink-0 place-items-center p-1"
            >
              <span key={bump} className="block block-bump">
                <QuestionBlock size={22} />
              </span>
            </button>
            <div className="min-w-0">
              <h1 className="pixel pixel-shadow whitespace-nowrap text-[11px] leading-none text-white md:text-[13px]">
                NYC ATLAS
              </h1>
              {/* The separator is a drawn pixel rather than a bullet glyph —
                  Press Start 2P has no ·, and the fallback's version sits at a
                  different weight and height from everything around it. */}
              <p className="pixel mt-1.5 flex items-center gap-1.5 whitespace-nowrap text-[6.5px] leading-none text-nes-coin md:mt-2 md:gap-2 md:text-[7.5px]">
                <span>WORLD 1-1</span>
                <span className="h-[4px] w-[4px] shrink-0 bg-nes-coin" aria-hidden />
                <span>{catalog?.count ?? '--'} LAYERS</span>
              </p>
            </div>
          </div>

          <div className="pointer-events-auto flex items-center gap-2">
            <div className="hidden md:block">
              <SearchBar onPick={pick} />
            </div>
            <button
              onClick={() => setSearchOpen(true)}
              aria-label="Search an address or place"
              className="btn tap grid place-items-center px-3 py-3 md:hidden"
            >
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
                <circle cx="7" cy="7" r="4.6" stroke="#fbd000" strokeWidth="2" />
                <path d="M10.6 10.6L14 14" stroke="#fbd000" strokeWidth="2" strokeLinecap="square" />
              </svg>
            </button>
            {/* The basemap switch is three buttons wide; on a phone it moves
                into the drawer rather than crowding the title out of the HUD. */}
            <div className="hidden items-center gap-1.5 md:flex">
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
        </div>
      </header>

      {/* ── left rail ───────────────────────────────────────────────── */}
      {/* On a wide screen the rail's height follows its open sections, capped
          at the viewport: fold everything away and it shrinks to a stack of
          headers. On a phone it becomes a drawer over a scrim — a 300px panel
          on a 360px screen is the whole screen, so it has to be dismissible,
          and it stays mounted so it can slide rather than blink. */}
      {/* The scrim is a shortcut, not the way out — the drawer carries a real
          close button, so this stays out of the accessibility tree rather than
          announcing a second control with the same name. */}
      {panelOpen && (
        <div
          aria-hidden
          onClick={() => setPanelOpen(false)}
          className="absolute inset-0 z-20 bg-black/60 md:hidden"
        />
      )}
      <div
        className={`blk sheet-in absolute inset-y-0 left-0 z-20 flex w-[min(88vw,320px)] flex-col
                    overflow-hidden md:inset-y-auto md:left-3 md:top-[86px] md:z-10
                    md:max-h-[calc(100%-6.5rem)] md:w-[300px]
                    ${panelOpen ? 'translate-x-0' : '-translate-x-full md:hidden'}`}
      >
        {/* The drawer's own title bar: the HUD behind it is covered by the
            scrim, so the way out has to be inside. */}
        <div className="safe-t flex shrink-0 items-center justify-between gap-2 border-b-[3px] border-black bg-black/40 px-3 pb-2.5 md:hidden">
          <h2 className="pixel text-[9px] leading-none text-white">LAYERS</h2>
          <button
            onClick={() => setPanelOpen(false)}
            aria-label="Close layers"
            className="btn tap grid place-items-center px-2.5 py-2.5"
          >
            <Cross />
          </button>
        </div>

        <div className="flex shrink-0 items-center gap-1.5 border-b-[3px] border-black px-3 py-2.5 md:hidden">
          {BASEMAPS.map((b) => (
            <button
              key={b.id}
              onClick={() => setBasemap(b.id)}
              className={`btn pixel tap flex-1 px-1 py-2.5 text-[8px] ${basemap === b.id ? 'btn-on' : ''}`}
            >
              {b.label}
            </button>
          ))}
        </div>

        <div className="safe-b flex-1 overflow-y-auto md:pb-0">
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

      {/* ── legend ──────────────────────────────────────────────────── */}
      <div className={`pointer-events-none absolute bottom-3 z-10 hidden md:block ${panelOpen ? 'left-[324px]' : 'left-3'}`}>
        <Legend
          breaks={housing?.breaks ?? null}
          metric={query.metric}
          options={options}
          active={active}
          areasWithData={housing?.meta?.areas_with_data}
          totalAreas={housing?.meta?.areas}
        />
      </div>

      {/* A phone can't afford a permanent key over the map, so it folds into a
          chip in the corner — and gets out of the way entirely while the
          inspector sheet or the drawer is up. */}
      {legendRows && !selection && !panelOpen && (
        <div className="safe-b safe-x absolute inset-x-0 bottom-0 z-10 flex flex-col items-start gap-2 md:hidden">
          {legendOpen && (
            <div className="max-h-[46dvh] overflow-y-auto">
              <Legend
                breaks={housing?.breaks ?? null}
                metric={query.metric}
                options={options}
                active={active}
                areasWithData={housing?.meta?.areas_with_data}
                totalAreas={housing?.meta?.areas}
              />
            </div>
          )}
          <button
            onClick={() => setLegendOpen((v) => !v)}
            aria-expanded={legendOpen}
            className={`btn pixel tap px-3 py-2.5 text-[8px] ${legendOpen ? 'btn-on' : ''}`}
          >
            {legendOpen ? 'HIDE KEY' : 'KEY'}
          </button>
        </div>
      )}

      {/* ── inspector ───────────────────────────────────────────────── */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-40
                      md:inset-x-auto md:bottom-auto md:right-3 md:top-[86px]">
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

/** A whole-pixel X, for the two dismiss buttons the phone layout adds. */
function Cross() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" shapeRendering="crispEdges"
         fill="currentColor" className="text-nes-ink2" aria-hidden>
      <rect x="0" y="0" width="2" height="2" /><rect x="2" y="2" width="2" height="2" />
      <rect x="4" y="4" width="2" height="2" /><rect x="6" y="2" width="2" height="2" />
      <rect x="8" y="0" width="2" height="2" /><rect x="6" y="6" width="2" height="2" />
      <rect x="8" y="8" width="2" height="2" /><rect x="2" y="6" width="2" height="2" />
      <rect x="0" y="8" width="2" height="2" />
    </svg>
  )
}

function omit<T extends Record<string, any>>(obj: T, key: string): T {
  const { [key]: _, ...rest } = obj
  return rest as T
}

function msg(e: any): string {
  return String(e?.message ?? e).slice(0, 160)
}
