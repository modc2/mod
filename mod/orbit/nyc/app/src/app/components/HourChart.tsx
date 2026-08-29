'use client'

import { useState } from 'react'

/**
 * A count location's 24-hour traffic profile.
 *
 * Bars, not a line: hour of day is a set of discrete buckets, each an average
 * of many counts, and the question asked of this chart is "which bar is
 * shortest" rather than "what is the trend". Bars are therefore baselined at
 * zero — the volumes here run from a few hundred to several thousand vehicles
 * an hour, and a padded baseline would make a quiet hour look like no traffic.
 *
 * The calmest and busiest hours are direct-labelled and given their own fill,
 * because those two are the entire answer for someone deciding when to leave;
 * the rest of the bars are context. Colour is never the only cue — both are
 * also named in the caption underneath.
 */
export default function HourChart({
  profile, peakHour, calmHour, now,
}: {
  profile: number[]
  peakHour: number
  calmHour: number
  /** The viewer's current hour, marked so "should I go now" reads at a glance. */
  now?: number
}) {
  const [hover, setHover] = useState<number | null>(null)
  if (!profile || profile.length !== 24) return null

  const W = 244, H = 74, PB = 13, PT = 4
  const max = Math.max(...profile) || 1
  const bw = W / 24

  const active = hover ?? null
  const shown = active !== null ? active : null

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="pixel text-[6.5px] leading-[1.8] text-nes-ink3">
          VEHICLES PER HOUR
        </span>
        <span className="text-[10px] tabular-nums text-nes-ink3">
          {shown !== null
            ? `${hourLabel(shown)} · ${Math.round(profile[shown]).toLocaleString()}`
            : `peak ${Math.round(max).toLocaleString()}`}
        </span>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}
           onMouseLeave={() => setHover(null)}>
        {profile.map((v, h) => {
          const bh = Math.max(1, (v / max) * (H - PB - PT))
          const fill = h === calmHour ? '#2fa36b'
            : h === peakHour ? '#d03b3b'
            : '#3d6b8f'
          return (
            <g key={h}>
              <rect
                x={h * bw + 0.8} y={H - PB - bh}
                width={bw - 1.6} height={bh}
                fill={fill}
                opacity={active === null || active === h ? 1 : 0.55}
              />
              {/* Hit target spans the full height — a 9px-wide bar is not a
                  reachable target with a finger. */}
              <rect x={h * bw} y={0} width={bw} height={H} fill="transparent"
                    onMouseEnter={() => setHover(h)}
                    onTouchStart={() => setHover(h)} />
            </g>
          )
        })}

        {/* Current hour: a tick under the axis rather than a fill, so it can
            coincide with the peak or calm bar without hiding either. */}
        {now !== undefined && (
          <rect x={now * bw + bw / 2 - 1} y={H - PB + 1} width="2" height="3"
                fill="#fbd000" />
        )}

        <line x1="0" y1={H - PB} x2={W} y2={H - PB} stroke="#000000" strokeWidth="1" />
        <text x="1" y={H - 3} fill="#898781" fontSize="8.5">12A</text>
        <text x={12 * bw} y={H - 3} fill="#898781" fontSize="8.5" textAnchor="middle">12P</text>
        <text x={W - 1} y={H - 3} fill="#898781" fontSize="8.5" textAnchor="end">11P</text>
      </svg>

      <p className="mt-1 text-[10.5px] leading-snug text-nes-ink2">
        <span style={{ color: '#2fa36b' }}>Calmest {hourLabel(calmHour)}</span>
        {' · '}
        <span style={{ color: '#d03b3b' }}>busiest {hourLabel(peakHour)}</span>
        {now !== undefined && (
          <span className="text-nes-ink3">
            {' · '}now {hourLabel(now)} at {pct(profile[now], max)} of peak
          </span>
        )}
      </p>
    </div>
  )
}

export function hourLabel(h: number): string {
  const ampm = h < 12 ? 'AM' : 'PM'
  return `${h % 12 || 12}${ampm}`
}

function pct(v: number, max: number): string {
  return `${Math.round((v / (max || 1)) * 100)}%`
}
