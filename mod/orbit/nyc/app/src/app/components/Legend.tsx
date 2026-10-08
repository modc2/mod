'use client'

import type { Breaks, Choropleth, Options } from '@/lib/api'
import { byFormat } from '@/lib/format'
import {
  DIVERGING, LAYER_COLOR, NEWS_TOPIC, NEWS_TOPIC_LABEL, NO_DATA, SEQUENTIAL,
  SPEED_BAND, SPEED_BAND_LABEL, ZONE_COLOR, rampOf,
} from '@/lib/palette'
import { SALE_BREAKS } from './MapView'

/** A layer payload that carries its own quantile breaks (crime, forsale, …). */
type SelfDescribing = GeoJSON.FeatureCollection & { breaks?: Breaks; meta?: any }

type Props = {
  breaks: Breaks | null
  metric: string
  options: Options | null
  active: string[]
  areasWithData?: number
  totalAreas?: number
  population?: Choropleth | null
  crime?: SelfDescribing | null
  forsale?: SelfDescribing | null
}

/**
 * The map's key. Every active layer that carries an encoding gets a row —
 * identity is never left to colour alone, and a value scale always states its
 * units and its "no data" class explicitly.
 */
export default function Legend({
  breaks, metric, options, active, areasWithData, totalAreas, population, crime,
  forsale,
}: Props) {
  const rows: React.ReactNode[] = []

  const pb = population?.breaks
  if (active.includes('population') && pb && pb.stops.length > 1) {
    const pm = population!.meta as any
    rows.push(
      <RampRow key="population" breaks={pb} label={pm?.label ?? 'Population'}
               format={pm?.format} noData="No data / no residents" />,
    )
  }
  const cb = crime?.breaks
  if (active.includes('crime') && cb && cb.stops.length > 1) {
    const cm = crime!.meta as any
    rows.push(
      <RampRow key="crime" breaks={cb} label={cm?.label ?? 'Crime complaints'}
               format={cm?.format ?? 'int'}
               note="Reported to the NYPD this year, by precinct" />,
    )
  }
  const fb = forsale?.breaks
  if (active.includes('forsale') && fb && fb.stops.length > 1) {
    const fm = forsale!.meta as any
    rows.push(
      <RampRow key="forsale" breaks={fb} label={fm?.label ?? 'Median asking price'}
               format={fm?.format ?? 'usd'} noData="Not tracked by StreetEasy"
               note="Active listings — asking, not closing, prices" />,
    )
  }

  if (active.includes('news')) {
    rows.push(
      <Row key="news" title="News topics">
        <ul className="space-y-1">
          {NEWS_TOPIC_LABEL.map(([key, label]) => (
            <li key={key} className="flex items-center gap-2 text-[10.5px] text-nes-ink2">
              <span className="h-3 w-3 shrink-0 rounded-full ring-1 ring-white/70"
                    style={{ background: NEWS_TOPIC[key] }} />
              <span>{label}</span>
            </li>
          ))}
        </ul>
        <p className="mt-1 text-[11.5px] leading-snug text-nes-ink3">
          Faint dots are pinned to a whole borough.
        </p>
      </Row>,
    )
  }

  const meta = options?.metrics?.[metric]
  const fmt = meta?.format ?? 'usd'

  if (active.includes('housing_prices') && breaks && breaks.stops.length > 0) {
    const diverging = metric === 'price_change'
    const colors = diverging ? DIVERGING : rampOf(breaks.stops.length, SEQUENTIAL)
    const labels = diverging
      ? divergingLabels(breaks)
      : breaks.stops.map((s) => byFormat(s, fmt))

    rows.push(
      <div key="housing">
        {/* The pixel face is wide enough that a metric name and its coverage
            count won't share a 236px row — the count goes underneath rather
            than wrapping the title mid-phrase. */}
        <div className="mb-1.5">
          <div className="pixel text-[10px] leading-snug text-nes-coin">
            {meta?.label ?? metric}
          </div>
          {areasWithData !== undefined && totalAreas !== undefined && (
            <div className="mt-0.5 text-[10px] tabular-nums text-nes-ink3">
              {areasWithData}/{totalAreas} areas
            </div>
          )}
        </div>
        <div className="flex h-3 overflow-hidden rounded-md border border-white/10">
          {colors.map((c, i) => (
            <div key={i} className="flex-1" style={{ background: c }} />
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[11.5px] tabular-nums text-nes-ink3">
          <span>{labels[0]}</span>
          {labels.length > 2 && <span>{labels[Math.floor(labels.length / 2)]}</span>}
          <span>
            {diverging ? labels[labels.length - 1] : `${byFormat(breaks.max, fmt)}`}
          </span>
        </div>
        <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-nes-ink3">
          <span className="h-3 w-3 border border-white/10" style={{ background: NO_DATA }} />
          No qualifying sales
        </div>
        {diverging && breaks.true_min !== undefined && (
          <p className="mt-1 text-[11.5px] leading-snug text-nes-ink3">
            Scale clipped to the middle 90% ({breaks.true_min?.toFixed(0)}% to
            {' '}+{breaks.true_max?.toFixed(0)}% in full); thin-volume areas sit
            past the ends.
          </p>
        )}
      </div>,
    )
  }

  if (active.includes('sales')) {
    rows.push(
      <Row key="sales" title="Sale price">
        <div className="flex h-3 overflow-hidden rounded-md border border-white/10">
          {rampOf(SALE_BREAKS.length, SEQUENTIAL).map((c, i) => (
            <div key={i} className="flex-1" style={{ background: c }} />
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[11.5px] tabular-nums text-nes-ink3">
          <span>$0</span><span>$1M</span><span>$5M+</span>
        </div>
      </Row>,
    )
  }

  if (active.includes('evacuation_zones')) {
    rows.push(
      <Row key="evac" title="Evacuation zone">
        <div className="flex items-center gap-1">
          {[1, 2, 3, 4, 5, 6].map((z) => (
            <div key={z} className="flex flex-1 flex-col items-center gap-0.5">
              <span className="h-3 w-full border border-white/10"
                    style={{ background: ZONE_COLOR[z] }} />
              <span className="text-[11px] tabular-nums text-nes-ink3">{z}</span>
            </div>
          ))}
        </div>
        <p className="mt-1 text-[11.5px] text-nes-ink3">Zone 1 evacuates first</p>
      </Row>,
    )
  }

  if (active.includes('traffic_speeds')) {
    rows.push(
      <Row key="speeds" title="Speed now">
        <ul className="space-y-1">
          {SPEED_BAND_LABEL.map(([key, label]) => (
            <li key={key} className="flex items-center gap-2 text-[10.5px] text-nes-ink2">
              <span className="h-1.5 w-4 shrink-0 ring-2 ring-black"
                    style={{ background: SPEED_BAND[key] }} />
              <span className="tabular-nums">{label}</span>
            </li>
          ))}
        </ul>
        <p className="mt-1 text-[11.5px] leading-snug text-nes-ink3">
          Highway and arterial sensors only — local streets have no detector.
        </p>
      </Row>,
    )
  }

  if (active.includes('shootings')) {
    rows.push(
      <Row key="shootings" title="Shootings">
        <div className="flex h-3 overflow-hidden rounded-md border border-white/10"
             style={{ background: 'linear-gradient(90deg,#3b2a80,#7d2b6b,#b83c3c,#d95926,#eda100)' }} />
        <div className="mt-1 flex justify-between text-[11.5px] text-nes-ink3">
          <span>fewer</span><span>more incidents, 3 years</span>
        </div>
      </Row>,
    )
  }

  if (active.includes('collisions')) {
    rows.push(
      <Row key="collisions" title="Traffic injuries">
        <div className="flex h-3 overflow-hidden rounded-md border border-white/10"
             style={{ background: 'linear-gradient(90deg,#3b2a80,#7d2b6b,#b83c3c,#d95926,#eda100)' }} />
        <div className="mt-1 flex justify-between text-[11.5px] text-nes-ink3">
          <span>fewer</span><span>more crashes</span>
        </div>
      </Row>,
    )
  }

  const dots: [string, string, string][] = []
  if (active.includes('traffic_volume'))
    dots.push(['traffic_volume', 'Traffic volume', 'circle size = vehicles/day, floored'])
  if (active.includes('subway_ridership'))
    dots.push(['subway_ridership', 'Station ridership', 'circle size = riders'])
  if (active.includes('affordable_housing'))
    dots.push(['affordable_housing', 'Affordable housing', 'circle size = units'])
  if (active.includes('subway_stations'))
    dots.push(['subway_stations', 'Subway station', ''])
  if (active.includes('bike_routes'))
    dots.push(['bike_routes', 'Bike network', 'thick = protected'])
  if (active.includes('parks')) dots.push(['parks', 'Parks & open space', ''])

  if (dots.length) {
    rows.push(
      <Row key="marks" title="">
        <ul className="space-y-1">
          {dots.map(([id, label, hint]) => (
            <li key={id} className="flex items-center gap-2 text-[10.5px] text-nes-ink2">
              <span
                className="h-3 w-3 shrink-0 rounded-full ring-2 ring-black"
                style={{ background: LAYER_COLOR[id] }}
              />
              <span>{label}</span>
              {hint && <span className="text-[11.5px] text-nes-ink3">· {hint}</span>}
            </li>
          ))}
        </ul>
      </Row>,
    )
  }

  if (active.includes('subway_lines')) {
    rows.push(
      <Row key="subway" title="Subway routes">
        <p className="text-[10px] leading-snug text-nes-ink3">
          Drawn in each route’s official MTA colour.
        </p>
      </Row>,
    )
  }

  if (!rows.length) return null

  return (
    <div className="blk pointer-events-auto w-[min(80vw,236px)] space-y-3 p-3 md:w-[236px]">
      {rows}
    </div>
  )
}

/** Layers that put a row in the key, besides the choropleth. */
const ENCODED = [
  'sales', 'forsale', 'news', 'evacuation_zones', 'collisions', 'shootings', 'crime',
  'subway_ridership', 'affordable_housing', 'subway_stations', 'bike_routes',
  'parks', 'subway_lines', 'traffic_speeds', 'traffic_volume',
]

/**
 * Whether the key would render anything. The phone layout folds the key behind
 * a button, and a button that opens an empty panel is worse than no button —
 * this lets the caller leave it out entirely.
 */
export function hasLegend(active: string[], breaks: Breaks | null): boolean {
  if (active.includes('housing_prices') && breaks && breaks.stops.length > 0) return true
  return ENCODED.some((id) => active.includes(id))
}

/**
 * One quantile-ramp key: label, swatch strip, the range the paint actually
 * draws (the first class covers values under stops[1] — stepExpression skips
 * stops[0]), and optional no-data / footnote lines. The format comes from the
 * payload's meta, the same contract the paint reads its field name from.
 */
function RampRow({ breaks, label, format, noData, note }: {
  breaks: Breaks
  label: string
  format?: string
  noData?: string
  note?: string
}) {
  return (
    <div>
      <div className="mb-1.5 pixel text-[10px] leading-snug text-nes-coin">{label}</div>
      <div className="flex h-3 overflow-hidden rounded-md border border-white/10">
        {rampOf(breaks.stops.length, SEQUENTIAL).map((c, i) => (
          <div key={i} className="flex-1" style={{ background: c }} />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11.5px] tabular-nums text-nes-ink3">
        <span>under {byFormat(breaks.stops[1], format ?? '')}</span>
        <span>{byFormat(breaks.stops[breaks.stops.length - 1], format ?? '')}+</span>
      </div>
      {noData && (
        <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-nes-ink3">
          <span className="h-3 w-3 border border-white/10" style={{ background: NO_DATA }} />
          {noData}
        </div>
      )}
      {note && <p className="mt-1 text-[11.5px] leading-snug text-nes-ink3">{note}</p>}
    </div>
  )
}

function Row({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      {title && <div className="mb-1 pixel text-[10px] text-nes-coin">{title}</div>}
      {children}
    </div>
  )
}

function divergingLabels(breaks: Breaks): string[] {
  const m = Math.max(Math.abs(breaks.min ?? 0), Math.abs(breaks.max ?? 0))
  return [`−${m.toFixed(0)}%`, '', '', '0%', '', '', `+${m.toFixed(0)}%`]
}
