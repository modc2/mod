/** Number formatting for labels, legends and the inspector. */

export function count(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (Math.abs(v) >= 10_000) return `${Math.round(v / 1_000)}K`
  return Math.round(v).toLocaleString('en-CA')
}

export function percent(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

/** A per-km² / per-month rate: one decimal below 10, whole numbers above. */
export function rate(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Math.abs(v) < 10 ? v.toFixed(1) : Math.round(v).toLocaleString('en-CA')
}

/** Format by the metric's declared format, for legends and tooltips. */
export function byFormat(v: number | null | undefined, fmt: string): string {
  switch (fmt) {
    case 'percent':
      return percent(v)
    case 'rate':
      return rate(v)
    case 'count':
      return count(v)
    default:
      return v === null || v === undefined ? '—' : String(v)
  }
}

export function titleCase(s: string): string {
  return s.replace(/\w\S*/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase())
}
