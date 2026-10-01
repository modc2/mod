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
import Mcp from './components/Mcp'
import CrimeControls from './components/CrimeControls'
import Inspector, { type Selection } from './components/Inspector'
import LayerPanel from './components/LayerPanel'
import Legend from './components/Legend'
import SearchBar from './components/SearchBar'
import ThemePicker from './components/ThemePicker'
import { useTheme } from './components/ThemeProvider'
import type { BasemapId } from '@/lib/theme'
import { useNarrow } from '@/lib/useNarrow'

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
type Dock = 'chat' | 'data' | 'housing' | 'mcp' | null

const TOOLS: { id: Exclude<Dock, null>; label: string; hint: string }[] = [
  { id: 'housing', label: 'Housing', hint: 'Every open Toronto housing dataset, and the lot finder' },
  { id: 'data', label: 'Data', hint: 'Add any dataset from the city open-data portal' },
  { id: 'chat', label: 'Ask', hint: 'Ask in plain words; the map answers' },
  { id: 'mcp', label: 'MCP', hint: 'The MCP server behind this map' },
]

/** One 14px line glyph per dock tool, drawn in the button's text colour. */
function ToolIcon({ id }: { id: Exclude<Dock, null> }) {
  const d = {
    housing: 'M2.5 7.2 8 2.8l5.5 4.4M4 6.2V13h8V6.2M6.6 13V9.6h2.8V13',
    data: 'M8 3.2v9.6M3.2 8h9.6',
    chat: 'M2.8 4.2c0-.8.6-1.4 1.4-1.4h7.6c.8 0 1.4.6 1.4 1.4v5c0 .8-.6 1.4-1.4 1.4H7l-2.8 2.4v-2.4c-.8 0-1.4-.6-1.4-1.4v-5Z',
    mcp: 'M5.5 4.5 2.5 8l3 3.5M10.5 4.5l3 3.5-3 3.5M9 3.5 7 12.5',
  }[id]
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden className="shrink-0">
      <path d={d} stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

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
  const [view3d, setView3d] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [flyTo, setFlyTo] = useState<{ lng: number; lat: number; zoom?: number; nonce: number } | null>(null)
  const [boot, setBoot] = useState<string | null>(null)
  const [dock, setDock] = useState<Dock>(null)
  const narrow = useNarrow()

  // A phone has room for one sheet at a time, and opens on the map itself.
  useEffect(() => { setPanelOpen(!narrow) }, [narrow])
  const openRail = useCallback((open: boolean) => {
    setPanelOpen(open)
    if (open && narrow) { setDock(null); setSelection(null) }
  }, [narrow])
  const openDock = useCallback((d: Dock) => {
    setDock(d)
    if (d && narrow) { setPanelOpen(false); setSelection(null) }
  }, [narrow])
  useEffect(() => {
    if (selection && narrow) { setPanelOpen(false); setDock(null) }
  }, [selection, narrow])

  // Escape peels the top layer: the inspector first, then the dock.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const t = e.target as HTMLElement | null
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return
      if (selection) setSelection(null)
      else if (dock) setDock(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selection, dock])

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
        view3d={view3d}
        flyTo={flyTo}
        onFeatureClick={setSelection}
        onMapReady={() => {}}
      />

      {/* ── top bar ─────────────────────────────────────────────────── */}
      <header className="pointer-events-none absolute inset-x-0 top-0 z-20 flex flex-wrap items-center gap-2 p-2 md:flex-nowrap md:items-start md:gap-3 md:p-3">
        <button
          onClick={() => openRail(!panelOpen)}
          aria-pressed={panelOpen}
          aria-label={panelOpen ? 'Hide layers' : 'Show layers'}
          className="brand panel pointer-events-auto flex shrink-0 items-center gap-2.5 px-2.5 py-2 text-left md:w-[288px]"
        >
          <span className="brand__mark" aria-hidden>
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M8 1.8 14.5 5 8 8.2 1.5 5 8 1.8Z" stroke="currentColor"
                    strokeWidth="1.4" strokeLinejoin="round" />
              <path d="M2.4 8.2 8 11l5.6-2.8M2.4 11.2 8 14l5.6-2.8" stroke="currentColor"
                    strokeWidth="1.4" strokeLinejoin="round" />
            </svg>
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[13.5px] font-semibold leading-tight tracking-[-0.01em] text-ink">
              Toronto Atlas
            </span>
            <span className="hidden text-[10px] leading-tight text-muted md:block">
              {catalog ? `${catalog.count} open layers · ${active.length} on` : 'Loading layers…'}
            </span>
          </span>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden
               className={`hidden shrink-0 text-muted transition-transform md:block ${panelOpen ? '' : '-rotate-90'}`}>
            <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.4"
                  strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>

        <div className="pointer-events-auto min-w-0 flex-1 md:ml-auto md:w-[200px] md:flex-none lg:w-[260px]">
          <SearchBar onPick={(h) => setFlyTo({ ...h, zoom: 15, nonce: Date.now() })} />
        </div>

        <nav
          aria-label="Map tools"
          className="panel no-scrollbar pointer-events-auto order-last flex w-full items-center gap-0.5 overflow-x-auto p-0.5 md:order-none md:w-auto"
        >
          <button
            onClick={() => openRail(!panelOpen)}
            aria-pressed={panelOpen}
            className="chip flex shrink-0 items-center gap-1.5 px-2.5 py-1 text-[11px] md:hidden"
          >
            Layers <span className="tabular-nums opacity-70">{active.length}</span>
          </button>
          <span className="mx-1 h-4 w-px shrink-0 bg-line-strong md:hidden" aria-hidden />
          <div className="flex shrink-0 gap-0.5" role="group" aria-label="Basemap">
            {BASEMAPS.map((b) => (
              <button
                key={b.id}
                onClick={() => setBasemap(b.id)}
                aria-pressed={basemap === b.id}
                className="chip shrink-0 px-2.5 py-1 text-[11px]"
              >
                {b.label}
              </button>
            ))}
            <button
              onClick={() => setView3d((v) => !v)}
              aria-pressed={view3d}
              className="chip shrink-0 px-2.5 py-1 text-[11px] font-semibold"
              title="Tilt the camera and extrude buildings. Drag with the right mouse button to rotate."
            >
              3D
            </button>
          </div>
          <span className="mx-1 h-4 w-px shrink-0 bg-line-strong" aria-hidden />
          {TOOLS.map((t) => (
            <button
              key={t.id}
              onClick={() => openDock(dock === t.id ? null : t.id)}
              aria-pressed={dock === t.id}
              title={t.hint}
              className="chip flex shrink-0 items-center gap-1.5 px-2.5 py-1 text-[11px]"
            >
              <ToolIcon id={t.id} />
              <span className="md:hidden xl:inline">{t.label}</span>
            </button>
          ))}
        </nav>

        <div className="pointer-events-auto shrink-0">
          <ThemePicker />
        </div>
      </header>

      {/* ── right dock: the chat agent and the open-data browser ────── */}
      {dock && (
        <aside className="dock panel sheet absolute z-30 flex flex-col overflow-hidden">
          {dock === 'chat' ? (
            <Chat onAction={applyAction} onClose={() => setDock(null)} />
          ) : dock === 'mcp' ? (
            <Mcp onAction={applyAction} onClose={() => setDock(null)} />
          ) : dock === 'housing' ? (
            <Housing
              active={active}
              onToggleLayer={toggle}
              onFlyTo={(lng, lat) => {
                setFlyTo({ lng, lat, zoom: 16, nonce: Date.now() })
                if (narrow) setDock(null)
              }}
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
        <div className="rail panel sheet absolute z-10 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-4 py-2 md:hidden">
            <span className="text-[12px] font-semibold text-ink">
              Layers <span className="font-normal text-muted">· {active.length} on</span>
            </span>
            <button onClick={() => setPanelOpen(false)} aria-label="Close layers"
                    className="rounded-ctl px-2 py-1 text-[11px] text-muted hover:bg-fill-hover hover:text-ink">
              Done
            </button>
          </div>
          <div className="flex-1 overflow-y-auto overscroll-contain">
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

      {/* ── legend: hidden on a phone while a sheet covers the bottom ─── */}
      {!(narrow && (panelOpen || dock || selection)) && (
        <div className={`pointer-events-none absolute bottom-2 right-2 z-10 md:bottom-3 md:right-auto ${
          panelOpen ? 'md:left-[312px]' : 'md:left-3'}`}>
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
      )}

      {/* ── inspector ───────────────────────────────────────────────── */}
      <div className={`inspector pointer-events-none absolute z-20 ${dock ? 'md:right-[364px]' : 'md:right-3'}`}>
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
