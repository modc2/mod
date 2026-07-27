'use client'

import type { Breaks, Options } from '@/lib/api'
import { byFormat } from '@/lib/format'
import { DIVERGING, LAYER_COLOR, NO_DATA, SEQUENTIAL, ZONE_COLOR, rampOf } from '@/lib/palette'
import { SALE_BREAKS } from './MapView'

type Props = {
  breaks: Breaks | null
  metric: string
  options: Options | null
  active: string[]
  areasWithData?: number
  totalAreas?: number
}

/**
 * The map's key. Every active layer that carries an encoding gets a row —
 * identity is never left to colour alone, and a value scale always states its
 * units and its "no data" class explicitly.
 */
export default function Legend({
  breaks, metric, options, active, areasWithData, totalAreas,
}: Props) {
  const rows: React.ReactNode[] = []
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
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="text-[11px] font-medium text-[#e6e8ee]">{meta?.label ?? metric}</span>
          {areasWithData !== undefined && totalAreas !== undefined && (
            <span className="text-[10px] tabular-nums text-[#898781]">
              {areasWithData}/{totalAreas} areas
            </span>
          )}
        </div>
        <div className="flex h-2.5 overflow-hidden rounded-[3px]">
          {colors.map((c, i) => (
            <div key={i} className="flex-1" style={{ background: c }} />
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[9.5px] tabular-nums text-[#898781]">
          <span>{labels[0]}</span>
          {labels.length > 2 && <span>{labels[Math.floor(labels.length / 2)]}</span>}
          <span>
            {diverging ? labels[labels.length - 1] : `${byFormat(breaks.max, fmt)}`}
          </span>
        </div>
        <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-[#898781]">
          <span className="h-2.5 w-2.5 rounded-[2px]" style={{ background: NO_DATA }} />
          No qualifying sales
        </div>
        {diverging && breaks.true_min !== undefined && (
          <p className="mt-1 text-[9.5px] leading-snug text-[#898781]">
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
        <div className="flex h-2.5 overflow-hidden rounded-[3px]">
          {rampOf(SALE_BREAKS.length, SEQUENTIAL).map((c, i) => (
            <div key={i} className="flex-1" style={{ background: c }} />
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[9.5px] tabular-nums text-[#898781]">
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
              <span className="h-2.5 w-full rounded-[2px]"
                    style={{ background: ZONE_COLOR[z] }} />
              <span className="text-[9px] tabular-nums text-[#898781]">{z}</span>
            </div>
          ))}
        </div>
        <p className="mt-1 text-[9.5px] text-[#898781]">Zone 1 evacuates first</p>
      </Row>,
    )
  }

  if (active.includes('collisions')) {
    rows.push(
      <Row key="collisions" title="Traffic injuries">
        <div className="flex h-2.5 overflow-hidden rounded-[3px]"
             style={{ background: 'linear-gradient(90deg,#3b2a80,#7d2b6b,#b83c3c,#d95926,#eda100)' }} />
        <div className="mt-1 flex justify-between text-[9.5px] text-[#898781]">
          <span>fewer</span><span>more crashes</span>
        </div>
      </Row>,
    )
  }

  const dots: [string, string, string][] = []
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
            <li key={id} className="flex items-center gap-2 text-[10.5px] text-[#c3c2b7]">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-black/40"
                style={{ background: LAYER_COLOR[id] }}
              />
              <span>{label}</span>
              {hint && <span className="text-[9.5px] text-[#898781]">· {hint}</span>}
            </li>
          ))}
        </ul>
      </Row>,
    )
  }

  if (active.includes('subway_lines')) {
    rows.push(
      <Row key="subway" title="Subway routes">
        <p className="text-[10px] leading-snug text-[#898781]">
          Drawn in each route’s official MTA colour.
        </p>
      </Row>,
    )
  }

  if (!rows.length) return null

  return (
    <div className="pointer-events-auto w-[228px] space-y-3 rounded-lg border border-white/10 bg-[#121722]/95 p-3 shadow-xl backdrop-blur">
      {rows}
    </div>
  )
}

function Row({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      {title && <div className="mb-1 text-[11px] font-medium text-[#e6e8ee]">{title}</div>}
      {children}
    </div>
  )
}

function divergingLabels(breaks: Breaks): string[] {
  const m = Math.max(Math.abs(breaks.min ?? 0), Math.abs(breaks.max ?? 0))
  return [`−${m.toFixed(0)}%`, '', '', '0%', '', '', `+${m.toFixed(0)}%`]
}
