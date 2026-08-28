"use client";

import { useState, useEffect, useCallback, useRef, useMemo, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { toast } from 'react-toastify'
import {
  MANIFESTO, ABSTRACT, SECTIONS, TOKENOMICS, ROADMAP, TICKER, LAUNCH, BENCHMARKS,
} from '../lib/whitepaper'

const API_URL = process.env.NEXT_PUBLIC_API_URL || '/openhouse/api'

interface StatusData {
  deployed: boolean
  shareholders: number; total_shares: number; shares_sold: number; available_shares: number
  total_contributed: number; total_dividends_distributed: number; dividend_count: number
  contract: string; is_active: boolean
}
interface Shareholder {
  address: string; shares: number; contribution: number
  ownership_pct: number; dividends_claimed: number; joined: number
}
interface PropertyData {
  deployed: boolean; description: string; total_shares: number; share_price: string
  available_shares: number; is_active: boolean; status: string; contract: string
}
interface ModelPreset {
  id: string; name: string; credit_pct: number; option_fee_pct: number
  headline: string; detail: string
}
interface TermsData {
  model: string; model_name: string; fee_pct: number; credit_pct: number; option_fee_pct: number
  home_price: number; monthly_rent: number; owner: string; updated: number; custom: boolean
  equity_pct_of_rent: number; owner_pct_of_rent: number; to_property_pct: number
  fee_band: { min_pct: number; max_pct: number }
}
interface RentStats {
  payments: number; renters: number; gross_rent: number; protocol_fees: number
  renter_equity: number; owner_income: number; to_property_pct: number; take_pct: number
  owned_pct: number; home_price: number
}
interface DividendRecord { timestamp: number; total_amount: number; per_share: number; recipients: number }
interface SourceFile { name: string; language: string; description: string; lines: number; bytes: number; content: string }

async function api(path: string, opts?: { method?: string; body?: any }) {
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

function formatNum(n: number, decimals = 2) {
  if (n == null || isNaN(n)) return '0'
  return n % 1 === 0 ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: decimals })
}
function timeAgo(ts: number) {
  if (!ts) return '--'
  const diff = Math.floor(Date.now() / 1000) - ts
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

/* ── Scroll reveal ───────────────────────────────────────── */
function Reveal({ children, delay = 0, className = '' }: { children: ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ob = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); ob.disconnect() } }, { threshold: 0.12 })
    ob.observe(el)
    return () => ob.disconnect()
  }, [])
  return <div ref={ref} className={`reveal ${seen ? 'in' : ''} ${className}`} style={{ transitionDelay: `${delay}ms` }}>{children}</div>
}

/* ── Count-up (re-animates when async data lands) ────────── */
function Counter({ value, decimals = 0, suffix = '' }: { value: number; decimals?: number; suffix?: string }) {
  const [n, setN] = useState(0)
  const [vis, setVis] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ob = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setVis(true); ob.disconnect() } }, { threshold: 0.4 })
    ob.observe(el)
    return () => ob.disconnect()
  }, [])
  useEffect(() => {
    if (!vis) return
    const dur = 1500, t0 = performance.now()
    let raf = 0
    const tick = (t: number) => {
      const p = Math.min((t - t0) / dur, 1)
      setN(value * (1 - Math.pow(1 - p, 3)))
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [vis, value])
  return <span ref={ref}>{formatNum(n, decimals)}{suffix}</span>
}

/* ── 3D tilt wrapper (vanilla) ───────────────────────────── */
function Tilt({ children, className = '', max = 6 }: { children: ReactNode; className?: string; max?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const onMove = (e: React.MouseEvent) => {
    const el = ref.current; if (!el) return
    const r = el.getBoundingClientRect()
    const px = (e.clientX - r.left) / r.width - 0.5
    const py = (e.clientY - r.top) / r.height - 0.5
    el.style.transform = `perspective(900px) rotateX(${(-py * max).toFixed(2)}deg) rotateY(${(px * max).toFixed(2)}deg)`
  }
  const reset = () => { if (ref.current) ref.current.style.transform = 'perspective(900px) rotateX(0deg) rotateY(0deg)' }
  return <div ref={ref} onMouseMove={onMove} onMouseLeave={reset} className={`tilt ${className}`}>{children}</div>
}

/* ── Procedural NYC skyline (deterministic) ──────────────── */
function Skyline({ shift }: { shift: number }) {
  const buildings = useMemo(() => {
    let s = 1337
    const rnd = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff }
    return Array.from({ length: 26 }, (_, i) => {
      const w = 26 + Math.floor(rnd() * 44)
      const h = 70 + Math.floor(rnd() * 270) + (i > 9 && i < 15 ? 90 : 0) // a midtown cluster
      const cols = Math.max(2, Math.floor(w / 11))
      const rows = Math.max(3, Math.floor(h / 20))
      const wins = Array.from({ length: rows * cols }, () => ({ lit: rnd() > 0.34, d: (rnd() * 5).toFixed(1) }))
      const spire = rnd() > 0.6
      return { w, h, cols, wins, spire }
    })
  }, [])
  return (
    <div className="skyline" style={{ transform: `translateY(${shift}px)` }}>
      {buildings.map((b, i) => (
        <div key={i} className="bldg" style={{ width: b.w, height: b.h, gridTemplateColumns: `repeat(${b.cols}, 4px)` }}>
          {b.spire && <span className="absolute -top-5 left-1/2 -translate-x-1/2 w-px h-5 bg-coral/40" />}
          {b.wins.map((win, j) => (
            <span key={j} className={`win ${win.lit ? '' : 'dim'}`} style={win.lit ? { animationDelay: `${win.d}s` } : undefined} />
          ))}
        </div>
      ))}
    </div>
  )
}

/* ── Editor-style code viewer ────────────────────────────── */
function CodeViewer({ files }: { files: SourceFile[] }) {
  const [active, setActive] = useState(0)
  if (!files.length) {
    return <div className="glass rounded-3xl py-20 text-center text-white/55 text-sm uppercase tracking-widest">Loading source…</div>
  }
  const f = files[Math.min(active, files.length - 1)]
  const lines = f.content.replace(/\n$/, '').split('\n')
  const langLabel: Record<string, string> = { solidity: 'Solidity', python: 'Python', typescript: 'TypeScript' }
  return (
    <div className="rounded-2xl overflow-hidden border border-white/10 bg-paper shadow-2xl shadow-black/50">
      {/* tab strip */}
      <div className="flex items-stretch overflow-x-auto border-b border-white/[0.07] bg-white/[0.02]">
        {files.map((file, i) => (
          <button key={file.name} onClick={() => setActive(i)}
            className={`px-4 py-3 text-xs font-mono whitespace-nowrap border-r border-white/[0.05] transition-colors ${i === active ? 'bg-white/[0.06] text-coral' : 'text-white/60 hover:text-white/80'}`}>
            {file.name.split('/').pop()}
          </button>
        ))}
      </div>
      {/* chrome bar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.05] bg-white/[0.015]">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-peach" /><span className="w-2.5 h-2.5 rounded-full bg-sun" /><span className="w-2.5 h-2.5 rounded-full bg-emerald-300" />
          <span className="ml-3 text-[11px] font-mono text-white/65">{f.name}</span>
          <span className="text-[10px] uppercase tracking-widest text-coral border border-coral/25 rounded px-1.5 py-0.5">{langLabel[f.language] || f.language}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-white/55 hidden sm:inline">{f.lines} lines · {(f.bytes / 1024).toFixed(1)} KB</span>
          <button onClick={() => { navigator.clipboard.writeText(f.content); toast.success(`${f.name} copied`) }}
            className="text-[10px] font-bold uppercase tracking-widest text-white/60 hover:text-coral transition-colors">Copy</button>
        </div>
      </div>
      <p className="px-4 py-2.5 text-xs text-white/60 border-b border-white/[0.05] bg-white/[0.01]">{f.description}</p>
      {/* code body */}
      <div className="overflow-auto max-h-[34rem] text-[12.5px] leading-[1.55] font-mono">
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((ln, i) => (
              <tr key={i} className="hover:bg-white/[0.025]">
                <td className="select-none text-right pr-4 pl-4 text-white/45 w-12 align-top tabular-nums sticky left-0 bg-paper">{i + 1}</td>
                <td className="pr-6 text-white/85 whitespace-pre align-top">{ln || ' '}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ── One payment, cut three ways ─────────────────────────── */
function SplitBar({ amount, fee, credit, owner, className = '', big = false }: {
  amount: number; fee: number; credit: number; owner: number; className?: string; big?: boolean
}) {
  const pct = (n: number) => (amount > 0 ? (n / amount) * 100 : 0)
  const legs = [
    { k: 'Your equity', v: credit, c: 'linear-gradient(90deg,var(--peach),var(--coral))', t: 'text-coral' },
    { k: 'Owner income', v: owner, c: 'linear-gradient(90deg,var(--mint),rgb(var(--em-600-rgb)))', t: 'text-emerald-400' },
    { k: 'Protocol fee', v: fee, c: 'linear-gradient(90deg,var(--pink),var(--pink-deep))', t: 'text-pink' },
  ]
  return (
    <div className={className}>
      <div className={`flex rounded-full overflow-hidden bg-white/[0.06] ${big ? 'h-6' : 'h-4'}`}>
        {legs.map(l => (
          <div key={l.k} className="transition-all duration-500" title={`${l.k}: ${pct(l.v).toFixed(1)}%`}
            style={{ width: `${pct(l.v)}%`, background: l.c }} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 mt-2.5">
        {legs.map(l => (
          <div key={l.k} className="flex items-baseline gap-2">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: l.c }} />
            <span className={`text-[11px] font-bold tabular-nums ${l.t}`}>{pct(l.v).toFixed(1)}%</span>
            <span className="text-[10px] uppercase tracking-widest text-white/58">{l.k}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── What everyone else takes off the top ────────────────── */
function TakeComparison({ ourTake }: { ourTake: number }) {
  const rows = BENCHMARKS.map(b => (b.ours ? { ...b, take: ourTake } : b))
  const max = Math.max(...rows.map(r => r.take), 1)
  return (
    <div className="space-y-3">
      {rows.map(r => (
        <div key={r.name} className="grid grid-cols-[8.5rem_1fr_3.5rem] items-center gap-3">
          <span className={`text-xs truncate ${r.ours ? 'text-coral font-bold' : 'text-white/68'}`} title={r.note}>{r.name}</span>
          <div className="h-2.5 rounded-full bg-white/[0.05] overflow-hidden">
            <div className="h-full rounded-full transition-all duration-700"
              style={{
                width: `${Math.max((r.take / max) * 100, 1.5)}%`,
                background: r.ours ? 'linear-gradient(90deg,var(--peach),var(--coral))' : 'rgb(var(--pink-rgb) / 0.5)',
              }} />
          </div>
          <span className={`text-xs font-bold tabular-nums text-right ${r.ours ? 'text-coral' : 'text-white/60'}`}>
            {r.take % 1 === 0 ? r.take : r.take.toFixed(1)}%
          </span>
        </div>
      ))}
      <p className="text-[10px] text-white/50 leading-relaxed pt-1">
        Published headline rates for comparison. Only the OpenHouse number is enforced by this contract —
        and only inside the 1–5% band written into it.
      </p>
    </div>
  )
}

/* ── The landscape — every other on-chain housing project ── */

interface Peer {
  id: string; name: string; chain: string; category: string; category_label: string
  thesis: string; wrapper: string | null; min_ticket: string | null
  occupant_equity: boolean; equity_to: string; take: string | null
  status: string; status_note: string | null; url: string; sources: string[]
  token?: { symbol: string; price_usd: number; market_cap_usd: number; ath_change_pct: number }
  live?: { tokens: number; retired_tokens?: number; min_token_price?: number; median_token_price?: number }
}
interface CompareData {
  openhouse: Peer
  peers: Peer[]
  headline: { total: number; occupant_side: number; occupant_side_onchain: number; claim: string }
  behind: string[]
  evidence: { claim: string; detail: string; source: string }[]
  fetched: number; cached: boolean
}

const STATUS_STYLE: Record<string, string> = {
  live: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10',
  testnet: 'text-coral border-coral/30 bg-coral/10',
  liquidating: 'text-pink border-pink/30 bg-pink/10',
  acquired: 'text-pink border-pink/30 bg-pink/10',
  quiet: 'text-white/60 border-white/15 bg-white/[0.03]',
}

function PeerCard({ p, ours = false }: { p: Peer; ours?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`rounded-2xl border p-5 transition-colors ${ours
      ? 'border-coral/40 bg-gradient-to-br from-coral/[0.09] to-transparent'
      : 'border-white/[0.07] bg-white/[0.02] hover:border-white/15'}`}>
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h4 className={`font-display font-extrabold text-lg truncate ${ours ? 'text-coral' : 'text-white'}`}>{p.name}</h4>
            {p.occupant_equity && (
              <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.14em] text-emerald-400" title="The person living there accrues ownership">⌂ resident</span>
            )}
          </div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-white/55 font-bold mt-1">{p.chain} · {p.category_label}</div>
        </div>
        <span className={`shrink-0 text-[9px] font-bold uppercase tracking-widest px-2 py-1 rounded-full border ${STATUS_STYLE[p.status] || STATUS_STYLE.quiet}`}>{p.status}</span>
      </div>

      <p className="text-white/72 text-[13px] leading-relaxed">{p.thesis}</p>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 mt-4 text-[11px]">
        {[
          { k: 'Equity goes to', v: p.equity_to },
          { k: 'Minimum', v: p.min_ticket },
          { k: 'Wrapper', v: p.wrapper },
          { k: 'The take', v: p.take },
        ].map(f => (
          <div key={f.k}>
            <dt className="text-white/50 uppercase tracking-[0.14em] font-bold text-[9px]">{f.k}</dt>
            <dd className="text-white/75 leading-snug mt-0.5">{f.v || '—'}</dd>
          </div>
        ))}
      </dl>

      {/* Live rails: only the projects that publish openly have anything here. */}
      {(p.token || p.live) && (
        <div className="flex flex-wrap gap-2 mt-4">
          {p.live?.tokens != null && (
            <span className="text-[10px] px-2 py-1 rounded-md bg-white/[0.04] border border-white/[0.07] text-white/68 tabular-nums">
              {formatNum(p.live.tokens)} property tokens
              {p.live.median_token_price != null && ` · $${p.live.median_token_price} median`}
            </span>
          )}
          {p.token && (
            <span className="text-[10px] px-2 py-1 rounded-md bg-white/[0.04] border border-white/[0.07] text-white/68 tabular-nums">
              {p.token.symbol} ${p.token.price_usd < 0.01 ? p.token.price_usd.toFixed(6) : p.token.price_usd.toFixed(4)}
              <span className={p.token.ath_change_pct < -80 ? 'text-pink/70' : 'text-white/58'}> · {p.token.ath_change_pct.toFixed(0)}% from ATH</span>
            </span>
          )}
        </div>
      )}

      {p.status_note && (
        <>
          <button onClick={() => setOpen(o => !o)}
            className="mt-4 text-[10px] font-bold uppercase tracking-widest text-white/58 hover:text-coral transition-colors">
            {open ? '− What happened' : '+ What happened'}
          </button>
          {open && (
            <div className="mt-3 pt-3 border-t border-white/[0.07]">
              <p className="text-white/68 text-[12px] leading-relaxed">{p.status_note}</p>
              {p.sources.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-3">
                  {p.sources.map((s, i) => (
                    <a key={s} href={s} target="_blank" rel="noopener noreferrer"
                      className="text-[10px] px-2 py-0.5 rounded border border-white/10 text-white/58 hover:text-coral hover:border-coral/30 transition-colors">
                      source {i + 1} ↗
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Landscape({ data, loading, onRefresh }: {
  data: CompareData | null; loading: boolean; onRefresh: () => void
}) {
  const [onlyResident, setOnlyResident] = useState(false)

  if (!data) {
    return <p className="text-white/55 text-sm">{loading ? 'Reading the landscape…' : 'Comparison unavailable — the API is not responding.'}</p>
  }

  // Us first, then the field: resident-side projects lead, then by category.
  const shown = data.peers.filter(p => !onlyResident || p.occupant_equity)
  const order = ['occupant-equity', 'offchain-rto', 'investor-fractional', 'homeowner-liquidity', 'title', 'infrastructure', 'synthetic']
  const sorted = [...shown].sort((a, b) => order.indexOf(a.category) - order.indexOf(b.category))

  return (
    <>
      <Reveal>
        <div className="glass rounded-3xl p-7 md:p-9 mb-8">
          <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mb-4">The finding</div>
          <p className="font-serif-ed text-3xl md:text-4xl text-white leading-tight">
            {data.headline.occupant_side} of {data.headline.total} comparable projects give the resident equity —
            <span className="text-surf-grad"> and none of those are on-chain.</span>
          </p>
          <p className="text-white/65 text-sm leading-relaxed mt-5 max-w-3xl">
            Everyone else tokenized the landlord. Investor-side platforms sell slices of a rental to
            people who will never live in it, and the tenant is the yield. The two projects that do
            credit the resident — Divvy and Landis — hold that credit on a company balance sheet.
            OpenHouse is the same promise, written into a contract instead.
          </p>
        </div>
      </Reveal>

      <Reveal delay={60}>
        <div className="flex items-center justify-between flex-wrap gap-3 mb-6">
          <div className="flex items-center gap-2">
            <button onClick={() => setOnlyResident(o => !o)}
              className={`px-3 py-1.5 rounded-full border text-[10px] font-bold uppercase tracking-widest transition-colors ${onlyResident
                ? 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
                : 'border-white/12 text-white/65 hover:text-white hover:border-white/30'}`}>
              ⌂ Resident gets equity {onlyResident ? '· on' : '· off'}
            </button>
            <span className="text-[10px] text-white/50 tabular-nums">{sorted.length} of {data.peers.length} peers</span>
          </div>
          <div className="flex items-center gap-3">
            {data.fetched > 0 && (
              <span className="text-[10px] text-white/50">
                live data {timeAgo(data.fetched)}{data.cached ? ' · cached' : ''}
              </span>
            )}
            <button onClick={onRefresh} disabled={loading}
              className="px-4 py-2 rounded-full border border-white/12 text-[11px] font-bold uppercase tracking-widest text-white/68 hover:text-white hover:border-white/30 disabled:opacity-30 transition-colors">
              {loading ? 'Syncing…' : '↻ Refresh'}
            </button>
          </div>
        </div>
      </Reveal>

      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        <Reveal delay={100}><PeerCard p={data.openhouse} ours /></Reveal>
        {sorted.map((p, i) => (
          <Reveal key={p.id} delay={140 + i * 40}><PeerCard p={p} /></Reveal>
        ))}
      </div>

      {/* The receipts for the model — and the receipts against us. */}
      <div className="grid lg:grid-cols-2 gap-6 mt-10">
        <Reveal>
          <div className="glass rounded-3xl p-7 h-full">
            <div className="text-pink text-[11px] font-bold uppercase tracking-[0.25em] mb-5">What the field has already proved</div>
            <div className="space-y-6">
              {data.evidence.map(e => (
                <div key={e.claim}>
                  <p className="text-white font-display font-bold text-[15px] leading-snug">{e.claim}</p>
                  <p className="text-white/65 text-[13px] leading-relaxed mt-2">{e.detail}</p>
                  <a href={e.source} target="_blank" rel="noopener noreferrer"
                    className="inline-block mt-2 text-[10px] font-bold uppercase tracking-widest text-white/55 hover:text-coral transition-colors">source ↗</a>
                </div>
              ))}
            </div>
          </div>
        </Reveal>
        <Reveal delay={80}>
          <div className="glass rounded-3xl p-7 h-full">
            <div className="text-white/68 text-[11px] font-bold uppercase tracking-[0.25em] mb-2">Where they're ahead of us</div>
            <p className="text-white/58 text-xs leading-relaxed mb-5">
              A better model on testnet loses to a worse model with a deed. Here's the honest gap.
            </p>
            <ul className="space-y-3">
              {data.behind.map(b => (
                <li key={b} className="flex gap-3 text-[13px] text-white/72 leading-relaxed">
                  <span className="text-pink/60 shrink-0 mt-0.5">✕</span>{b}
                </li>
              ))}
            </ul>
          </div>
        </Reveal>
      </div>
    </>
  )
}

/* ── Owner's terms desk — the dial, live ─────────────────── */
function TermsDesk({ terms, models, onSaved }: {
  terms: TermsData | null; models: ModelPreset[]; onSaved: () => void
}) {
  const band = terms?.fee_band ?? { min_pct: 1, max_pct: 5 }
  const [model, setModel] = useState('full_credit')
  const [feePct, setFeePct] = useState(2.5)
  const [creditPct, setCreditPct] = useState(100)
  const [rent, setRent] = useState('')
  const [price, setPrice] = useState('')
  const [ownerAddr, setOwnerAddr] = useState('')
  const [saving, setSaving] = useState(false)
  const loaded = useRef(false)

  // Seed the desk from the live deal once it lands, then leave the owner alone.
  useEffect(() => {
    if (!terms || loaded.current) return
    loaded.current = true
    setModel(terms.model); setFeePct(terms.fee_pct); setCreditPct(terms.credit_pct)
    setRent(terms.monthly_rent ? String(terms.monthly_rent) : '')
    setPrice(terms.home_price ? String(terms.home_price) : '')
    setOwnerAddr(terms.owner || '')
  }, [terms])

  const pickModel = (m: ModelPreset) => { setModel(m.id); setCreditPct(m.credit_pct) }

  const monthly = parseFloat(rent) || 1
  const fee = monthly * (feePct / 100)
  const credit = (monthly - fee) * (creditPct / 100)
  const ownerCut = monthly - fee - credit
  const preset = models.find(m => m.id === model)
  const isCustom = !!preset && Math.abs(preset.credit_pct - creditPct) > 1e-9

  const save = async () => {
    setSaving(true)
    try {
      const body: any = { model, fee_pct: feePct, credit_pct: creditPct }
      if (rent.trim()) body.monthly_rent = parseFloat(rent)
      if (price.trim()) body.home_price = parseFloat(price)
      if (ownerAddr.trim()) body.owner = ownerAddr.trim()
      await api('terms', { method: 'POST', body })
      toast.success(`Terms live — protocol takes ${feePct}%, ${(100 - feePct).toFixed(1)}% stays with the home`)
      onSaved()
    } catch (err: any) {
      toast.error(err?.message || 'Could not set terms')
    }
    setSaving(false)
  }

  return (
    <div className="grid lg:grid-cols-2 gap-6">
      {/* ── The dial ── */}
      <div className="glass rounded-3xl p-6 md:p-8 border-coral/15">
        <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-coral mb-2">Owner console</div>
        <h3 className="font-serif-ed text-2xl text-white mb-6">Set the deal.</h3>

        <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-2 block">Rent-to-own model</label>
        <div className="grid sm:grid-cols-2 gap-2.5 mb-7">
          {models.map(m => (
            <button key={m.id} onClick={() => pickModel(m)}
              className={`text-left rounded-2xl border p-3.5 transition-colors ${model === m.id ? 'border-coral/50 bg-coral/[0.07]' : 'border-white/[0.08] bg-white/[0.02] hover:border-white/20'}`}>
              <div className="flex items-baseline justify-between gap-2">
                <span className={`text-sm font-bold ${model === m.id ? 'text-coral' : 'text-white/80'}`}>{m.name}</span>
                <span className="text-[11px] font-mono text-white/60 tabular-nums">{m.credit_pct}%</span>
              </div>
              <p className="text-[11px] text-white/60 mt-1 leading-snug">{m.headline}</p>
            </button>
          ))}
        </div>

        {/* Protocol fee — bounded by the contract, not by us */}
        <div className="flex items-baseline justify-between mb-1.5">
          <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold">Protocol fee</label>
          <span className="text-pink font-bold tabular-nums text-sm">{feePct.toFixed(1)}%</span>
        </div>
        <input type="range" min={band.min_pct} max={band.max_pct} step={0.1} value={feePct}
          onChange={e => setFeePct(parseFloat(e.target.value))}
          className="w-full accent-pink cursor-pointer" />
        <div className="flex justify-between text-[10px] text-white/50 font-mono mb-6">
          <span>{band.min_pct}% floor</span>
          <span className="text-white/60">Airbnb takes ~15%</span>
          <span>{band.max_pct}% ceiling — hard-capped in the contract</span>
        </div>

        {/* Rent credit — the model dial */}
        <div className="flex items-baseline justify-between mb-1.5">
          <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold">Rent credit → equity</label>
          <span className="text-coral font-bold tabular-nums text-sm">
            {creditPct.toFixed(0)}%{isCustom && <span className="text-white/55 font-normal"> · custom</span>}
          </span>
        </div>
        <input type="range" min={0} max={100} step={1} value={creditPct}
          onChange={e => setCreditPct(parseFloat(e.target.value))}
          className="w-full accent-coral cursor-pointer" />
        <div className="flex justify-between text-[10px] text-white/50 font-mono mb-6">
          <span>0% — plain lease</span>
          <span>100% — every net dollar buys the house</span>
        </div>

        <div className="grid sm:grid-cols-2 gap-3 mb-5">
          <div>
            <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Monthly payment</label>
            <input type="number" min="0" step="0.1" placeholder="2.0" value={rent} onChange={e => setRent(e.target.value)}
              className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors" />
          </div>
          <div>
            <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Home price</label>
            <input type="number" min="0" step="1" placeholder="120" value={price} onChange={e => setPrice(e.target.value)}
              className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors" />
          </div>
        </div>

        <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">
          Owner address {terms?.owner && <span className="text-white/50 normal-case tracking-normal">· claimed</span>}
        </label>
        <input type="text" placeholder="0x…" value={ownerAddr} onChange={e => setOwnerAddr(e.target.value)}
          className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors mb-5" />

        <button onClick={save} disabled={saving}
          className="btn-shine w-full py-4 rounded-xl bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-xl hover:shadow-coral/30 disabled:opacity-30 transition-all">
          {saving ? 'Setting…' : 'Set the terms →'}
        </button>
        <p className="text-[10px] text-white/50 text-center mt-3 leading-relaxed">
          Once an owner address is recorded, only that address can change these terms — the same rule
          <code className="text-white/60"> onlyOwner </code> enforces on-chain.
        </p>
      </div>

      {/* ── The consequence ── */}
      <div className="space-y-6">
        <div className="glass rounded-3xl p-6 md:p-8">
          <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/60 mb-5">
            Every {rent.trim() ? `${monthly} Ξ` : '1 Ξ'} paid
          </div>
          <SplitBar amount={monthly} fee={fee} credit={credit} owner={ownerCut} big className="mb-6" />
          <div className="grid grid-cols-3 gap-3">
            {[
              { k: 'Your equity', v: credit, c: 'text-coral' },
              { k: 'Owner income', v: ownerCut, c: 'text-emerald-400' },
              { k: 'Protocol fee', v: fee, c: 'text-pink' },
            ].map(s => (
              <div key={s.k} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4">
                <div className={`text-xl font-display font-extrabold tabular-nums ${s.c}`}>{s.v.toFixed(3)}</div>
                <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
              </div>
            ))}
          </div>
          <div className="mt-6 pt-5 border-t border-white/[0.07] flex items-baseline justify-between">
            <span className="text-[11px] uppercase tracking-widest text-white/60 font-bold">Stays with the property</span>
            <span className="headline text-3xl text-surf-grad tabular-nums">{(100 - feePct).toFixed(1)}%</span>
          </div>
        </div>

        <div className="glass rounded-3xl p-6 md:p-8">
          <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/60 mb-5">Skimmed off the top</div>
          <TakeComparison ourTake={feePct} />
        </div>
      </div>
    </div>
  )
}

/* ── Interactive rent-to-own equity simulator ───────────── */
function SimField({ label, value, set, min, max, step, unit }: {
  label: string; value: number; set: (n: number) => void; min: number; max: number; step: number; unit: string
}) {
  return (
    <div className="flex-1 min-w-[120px]">
      <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">{label}</label>
      <div className="flex items-center rounded-xl border border-white/10 bg-white/5 focus-within:border-coral/50 transition-colors">
        <input type="number" min={min} max={max} step={step} value={value}
          onChange={e => set(Math.max(min, Math.min(max, parseFloat(e.target.value) || 0)))}
          className="w-full bg-transparent text-white text-sm px-3 py-2.5 font-mono focus:outline-none" />
        <span className="text-white/55 text-xs pr-3 font-mono">{unit}</span>
      </div>
    </div>
  )
}

function EquitySimulator({ feePct: feeInit = 2.5, creditPct: creditInit = 100 }: { feePct?: number; creditPct?: number }) {
  const [price, setPrice] = useState(120)     // home price, Ξ
  const [monthly, setMonthly] = useState(2)   // monthly payment, Ξ
  const [apy, setApy] = useState(5)           // lowfi APY, %
  const [feePct, setFeePct] = useState(feeInit)       // protocol take, 1–5%
  const [creditPct, setCreditPct] = useState(creditInit) // of the net payment → principal
  // Follow the live deal until someone drags a slider of their own.
  const touched = useRef(false)
  useEffect(() => { if (!touched.current) { setFeePct(feeInit); setCreditPct(creditInit) } }, [feeInit, creditInit])

  // One month of rent, split the way the contract splits it.
  const perMonthFee = monthly * (feePct / 100)
  const perMonthCredit = (monthly - perMonthFee) * (creditPct / 100)
  const canOwn = perMonthCredit > 0
  // A plain lease never pays it off — cap the timeline at 50 years so the scrubber still works.
  const monthsToOwn = canOwn ? Math.max(1, Math.ceil(price / perMonthCredit)) : 600
  const [month, setMonth] = useState(18)
  const [full, setFull] = useState(false)

  useEffect(() => { setMonth(m => Math.min(m, monthsToOwn)) }, [monthsToOwn])
  useEffect(() => {
    if (!full) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFull(false) }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = prev }
  }, [full])

  const principalPaid = Math.min(month * perMonthCredit, price)
  const ownPct = price > 0 ? (principalPaid / price) * 100 : 0
  const remaining = Math.max(price - principalPaid, 0)
  const grossPaid = month * monthly
  const feesPaid = month * perMonthFee
  const ownerIncome = Math.max(grossPaid - feesPaid - principalPaid, 0)

  // Owner's lowfi yield: credited principal compounds at the APY while it sits.
  let bal = 0, yieldEarned = 0
  const r = apy / 100 / 12
  for (let i = 0; i < month; i++) {
    if (i * perMonthCredit < price) bal += Math.min(perMonthCredit, price - i * perMonthCredit)
    const interest = bal * r
    yieldEarned += interest
    bal += interest
  }

  const filled = Math.round(ownPct)
  const owned = ownPct >= 100

  const stats = [
    { v: `${principalPaid.toFixed(1)} Ξ`, c: 'text-white', k: 'your equity' },
    { v: `${remaining.toFixed(1)} Ξ`, c: 'text-emerald-400', k: 'left to own' },
    { v: canOwn ? `${monthsToOwn} mo` : 'never', c: 'text-coral', k: 'to own outright' },
    { v: `${ownerIncome.toFixed(2)} Ξ`, c: 'text-white/80', k: "owner's rent income" },
    { v: `${feesPaid.toFixed(2)} Ξ`, c: 'text-pink', k: `protocol fee · ${feePct}%` },
    { v: `${yieldEarned.toFixed(2)} Ξ`, c: 'text-pink', k: "owner's lowfi yield" },
  ]

  const panel = (big: boolean) => (
    <>
      <div className="flex flex-wrap items-end gap-4 mb-5">
        <SimField label="Home price" value={price} set={setPrice} min={1} max={100000} step={1} unit="Ξ" />
        <SimField label="Monthly payment" value={monthly} set={setMonthly} min={0.01} max={10000} step={0.1} unit="Ξ" />
        <SimField label="lowfi APY" value={apy} set={setApy} min={0} max={50} step={0.5} unit="%" />
        <SimField label="Protocol fee" value={feePct} set={n => { touched.current = true; setFeePct(n) }} min={1} max={5} step={0.1} unit="%" />
        <SimField label="Rent credit" value={creditPct} set={n => { touched.current = true; setCreditPct(n) }} min={0} max={100} step={1} unit="%" />
      </div>

      {/* One month of rent, cut three ways */}
      <SplitBar amount={monthly} fee={perMonthFee} credit={perMonthCredit}
        owner={monthly - perMonthFee - perMonthCredit} className="mb-7" />

      <div className={`grid gap-7 items-center ${big ? 'lg:grid-cols-[1.6fr_1fr]' : 'md:grid-cols-[1.3fr_1fr]'}`}>
        {/* Brick wall */}
        <div>
          <div className={`grid grid-cols-10 mb-4 ${big ? 'gap-2 md:gap-2.5' : 'gap-1.5'}`}>
            {Array.from({ length: 100 }, (_, i) => (
              <div key={i} className="aspect-[3/2] rounded-[3px] transition-all duration-300"
                style={{
                  background: i < filled ? 'linear-gradient(135deg,var(--peach),var(--coral))' : 'rgb(var(--ink-rgb) / 0.06)',
                  boxShadow: i < filled ? '0 0 8px rgb(var(--coral-rgb) / 0.45)' : 'none',
                  transitionDelay: `${Math.min(i, 60) * 4}ms`,
                }} />
            ))}
          </div>
          <input type="range" min={0} max={monthsToOwn} value={month}
            onChange={e => setMonth(parseInt(e.target.value))}
            className="w-full accent-coral cursor-pointer" />
          <div className="flex justify-between text-[11px] text-white/58 mt-1.5 font-mono">
            <span>Move in</span>
            <span className="text-coral font-bold">Month {month} · year {(month / 12).toFixed(1)}</span>
            <span>Owned</span>
          </div>
        </div>

        {/* Live readout */}
        <div className="space-y-4">
          <div>
            <div className={`headline text-surf-grad leading-none ${big ? 'text-7xl md:text-8xl' : 'text-6xl'}`}>{ownPct.toFixed(1)}<span className={big ? 'text-4xl' : 'text-3xl'}>%</span></div>
            <div className="text-[11px] uppercase tracking-widest text-white/60 font-bold mt-1">{owned ? 'The house is yours' : 'of the home is yours'}</div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {stats.map((s, i) => (
              <div key={i} className={`rounded-xl bg-white/[0.03] border border-white/[0.06] ${big ? 'p-4' : 'p-3'}`}>
                <div className={`font-bold tabular-nums ${s.c} ${big ? 'text-xl' : 'text-sm'}`}>{s.v}</div>
                <div className="text-[10px] uppercase tracking-widest text-white/58 mt-0.5">{s.k}</div>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-white/50 leading-relaxed">
            Illustrative projection from your inputs — not live data or financial advice. Real stakes come straight from the contract.
          </p>
        </div>
      </div>
    </>
  )

  return (
    <>
      <div className="glass rounded-3xl p-6 md:p-9 border-coral/15 relative">
        <button onClick={() => setFull(true)}
          className="absolute top-4 right-4 z-10 flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-white/15 bg-white/5 text-[10px] font-bold uppercase tracking-widest text-white/68 hover:text-coral hover:border-coral/40 transition-colors">
          ⤢ Expand
        </button>
        {panel(false)}
      </div>

      {full && createPortal(
        <div className="fixed inset-0 z-[100] bg-paper/95 backdrop-blur-2xl overflow-y-auto grain" onClick={() => setFull(false)}>
          <div className="aurora" />
          <div className="relative min-h-full flex flex-col">
            <div className="flex items-center justify-between px-5 md:px-8 h-16 border-b border-white/10 sticky top-0 bg-paper/70 backdrop-blur-xl z-10">
              <div className="flex items-center gap-2.5">
                <span className="w-7 h-7 rounded-md bg-gradient-to-br from-coral to-ember flex items-center justify-center text-onaccent font-black text-xs">⌂</span>
                <span className="font-display font-extrabold text-white text-sm">Rent-to-own builder</span>
              </div>
              <button onClick={() => setFull(false)}
                className="flex items-center gap-2 px-4 py-2 rounded-full border border-white/15 text-[11px] font-bold uppercase tracking-widest text-white/75 hover:text-white hover:border-white/30 transition-colors">
                Close ✕ <span className="text-white/55 normal-case tracking-normal">Esc</span>
              </button>
            </div>
            <div className="flex-1 flex items-center justify-center p-5 md:p-12" onClick={e => e.stopPropagation()}>
              <div className="w-full max-w-6xl">
                <div className="text-center mb-10">
                  <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-3">Builder · Fullscreen</div>
                  <h3 className="headline text-4xl md:text-6xl text-white">Build your path to ownership.</h3>
                </div>
                {panel(true)}
              </div>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}

/* ── Vibes ───────────────────────────────────────────────
   One page, eight cabinets. Every colour, font and texture on the page is
   a CSS token, so a vibe is nothing but three attributes on <html>:

     mode  the field      paper (washi, daylight) | digital (a CRT)
     skin  the treatment  soft (curves, blur) | pixel (8-bit, hard shadows)
     id    the palette    ten triplets and nothing else

   That split is the reason this table is ten lines and not eight
   stylesheets: MARIO and GAMEBOY differ by colour alone, and both ride
   the same paper·pixel structure. See the VIBES section of globals.css.
   `chips` are three colours lifted from the cabinet's own palette (field,
   accent, gain) — the swatch you read before you switch, and the only
   identity a row needs. (A glyph column was tried and cut: the pixel
   skins' faces have no box-drawing glyphs, so every row rendered tofu.)

   layout.tsx applies the saved vibe before first paint, so nothing
   flashes; here we only read it back and rewrite it. */
type Mode = 'paper' | 'digital'
type Skin = 'soft' | 'pixel'
const VIBE_KEY = 'openhouse_vibe'

const VIBES = [
  { id: 'sunday',   label: 'Sunday',   mode: 'paper',   skin: 'soft',  note: 'washi paper, Memphis pastels',     chips: ['#fff6ec', '#ff7a5c', '#2ed3b7'] },
  { id: 'mario',    label: 'Mario',    mode: 'paper',   skin: 'pixel', note: 'world 1-1: brick, coin, pipe',     chips: ['#5c94fc', '#e43434', '#fcd83c'] },
  { id: 'gameboy',  label: 'Game Boy', mode: 'paper',   skin: 'pixel', note: 'DMG — four greens, no fifth',      chips: ['#9bbc0f', '#306230', '#0f380f'] },
  { id: 'terminal', label: 'Terminal', mode: 'digital', skin: 'soft',  note: 'the building on a green screen',   chips: ['#040807', '#00e8a0', '#ff2f87'] },
  { id: 'amber',    label: 'Amber',    mode: 'digital', skin: 'soft',  note: 'single-gun CRT, one hue burnt in', chips: ['#0d0700', '#ffb028', '#ffe896'] },
  { id: 'arcade',   label: 'Arcade',   mode: 'digital', skin: 'pixel', note: 'NES black under cabinet neon',     chips: ['#0a0614', '#22f0ff', '#2bff88'] },
  { id: 'c64',      label: 'C64',      mode: 'digital', skin: 'pixel', note: '64K BASIC, light blue on blue',    chips: ['#30288a', '#aaffee', '#aaff66'] },
  { id: 'vapor',    label: 'Vapor',    mode: 'digital', skin: 'pixel', note: 'Miami at 2am, horizon grid',       chips: ['#160328', '#ff2d95', '#2bffce'] },
] as const satisfies readonly { id: string; label: string; mode: Mode; skin: Skin; note: string; chips: readonly string[] }[]

type VibeId = (typeof VIBES)[number]['id']
const DEFAULT_VIBE: VibeId = 'sunday'

function useVibe(): [(typeof VIBES)[number], (id: VibeId) => void] {
  const [id, setId] = useState<VibeId>(DEFAULT_VIBE)
  // The boot script already stamped <html>; read it back rather than
  // re-reading localStorage, so the two can never disagree.
  useEffect(() => {
    const stamped = document.documentElement.getAttribute('data-vibe')
    if (stamped && VIBES.some(v => v.id === stamped)) setId(stamped as VibeId)
  }, [])
  const pick = useCallback((next: VibeId) => {
    const v = VIBES.find(x => x.id === next)
    if (!v) return
    const d = document.documentElement
    d.setAttribute('data-vibe', v.id)
    d.setAttribute('data-mode', v.mode)
    d.setAttribute('data-skin', v.skin)
    try { localStorage.setItem(VIBE_KEY, v.id) } catch {}
    setId(v.id)
  }, [])
  return [VIBES.find(v => v.id === id) ?? VIBES[0], pick]
}

/* The DIP-switch panel. The cap on the nav wears the current cabinet's
   three chips; pressing it drops the whole set, each row wearing its own.
   No preview and no animation — you flip the switch and the page is a
   different machine. */
function VibePicker() {
  const [vibe, pick] = useVibe()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)

  // Click-away and Escape both close it. A menu you can only leave by
  // choosing something is a modal, and this isn't one.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrap} className="relative shrink-0">
      <button onClick={() => setOpen(o => !o)} aria-haspopup="menu" aria-expanded={open}
        title={`Vibe: ${vibe.label} — ${vibe.note}`}
        className="flex items-center gap-2 px-3 py-2 rounded-full border border-white/12 text-[11px] font-bold uppercase tracking-widest text-white/68 hover:text-coral hover:border-coral/40 transition-colors">
        <Chips chips={vibe.chips} />
        {/* On a phone the chips already say which cabinet you're in, and
            the nav is out of room. */}
        <span className="hidden sm:inline">{vibe.label}</span>
      </button>

      {open && (
        <div role="menu" aria-label="Vibe"
          className="absolute right-0 mt-2 w-[17.5rem] p-1.5 glass rounded-2xl shadow-xl z-50">
          {VIBES.map(v => (
            <button key={v.id} role="menuitemradio" aria-checked={v.id === vibe.id}
              onClick={() => { pick(v.id); setOpen(false) }}
              className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl text-left transition-colors ${
                v.id === vibe.id ? 'bg-coral/[0.12] text-coral' : 'text-white/75 hover:bg-white/[0.06]'}`}>
              <span aria-hidden className="w-3 text-[11px] leading-none">{v.id === vibe.id ? '▸' : ''}</span>
              <span className="flex-1 min-w-0">
                <span className="block text-[12px] font-bold">{v.label}</span>
                <span className="block text-[10px] text-white/45 truncate">{v.note}</span>
              </span>
              <Chips chips={v.chips} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Chips({ chips }: { chips: readonly string[] }) {
  return (
    <span aria-hidden className="flex shrink-0 rounded-[3px] overflow-hidden border border-white/15">
      {chips.map(c => <span key={c} className="w-2 h-3.5" style={{ background: c }} />)}
    </span>
  )
}

const NAV = [
  { id: 'manifesto', label: 'Manifesto' },
  { id: 'split', label: 'The Split' },
  { id: 'invest', label: 'Invest' },
  { id: 'whitepaper', label: 'Whitepaper' },
  { id: 'landscape', label: 'Landscape' },
  { id: 'code', label: 'Code' },
  { id: 'captable', label: 'Cap Table' },
]

function OpenHousePageInner() {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [property, setProperty] = useState<PropertyData | null>(null)
  const [shareholders, setShareholders] = useState<Shareholder[]>([])
  const [dividends, setDividends] = useState<DividendRecord[]>([])
  const [sources, setSources] = useState<SourceFile[]>([])
  const [terms, setTerms] = useState<TermsData | null>(null)
  const [models, setModels] = useState<ModelPreset[]>([])
  const [rentStats, setRentStats] = useState<RentStats | null>(null)
  const [compare, setCompare] = useState<CompareData | null>(null)
  const [comparing, setComparing] = useState(false)
  const [loading, setLoading] = useState(false)
  const [buyAddr, setBuyAddr] = useState('')
  const [buyShares, setBuyShares] = useState('')
  const [purchasing, setPurchasing] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [progress, setProgress] = useState(0)
  const [scrollY, setScrollY] = useState(0)
  const heroRef = useRef<HTMLElement>(null)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [s, p, sh, d, t, rs] = await Promise.all([
        api('status').catch(() => null),
        api('property').catch(() => null),
        api('shareholders').catch(() => []),
        api('dividends').catch(() => []),
        api('terms').catch(() => null),
        api('rent_stats').catch(() => null),
      ])
      if (s) setStatus(s)
      if (p) setProperty(p)
      setShareholders(Array.isArray(sh) ? sh : [])
      setDividends(Array.isArray(d) ? d : [])
      if (t) setTerms(t)
      if (rs) setRentStats(rs)
    } catch {}
    setLoading(false)
  }, [])

  // The landscape reaches other people's APIs — keep it off the main refresh
  // path so the dashboard never waits on RealT or CoinGecko.
  const fetchCompare = useCallback(async (refresh = false) => {
    setComparing(true)
    try { setCompare(await api(`compare${refresh ? '?refresh=true' : ''}`)) } catch {}
    setComparing(false)
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])
  useEffect(() => { fetchCompare() }, [fetchCompare])
  useEffect(() => { api('models').then(r => setModels(r?.models ?? [])).catch(() => {}) }, [])
  useEffect(() => { api('source').then(s => setSources(Array.isArray(s) ? s : [])).catch(() => {}) }, [])
  useEffect(() => {
    const onScroll = () => {
      const y = window.scrollY
      setScrolled(y > 40)
      setScrollY(y)
      const h = document.documentElement.scrollHeight - window.innerHeight
      setProgress(h > 0 ? (y / h) * 100 : 0)
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const onHeroMove = (e: React.MouseEvent) => {
    const el = heroRef.current; if (!el) return
    const r = el.getBoundingClientRect()
    el.style.setProperty('--mx', `${e.clientX - r.left}px`)
    el.style.setProperty('--my', `${e.clientY - r.top}px`)
  }

  const handlePurchase = async () => {
    if (!buyAddr.trim() || !buyShares.trim()) { toast.error('Address and share count required'); return }
    setPurchasing(true)
    try {
      const result = await api('purchase', { method: 'POST', body: { buyer: buyAddr.trim(), share_count: parseInt(buyShares), payment: 0 } })
      if (result.success) {
        toast.success(`You own ${result.shares_purchased} more shares. Welcome to the building.`)
        setBuyAddr(''); setBuyShares(''); fetchAll()
      }
    } catch (err: any) { toast.error(err?.message || 'Purchase failed') }
    setPurchasing(false)
  }

  const deployed = !!(property?.deployed && status?.deployed && status.total_shares > 0)
  const soldPct = deployed && status ? (status.shares_sold / status.total_shares) * 100 : 0
  const price = deployed && property ? parseFloat(property.share_price || '0') : 0
  const cost = buyShares ? parseInt(buyShares || '0') * price : 0

  // Before a property is on-chain the live counters are all zero — a wall of goose
  // eggs reads as broken. Show what's actually true about the stage instead.
  const heroStats: { k: string; v: ReactNode; c: string; small?: boolean }[] = deployed ? [
    { k: 'Shareholders', v: <Counter value={status!.shareholders} />, c: 'text-white' },
    { k: 'Shares Sold', v: <Counter value={status!.shares_sold} />, c: 'text-coral' },
    { k: 'Contributed', v: <Counter value={status!.total_contributed} decimals={2} suffix=" Ξ" />, c: 'text-emerald-400' },
    { k: 'Dividends Paid', v: <Counter value={status!.total_dividends_distributed} decimals={2} suffix=" Ξ" />, c: 'text-pink' },
  ] : [
    { k: 'Stage', v: 'Testnet', c: 'text-coral', small: true },
    { k: 'Network', v: LAUNCH.chain, c: 'text-white', small: true },
    { k: 'Mainnet launch', v: 'TBA', c: 'text-pink', small: true },
    { k: 'Cost to try', v: 'Test ETH', c: 'text-emerald-400', small: true },
  ]

  return (
    <div className="relative grain vignette min-h-screen">
      <div className="aurora" />
      <div className="scrollbar-prog" style={{ width: `${progress}%` }} />

      {/* ── Testnet banner + nav ────────────────────────── */}
      <div className="fixed top-0 inset-x-0 z-50">
        <div className="banner-strip">
          <div className="max-w-6xl mx-auto px-5 md:px-8 min-h-[2.25rem] py-1.5 flex items-center justify-center gap-2.5 text-center">
            <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.18em] text-paper bg-ink rounded px-1.5 py-0.5">{LAUNCH.stage}</span>
            <p className="text-[11px] leading-tight text-white/85">
              Running on {LAUNCH.chain} — test ETH only, nothing here is real money or a real deed.
              <span className="text-white/68"> Mainnet launch: {LAUNCH.date.toLowerCase()}.</span>
            </p>
          </div>
        </div>
        <nav className={`transition-all duration-500 ${scrolled ? 'bg-paper/80 backdrop-blur-xl border-b border-white/[0.07]' : 'bg-transparent'}`}>
          <div className="max-w-6xl mx-auto px-5 md:px-8 h-16 flex items-center justify-between">
            <a href="#top" className="flex items-center gap-2.5">
              <span className="w-8 h-8 rounded-md bg-gradient-to-br from-coral to-ember flex items-center justify-center text-onaccent font-black text-sm shadow-lg shadow-coral/20">⌂</span>
              <span className="font-display font-extrabold tracking-tight text-[15px] text-white">OpenHouse</span>
            </a>
            <div className="hidden md:flex items-center gap-7 text-[12px] font-semibold uppercase tracking-widest text-white/65">
              {NAV.map(n => <a key={n.id} href={`#${n.id}`} className="hover:text-coral transition-colors">{n.label}</a>)}
            </div>
            <div className="flex items-center gap-3">
              {deployed && status!.available_shares > 0 && (
                <span className="hidden sm:flex items-center gap-1.5 text-[11px] font-bold text-white/68">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  {formatNum(status!.available_shares)} left
                </span>
              )}
              <VibePicker />
              <a href="#invest" className="btn-shine px-4 py-2 rounded-full bg-ink text-paper text-[11px] font-bold uppercase tracking-widest hover:bg-pink transition-colors">
                {deployed ? 'Buy In' : 'Try Testnet'}
              </a>
            </div>
          </div>
        </nav>
      </div>

      <div id="top" className="relative z-10">

        {/* ── Hero ───────────────────────────────────────── */}
        <header ref={heroRef} onMouseMove={onHeroMove} className="relative min-h-screen flex flex-col justify-center overflow-hidden">
          <Skyline shift={scrollY * 0.25} />
          <div className="spotlight" />
          {/* let the pastel city keep its color — just enough haze to seat the type */}
          <div className="absolute inset-x-0 bottom-0 h-[46vh] bg-gradient-to-t from-paper/80 via-paper/25 to-transparent z-[1]" />

          <div className="relative z-10 px-5 md:px-8 pt-32 pb-16 max-w-6xl mx-auto w-full">
            <Reveal>
              <div className="inline-flex flex-wrap items-center gap-2 px-3 py-1.5 rounded-full glass text-[11px] font-bold uppercase tracking-[0.2em] text-coral mb-8">
                <span className="w-1.5 h-1.5 rounded-full bg-coral animate-pulse" />
                {LAUNCH.stage} on {LAUNCH.chain}
                <span className="text-white/68">Launch {LAUNCH.date.toLowerCase()}</span>
              </div>
            </Reveal>
            <Reveal delay={80}>
              <h1 className="headline text-[16vw] md:text-[8.5rem] leading-[0.85] text-white glow-warm">
                OWN THE<br /><span className="text-surf-grad">SKYLINE.</span>
              </h1>
            </Reveal>
            <Reveal delay={180}>
              <p className="font-serif-ed text-2xl md:text-3xl text-white/80 max-w-2xl mt-8 leading-snug">
                You've paid someone else's mortgage long enough.
                <span className="text-white"> OpenHouse is rent-to-own, on-chain</span> — every payment
                becomes principal in the home, redistributed quarterly. Pay it off, own it.
              </p>
            </Reveal>
            <Reveal delay={280}>
              <div className="flex flex-wrap items-center gap-4 mt-10">
                <a href="#invest" className="btn-shine px-7 py-3.5 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
                  {deployed ? 'Start owning →' : 'Try it on testnet →'}
                </a>
                <a href="#whitepaper" className="px-7 py-3.5 rounded-full border border-white/15 text-white/80 font-bold uppercase tracking-widest text-sm hover:border-coral/50 hover:text-white transition-colors">Read the whitepaper</a>
              </div>
              {!deployed && (
                /* sits over the skyline art — needs its own backdrop to stay readable */
                <p className="glass rounded-xl px-4 py-2.5 text-white/65 text-xs mt-5 max-w-md leading-relaxed">
                  Testnet preview — the contract, the math, and the cap table are all real code on {LAUNCH.chain}.
                  The money isn't. Mainnet launch date {LAUNCH.date.toLowerCase()}.
                </p>
              )}
            </Reveal>
            <Reveal delay={400}>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-px mt-16 rounded-2xl overflow-hidden glass">
                {heroStats.map((s, i) => (
                  <div key={i} className="p-5 md:p-6 bg-white/[0.015]">
                    <div className={`headline ${s.small ? 'text-2xl md:text-3xl' : 'text-3xl md:text-4xl'} ${s.c} tabular-nums`}>{s.v}</div>
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/58 mt-2">{s.k}</div>
                  </div>
                ))}
              </div>
            </Reveal>
          </div>
          <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-10 text-white/50 text-xs tracking-widest uppercase animate-bounce">scroll ↓</div>
        </header>

        {/* ── Times-Square dual ticker ───────────────────── */}
        <div className="border-y border-white/10 bg-white/[0.02] py-5 overflow-hidden space-y-2">
          <div className="marquee">
            {[...TICKER, ...TICKER].map((t, i) => (
              <span key={i} className="headline text-2xl md:text-3xl text-white/26 px-8 whitespace-nowrap flex items-center gap-8">{t} <span className="text-coral/40">✦</span></span>
            ))}
          </div>
          <div className="marquee marquee-rev">
            {[...TICKER].reverse().concat([...TICKER].reverse()).map((t, i) => (
              <span key={i} className="headline text-2xl md:text-3xl px-8 whitespace-nowrap flex items-center gap-8" style={{ WebkitTextStroke: '1px rgb(var(--coral-rgb) / 0.55)', color: 'transparent' }}>{t} <span className="text-pink/30">●</span></span>
            ))}
          </div>
        </div>

        {/* ── Manifesto ──────────────────────────────────── */}
        <section id="manifesto" className="max-w-5xl mx-auto px-5 md:px-8 py-28 md:py-40">
          {MANIFESTO.map((line, i) => (
            <Reveal key={i} delay={i * 120}>
              <p className={`font-serif-ed text-4xl md:text-6xl leading-[1.05] mb-3 ${i === MANIFESTO.length - 1 ? 'text-surf-grad font-black' : 'text-white/60'}`}>{line}</p>
            </Reveal>
          ))}
          <Reveal delay={500}>
            <p className="text-white/75 text-lg md:text-xl max-w-2xl mt-12 leading-relaxed border-l-2 border-coral/40 pl-6">{ABSTRACT}</p>
          </Reveal>
        </section>

        {/* ── The split — fee band + owner's dial ────────── */}
        <section id="split" className="max-w-6xl mx-auto px-5 md:px-8 py-20 md:py-28">
          <Reveal>
            <div className="text-center mb-14">
              <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-4">Where the rent goes</div>
              <h2 className="headline text-5xl md:text-7xl text-white leading-[0.95]">
                THEY TAKE 15%.<br /><span className="text-surf-grad">WE TAKE {terms ? terms.fee_pct : '1–5'}%.</span>
              </h2>
              <p className="font-serif-ed text-xl text-white/68 max-w-2xl mx-auto mt-7">
                {terms ? `${terms.to_property_pct}%` : '95–99%'} of every payment stays with the property —
                split between the renter's equity and the owner's income by whichever rent-to-own model the owner picked.
                The 1–5% band is a constant in the contract, not a promise on a pricing page.
              </p>
            </div>
          </Reveal>

          {/* Live deal summary — only once someone has actually set terms */}
          {terms && terms.updated > 0 && (
            <Reveal delay={60}>
              <div className="glass rounded-3xl p-6 md:p-7 mb-8 grid sm:grid-cols-2 lg:grid-cols-4 gap-5">
                {[
                  { k: 'Live model', v: terms.custom ? `${terms.model_name} · tuned` : terms.model_name, c: 'text-white' },
                  { k: 'Protocol take', v: `${terms.fee_pct}%`, c: 'text-pink' },
                  { k: 'To renter equity', v: `${terms.equity_pct_of_rent}%`, c: 'text-coral' },
                  { k: 'To owner income', v: `${terms.owner_pct_of_rent}%`, c: 'text-emerald-400' },
                ].map(s => (
                  <div key={s.k}>
                    <div className={`text-2xl font-display font-extrabold tabular-nums ${s.c}`}>{s.v}</div>
                    <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                  </div>
                ))}
              </div>
            </Reveal>
          )}

          <Reveal delay={100}>
            <TermsDesk terms={terms} models={models} onSaved={fetchAll} />
          </Reveal>

          {/* What has actually been paid, if anything has */}
          {rentStats && rentStats.payments > 0 && (
            <Reveal delay={140}>
              <div className="glass rounded-3xl p-6 md:p-8 mt-8">
                <div className="flex items-baseline justify-between flex-wrap gap-3 mb-6">
                  <h3 className="font-display font-bold text-white uppercase tracking-wider text-sm">Rent recorded so far</h3>
                  <span className="text-[11px] text-white/58">
                    {rentStats.payments} payment{rentStats.payments === 1 ? '' : 's'} · {rentStats.renters} renter{rentStats.renters === 1 ? '' : 's'}
                  </span>
                </div>
                <SplitBar amount={rentStats.gross_rent} fee={rentStats.protocol_fees}
                  credit={rentStats.renter_equity} owner={rentStats.owner_income} big className="mb-6" />
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {[
                    { k: 'Gross rent', v: `${formatNum(rentStats.gross_rent, 3)} Ξ`, c: 'text-white' },
                    { k: 'Renter equity', v: `${formatNum(rentStats.renter_equity, 3)} Ξ`, c: 'text-coral' },
                    { k: 'Owner income', v: `${formatNum(rentStats.owner_income, 3)} Ξ`, c: 'text-emerald-400' },
                    { k: 'Protocol fees', v: `${formatNum(rentStats.protocol_fees, 3)} Ξ`, c: 'text-pink' },
                  ].map(s => (
                    <div key={s.k} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4">
                      <div className={`text-lg font-display font-extrabold tabular-nums ${s.c}`}>{s.v}</div>
                      <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                    </div>
                  ))}
                </div>
                <p className="text-[11px] text-white/55 mt-5">
                  {rentStats.to_property_pct}% of everything paid stayed with the property.
                  An Airbnb-rate platform would have taken {formatNum(rentStats.gross_rent * 0.15, 3)} Ξ instead of {formatNum(rentStats.protocol_fees, 3)} Ξ.
                </p>
              </div>
            </Reveal>
          )}
        </section>

        {/* ── Invest / live dashboard ────────────────────── */}
        <section id="invest" className="max-w-6xl mx-auto px-5 md:px-8 py-20 md:py-28">
          <Reveal>
            <div className="flex items-end justify-between flex-wrap gap-4 mb-12">
              <div>
                <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mb-3">The Building</div>
                <h2 className="headline text-5xl md:text-7xl text-white">Rent toward ownership.</h2>
              </div>
              <button onClick={fetchAll} disabled={loading} className="px-4 py-2 rounded-full border border-white/12 text-[11px] font-bold uppercase tracking-widest text-white/68 hover:text-white hover:border-white/30 disabled:opacity-30 transition-colors">
                {loading ? 'Syncing…' : '↻ Live data'}
              </button>
            </div>
          </Reveal>

          <div className="grid lg:grid-cols-5 gap-6">
            <Reveal className="lg:col-span-3" delay={60}>
              <Tilt className="h-full">
                <div className="glass glass-hover rounded-3xl p-7 md:p-9 h-full">
                  <div className="flex items-start justify-between mb-7">
                    <div>
                      <h3 className="font-serif-ed text-3xl text-white mb-1">{deployed ? property!.description : 'No property deployed yet'}</h3>
                      <p className="text-white/60 text-sm">{deployed ? (property!.contract || 'On-chain') : 'Deploy a property to open the float'}</p>
                    </div>
                    <span className={`text-[11px] font-bold uppercase tracking-widest px-3 py-1.5 rounded-full border ${deployed && property!.is_active ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10' : 'text-white/60 border-white/15'}`}>
                      {deployed ? (property!.is_active ? '● Active' : 'Paused') : `${LAUNCH.stage} · not deployed`}
                    </span>
                  </div>
                  {deployed ? (
                    <>
                      <div className="mb-8">
                        <div className="flex justify-between text-xs mb-2">
                          <span className="text-white/60 uppercase tracking-widest font-bold">Float sold</span>
                          <span className="text-coral font-bold">{soldPct.toFixed(1)}%</span>
                        </div>
                        <div className="h-3 rounded-full bg-white/[0.06] overflow-hidden">
                          <div className="h-full rounded-full bg-gradient-to-r from-ember via-coral to-peach transition-all duration-1000" style={{ width: `${Math.max(soldPct, 1)}%` }} />
                        </div>
                        <div className="flex justify-between text-[11px] text-white/55 mt-2">
                          <span>{formatNum(status!.shares_sold)} sold</span>
                          <span>{formatNum(status!.total_shares)} total supply</span>
                        </div>
                      </div>
                      <div className="grid grid-cols-3 gap-4">
                        {[
                          { k: 'Share price', v: `${property!.share_price} Ξ`, c: 'text-coral' },
                          { k: 'Available', v: formatNum(status!.available_shares), c: 'text-emerald-400' },
                          { k: 'Owners', v: formatNum(status!.shareholders), c: 'text-white' },
                        ].map((s, i) => (
                          <div key={i} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4">
                            <div className={`text-2xl font-display font-extrabold ${s.c} tabular-nums`}>{s.v}</div>
                            <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="rounded-2xl border border-dashed border-white/12 bg-white/[0.015] p-8 text-center">
                      <div className="text-4xl mb-3 float">⌂</div>
                      <p className="text-white/72 text-sm leading-relaxed max-w-sm mx-auto">
                        No building has been fractionalized on this contract yet. Once a property is deployed,
                        its float, share price, and live ownership show up here — straight from chain, no placeholders.
                      </p>
                      <p className="text-white/58 text-xs leading-relaxed max-w-sm mx-auto mt-3">
                        On {LAUNCH.chain} you can deploy one yourself and drive the whole loop with test ETH.
                        The first real home lands with the mainnet launch — date {LAUNCH.date.toLowerCase()}.
                      </p>
                      <code className="inline-block mt-4 text-[11px] font-mono text-coral/80 bg-coral/5 border border-coral/15 rounded-lg px-3 py-1.5">openhouse deploy property_details=… total_shares=… share_price=…</code>
                    </div>
                  )}
                </div>
              </Tilt>
            </Reveal>

            <Reveal className="lg:col-span-2" delay={140}>
              <div className="glass rounded-3xl p-7 md:p-9 h-full flex flex-col border-coral/20">
                <div className="flex items-center justify-between gap-3 mb-2">
                  <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-coral">Take a position</div>
                  <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.15em] text-onaccent bg-coral rounded px-1.5 py-0.5">{LAUNCH.stage}</span>
                </div>
                <h3 className="font-serif-ed text-2xl text-white mb-2">Mint your shares</h3>
                <p className="text-[11px] text-white/60 leading-relaxed mb-6">
                  {deployed
                    ? `Settles on ${LAUNCH.chain} with test ETH — the mainnet sale opens at launch, date ${LAUNCH.date.toLowerCase()}.`
                    : `The mainnet sale hasn't opened. Launch date: ${LAUNCH.date.toLowerCase()}.`}
                </p>
                <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Your wallet</label>
                <input type="text" placeholder="0x…" value={buyAddr} onChange={e => setBuyAddr(e.target.value)} className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors mb-4" />
                <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Shares</label>
                <input type="number" min="1" placeholder="100" value={buyShares} onChange={e => setBuyShares(e.target.value)} className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors mb-5" />
                <div className="flex justify-between items-baseline mb-5 pb-5 border-b border-white/10">
                  <span className="text-white/60 text-sm uppercase tracking-widest font-bold">Total</span>
                  <span className="headline text-3xl text-coral tabular-nums">{deployed ? (cost ? cost.toFixed(4) : '0.00') : '—'} {deployed && <span className="text-lg">Ξ</span>}</span>
                </div>
                <button onClick={handlePurchase} disabled={!deployed || purchasing || !buyAddr.trim() || !buyShares.trim()} className="btn-shine w-full py-4 rounded-xl bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-xl hover:shadow-coral/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all mt-auto">
                  {!deployed ? 'Sale not open · launch TBA' : purchasing ? 'Minting…' : 'Own it (testnet) →'}
                </button>
                <p className="text-[10px] text-white/50 text-center mt-3 leading-relaxed">
                  {deployed
                    ? `Shares are pro-rata claims on the wrapped entity. On ${LAUNCH.chain} they settle in test ETH and carry no real-world claim.`
                    : 'The primary sale opens once a property is deployed to the contract — on testnet today, on mainnet at launch.'}
                </p>
              </div>
            </Reveal>
          </div>
        </section>

        {/* ── Whitepaper ─────────────────────────────────── */}
        <section id="whitepaper" className="relative py-24 md:py-32 border-y border-white/[0.07] bg-gradient-to-b from-white/[0.015] to-transparent">
          <div className="max-w-5xl mx-auto px-5 md:px-8">
            <Reveal>
              <div className="text-center mb-20">
                <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-5">The Whitepaper</div>
                <h2 className="headline text-6xl md:text-8xl text-white">RENT<br /><span className="text-surf-grad">→ OWN</span></h2>
                <p className="font-serif-ed text-xl text-white/68 max-w-xl mx-auto mt-8">How every rent check stops paying a landlord — and starts buying you the house.</p>
                {/* the same six sections, one page each — linkable, and readable without the scroll */}
                <Link href="/paper" className="inline-block mt-7 px-5 py-2.5 rounded-full border border-white/15 text-white/70 text-[11px] font-bold uppercase tracking-widest hover:border-coral/50 hover:text-coral transition-colors">
                  Read it section by section →
                </Link>
              </div>
            </Reveal>
            <div className="space-y-20 md:space-y-28">
              {SECTIONS.map((sec) => (
                <Reveal key={sec.no} delay={40}>
                  <div className="grid md:grid-cols-[auto_1fr] gap-6 md:gap-12">
                    <div className="md:text-right md:w-32">
                      <Link href={`/paper/${sec.slug}`} className="block group">
                        <div className="headline text-7xl md:text-8xl text-white/[0.13] leading-none group-hover:text-coral/30 transition-colors">{sec.no}</div>
                        <div className="text-coral text-[11px] font-bold uppercase tracking-[0.2em] mt-1">{sec.kicker}</div>
                        <div className="text-[10px] font-bold uppercase tracking-widest text-white/35 group-hover:text-coral transition-colors mt-2">Permalink →</div>
                      </Link>
                    </div>
                    <div>
                      <h3 className="font-serif-ed text-3xl md:text-4xl text-white leading-tight mb-6">{sec.title}</h3>
                      <div className="space-y-4">
                        {sec.body.map((p, j) => <p key={j} className="text-white/72 text-base md:text-lg leading-relaxed">{p}</p>)}
                      </div>
                      {sec.pull && <p className="font-serif-ed italic text-2xl md:text-3xl text-surf-grad mt-8 leading-snug">“{sec.pull}”</p>}
                    </div>
                  </div>
                </Reveal>
              ))}
            </div>

            {/* ── Interactive equity simulator ──────────── */}
            <Reveal>
              <div className="mt-28">
                <div className="text-center mb-8">
                  <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-3">Drag the timeline</div>
                  <h3 className="headline text-4xl md:text-5xl text-white">Watch your equity fill up.</h3>
                  <p className="font-serif-ed text-lg text-white/68 max-w-xl mx-auto mt-4">Every payment lays a brick. Scrub through the months and watch the house become yours — while the owner's idle funds earn lowfi yield.</p>
                </div>
                <EquitySimulator feePct={terms?.fee_pct ?? 2.5} creditPct={terms?.credit_pct ?? 100} />
              </div>
            </Reveal>

            <Reveal>
              <div className="mt-28">
                <h3 className="headline text-3xl text-white mb-8 text-center">The mechanics, in five numbers</h3>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                  {TOKENOMICS.map((t, i) => (
                    <Tilt key={i} max={10}>
                      <div className="glass glass-hover rounded-2xl p-6 text-center h-full">
                        <div className="text-[10px] uppercase tracking-widest text-white/58 font-bold mb-2">{t.label}</div>
                        <div className="font-display font-extrabold text-xl text-coral mb-1">{t.value}</div>
                        <div className="text-[11px] text-white/55">{t.note}</div>
                      </div>
                    </Tilt>
                  ))}
                </div>
              </div>
            </Reveal>

            <Reveal>
              <div className="mt-24">
                <h3 className="headline text-3xl text-white mb-3 text-center">The road to a city you can own</h3>
                <p className="text-white/60 text-sm text-center max-w-lg mx-auto mb-10">
                  Where we actually are today: {LAUNCH.stage.toLowerCase()} on {LAUNCH.chain}.
                  Mainnet launch date — {LAUNCH.date.toLowerCase()}.
                </p>
                <div className="space-y-3">
                  {ROADMAP.map((r, i) => (
                    <div key={i} className={`flex items-start gap-5 rounded-2xl p-5 border ${r.done ? 'border-emerald-500/25 bg-emerald-500/[0.04]' : 'border-white/[0.07] bg-white/[0.015]'}`}>
                      <div className={`mt-0.5 w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-sm font-black ${r.done ? 'bg-emerald-400 text-onaccent' : 'bg-white/10 text-white/60'}`}>{r.done ? '✓' : i + 1}</div>
                      <div className="flex-1">
                        <div className="flex items-center gap-3 flex-wrap">
                          <span className="text-[10px] font-bold uppercase tracking-widest text-coral">{r.phase}</span>
                          <h4 className="font-display font-bold text-white">{r.title}</h4>
                        </div>
                        <p className="text-white/65 text-sm mt-1">{r.detail}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </Reveal>
          </div>
        </section>

        {/* ── The landscape — everyone else, honestly ────── */}
        <section id="landscape" className="max-w-6xl mx-auto px-5 md:px-8 py-24 md:py-32">
          <Reveal>
            <div className="mb-12">
              <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mb-3">The Landscape</div>
              <h2 className="headline text-5xl md:text-7xl text-white">Everyone else<br />tokenized the <span className="text-surf-grad">landlord.</span></h2>
              <p className="text-white/65 text-base md:text-lg max-w-2xl mt-6 leading-relaxed">
                Twelve projects that put housing on a chain, sorted by the only question that
                matters: who ends up owning the house. Live numbers pulled from public endpoints;
                every editorial claim carries its source.
              </p>
            </div>
          </Reveal>
          <Landscape data={compare} loading={comparing} onRefresh={() => fetchCompare(true)} />
        </section>

        {/* ── Code & contracts ───────────────────────────── */}
        <section id="code" className="max-w-6xl mx-auto px-5 md:px-8 py-24 md:py-32">
          <Reveal>
            <div className="text-center mb-12">
              <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-4">Open Source · No Black Box</div>
              <h2 className="headline text-5xl md:text-7xl text-white">Read the contract.</h2>
              <p className="font-serif-ed text-xl text-white/68 max-w-2xl mx-auto mt-6">
                The whole thing is right here — the Solidity that holds your shares, the logic that splits the rent, the API that serves it. No trust required. Verify.
              </p>
            </div>
          </Reveal>
          <Reveal delay={80}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
              {[
                { k: 'Contract', v: 'OpenHouse.sol' },
                { k: 'Network', v: `${LAUNCH.chain} · ${LAUNCH.chainId}` },
                { k: 'Stage', v: `${LAUNCH.stage} · launch TBA` },
                { k: 'License', v: 'MIT' },
              ].map((s, i) => (
                <div key={i} className="glass rounded-xl p-4">
                  <div className="text-[10px] uppercase tracking-widest text-white/58 font-bold mb-1">{s.k}</div>
                  <div className="font-mono text-sm text-coral truncate">{s.v}</div>
                </div>
              ))}
            </div>
          </Reveal>
          <Reveal delay={140}>
            <CodeViewer files={sources} />
          </Reveal>
        </section>

        {/* ── Cap table ──────────────────────────────────── */}
        <section id="captable" className="max-w-6xl mx-auto px-5 md:px-8 py-24 md:py-32">
          <Reveal>
            <div className="text-center mb-14">
              <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-4">Radical Transparency</div>
              <h2 className="headline text-5xl md:text-7xl text-white">The cap table is public.</h2>
            </div>
          </Reveal>
          <div className="grid lg:grid-cols-2 gap-6">
            <Reveal delay={60}>
              <div className="glass rounded-3xl overflow-hidden h-full">
                <div className="px-6 py-4 border-b border-white/[0.07] flex items-center justify-between">
                  <h3 className="font-display font-bold text-white uppercase tracking-wider text-sm">Owners</h3>
                  <span className="text-[11px] text-white/58">{shareholders.length} on the cap table</span>
                </div>
                {shareholders.length === 0 ? (
                  <div className="py-20 text-center text-white/50 text-sm uppercase tracking-widest">Be the first owner</div>
                ) : (
                  <div className="divide-y divide-white/[0.04] max-h-[28rem] overflow-y-auto">
                    {shareholders.map(sh => (
                      <button key={sh.address} onClick={() => { navigator.clipboard.writeText(sh.address); toast.success('Address copied') }} className="w-full grid grid-cols-[1fr_auto_auto] gap-4 items-center px-6 py-4 hover:bg-white/[0.03] transition-colors text-left">
                        <span className="font-mono text-xs text-white/72 truncate" title={sh.address}>{sh.address}</span>
                        <span className="text-xs text-coral font-bold tabular-nums">{formatNum(sh.shares, 0)} sh</span>
                        <span className="text-xs text-white/60 tabular-nums w-14 text-right">{sh.ownership_pct}%</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </Reveal>
            <Reveal delay={140}>
              <div className="glass rounded-3xl overflow-hidden h-full">
                <div className="px-6 py-4 border-b border-white/[0.07] flex items-center justify-between">
                  <h3 className="font-display font-bold text-white uppercase tracking-wider text-sm">Distributions</h3>
                  <span className="text-[11px] text-white/58">{formatNum(status?.total_dividends_distributed ?? 0)} Ξ paid</span>
                </div>
                {dividends.length === 0 ? (
                  <div className="py-20 text-center text-white/50 text-sm uppercase tracking-widest">Rent flows here</div>
                ) : (
                  <div className="divide-y divide-white/[0.04] max-h-[28rem] overflow-y-auto">
                    {[...dividends].reverse().map((d, i) => (
                      <div key={i} className="grid grid-cols-[1fr_auto_auto] gap-4 items-center px-6 py-4">
                        <span className="text-xs text-white/65">{timeAgo(d.timestamp)}</span>
                        <span className="text-xs text-emerald-400 font-bold tabular-nums">{formatNum(d.total_amount)} Ξ</span>
                        <span className="text-[11px] text-white/58 w-20 text-right">{d.recipients} owners</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Reveal>
          </div>
        </section>

        {/* ── Closing CTA ────────────────────────────────── */}
        <section className="max-w-5xl mx-auto px-5 md:px-8 pb-32 text-center">
          <Reveal>
            <h2 className="headline text-6xl md:text-9xl text-white leading-[0.9] glow-warm">THE DOOR<br />IS <span className="text-surf-grad">OPEN.</span></h2>
            <p className="font-serif-ed text-xl text-white/68 max-w-lg mx-auto mt-8">Stop renting the dream. Own the building it lives in.</p>
            <a href="#invest" className="btn-shine inline-block mt-10 px-10 py-4 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
              {deployed ? 'Start owning →' : 'Open the testnet →'}
            </a>
            <p className="text-white/58 text-xs uppercase tracking-[0.2em] font-bold mt-6">
              {LAUNCH.stage} today on {LAUNCH.chain} · Mainnet launch {LAUNCH.date.toLowerCase()}
            </p>
          </Reveal>
        </section>

        {/* ── Footer ─────────────────────────────────────── */}
        <footer className="border-t border-white/[0.07]">
          <div className="max-w-6xl mx-auto px-5 md:px-8 pt-10 pb-6 flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2.5">
              <span className="w-7 h-7 rounded-md bg-gradient-to-br from-coral to-ember flex items-center justify-center text-onaccent font-black text-xs">⌂</span>
              <span className="font-display font-extrabold text-white text-sm">OpenHouse</span>
            </div>
            <p className="text-white/55 text-xs text-center">Rent-to-own on-chain · Fractional property via smart contracts · Base</p>
            <p className="text-white/45 text-[11px] uppercase tracking-widest">Equity for everybody</p>
          </div>
          <div className="max-w-6xl mx-auto px-5 md:px-8 pb-10">
            <p className="text-white/55 text-[11px] leading-relaxed text-center max-w-2xl mx-auto border-t border-white/[0.05] pt-6">
              <span className="text-coral font-bold uppercase tracking-widest">{LAUNCH.stage} — </span>
              {LAUNCH.notice} Nothing on this page is an offer to sell securities or financial advice.
            </p>
          </div>
        </footer>
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(OpenHousePageInner), { ssr: false })
