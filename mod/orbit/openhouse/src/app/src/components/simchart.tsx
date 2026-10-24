/* The simulation's one chart: where every Ξ went, month by month.

   Three cumulative series on one axis — the renter's equity, the owner's
   income, the protocol's fee — against the price of the house, which is the
   line equity is climbing toward. The crosshair IS the scrubber: wherever
   the pointer (or the range input below) stands, the hero figure and the
   tiles read that month. Pure SVG, no library; every colour is a vibe token
   so the eight cabinets repaint it. */

"use client";

import { useMemo, useRef, useState } from 'react'
import type { SimMonth, SimQuarter } from '../lib/api'

export const SERIES = [
  { key: 'equity',       label: 'Your equity',   color: 'var(--coral)' },
  { key: 'owner_income', label: 'Owner income',  color: 'rgb(var(--em-600-rgb))' },
  { key: 'fees',         label: 'Protocol fee',  color: 'var(--pink)' },
] as const
type SeriesKey = (typeof SERIES)[number]['key']

const W = 960, H = 340
const PAD = { l: 52, r: 20, t: 18, b: 34 }

function nice(max: number) {
  if (max <= 0) return 1
  const p = Math.pow(10, Math.floor(Math.log10(max)))
  const f = max / p
  const step = f <= 1 ? 0.25 : f <= 2 ? 0.5 : f <= 5 ? 1 : 2.5
  return Math.ceil(max / (step * p)) * step * p
}

export function SimChart({ rows, price, month, setMonth, quarters, ownedMonth, fiat }: {
  rows: SimMonth[]; price: number; month: number; setMonth: (m: number) => void
  quarters: SimQuarter[]; ownedMonth: number | null; fiat: (n: number) => string | null
}) {
  const svg = useRef<SVGSVGElement>(null)
  const [hover, setHover] = useState(false)
  const n = rows.length
  const yMax = useMemo(() => nice(Math.max(price, ...rows.map(r => Math.max(r.equity, r.owner_income, r.fees))) * 1.04), [rows, price])

  const x = (m: number) => PAD.l + ((m - 1) / Math.max(n - 1, 1)) * (W - PAD.l - PAD.r)
  const y = (v: number) => H - PAD.b - (v / yMax) * (H - PAD.t - PAD.b)

  const path = (key: SeriesKey) => rows.map((r, i) => `${i ? 'L' : 'M'}${x(r.m).toFixed(1)},${y(r[key]).toFixed(1)}`).join(' ')
  const area = `${path('equity')} L${x(n).toFixed(1)},${y(0)} L${x(1).toFixed(1)},${y(0)} Z`

  const gridY = [0.25, 0.5, 0.75, 1].map(f => f * yMax)
  const years = Math.ceil(n / 12)
  const yearStep = years <= 10 ? 1 : years <= 20 ? 2 : 5
  const xTicks = Array.from({ length: Math.floor(years / yearStep) }, (_, i) => (i + 1) * yearStep * 12).filter(m => m <= n)

  const cur = rows[Math.min(Math.max(month, 1), n) - 1]
  const pick = (e: React.PointerEvent) => {
    const el = svg.current; if (!el || !n) return
    const r = el.getBoundingClientRect()
    const px = ((e.clientX - r.left) / r.width) * W
    const m = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (n - 1)) + 1
    setMonth(Math.min(Math.max(m, 1), n))
  }

  if (!n || !cur) return <div className="h-[340px]" />
  const cx = x(cur.m)
  const flip = cx > W * 0.62

  return (
    <div className="relative select-none">
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="w-full h-auto block cursor-crosshair touch-none"
        onPointerMove={e => { setHover(true); pick(e) }} onPointerDown={pick} onPointerLeave={() => setHover(false)}
        role="img" aria-label="Cumulative equity, owner income and protocol fee by month">
        {/* grid — hairline, solid, one step off the surface */}
        {gridY.map(v => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} style={{ stroke: 'rgb(var(--ink-rgb) / 0.09)', strokeWidth: 1 }} />
            <text x={PAD.l - 8} y={y(v) + 4} textAnchor="end" className="tabular-nums" style={{ fill: 'rgb(var(--ink-rgb) / 0.5)', fontSize: 11, fontFamily: 'var(--font-body)' }}>
              {v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)}
            </text>
          </g>
        ))}
        <line x1={PAD.l} x2={W - PAD.r} y1={y(0)} y2={y(0)} style={{ stroke: 'rgb(var(--ink-rgb) / 0.2)', strokeWidth: 1 }} />
        {xTicks.map(m => (
          <text key={m} x={x(m)} y={H - PAD.b + 18} textAnchor="middle" style={{ fill: 'rgb(var(--ink-rgb) / 0.5)', fontSize: 11, fontFamily: 'var(--font-body)' }}>
            yr {m / 12}
          </text>
        ))}
        <text x={PAD.l - 8} y={PAD.t - 4} textAnchor="end" style={{ fill: 'rgb(var(--ink-rgb) / 0.5)', fontSize: 10, fontFamily: 'var(--font-body)' }}>Ξ</text>

        {/* the house — the line equity is climbing to */}
        {price <= yMax && (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(price)} y2={y(price)} style={{ stroke: 'rgb(var(--ink-rgb) / 0.45)', strokeWidth: 1 }} />
            <text x={PAD.l + 8} y={y(price) - 6} textAnchor="start" style={{ fill: 'rgb(var(--ink-rgb) / 0.6)', fontSize: 11, fontWeight: 700, fontFamily: 'var(--font-body)', letterSpacing: '0.08em' }}>
              THE HOUSE · {price} Ξ
            </text>
          </g>
        )}

        {/* quarter closes — ticks on the baseline */}
        {quarters.map(q => (
          <line key={q.quarter} x1={x(q.month)} x2={x(q.month)} y1={y(0)} y2={y(0) + 5} style={{ stroke: 'rgb(var(--ink-rgb) / 0.35)', strokeWidth: 1 }} />
        ))}
        {/* skipped months — a hollow mark on the equity line */}
        {rows.filter(r => r.skipped.length).map(r => (
          <circle key={r.m} cx={x(r.m)} cy={y(r.equity)} r={4} style={{ fill: 'var(--paper)', stroke: 'var(--sun)', strokeWidth: 2 }} />
        ))}

        {/* equity wash + three lines */}
        <path d={area} style={{ fill: 'var(--coral)', opacity: 0.1 }} />
        {SERIES.map(s => (
          <path key={s.key} d={path(s.key)} fill="none" style={{ stroke: s.color, strokeWidth: 2, strokeLinejoin: 'round', strokeLinecap: 'round' }} />
        ))}
        {/* end markers: ≥8px, surface ring */}
        {SERIES.map(s => (
          <circle key={s.key} cx={x(n)} cy={y(rows[n - 1][s.key])} r={4.5} style={{ fill: s.color, stroke: 'var(--paper)', strokeWidth: 2 }} />
        ))}
        {/* direct label: the endpoint of the story series only */}
        <text x={x(n) - 8} y={y(rows[n - 1].equity) - 10} textAnchor="end" className="tabular-nums"
          style={{ fill: 'rgb(var(--ink-rgb) / 0.85)', fontSize: 12, fontWeight: 700, fontFamily: 'var(--font-body)' }}>
          {rows[n - 1].equity.toFixed(1)} Ξ
        </text>

        {/* the month the house changed hands */}
        {ownedMonth && ownedMonth <= n && (
          <g>
            <line x1={x(ownedMonth)} x2={x(ownedMonth)} y1={PAD.t} y2={y(0)} style={{ stroke: 'var(--coral)', strokeWidth: 1, opacity: 0.6 }} />
            <text x={x(ownedMonth) - 6} y={PAD.t + 10} textAnchor="end" style={{ fill: 'var(--coral)', fontSize: 10, fontWeight: 800, fontFamily: 'var(--font-body)', letterSpacing: '0.12em' }}>YOURS</text>
          </g>
        )}

        {/* crosshair = scrubber */}
        <line x1={cx} x2={cx} y1={PAD.t} y2={y(0)} style={{ stroke: 'rgb(var(--ink-rgb) / 0.55)', strokeWidth: 1 }} />
        {SERIES.map(s => (
          <circle key={s.key} cx={cx} cy={y(cur[s.key])} r={4.5} style={{ fill: s.color, stroke: 'var(--paper)', strokeWidth: 2 }} />
        ))}
      </svg>

      {/* tooltip — every series at this X, values first */}
      <div className={`absolute top-2 pointer-events-none transition-opacity ${hover ? 'opacity-100' : 'opacity-0'}`}
        style={{ left: `${(cx / W) * 100}%`, transform: flip ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)' }}>
        <div className="glass rounded-xl px-3.5 py-2.5 min-w-[11rem]">
          <div className="text-[10px] font-bold uppercase tracking-widest text-white/50 mb-1.5">
            Month {cur.m} · year {(cur.m / 12).toFixed(1)}
          </div>
          {SERIES.map(s => (
            <div key={s.key} className="flex items-center gap-2 text-[12px] leading-5">
              <span className="w-3 h-0.5 shrink-0" style={{ background: s.color }} />
              <span className="font-bold tabular-nums text-white">{cur[s.key].toFixed(2)} Ξ</span>
              <span className="text-white/55">{s.label}</span>
            </div>
          ))}
          <div className="text-[11px] text-white/55 mt-1.5 tabular-nums">
            {cur.owned_pct.toFixed(1)}% yours{fiat(cur.equity) ? ` · ≈ ${fiat(cur.equity)}` : ''}
            {cur.skipped.length ? ` · ${cur.skipped.join(', ')} skipped` : ''}
          </div>
        </div>
      </div>
    </div>
  )
}

/** Legend — always present for three series; text wears text tokens. */
export function SimLegend({ hasSkips, hasQuarters }: { hasSkips: boolean; hasQuarters: boolean }) {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-[11px] text-white/65">
      {SERIES.map(s => (
        <span key={s.key} className="inline-flex items-center gap-2"><span className="w-4 h-0.5" style={{ background: s.color }} />{s.label}</span>
      ))}
      <span className="inline-flex items-center gap-2"><span className="w-4 h-px" style={{ background: 'rgb(var(--ink-rgb) / 0.45)' }} />The house</span>
      {hasQuarters && <span className="inline-flex items-center gap-2"><span className="w-px h-2.5" style={{ background: 'rgb(var(--ink-rgb) / 0.35)' }} />Quarter closes</span>}
      {hasSkips && <span className="inline-flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full border-2" style={{ borderColor: 'var(--sun)' }} />Month skipped</span>}
    </div>
  )
}

/** The same checks, somewhere else — three stacked bars, 2px surface gaps. */
export function VersusBars({ gross, equity, ownerIncome, fees }: { gross: number; equity: number; ownerIncome: number; fees: number }) {
  const rows = [
    { name: 'OpenHouse', segs: [equity, ownerIncome, fees], ours: true },
    { name: 'A 15% platform', segs: [0, gross * 0.85, gross * 0.15] },
    { name: 'A plain lease', segs: [0, gross, 0] },
  ]
  const legs = [
    { label: 'Your equity', color: 'var(--coral)' },
    { label: 'Landlord / owner', color: 'rgb(var(--em-600-rgb))' },
    { label: 'Platform take', color: 'var(--pink)' },
  ]
  return (
    <div>
      <div className="space-y-3">
        {rows.map(r => (
          <div key={r.name} className="grid grid-cols-[8.5rem_1fr] items-center gap-3">
            <span className={`text-xs ${r.ours ? 'text-coral font-bold' : 'text-white/70'}`}>{r.name}</span>
            <div className="flex h-5 gap-0.5">
              {r.segs.map((v, i) => gross > 0 && v > 0 ? (
                <div key={i} title={`${legs[i].label}: ${v.toFixed(2)} Ξ`} className="h-full first:rounded-l last:rounded-r"
                  style={{ width: `${(v / gross) * 100}%`, background: legs[i].color }} />
              ) : null)}
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 mt-3 text-[11px] text-white/65">
        {legs.map(l => <span key={l.label} className="inline-flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: l.color }} />{l.label}</span>)}
      </div>
    </div>
  )
}
