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

/* ── walkthroughs + the bank rail ───────────────────────── */

export interface ExampleMeta { name: string; title: string; shows: string; level: string }

export interface ExampleStep {
  n: number; tool: string; args: Record<string, any>; note: string
  result?: any; error?: string
}

export interface ExampleRun extends ExampleMeta {
  ok: boolean; error: string; summary: string; store: string; replay: string
  steps: ExampleStep[]; checks: { claim: string; ok: boolean }[]
}

export interface BankKind {
  kind: string; label: string; live: boolean; caps: string[]; doc: string
  fields: { name: string; required: boolean; secret: boolean; help: string }[]
}

/* ── proofs: proof of rent paid, without the tenant ─────────── */

export interface ProofChainInfo { configured?: boolean; paid?: boolean; error?: string }
export interface ProofReceipt {
  kind: string; version: number; program_hash: string; outputs: string[]
  property_id: number; period: number; rent_due: number
  commitment: string; nullifier: string; fact: string
  bank: { name: string; near: string; near_hex: string; eth: string; pq?: ProofPQKeys }
  sig: { near: string; eth: string; pq?: { ohx?: string; mldsa65?: string } }
  pq_upgraded?: number
  issued: number; note?: string
  source?: { bank?: string; txn?: string; booked_at_bank?: number; matched_by?: string }
  chain: Record<string, { ok?: boolean; hash?: string; explorer?: string; contract?: string; why?: string; at?: number }>
  chains?: Record<string, ProofChainInfo>
}
export interface ProofVerdict {
  ok: boolean; checks: { claim: string; ok: boolean | null; pq?: boolean; ignored?: string }[]
  fact?: string; nullifier?: string
  kind?: string; known_here?: boolean; error?: string
  policy?: 'classical' | 'hybrid' | 'pq'; quantum?: { safe: boolean; says: string }
}
export interface ProofPQKeys {
  ohx: { scheme: string; params: string; id: string; pub_seed: string; root: string }
  mldsa65?: { scheme: string; pk: string; fingerprint: string; ctx: string }
}
export interface ProofPQ {
  schemes: Record<string, { name: string; kind: string; sig_bytes?: number; available?: boolean; verified_on: string[] }>
  keys: ProofPQKeys
  leaves: { capacity: number; used: number; left: number; retired_keys: number; warning: string }
  retired: { id: string; used: number; retired: number }[]
  receipts: { total: number; pq: number; classical_only: string[] }
  threats: { part: string; quantum: 'safe' | 'breakable'; why: string }[]
  switch: string
}
export interface ProofStatus {
  program: { program_hash: string | null; built: boolean; scarb: boolean; path: string; version: number
    public_outputs: string[]; private_inputs: string[]; fact: string; source_sha256?: string; error?: string }
  issuer: { name: string; near: string; near_hex: string; eth: string } | null
  receipts: number; submitted: { near: number; eth: number }
  chains: { near: { network: string; account: string; explorer: string }
            eth: { network: string; address: string; url: string; chainId: string | number; explorer: string } }
  property_id: number; rent_due: number; unit: string; kinds: Record<string, string>
}

/* ── the simulation: years of the deal, played through the real code ── */

export interface SimParams {
  years: number; home_price: number; monthly_rent: number; model: string
  fee_pct: number; credit_pct: number | null; option_fee_pct: number | null
  renters: number; apy: number; rate: number; currency: string; bank: boolean
  skip_every: number; proofs: number; seed: string; months: number; start: number
}
export interface SimMonth {
  m: number; t: number; period: number
  paid: number; fees: number; equity: number; owner_income: number; credit: number
  owned_pct: number; remaining: number; yield: number
  paid_by: string[]; skipped: string[]
}
export interface SimQuarter {
  quarter: number; month: number; pool: number; total_weight_days: number
  allocations: { who: string; address: string; kinds: string[]; share_pct: number; amount: number }[]
}
export interface SimEvent { month: number; kind: 'option' | 'skipped' | 'quarter' | 'owned'; says: string; who?: string; amount?: number; quarter?: number }
export interface SimResult {
  ok: boolean
  params: SimParams
  terms: { model: string; model_name: string; fee_pct: number; credit_pct: number; option_fee_pct: number
           home_price: number; monthly_rent: number; equity_pct_of_rent: number; owner_pct_of_rent: number; to_property_pct: number }
  cast: { who: string; name: string; address: string; reference?: string | null }[]
  months: SimMonth[]
  quarters: SimQuarter[]
  events: SimEvent[]
  end: {
    months_run: number; owned: boolean; owned_month: number | null; owned_pct: number
    gross_paid: number; fees: number; equity: number; owner_income: number; yield_modeled: number
    to_property_pct: number; quarters_closed: number; fees_distributed: number; pool_accruing: number
    proofs_issued: number; payments: number
    renters: { who: string; name: string; address: string; payments: number; rent_paid: number; principal: number; equity_pct: number; fees_paid: number }[]
  }
  versus: { note: string; plain_lease: SimVersus; platform_15: SimVersus; openhouse: SimVersus }
  bank: { transfers: number; booked: number; booked_units: number; currency: string; rate: number; balance: number } | null
  modeled: string[]; played: string[]; store: string; elapsed_ms: number
}
export interface SimVersus { paid: number; equity: number; platform_take: number }
export interface SimPreset {
  id: string; title: string; shows: string; years: number; model: string; fee_pct: number
  renters: number; apy: number; skip_every?: number
}
