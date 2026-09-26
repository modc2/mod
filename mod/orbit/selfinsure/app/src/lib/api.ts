'use client'

import { useCallback, useEffect, useState } from 'react'

// All data reaches the app through the /selfinsure/api rewrite → the module's
// stdlib API on :50850. No third-party services, no keys, no trackers.
export const API = process.env.NEXT_PUBLIC_API_URL || '/selfinsure/api'
export const DEMO_POOL = process.env.NEXT_PUBLIC_DEMO_POOL || ''

export async function api<T = any>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: 'no-store' })
  const body = await r.json().catch(() => null)
  if (!r.ok) throw new Error(body?.error || `${r.status} on ${path}`)
  return body as T
}

export function useResource<T = any>(path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(Boolean(path))

  const reload = useCallback(() => {
    if (!path) return
    setLoading(true)
    api<T>(path)
      .then((d) => { setData(d); setError(null) })
      .catch((e) => setError(e.message || String(e)))
      .finally(() => setLoading(false))
  }, [path])

  useEffect(() => { reload() }, [reload])
  return { data, error, loading, reload }
}

// ── shapes served by src/api.py (the contract with the backend) ──

export interface Stats {
  pools: number
  open_pools: number
  members: number
  agents: number
  claims: number
  open_claims: number
  accept_rate: number | null
  premiums_in: number
  paid_in_claims: number
  returned_to_members: number
  operator_fees: number
  held_in_pools: number
  operator_share: number
  note?: string
}

export interface PoolRow {
  id: string
  name?: string
  state?: string
  unit?: string
  members?: number
  balance?: number
  [k: string]: any
}

export interface PoolsResponse {
  count: number
  pools: PoolRow[]
  note?: string
}

export interface ContractDescribe {
  contracts: Record<string, string>
  guarantees: string[]
  presets: Record<string, string>
  built: boolean
  compiler?: string
  source_files: string[]
  calls: Record<string, Record<string, string>>
}

export interface OnchainPool {
  address: string
  network: string
  name: string
  asset: string
  symbol: string
  decimals: number
  owner: string
  oracle: string
  oracle_mode: string
  money: Record<string, string | number>
  provider: {
    fee_bps_now: number
    fee_pct_now: number
    fee_cap_bps: number
    profit_accrued: string
    profit_withdrawn: string
    profit_share_of_premium: number
    member_share_of_premium: number
    pending_fee_bps: number | null
    pending_fee_at: string | null
    notice: string
  }
  solvency: { reconciles: boolean; solvent: boolean; verdict: string }
  terms: Record<string, string | number | boolean>
  counts: { members: number; agents: number; claims: number }
}

export interface Preset {
  preset: string
  title: string
  about: string
  terms_human: Record<string, number | boolean>
  decimals: number
  terms: (number | boolean)[]
  why: Record<string, string>
}

// ── formatting ──

export function fmt(n: number | string | null | undefined, dp = 0): string {
  if (n === null || n === undefined || n === '') return '—'
  const v = typeof n === 'string' ? Number(n) : n
  if (!Number.isFinite(v)) return String(n)
  return v.toLocaleString('en-US', { maximumFractionDigits: dp, minimumFractionDigits: 0 })
}

export function pct(v: number | null | undefined, dp = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(dp)}%`
}

export function short(addr: string | null | undefined): string {
  if (!addr) return '—'
  return addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr
}
