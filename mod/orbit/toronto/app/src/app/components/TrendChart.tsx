'use client'

import { useMemo, useState } from 'react'
import type { TrendPoint } from '@/lib/api'
import { count } from '@/lib/format'

type Props = {
  series: TrendPoint[]
  /** The most recent year, when it is still in progress. */
  partialYear?: number | null
  title?: string
}

/**
 * Yearly incident history for one area.
 *
 * The mark is an area, and an area encodes magnitude by height from its
 * baseline — so the baseline is zero. Padding the band around the data (the
 * right call for a price line) would make a 5% wobble look like a collapse.
 *
 * The current year is a partial count. It is drawn dashed and labelled rather
 * than annualised, because a projection here would be our number, not the
 * police service's.
 */
export default function TrendChart({ series, partialYear, title }: Props) {
  const [hover, setHover] = useState<number | null>(null)

  const pts = useMemo(
    () => series.filter((s) => typeof s.incidents === 'number'),
    [series],
  )

  const W = 244, H = 88, PL = 6, PR = 6, PT = 10, PB = 16

  const geom = useMemo(() => {
    if (pts.length < 2) return null
    const xs = pts.map((p) => p.year)
    const x0 = Math.min(...xs), x1 = Math.max(...xs)
    const hi = Math.max(...pts.map((p) => p.incidents)) || 1
    const sx = (x: number) => PL + ((x - x0) / Math.max(x1 - x0, 1)) * (W - PL - PR)
    const sy = (y: number) => PT + (1 - y / (hi * 1.12)) * (H - PT - PB)
    return { sx, sy }
  }, [pts])

  if (!geom) {
    return (
      <p className="px-3 py-2 text-[11px] text-muted">
        Not enough history to chart.
      </p>
    )
  }

  const { sx, sy } = geom
  const d = pts
    .map((p, i) => `${i ? 'L' : 'M'}${sx(p.year).toFixed(1)},${sy(p.incidents).toFixed(1)}`)
    .join(' ')
  const area = `${d} L${sx(pts[pts.length - 1].year).toFixed(1)},${H - PB} L${sx(pts[0].year).toFixed(1)},${H - PB} Z`
  const first = pts[0]
  const last = pts[pts.length - 1]

  // A partial final year can't be compared with a full one, so the headline
  // change is measured to the last *complete* year.
  const endIdx = partialYear === last.year && pts.length > 2 ? pts.length - 2 : pts.length - 1
  const end = pts[endIdx]
  const change = first.incidents > 0
    ? ((end.incidents - first.incidents) / first.incidents) * 100
    : null

  const partialSeg = partialYear === last.year && pts.length > 1
    ? `M${sx(pts[pts.length - 2].year).toFixed(1)},${sy(pts[pts.length - 2].incidents).toFixed(1)} L${sx(last.year).toFixed(1)},${sy(last.incidents).toFixed(1)}`
    : null

  const hp = hover !== null ? pts[hover] : null

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wider text-muted">
          {title ?? 'Incidents by year'}
        </span>
        {partialSeg && (
          <span className="text-[9.5px] text-muted">{last.year} partial</span>
        )}
      </div>

      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}
             onMouseLeave={() => setHover(null)}>
          <defs>
            <linearGradient id="trendfill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.28" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
            </linearGradient>
          </defs>

          <line x1={PL} y1={H - PB} x2={W - PR} y2={H - PB} style={{ stroke: 'var(--line-strong)' }} strokeWidth="1" />
          <path d={area} fill="url(#trendfill)" />
          <path d={d} fill="none" style={{ stroke: 'var(--accent)' }} strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round" />
          {/* redraw the incomplete year's segment dashed, over the solid line */}
          {partialSeg && (
            <path d={partialSeg} fill="none" style={{ stroke: 'var(--surface-solid)' }} strokeWidth="3.2" />
          )}
          {partialSeg && (
            <path d={partialSeg} fill="none" style={{ stroke: 'var(--accent)' }} strokeWidth="2"
                  strokeDasharray="3 2.5" strokeLinecap="round" />
          )}

          {hp && (
            <line x1={sx(hp.year)} y1={PT - 4} x2={sx(hp.year)} y2={H - PB}
                  style={{ stroke: 'var(--ink-2)' }} strokeWidth="1" strokeDasharray="2 2" />
          )}

          {/* endpoint marker, direct-labelled below — no dot on every year */}
          <circle cx={sx(last.year)} cy={sy(last.incidents)} r="3.4"
                  style={{ fill: 'var(--accent)', stroke: 'var(--surface-solid)' }} strokeWidth="2" />
          {hp && hover !== pts.length - 1 && (
            <circle cx={sx(hp.year)} cy={sy(hp.incidents)} r="3.4"
                    style={{ fill: 'var(--accent-soft)', stroke: 'var(--surface-solid)' }} strokeWidth="2" />
          )}

          <text x={PL} y={H - 4} style={{ fill: 'var(--muted)' }} fontSize="9">{first.year}</text>
          <text x={W - PR} y={H - 4} style={{ fill: 'var(--muted)' }} fontSize="9" textAnchor="end">{last.year}</text>

          {/* invisible hit strips — bigger targets than the marks */}
          {pts.map((p, i) => (
            <rect key={p.year} x={sx(p.year) - (W / pts.length) / 2} y={0}
                  width={W / pts.length} height={H} fill="transparent"
                  onMouseEnter={() => setHover(i)} />
          ))}
        </svg>

        {hp && (
          <div className="pointer-events-none absolute -top-1 left-0 right-0 flex justify-center">
            <div className="panel-solid px-1.5 py-0.5 text-[10px] tabular-nums text-ink">
              {hp.year} · {hp.incidents.toLocaleString()} incidents
              {partialYear === hp.year ? ' so far' : ''}
            </div>
          </div>
        )}
      </div>

      <div className="mt-0.5 flex items-baseline justify-between">
        <span className="text-[12px] font-medium tabular-nums text-ink">
          {count(last.incidents)}
          <span className="ml-1 text-[10px] font-normal text-muted">in {last.year}</span>
        </span>
        {change !== null && (
          <span className="text-[10.5px] tabular-nums"
                style={{ color: change >= 0 ? 'var(--bad)' : 'var(--good)' }}>
            {change >= 0 ? '↑' : '↓'} {Math.abs(change).toFixed(0)}% {first.year}→{end.year}
          </span>
        )}
      </div>
    </div>
  )
}
