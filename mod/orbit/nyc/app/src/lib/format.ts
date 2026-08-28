/** Number formatting for labels, legends and the inspector. */

export function usd(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  if (Math.abs(v) >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(v < 10_000_000 ? 2 : 1)}M`
  if (Math.abs(v) >= 1_000) return `$${Math.round(v / 1_000)}K`
  return `$${Math.round(v)}`
}

export function usdExact(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `$${Math.round(v).toLocaleString('en-US')}`
}

export function count(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (Math.abs(v) >= 10_000) return `${Math.round(v / 1_000)}K`
  return Math.round(v).toLocaleString('en-US')
}

export function percent(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

/** Format by the metric's declared format, for legends and tooltips. */
export function byFormat(v: number | null | undefined, fmt: string): string {
  switch (fmt) {
    case 'usd':
      return usd(v)
    case 'usd_compact':
      return usd(v)
    case 'percent':
      return percent(v)
    case 'count':
      return count(v)
    default:
      return v === null || v === undefined ? '—' : String(v)
  }
}

export function titleCase(s: string): string {
  return s.replace(/\w\S*/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase())
}

/** ISO date `n` years before today, for the default time window. */
export function yearsAgo(n: number): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - n)
  return d.toISOString().slice(0, 10)
}
