'use client'

import { useMemo, useState } from 'react'
import type { TrendPoint } from '@/lib/api'
import { usd, usdExact } from '@/lib/format'

type Measure = 'median_price' | 'median_ppsf'

const MEASURES: Record<Measure, { label: string; short: string }> = {
  median_price: { label: 'Median sale price', short: 'Price' },
  median_ppsf: { label: 'Median price per ft²', short: '$/ft²' },
}

/**
 * Yearly price history for one area.
 *
 * Price and $/ft² are different scales, so they are never drawn on two y-axes
 * — the viewer switches between them and each gets the full height.
 */
export default function TrendChart({ series, title }: { series: TrendPoint[]; title?: string }) {
  const [measure, setMeasure] = useState<Measure>('median_price')
  const [hover, setHover] = useState<number | null>(null)

  const pts = useMemo(
    () => series.filter((s) => s[measure] !== null && s[measure] !== undefined),
    [series, measure],
  )

  const W = 244, H = 88, PL = 6, PR = 6, PT = 10, PB = 16

  const geom = useMemo(() => {
    if (pts.length < 2) return null
    const xs = pts.map((p) => p.year)
    const ys = pts.map((p) => p[measure] as number)
    const x0 = Math.min(...xs), x1 = Math.max(...xs)
    // Baseline at zero would flatten every real movement into a straight line,
    // so the band is padded around the data. A line chart may do this; a bar
    // chart may not.
    const lo = Math.min(...ys), hi = Math.max(...ys)
    const pad = (hi - lo) * 0.18 || hi * 0.1 || 1
    const yLo = lo - pad, yHi = hi + pad
    const sx = (x: number) => PL + ((x - x0) / Math.max(x1 - x0, 1)) * (W - PL - PR)
    const sy = (y: number) => PT + (1 - (y - yLo) / Math.max(yHi - yLo, 1)) * (H - PT - PB)
    return { sx, sy, x0, x1, yLo, yHi, xs, ys }
  }, [pts, measure])

  if (!geom) {
    return (
      <p className="px-3 py-2 text-[11px] text-nes-ink3">
        Not enough {MEASURES[measure].label.toLowerCase()} history to chart.
      </p>
    )
  }

  const { sx, sy } = geom
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.year).toFixed(1)},${sy(p[measure] as number).toFixed(1)}`).join(' ')
  const area = `${d} L${sx(pts[pts.length - 1].year).toFixed(1)},${H - PB} L${sx(pts[0].year).toFixed(1)},${H - PB} Z`
  const last = pts[pts.length - 1]
  const first = pts[0]
  const change = ((last[measure] as number) - (first[measure] as number)) / (first[measure] as number) * 100
  const hp = hover !== null ? pts[hover] : null

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="pixel text-[6.5px] leading-[1.8] text-nes-ink3">
          {title ?? MEASURES[measure].label}
        </span>
        <div className="flex gap-1">
          {(Object.keys(MEASURES) as Measure[]).map((k) => (
            <button
              key={k}
              onClick={() => { setMeasure(k); setHover(null) }}
              className={`btn px-1.5 py-1 text-[9px] ${measure === k ? 'btn-on' : ''}`}
            >
              {MEASURES[k].short}
            </button>
          ))}
        </div>
      </div>

      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}
             onMouseLeave={() => setHover(null)}>
          <defs>
            <linearGradient id="trendfill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3987e5" stopOpacity="0.28" />
              <stop offset="100%" stopColor="#3987e5" stopOpacity="0" />
            </linearGradient>
          </defs>

          <line x1={PL} y1={H - PB} x2={W - PR} y2={H - PB} stroke="#000000" strokeWidth="1" />
          <path d={area} fill="url(#trendfill)" />
          <path d={d} fill="none" stroke="#3987e5" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round" />

          {hp && (
            <line x1={sx(hp.year)} y1={PT - 4} x2={sx(hp.year)} y2={H - PB}
                  stroke="#fbd000" strokeWidth="1" strokeDasharray="2 2" />
          )}

          {/* endpoint marker, direct-labelled below — no dot on every year */}
          <circle cx={sx(last.year)} cy={sy(last[measure] as number)} r="3.4"
                  fill="#3987e5" stroke="#0e1330" strokeWidth="2" />
          {hp && hover !== pts.length - 1 && (
            <circle cx={sx(hp.year)} cy={sy(hp[measure] as number)} r="3.4"
                    fill="#9ec5f4" stroke="#0e1330" strokeWidth="2" />
          )}

          <text x={PL} y={H - 4} fill="#8f98c8" fontSize="9">{first.year}</text>
          <text x={W - PR} y={H - 4} fill="#8f98c8" fontSize="9" textAnchor="end">{last.year}</text>

          {/* Invisible hit strips — bigger targets than the marks. A finger
              has no hover, so the same strip reads a tap: on a phone the year
              readout is the only way to get at the years between the two the
              axis labels. */}
          {pts.map((p, i) => (
            <rect key={p.year} x={sx(p.year) - (W / pts.length) / 2} y={0}
                  width={W / pts.length} height={H} fill="transparent"
                  onMouseEnter={() => setHover(i)}
                  onTouchStart={() => setHover(i)} />
          ))}
        </svg>

        {hp && (
          <div className="pointer-events-none absolute -top-1 left-0 right-0 flex justify-center">
            <div className="border-2 border-black bg-nes-void px-1.5 py-0.5 text-[10px] tabular-nums text-white">
              {hp.year} · {measure === 'median_price'
                ? usdExact(hp.median_price)
                : `$${hp.median_ppsf}/ft²`} · {hp.sales.toLocaleString()} sales
            </div>
          </div>
        )}
      </div>

      <div className="mt-0.5 flex items-baseline justify-between">
        <span className="text-[12px] font-medium tabular-nums text-white">
          {measure === 'median_price' ? usd(last.median_price) : `$${last.median_ppsf}/ft²`}
          <span className="ml-1 text-[10px] font-normal text-nes-ink3">in {last.year}</span>
        </span>
        <span className="text-[10.5px] tabular-nums"
              style={{ color: change >= 0 ? '#43b047' : '#e52521' }}>
          {change >= 0 ? '↑' : '↓'} {Math.abs(change).toFixed(0)}% since {first.year}
        </span>
      </div>
    </div>
  )
}
