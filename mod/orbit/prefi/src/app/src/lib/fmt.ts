/**
 * Number formatting shared by every panel. Prices here span eight orders of
 * magnitude — BTC and a sub-cent memecoin are both listable — so precision is
 * picked per value, and "$0.00" is never a price.
 */

export const fmt = (n: number, d = 2) =>
  n == null || Number.isNaN(n) ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })

export const fmtUsd = (n: number, d = 2) => (n == null ? '—' : `$${fmt(n, d)}`)
export const usd = fmtUsd

const pxDigits = (n: number) => (n === 0 ? 2 : n < 0.01 ? 6 : n < 1 ? 4 : 2)

export const fmtPx = (n: number) => (n == null || Number.isNaN(n) ? '—' : fmtUsd(n, pxDigits(n)))
export const px = fmtPx

/** Subnet alpha is quoted in TAO by the chain itself — a τ, not a $. */
export const fmtTao = (n: number) => (n == null || Number.isNaN(n) ? '—' : `τ${fmt(n, pxDigits(n))}`)

export const fmtQuoted = (n: number, quote?: string) => (quote === 'TAO' ? fmtTao(n) : fmtPx(n))
export const pxq = fmtQuoted

export const pct = (n: number, d = 1) => (n == null ? '—' : `${(n * 100).toFixed(d)}%`)
export const pctClass = (n: number) => (n >= 0 ? 'up' : 'down')
export const pctSign = (n: number) => (n >= 0 ? '+' : '')

export const short = (a?: string) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—')

export function fmtVol(n: number) {
  if (!n) return '—'
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}b`
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}m`
  if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}k`
  return `$${n.toFixed(0)}`
}

export function fmtTaoVol(n: number) {
  if (!n) return '—'
  if (n >= 1e6) return `τ${(n / 1e6).toFixed(1)}m`
  if (n >= 1e3) return `τ${(n / 1e3).toFixed(1)}k`
  return `τ${n.toFixed(0)}`
}

export function countdown(seconds: number) {
  if (seconds == null) return '—'
  if (seconds <= 0) return 'now'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m ${Math.floor(seconds % 60)}s`
}

/** Compact "3d" / "5h" / "12m". */
export const countdownShort = (s: number) =>
  s == null ? '—' : s <= 0 ? 'now' : s >= 86400 ? `${Math.ceil(s / 86400)}d` : s >= 3600 ? `${Math.ceil(s / 3600)}h` : `${Math.ceil(s / 60)}m`

/** How a market is displayed: BTC-PERP, HYPE/USDC, SN64 · Chutes, WETH/USDC. */
export function pairLabel(m: any) {
  if (!m) return ''
  if (m.source === 'bittensor') return m.bt_name ? `${m.symbol} · ${m.bt_name}` : m.symbol
  if (m.source === 'dex') return `${m.symbol} · ${m.chain === 'solana' ? 'Solana' : 'Base'}`
  if (m.source !== 'hyperliquid') return `${m.symbol}/USDC`
  return m.hl_kind === 'spot' || m.symbol?.includes('/') ? m.symbol : `${m.symbol}-PERP`
}

/** Where a market's price comes from, as a one-liner. */
export function sourceLabel(m: any) {
  if (m.source === 'hyperliquid')
    return `Hyperliquid ${m.hl_kind === 'spot' ? 'spot' : 'perp'}${m.hl_key && m.hl_key !== m.symbol ? ` · ${m.hl_key}` : ''}`
  if (m.source === 'bittensor') return `Bittensor subnet ${m.bt_netuid} · alpha in TAO`
  if (m.source === 'dex') return `${m.dex || 'DEX'} pool on ${m.chain === 'solana' ? 'Solana' : 'Base'}${m.liquidity_usd ? ` · ${fmtVol(m.liquidity_usd)} liquidity` : ''}`
  return `Uniswap V3 · ${((m.fee_tier || 0) / 10000).toFixed(1)}% fee`
}

/** A stable hue per symbol so avatars are distinguishable at a glance. */
export function hue(s: string) {
  let h = 0
  for (let i = 0; i < (s || '').length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return h % 360
}
