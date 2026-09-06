/* The API surface the pages read.

   One page per concern means one fetch set per page: /landscape is the only
   route that waits on RealT and CoinGecko, /code the only one that pulls
   40 KB of Solidity. Nothing loads data for a section you didn't open. */

"use client";

import { useCallback, useEffect, useState } from 'react'

export const API_URL = process.env.NEXT_PUBLIC_API_URL || '/openhouse/api'

export async function api(path: string, opts?: { method?: string; body?: any }) {
  const res = await fetch(`${API_URL}/${path}`, {
    method: opts?.method || 'GET',
    headers: opts?.body ? { 'Content-Type': 'application/json' } : undefined,
    body: opts?.body ? JSON.stringify(opts.body) : undefined,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || 'Request failed')
  }
  return res.json()
}

/** Fetch one endpoint, keep the last good value, expose a manual reload.
 *  A failed fetch leaves the fallback in place — pages render the honest
 *  empty state rather than an error screen.
 *
 *  `error` carries WHY the fallback is showing. Most pages ignore it and
 *  render zeros, which is right for a pre-launch counter; a page whose whole
 *  job is to display fetched content (/code) needs to say "the API is down"
 *  instead of spinning on "Loading…" forever. */
export function useResource<T>(path: string, fallback: T) {
  const [data, setData] = useState<T>(fallback)
  const [loading, setLoading] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async (p = path) => {
    setLoading(true)
    try {
      const next = await api(p)
      if (next != null) setData(next as T)
      setError(null)
    } catch (e: any) {
      setError(e?.message || 'Request failed')
    }
    setLoading(false)
    setLoaded(true)
  }, [path])

  useEffect(() => { reload() }, [reload])
  return { data, loading, loaded, error, reload }
}

/* ── formatting ─────────────────────────────────────────── */

export function formatNum(n: number, decimals = 2) {
  if (n == null || isNaN(n)) return '0'
  return n % 1 === 0 ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: decimals })
}

export function timeAgo(ts: number) {
  if (!ts) return '--'
  const diff = Math.floor(Date.now() / 1000) - ts
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

/* ── shapes ─────────────────────────────────────────────── */

export interface StatusData {
  deployed: boolean
  shareholders: number; total_shares: number; shares_sold: number; available_shares: number
  total_contributed: number; total_dividends_distributed: number; dividend_count: number
  contract: string; is_active: boolean
}
export interface Shareholder {
  address: string; shares: number; contribution: number
  ownership_pct: number; dividends_claimed: number; joined: number
}
export interface PropertyData {
  deployed: boolean; description: string; total_shares: number; share_price: string
  available_shares: number; is_active: boolean; status: string; contract: string
}
export interface ModelPreset {
  id: string; name: string; credit_pct: number; option_fee_pct: number
  headline: string; detail: string
}
export interface TermsData {
  model: string; model_name: string; fee_pct: number; credit_pct: number; option_fee_pct: number
  home_price: number; monthly_rent: number; owner: string; updated: number; custom: boolean
  equity_pct_of_rent: number; owner_pct_of_rent: number; to_property_pct: number
  fee_band: { min_pct: number; max_pct: number }
}
export interface RentStats {
  payments: number; renters: number; gross_rent: number; protocol_fees: number
  renter_equity: number; owner_income: number; to_property_pct: number; take_pct: number
  owned_pct: number; home_price: number
}
export interface DividendRecord { timestamp: number; total_amount: number; per_share: number; recipients: number }
export interface SourceFile { name: string; language: string; description: string; lines: number; bytes: number; content: string }

export interface Peer {
  id: string; name: string; chain: string; category: string; category_label: string
  thesis: string; wrapper: string | null; min_ticket: string | null
  occupant_equity: boolean; equity_to: string; take: string | null
  status: string; status_note: string | null; url: string; sources: string[]
  token?: { symbol: string; price_usd: number; market_cap_usd: number; ath_change_pct: number }
  live?: { tokens: number; retired_tokens?: number; min_token_price?: number; median_token_price?: number }
}
export interface CompareData {
  openhouse: Peer
  peers: Peer[]
  headline: { total: number; occupant_side: number; occupant_side_onchain: number; claim: string }
  behind: string[]
  evidence: { claim: string; detail: string; source: string }[]
  fetched: number; cached: boolean
}
