'use client'

import { useEffect, useState } from 'react'
import { api, type Catalog, type TrendPoint } from '@/lib/api'
import { count, percent, titleCase, usd, usdExact } from '@/lib/format'
import TrendChart from './TrendChart'

export type Selection = { layerId: string; props: Record<string, any> }

type Props = {
  selection: Selection | null
  catalog: Catalog | null
  propertyType: string
  onClose: () => void
}

/**
 * The feature inspector: what you get when you click the map.
 *
 * Each layer gets a hand-written body, because a generic key/value dump of raw
 * open-data column names ("ft_facilit", "hurricane_") is not something anyone
 * can read. Unmapped layers still fall back to a readable table.
 */
export default function Inspector({ selection, catalog, propertyType, onClose }: Props) {
  const [trend, setTrend] = useState<TrendPoint[] | null>(null)
  const [trendFor, setTrendFor] = useState<string | null>(null)

  const area = selection?.layerId === 'housing_prices' ? selection.props.area : null

  useEffect(() => {
    if (!area) { setTrend(null); setTrendFor(null); return }
    let alive = true
    setTrend(null)
    setTrendFor(area)
    api.trend({ area, property_type: propertyType })
      .then((t) => { if (alive) setTrend(t.series) })
      .catch(() => { if (alive) setTrend([]) })
    return () => { alive = false }
  }, [area, propertyType])

  if (!selection) return null
  const def = catalog?.layers.find((l) => l.id === selection.layerId)
  const p = selection.props

  return (
    // On a phone the inspector is a sheet across the foot of the screen: a
    // 292px card pinned to the right edge would cover the map it is describing
    // and still be too narrow to read. Height is capped so the tapped feature
    // stays visible above it.
    <aside className="blk pointer-events-auto flex max-h-[62dvh] w-full flex-col overflow-hidden
                      md:max-h-[calc(100dvh-104px)] md:w-[292px]">
      <header className="relative flex items-start gap-2 border-b-[3px] border-black bg-black/40 py-2.5 pl-4 pr-2.5">
        <span className="brick brick-strip absolute inset-y-0 left-0 w-2.5" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="pixel truncate text-[7px] leading-none text-nes-coin">
            {def?.title ?? selection.layerId}
          </div>
          <h2 className="mt-1.5 truncate text-[14px] font-medium text-white">
            {headline(selection)}
          </h2>
        </div>
        <button onClick={onClose} aria-label="Close"
                className="tap -m-1.5 grid shrink-0 place-items-center p-1.5 text-nes-ink3 hover:text-nes-red">
          <svg width="14" height="14" viewBox="0 0 14 14" shapeRendering="crispEdges"
               fill="currentColor">
            {/* a whole-pixel X */}
            <rect x="2" y="2" width="2" height="2" /><rect x="4" y="4" width="2" height="2" />
            <rect x="6" y="6" width="2" height="2" /><rect x="8" y="4" width="2" height="2" />
            <rect x="10" y="2" width="2" height="2" /><rect x="8" y="8" width="2" height="2" />
            <rect x="10" y="10" width="2" height="2" /><rect x="4" y="8" width="2" height="2" />
            <rect x="2" y="10" width="2" height="2" />
          </svg>
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-3.5 py-3">
        {selection.layerId === 'housing_prices' && (
          <div className="space-y-3">
            {p.sales > 0 ? (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <Stat label="Median price" value={usd(p.median_price)} big />
                  <Stat label="Median $/ft²"
                        value={p.median_ppsf ? `$${p.median_ppsf}` : '—'} big />
                  <Stat label="Sales" value={count(p.sales)} />
                  <Stat
                    label="vs prior period"
                    value={percent(p.price_change)}
                    tone={p.price_change === null ? undefined
                      : p.price_change >= 0 ? 'good' : 'bad'}
                  />
                </div>
                {p.median_ppsf === null && p.sales > 0 && (
                  <p className="text-[10.5px] leading-snug text-nes-ink3">
                    No reliable floor-area figures here — the city’s file often
                    reports a whole building’s square footage for apartment
                    sales, so those rows are excluded.
                  </p>
                )}
                <div className="border-t-2 border-black pt-2.5">
                  {trendFor === p.area && trend === null && (
                    <p className="text-[11px] text-nes-ink3">Loading history…</p>
                  )}
                  {trend && trend.length > 0 && <TrendChart series={trend} />}
                  {trend && trend.length === 0 && (
                    <p className="text-[11px] text-nes-ink3">No history available.</p>
                  )}
                </div>
                <Meta rows={[
                  ['Total value', usd(p.total_value)],
                  ['Average price', usd(p.avg_price)],
                  ['Borough', p.borough || '—'],
                  ['Area code', p.area],
                ]} />
              </>
            ) : (
              <p className="text-[12px] leading-relaxed text-nes-ink2">
                No qualifying sales recorded here in this window. That usually
                means the area is parkland, an airport, an industrial zone, or
                had only non-market transfers.
              </p>
            )}
          </div>
        )}

        {selection.layerId === 'sales' && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Sale price" value={usdExact(p.price)} big />
              <Stat label="Price per ft²" value={p.ppsf ? `$${p.ppsf}` : '—'} big />
            </div>
            <Meta rows={[
              ['Date', p.date],
              ['Type', titleCase(String(p.type || '').replace(/^\d+\s+/, ''))],
              ['Size', p.sqft ? `${count(p.sqft)} ft²` : '—'],
              ['Units', p.units || '—'],
              ['Built', p.year_built || '—'],
              ['Neighborhood', titleCase(p.neighborhood || '')],
              ['Borough', p.borough],
              ['ZIP', p.zip],
            ]} />
          </div>
        )}

        {selection.layerId === 'subway_stations' && (
          <div className="space-y-3">
            <RouteBullets routes={String(p.routes || '')} />
            <Meta rows={[
              ['Line', p.line],
              ['Borough', p.borough],
              ['Structure', p.structure],
              ['Division', p.division],
              ['Wheelchair access', p.accessible === true || p.accessible === 'true' ? 'Yes' : 'No'],
            ]} />
          </div>
        )}

        {selection.layerId === 'subway_ridership' && (
          <div className="space-y-3">
            <Stat label="Riders since Jan 2025" value={count(p.riders)} big />
            <Meta rows={[
              ['Transfers', count(p.transfers)],
              ['Borough', p.borough],
            ]} />
          </div>
        )}

        {selection.layerId === 'affordable_housing' && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Affordable units" value={count(p.units)} big />
              <Stat label="Total units" value={count(p.total)} big />
            </div>
            <Meta rows={[
              ['Address', p.address],
              ['Borough', p.borough],
              ['Started', p.started],
              ['Type', p.construction],
              ['Extremely low income', count(p.extremely_low)],
              ['Very low income', count(p.very_low)],
              ['Low income', count(p.low)],
            ]} />
          </div>
        )}

        {selection.layerId === 'collisions' && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Injured" value={count(p.injured)} big />
              <Stat label="Killed" value={count(p.killed)} big
                    tone={p.killed > 0 ? 'bad' : undefined} />
            </div>
            <Meta rows={[
              ['Date', p.date],
              ['Street', titleCase(p.street || '—')],
              ['Borough', titleCase(p.borough || '—')],
              ['Pedestrians hurt', count(p.peds)],
              ['Cyclists hurt', count(p.cyclists)],
              ['Contributing factor', p.cause || 'Unspecified'],
            ]} />
          </div>
        )}

        {selection.layerId === 'parks' && (
          <Meta rows={[
            ['Type', p.typecategory],
            ['Size', p.acres ? `${Number(p.acres).toFixed(1)} acres` : '—'],
            ['Borough', p.borough],
            ['Address', titleCase(p.address || '—')],
            ['Waterfront', p.waterfront === 'True' ? 'Yes' : 'No'],
          ]} />
        )}

        {selection.layerId === 'bike_routes' && (
          <Meta rows={[
            ['Street', titleCase(p.street || '—')],
            ['Protection', p.protection],
            ['Facility', p.facility || '—'],
          ]} />
        )}

        {selection.layerId === 'evacuation_zones' && (
          <div className="space-y-2">
            <Stat label="Evacuation zone" value={String(p.zone)} big />
            <p className="text-[12px] leading-relaxed text-nes-ink2">
              {p.zone === 1
                ? 'Zone 1 is the first ordered to evacuate — the lowest-lying, most flood-exposed land in the city.'
                : `Zone ${p.zone} evacuates after the lower-numbered zones, in the strongest storms.`}
            </p>
          </div>
        )}

        {['boroughs', 'neighborhoods'].includes(selection.layerId) && (
          <Meta rows={Object.entries(p).map(([k, v]) => [titleCase(k.replace(/_/g, ' ')), String(v)])} />
        )}

        {!KNOWN.includes(selection.layerId) && (
          <Meta rows={Object.entries(p).map(([k, v]) => [k, String(v)])} />
        )}
      </div>

      {def && (
        <footer className="border-t-2 border-black px-3.5 py-2
                           pb-[max(0.5rem,env(safe-area-inset-bottom))] md:pb-2">
          <a href={def.source.url} target="_blank" rel="noreferrer"
             className="inline-block py-1 text-[10.5px] text-nes-sky hover:underline">
            Source: {def.source.name} ↗
          </a>
        </footer>
      )}
    </aside>
  )
}

const KNOWN = [
  'housing_prices', 'sales', 'subway_stations', 'subway_ridership',
  'affordable_housing', 'collisions', 'parks', 'bike_routes',
  'evacuation_zones', 'boroughs', 'neighborhoods',
]

function headline(sel: Selection): string {
  const p = sel.props
  switch (sel.layerId) {
    case 'housing_prices': return p.name || p.area
    case 'sales': return titleCase(p.address || 'Sale')
    case 'subway_stations': return p.name
    case 'subway_ridership': return p.name
    case 'affordable_housing': return p.name || p.address
    case 'collisions': return `${p.injured} injured${p.killed > 0 ? `, ${p.killed} killed` : ''}`
    case 'parks': return p.signname || p.name311 || 'Park'
    case 'bike_routes': return titleCase(p.street || 'Bike route')
    case 'evacuation_zones': return `Zone ${p.zone}`
    case 'boroughs': return p.boroname
    case 'neighborhoods': return p.ntaname
    default: return sel.layerId
  }
}

function Stat({ label, value, big, tone }: {
  label: string; value: string; big?: boolean; tone?: 'good' | 'bad'
}) {
  // Luigi green and Mario red — the same up/down pair the rest of the HUD uses.
  const color = tone === 'good' ? '#43b047' : tone === 'bad' ? '#e52521' : '#ffffff'
  return (
    <div className="border-2 border-black bg-black/40 px-2.5 py-2">
      <div className="pixel text-[6.5px] leading-[1.8] text-nes-ink3">{label}</div>
      <div className={`${big ? 'text-[16px]' : 'text-[13px]'} font-medium tabular-nums`}
           style={{ color }}>
        {value}
      </div>
    </div>
  )
}

function Meta({ rows }: { rows: (string | number | null | undefined)[][] }) {
  const clean = rows.filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== '—')
  if (!clean.length) return null
  return (
    <dl className="space-y-1">
      {clean.map(([k, v], i) => (
        <div key={i} className="flex items-baseline justify-between gap-3 text-[11.5px]">
          <dt className="shrink-0 text-nes-ink3">{k}</dt>
          <dd className="truncate text-right text-white">{String(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

/** MTA route bullets, in the network's own colours. */
const ROUTE_COLOR: Record<string, string> = {
  '1': '#EE352E', '2': '#EE352E', '3': '#EE352E',
  '4': '#00933C', '5': '#00933C', '6': '#00933C',
  '7': '#B933AD',
  A: '#0039A6', C: '#0039A6', E: '#0039A6',
  B: '#FF6319', D: '#FF6319', F: '#FF6319', M: '#FF6319',
  G: '#6CBE45', J: '#996633', Z: '#996633', L: '#A7A9AC',
  N: '#FCCC0A', Q: '#FCCC0A', R: '#FCCC0A', W: '#FCCC0A',
  S: '#808183',
}

function RouteBullets({ routes }: { routes: string }) {
  const list = routes.split(/[\s,]+/).filter(Boolean)
  if (!list.length) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {list.map((r) => {
        const bg = ROUTE_COLOR[r.toUpperCase()] || '#8b93a7'
        const dark = ['#FCCC0A', '#A7A9AC', '#6CBE45'].includes(bg)
        return (
          <span key={r}
                className="flex h-6 w-6 items-center justify-center rounded-full text-[12px] font-bold ring-2 ring-black"
                style={{ background: bg, color: dark ? '#000000' : '#ffffff' }}>
            {r.toUpperCase()}
          </span>
        )
      })}
    </div>
  )
}
