'use client'

import { useState } from 'react'
import type { AgentOverlay, Infographic as Card } from '@/lib/scene'
import { INK, SEQUENTIAL, rampOf } from '@/lib/palette'
import { count } from '@/lib/format'

/** The one hue a single-series chart needs: the sequential ramp's mid step. */
const BAR = SEQUENTIAL[3]

/**
 * The card the agent pins on the map. Agent-written text stays in the body
 * sans (it is free text — lower case, punctuation, units); only the fixed
 * chrome is in the pixel face.
 */
export default function Infographic({ card, onClose }: { card: Card; onClose: () => void }) {
  return (
    <section className="blk sheet-in pointer-events-auto flex max-h-full flex-col overflow-hidden">
      <header className="relative flex shrink-0 items-start gap-2 border-b border-white/10 bg-black/40 py-2.5 pl-4 pr-2">
        <span className="accent-bar absolute inset-y-1.5 left-0 w-[3px]" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="pixel text-[11.5px] leading-none text-nes-coin">INFOGRAPHIC</p>
          <h2 className="mt-1.5 text-[14px] font-semibold leading-snug text-white">{card.title}</h2>
          {card.subtitle && <p className="mt-0.5 text-[11.5px] leading-snug text-nes-ink3">{card.subtitle}</p>}
        </div>
        <button onClick={onClose} aria-label="Close infographic"
                className="tap -m-1 grid shrink-0 place-items-center p-1.5 text-nes-ink3 hover:text-nes-red">
          <X />
        </button>
      </header>

      <div className="space-y-3.5 overflow-y-auto px-3.5 py-3">
        {!!card.stats?.length && (
          <div className={`grid gap-2 ${card.stats.length === 1 ? 'grid-cols-1' : card.stats.length % 3 === 0 ? 'grid-cols-3' : 'grid-cols-2'}`}>
            {card.stats.map((s, i) => (
              <div key={i} className="rounded-lg border border-white/10 bg-black/30 px-2.5 py-2">
                <div className="text-[18px] font-semibold leading-tight text-white tabular-nums">{s.value}</div>
                <div className="mt-0.5 text-[11px] leading-snug text-nes-ink2">{s.label}</div>
                {s.note && <div className="mt-0.5 text-[10.5px] leading-snug text-nes-ink3">{s.note}</div>}
              </div>
            ))}
          </div>
        )}
        {!!card.bars?.items.length && <Bars bars={card.bars} />}
        {card.series && card.series.points.length > 1 && <Series series={card.series} />}
        {!!card.bullets?.length && (
          <ul className="space-y-1 text-[12px] leading-relaxed text-nes-ink2">
            {card.bullets.map((b, i) => (
              <li key={i} className="flex gap-2">
                <span className="mt-[7px] h-[4px] w-[4px] shrink-0 bg-nes-coin" aria-hidden />
                <span>{b}</span>
              </li>
            ))}
          </ul>
        )}
        {!!card.sources?.length && (
          <p className="text-[10.5px] leading-relaxed text-nes-ink3">
            Source:{' '}
            {card.sources.map((s, i) => (
              <span key={i}>
                {i > 0 && '; '}
                {s.url
                  ? <a href={s.url} target="_blank" rel="noreferrer" className="underline decoration-dotted hover:text-white">{s.name}</a>
                  : s.name}
              </span>
            ))}
          </p>
        )}
      </div>
    </section>
  )
}

/** A ranked bar list: zero baseline, one hue, values direct-labelled. */
function Bars({ bars }: { bars: NonNullable<Card['bars']> }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(...bars.items.map((i) => Math.abs(i.value)), 1)
  return (
    <figure>
      {(bars.title || bars.unit) && (
        <figcaption className="mb-1.5 text-[11px] text-nes-ink3">
          {bars.title}{bars.unit && <span> ({bars.unit})</span>}
        </figcaption>
      )}
      <div className="space-y-[3px]" role="table">
        {bars.items.map((it, i) => (
          <div key={i} role="row"
               onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
               className={`grid grid-cols-[minmax(0,38%)_1fr_auto] items-center gap-2 py-[2px] ${hover === i ? 'bg-white/5' : ''}`}>
            <span role="cell" className="truncate text-[11.5px] text-nes-ink2" title={it.label}>{it.label}</span>
            <span role="cell" className="relative h-[12px]">
              <span className="absolute inset-y-0 left-0 rounded-r-[3px]"
                    style={{ width: `${Math.max(1.5, (Math.abs(it.value) / max) * 100)}%`,
                             background: BAR, opacity: hover === null || hover === i ? 1 : 0.55 }} />
            </span>
            <span role="cell" className="text-right text-[11px] tabular-nums text-white">{fmt(it.value)}</span>
          </div>
        ))}
      </div>
    </figure>
  )
}

/** A small line over time, with a hover readout; the y band hugs the data. */
function Series({ series }: { series: NonNullable<Card['series']> }) {
  const [hover, setHover] = useState<number | null>(null)
  const pts = series.points
  const W = 380, H = 96, PL = 4, PR = 4, PT = 10, PB = 16
  const ys = pts.map((p) => p.y)
  const lo = Math.min(...ys), hi = Math.max(...ys)
  const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 1
  const sx = (i: number) => PL + (i / (pts.length - 1)) * (W - PL - PR)
  const sy = (y: number) => PT + (1 - (y - (lo - pad)) / (hi - lo + 2 * pad)) * (H - PT - PB)
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ')
  const h = hover ?? pts.length - 1
  return (
    <figure>
      <figcaption className="mb-1 flex justify-between text-[11px] text-nes-ink3">
        <span>{series.title}{series.unit && ` (${series.unit})`}</span>
        <span className="tabular-nums text-white">{pts[h].x}: {fmt(pts[h].y)}</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
           aria-label={`${series.title}: ${pts.map((p) => `${p.x} ${fmt(p.y)}`).join(', ')}`}
           onMouseLeave={() => setHover(null)}
           onMouseMove={(e) => {
             const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
             const x = ((e.clientX - r.left) / r.width) * W
             setHover(Math.max(0, Math.min(pts.length - 1, Math.round(((x - PL) / (W - PL - PR)) * (pts.length - 1)))))
           }}>
        <line x1={PL} x2={W - PR} y1={H - PB} y2={H - PB} stroke={INK.axis} strokeWidth={1} />
        <path d={d} fill="none" stroke={BAR} strokeWidth={2} strokeLinejoin="round" />
        <line x1={sx(h)} x2={sx(h)} y1={PT} y2={H - PB} stroke={INK.muted} strokeWidth={1} strokeDasharray="2 2" />
        <circle cx={sx(h)} cy={sy(pts[h].y)} r={4} fill={BAR} stroke={INK.plane} strokeWidth={2} />
        <text x={PL} y={H - 3} fontSize={10} fill={INK.muted}>{pts[0].x}</text>
        <text x={W - PR} y={H - 3} fontSize={10} fill={INK.muted} textAnchor="end">{pts[pts.length - 1].x}</text>
      </svg>
    </figure>
  )
}

/**
 * The key for whatever the agent drew: its title, a ramp for an area
 * choropleth, the count for points, and the way to take it off the map.
 */
export function AgentLegend({ overlay, caption, onClear }: {
  overlay: AgentOverlay | null
  caption: string | null
  onClear: () => void
}) {
  if (!overlay && !caption) return null
  const meta = overlay?.data?.meta ?? {}
  const stops = overlay?.data?.breaks?.stops ?? []
  const colors = rampOf(stops.length, SEQUENTIAL)
  return (
    <div className="blk pointer-events-auto max-w-[min(92vw,420px)] px-3 py-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="pixel text-[11.5px] leading-none text-nes-coin">AGENT VIEW</p>
          {overlay && <p className="mt-1.5 text-[12px] font-semibold leading-snug text-white">{overlay.spec.title}</p>}
          {caption && <p className="mt-0.5 text-[11px] leading-snug text-nes-ink2">{caption}</p>}
        </div>
        <button onClick={onClear} aria-label="Clear agent view"
                className="tap -m-1 grid shrink-0 place-items-center p-1.5 text-nes-ink3 hover:text-nes-red">
          <X />
        </button>
      </div>
      {overlay && !overlay.data && !overlay.error && (
        <p className="pixel mt-2 text-[10px] text-nes-ink3">Loading…</p>
      )}
      {overlay?.error && <p className="mt-1.5 text-[11px] text-nes-red">{overlay.error}</p>}
      {overlay?.spec.mode === 'areas' && stops.length > 0 && (
        <div className="mt-2">
          <div className="flex h-[10px] gap-[2px]">
            <span className="flex-1" style={{ background: '#5c5b56' }} title="No data" />
            {colors.map((c, i) => <span key={i} className="flex-[2]" style={{ background: c }} />)}
          </div>
          <div className="mt-1 flex justify-between text-[10px] tabular-nums text-nes-ink3">
            <span>no data</span>
            <span>{fmt(stops[0])}</span>
            <span>{fmt(stops[stops.length - 1])}+</span>
          </div>
          <p className="mt-0.5 text-[10px] text-nes-ink3">{meta.unit} by {overlay.spec.by}</p>
        </div>
      )}
      {overlay && overlay.spec.mode !== 'areas' && overlay.data && (
        <p className="mt-1 text-[10.5px] text-nes-ink3">
          {count(meta.plotted)} {overlay.spec.mode === 'heat' ? 'records as density' : 'points'}
          {meta.capped ? ' (first ' + count(meta.rows) + ' rows)' : ''}
        </p>
      )}
      {meta.url && (
        <a href={meta.url} target="_blank" rel="noreferrer"
           className="mt-0.5 block text-[10px] text-nes-ink3 underline decoration-dotted hover:text-white">
          {overlay?.spec.dataset} on {overlay?.spec.domain}
        </a>
      )}
    </div>
  )
}

function fmt(v: number): string {
  if (Math.abs(v) >= 1000) return count(v)
  return Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 10 ? 2 : 1)
}

function X() {
  return (
    <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden>
      <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.6"
            strokeLinecap="round" />
    </svg>
  )
}
