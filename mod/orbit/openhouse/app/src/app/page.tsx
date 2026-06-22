"use client";

import { useState, useEffect, useCallback, useRef, useMemo, ReactNode } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import {
  MANIFESTO, ABSTRACT, SECTIONS, TOKENOMICS, ROADMAP, TICKER,
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
          {b.spire && <span className="absolute -top-5 left-1/2 -translate-x-1/2 w-px h-5 bg-[#e8c07d]/40" />}
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
    return <div className="glass rounded-3xl py-20 text-center text-white/30 text-sm uppercase tracking-widest">Loading source…</div>
  }
  const f = files[Math.min(active, files.length - 1)]
  const lines = f.content.replace(/\n$/, '').split('\n')
  const langLabel: Record<string, string> = { solidity: 'Solidity', python: 'Python', typescript: 'TypeScript' }
  return (
    <div className="rounded-2xl overflow-hidden border border-white/10 bg-[#0b0b10] shadow-2xl shadow-black/50">
      {/* tab strip */}
      <div className="flex items-stretch overflow-x-auto border-b border-white/[0.07] bg-white/[0.02]">
        {files.map((file, i) => (
          <button key={file.name} onClick={() => setActive(i)}
            className={`px-4 py-3 text-xs font-mono whitespace-nowrap border-r border-white/[0.05] transition-colors ${i === active ? 'bg-white/[0.06] text-[#e8c07d]' : 'text-white/40 hover:text-white/70'}`}>
            {file.name.split('/').pop()}
          </button>
        ))}
      </div>
      {/* chrome bar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.05] bg-white/[0.015]">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" /><span className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" /><span className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
          <span className="ml-3 text-[11px] font-mono text-white/45">{f.name}</span>
          <span className="text-[10px] uppercase tracking-widest text-[#e8c07d] border border-[#e8c07d]/25 rounded px-1.5 py-0.5">{langLabel[f.language] || f.language}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-white/30 hidden sm:inline">{f.lines} lines · {(f.bytes / 1024).toFixed(1)} KB</span>
          <button onClick={() => { navigator.clipboard.writeText(f.content); toast.success(`${f.name} copied`) }}
            className="text-[10px] font-bold uppercase tracking-widest text-white/40 hover:text-[#e8c07d] transition-colors">Copy</button>
        </div>
      </div>
      <p className="px-4 py-2.5 text-xs text-white/40 border-b border-white/[0.05] bg-white/[0.01]">{f.description}</p>
      {/* code body */}
      <div className="overflow-auto max-h-[34rem] text-[12.5px] leading-[1.55] font-mono">
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((ln, i) => (
              <tr key={i} className="hover:bg-white/[0.025]">
                <td className="select-none text-right pr-4 pl-4 text-white/20 w-12 align-top tabular-nums sticky left-0 bg-[#0b0b10]">{i + 1}</td>
                <td className="pr-6 text-white/75 whitespace-pre align-top">{ln || ' '}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
      <label className="text-[10px] uppercase tracking-widest text-white/40 font-bold mb-1.5 block">{label}</label>
      <div className="flex items-center rounded-xl border border-white/10 bg-white/5 focus-within:border-[#e8c07d]/50 transition-colors">
        <input type="number" min={min} max={max} step={step} value={value}
          onChange={e => set(Math.max(min, Math.min(max, parseFloat(e.target.value) || 0)))}
          className="w-full bg-transparent text-white text-sm px-3 py-2.5 font-mono focus:outline-none" />
        <span className="text-white/30 text-xs pr-3 font-mono">{unit}</span>
      </div>
    </div>
  )
}

function EquitySimulator() {
  const [price, setPrice] = useState(120)     // home price, Ξ
  const [monthly, setMonthly] = useState(2)   // monthly payment, Ξ
  const [apy, setApy] = useState(5)           // lowfi APY, %
  const monthsToOwn = Math.max(1, Math.ceil(price / monthly))
  const [month, setMonth] = useState(18)

  useEffect(() => { setMonth(m => Math.min(m, monthsToOwn)) }, [monthsToOwn])

  const principalPaid = Math.min(month * monthly, price)
  const ownPct = price > 0 ? (principalPaid / price) * 100 : 0
  const remaining = Math.max(price - principalPaid, 0)

  // Owner's lowfi yield: each month's deposit compounds at the APY while it sits.
  let bal = 0, yieldEarned = 0
  const r = apy / 100 / 12
  for (let i = 0; i < month; i++) {
    if (i * monthly < price) bal += Math.min(monthly, price - i * monthly)
    const interest = bal * r
    yieldEarned += interest
    bal += interest
  }

  const filled = Math.round(ownPct)
  const owned = ownPct >= 100

  return (
    <div className="glass rounded-3xl p-6 md:p-9 border-[#e8c07d]/15">
      <div className="flex flex-wrap items-end gap-4 mb-7">
        <SimField label="Home price" value={price} set={setPrice} min={1} max={100000} step={1} unit="Ξ" />
        <SimField label="Monthly payment" value={monthly} set={setMonthly} min={0.01} max={10000} step={0.1} unit="Ξ" />
        <SimField label="lowfi APY" value={apy} set={setApy} min={0} max={50} step={0.5} unit="%" />
      </div>

      <div className="grid md:grid-cols-[1.3fr_1fr] gap-7 items-center">
        {/* Brick wall */}
        <div>
          <div className="grid grid-cols-10 gap-1.5 mb-4">
            {Array.from({ length: 100 }, (_, i) => (
              <div key={i} className="aspect-[3/2] rounded-[3px] transition-all duration-300"
                style={{
                  background: i < filled ? 'linear-gradient(135deg,#f5d9a8,#e8c07d)' : 'rgba(255,255,255,0.05)',
                  boxShadow: i < filled ? '0 0 8px rgba(232,192,125,0.45)' : 'none',
                  transitionDelay: `${Math.min(i, 60) * 4}ms`,
                }} />
            ))}
          </div>
          <input type="range" min={0} max={monthsToOwn} value={month}
            onChange={e => setMonth(parseInt(e.target.value))}
            className="w-full accent-[#e8c07d] cursor-pointer" />
          <div className="flex justify-between text-[11px] text-white/35 mt-1.5 font-mono">
            <span>Move in</span>
            <span className="text-[#e8c07d] font-bold">Month {month} · year {(month / 12).toFixed(1)}</span>
            <span>Owned</span>
          </div>
        </div>

        {/* Live readout */}
        <div className="space-y-4">
          <div>
            <div className="headline text-6xl text-gold-grad leading-none">{ownPct.toFixed(1)}<span className="text-3xl">%</span></div>
            <div className="text-[11px] uppercase tracking-widest text-white/40 font-bold mt-1">{owned ? 'The house is yours' : 'of the home is yours'}</div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
              <div className="text-sm font-bold text-white tabular-nums">{principalPaid.toFixed(1)} Ξ</div>
              <div className="text-[10px] uppercase tracking-widest text-white/35 mt-0.5">principal paid</div>
            </div>
            <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
              <div className="text-sm font-bold text-emerald-400 tabular-nums">{remaining.toFixed(1)} Ξ</div>
              <div className="text-[10px] uppercase tracking-widest text-white/35 mt-0.5">left to own</div>
            </div>
            <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
              <div className="text-sm font-bold text-[#e8c07d] tabular-nums">{monthsToOwn} mo</div>
              <div className="text-[10px] uppercase tracking-widest text-white/35 mt-0.5">to own outright</div>
            </div>
            <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
              <div className="text-sm font-bold text-[#ff3d81] tabular-nums">{yieldEarned.toFixed(2)} Ξ</div>
              <div className="text-[10px] uppercase tracking-widest text-white/35 mt-0.5">owner's lowfi yield</div>
            </div>
          </div>
          <p className="text-[10px] text-white/25 leading-relaxed">
            Illustrative projection from your inputs — not live data or financial advice. Real stakes come straight from the contract.
          </p>
        </div>
      </div>
    </div>
  )
}

const NAV = [
  { id: 'manifesto', label: 'Manifesto' },
  { id: 'invest', label: 'Invest' },
  { id: 'whitepaper', label: 'Whitepaper' },
  { id: 'code', label: 'Code' },
  { id: 'captable', label: 'Cap Table' },
]

function OpenHousePageInner() {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [property, setProperty] = useState<PropertyData | null>(null)
  const [shareholders, setShareholders] = useState<Shareholder[]>([])
  const [dividends, setDividends] = useState<DividendRecord[]>([])
  const [sources, setSources] = useState<SourceFile[]>([])
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
      const [s, p, sh, d] = await Promise.all([
        api('status').catch(() => null),
        api('property').catch(() => null),
        api('shareholders').catch(() => []),
        api('dividends').catch(() => []),
      ])
      if (s) setStatus(s)
      if (p) setProperty(p)
      setShareholders(Array.isArray(sh) ? sh : [])
      setDividends(Array.isArray(d) ? d : [])
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])
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

  return (
    <div className="relative grain vignette min-h-screen">
      <div className="aurora" />
      <div className="scrollbar-prog" style={{ width: `${progress}%` }} />

      {/* ── Nav ─────────────────────────────────────────── */}
      <nav className={`fixed top-0 inset-x-0 z-50 transition-all duration-500 ${scrolled ? 'bg-[#050507]/80 backdrop-blur-xl border-b border-white/[0.07]' : 'bg-transparent'}`}>
        <div className="max-w-6xl mx-auto px-5 md:px-8 h-16 flex items-center justify-between">
          <a href="#top" className="flex items-center gap-2.5">
            <span className="w-8 h-8 rounded-md bg-gradient-to-br from-[#e8c07d] to-[#b8893f] flex items-center justify-center text-[#0a0a0a] font-black text-sm shadow-lg shadow-[#e8c07d]/20">⌂</span>
            <span className="font-display font-extrabold tracking-tight text-[15px] text-white">OpenHouse</span>
          </a>
          <div className="hidden md:flex items-center gap-7 text-[12px] font-semibold uppercase tracking-widest text-white/45">
            {NAV.map(n => <a key={n.id} href={`#${n.id}`} className="hover:text-[#e8c07d] transition-colors">{n.label}</a>)}
          </div>
          <div className="flex items-center gap-3">
            {status?.deployed && status.available_shares > 0 && (
              <span className="hidden sm:flex items-center gap-1.5 text-[11px] font-bold text-white/50">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                {formatNum(status.available_shares)} left
              </span>
            )}
            <a href="#invest" className="btn-shine px-4 py-2 rounded-full bg-white text-[#0a0a0a] text-[11px] font-bold uppercase tracking-widest hover:bg-[#e8c07d] transition-colors">Buy In</a>
          </div>
        </div>
      </nav>

      <div id="top" className="relative z-10">

        {/* ── Hero ───────────────────────────────────────── */}
        <header ref={heroRef} onMouseMove={onHeroMove} className="relative min-h-screen flex flex-col justify-center overflow-hidden">
          <Skyline shift={scrollY * 0.25} />
          <div className="spotlight" />
          <div className="absolute inset-x-0 bottom-0 h-[46vh] bg-gradient-to-t from-[#050507] via-[#050507]/60 to-transparent z-[1]" />

          <div className="relative z-10 px-5 md:px-8 pt-24 pb-16 max-w-6xl mx-auto w-full">
            <Reveal>
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full glass text-[11px] font-bold uppercase tracking-[0.2em] text-[#e8c07d] mb-8">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Live on Base · Collective Ownership Protocol
              </div>
            </Reveal>
            <Reveal delay={80}>
              <h1 className="headline text-[16vw] md:text-[8.5rem] leading-[0.85] text-white glow-gold">
                OWN THE<br /><span className="text-gold-grad">SKYLINE.</span>
              </h1>
            </Reveal>
            <Reveal delay={180}>
              <p className="font-serif-ed text-2xl md:text-3xl text-white/70 max-w-2xl mt-8 leading-snug">
                You've paid someone else's mortgage long enough.
                <span className="text-white"> OpenHouse is rent-to-own, on-chain</span> — every payment
                becomes principal in the home, redistributed quarterly. Pay it off, own it.
              </p>
            </Reveal>
            <Reveal delay={280}>
              <div className="flex flex-wrap items-center gap-4 mt-10">
                <a href="#invest" className="btn-shine px-7 py-3.5 rounded-full bg-gradient-to-r from-[#f5d9a8] to-[#e8c07d] text-[#0a0a0a] font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-[#e8c07d]/40 transition-shadow">Start owning →</a>
                <a href="#whitepaper" className="px-7 py-3.5 rounded-full border border-white/15 text-white/80 font-bold uppercase tracking-widest text-sm hover:border-[#e8c07d]/50 hover:text-white transition-colors">Read the whitepaper</a>
              </div>
            </Reveal>
            <Reveal delay={400}>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-px mt-16 rounded-2xl overflow-hidden glass">
                {[
                  { k: 'Shareholders', v: <Counter value={status?.shareholders ?? 0} />, c: 'text-white' },
                  { k: 'Shares Sold', v: <Counter value={status?.shares_sold ?? 0} />, c: 'text-[#e8c07d]' },
                  { k: 'Contributed', v: <Counter value={status?.total_contributed ?? 0} decimals={2} suffix=" Ξ" />, c: 'text-emerald-400' },
                  { k: 'Dividends Paid', v: <Counter value={status?.total_dividends_distributed ?? 0} decimals={2} suffix=" Ξ" />, c: 'text-[#ff3d81]' },
                ].map((s, i) => (
                  <div key={i} className="p-5 md:p-6 bg-white/[0.015]">
                    <div className={`headline text-3xl md:text-4xl ${s.c} tabular-nums`}>{s.v}</div>
                    <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/35 mt-2">{s.k}</div>
                  </div>
                ))}
              </div>
            </Reveal>
          </div>
          <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-10 text-white/25 text-xs tracking-widest uppercase animate-bounce">scroll ↓</div>
        </header>

        {/* ── Times-Square dual ticker ───────────────────── */}
        <div className="border-y border-white/10 bg-white/[0.02] py-5 overflow-hidden space-y-2">
          <div className="marquee">
            {[...TICKER, ...TICKER].map((t, i) => (
              <span key={i} className="headline text-2xl md:text-3xl text-white/15 px-8 whitespace-nowrap flex items-center gap-8">{t} <span className="text-[#e8c07d]/40">✦</span></span>
            ))}
          </div>
          <div className="marquee marquee-rev">
            {[...TICKER].reverse().concat([...TICKER].reverse()).map((t, i) => (
              <span key={i} className="headline text-2xl md:text-3xl px-8 whitespace-nowrap flex items-center gap-8" style={{ WebkitTextStroke: '1px rgba(232,192,125,0.25)', color: 'transparent' }}>{t} <span className="text-[#ff3d81]/30">●</span></span>
            ))}
          </div>
        </div>

        {/* ── Manifesto ──────────────────────────────────── */}
        <section id="manifesto" className="max-w-5xl mx-auto px-5 md:px-8 py-28 md:py-40">
          {MANIFESTO.map((line, i) => (
            <Reveal key={i} delay={i * 120}>
              <p className={`font-serif-ed text-4xl md:text-6xl leading-[1.05] mb-3 ${i === MANIFESTO.length - 1 ? 'text-gold-grad font-black' : 'text-white/40'}`}>{line}</p>
            </Reveal>
          ))}
          <Reveal delay={500}>
            <p className="text-white/60 text-lg md:text-xl max-w-2xl mt-12 leading-relaxed border-l-2 border-[#e8c07d]/40 pl-6">{ABSTRACT}</p>
          </Reveal>
        </section>

        {/* ── Invest / live dashboard ────────────────────── */}
        <section id="invest" className="max-w-6xl mx-auto px-5 md:px-8 py-20 md:py-28">
          <Reveal>
            <div className="flex items-end justify-between flex-wrap gap-4 mb-12">
              <div>
                <div className="text-[#e8c07d] text-[11px] font-bold uppercase tracking-[0.25em] mb-3">The Building</div>
                <h2 className="headline text-5xl md:text-7xl text-white">Rent toward ownership.</h2>
              </div>
              <button onClick={fetchAll} disabled={loading} className="px-4 py-2 rounded-full border border-white/12 text-[11px] font-bold uppercase tracking-widest text-white/50 hover:text-white hover:border-white/30 disabled:opacity-30 transition-colors">
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
                      <p className="text-white/40 text-sm">{deployed ? (property!.contract || 'On-chain') : 'Deploy a property to open the float'}</p>
                    </div>
                    <span className={`text-[11px] font-bold uppercase tracking-widest px-3 py-1.5 rounded-full border ${deployed && property!.is_active ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10' : 'text-white/40 border-white/15'}`}>
                      {deployed ? (property!.is_active ? '● Active' : 'Paused') : 'Not deployed'}
                    </span>
                  </div>
                  {deployed ? (
                    <>
                      <div className="mb-8">
                        <div className="flex justify-between text-xs mb-2">
                          <span className="text-white/40 uppercase tracking-widest font-bold">Float sold</span>
                          <span className="text-[#e8c07d] font-bold">{soldPct.toFixed(1)}%</span>
                        </div>
                        <div className="h-3 rounded-full bg-white/[0.06] overflow-hidden">
                          <div className="h-full rounded-full bg-gradient-to-r from-[#b8893f] via-[#e8c07d] to-[#f5d9a8] transition-all duration-1000" style={{ width: `${Math.max(soldPct, 1)}%` }} />
                        </div>
                        <div className="flex justify-between text-[11px] text-white/30 mt-2">
                          <span>{formatNum(status!.shares_sold)} sold</span>
                          <span>{formatNum(status!.total_shares)} total supply</span>
                        </div>
                      </div>
                      <div className="grid grid-cols-3 gap-4">
                        {[
                          { k: 'Share price', v: `${property!.share_price} Ξ`, c: 'text-[#e8c07d]' },
                          { k: 'Available', v: formatNum(status!.available_shares), c: 'text-emerald-400' },
                          { k: 'Owners', v: formatNum(status!.shareholders), c: 'text-white' },
                        ].map((s, i) => (
                          <div key={i} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4">
                            <div className={`text-2xl font-display font-extrabold ${s.c} tabular-nums`}>{s.v}</div>
                            <div className="text-[10px] uppercase tracking-widest text-white/35 mt-1 font-bold">{s.k}</div>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="rounded-2xl border border-dashed border-white/12 bg-white/[0.015] p-8 text-center">
                      <div className="text-4xl mb-3 float">⌂</div>
                      <p className="text-white/55 text-sm leading-relaxed max-w-sm mx-auto">
                        No building has been fractionalized on this contract yet. Once a property is deployed,
                        its float, share price, and live ownership show up here — straight from chain, no placeholders.
                      </p>
                      <code className="inline-block mt-4 text-[11px] font-mono text-[#e8c07d]/80 bg-[#e8c07d]/5 border border-[#e8c07d]/15 rounded-lg px-3 py-1.5">openhouse deploy property_details=… total_shares=… share_price=…</code>
                    </div>
                  )}
                </div>
              </Tilt>
            </Reveal>

            <Reveal className="lg:col-span-2" delay={140}>
              <div className="glass rounded-3xl p-7 md:p-9 h-full flex flex-col border-[#e8c07d]/20">
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-[#e8c07d] mb-2">Take a position</div>
                <h3 className="font-serif-ed text-2xl text-white mb-6">Mint your shares</h3>
                <label className="text-[10px] uppercase tracking-widest text-white/40 font-bold mb-1.5 block">Your wallet</label>
                <input type="text" placeholder="0x…" value={buyAddr} onChange={e => setBuyAddr(e.target.value)} className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/20 focus:outline-none focus:border-[#e8c07d]/50 font-mono transition-colors mb-4" />
                <label className="text-[10px] uppercase tracking-widest text-white/40 font-bold mb-1.5 block">Shares</label>
                <input type="number" min="1" placeholder="100" value={buyShares} onChange={e => setBuyShares(e.target.value)} className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/20 focus:outline-none focus:border-[#e8c07d]/50 font-mono transition-colors mb-5" />
                <div className="flex justify-between items-baseline mb-5 pb-5 border-b border-white/10">
                  <span className="text-white/40 text-sm uppercase tracking-widest font-bold">Total</span>
                  <span className="headline text-3xl text-[#e8c07d] tabular-nums">{deployed ? (cost ? cost.toFixed(4) : '0.00') : '—'} {deployed && <span className="text-lg">Ξ</span>}</span>
                </div>
                <button onClick={handlePurchase} disabled={!deployed || purchasing || !buyAddr.trim() || !buyShares.trim()} className="btn-shine w-full py-4 rounded-xl bg-gradient-to-r from-[#f5d9a8] to-[#e8c07d] text-[#0a0a0a] font-bold uppercase tracking-widest text-sm hover:shadow-xl hover:shadow-[#e8c07d]/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all mt-auto">
                  {!deployed ? 'Sale not open yet' : purchasing ? 'Minting…' : 'Own it →'}
                </button>
                <p className="text-[10px] text-white/25 text-center mt-3 leading-relaxed">{deployed ? 'Shares are pro-rata claims on the wrapped entity. Dividends settle on Base.' : 'The primary sale opens once a property is deployed to the contract.'}</p>
              </div>
            </Reveal>
          </div>
        </section>

        {/* ── Whitepaper ─────────────────────────────────── */}
        <section id="whitepaper" className="relative py-24 md:py-32 border-y border-white/[0.07] bg-gradient-to-b from-white/[0.015] to-transparent">
          <div className="max-w-5xl mx-auto px-5 md:px-8">
            <Reveal>
              <div className="text-center mb-20">
                <div className="text-[#e8c07d] text-[11px] font-bold uppercase tracking-[0.3em] mb-5">The Whitepaper</div>
                <h2 className="headline text-6xl md:text-8xl text-white">RENT<br /><span className="text-gold-grad">→ OWN</span></h2>
                <p className="font-serif-ed text-xl text-white/50 max-w-xl mx-auto mt-8">How every rent check stops paying a landlord — and starts buying you the house.</p>
              </div>
            </Reveal>
            <div className="space-y-20 md:space-y-28">
              {SECTIONS.map((sec) => (
                <Reveal key={sec.no} delay={40}>
                  <div className="grid md:grid-cols-[auto_1fr] gap-6 md:gap-12">
                    <div className="md:text-right md:w-32">
                      <div className="headline text-7xl md:text-8xl text-white/[0.08] leading-none">{sec.no}</div>
                      <div className="text-[#e8c07d] text-[11px] font-bold uppercase tracking-[0.2em] mt-1">{sec.kicker}</div>
                    </div>
                    <div>
                      <h3 className="font-serif-ed text-3xl md:text-4xl text-white leading-tight mb-6">{sec.title}</h3>
                      <div className="space-y-4">
                        {sec.body.map((p, j) => <p key={j} className="text-white/55 text-base md:text-lg leading-relaxed">{p}</p>)}
                      </div>
                      {sec.pull && <p className="font-serif-ed italic text-2xl md:text-3xl text-gold-grad mt-8 leading-snug">“{sec.pull}”</p>}
                    </div>
                  </div>
                </Reveal>
              ))}
            </div>

            {/* ── Interactive equity simulator ──────────── */}
            <Reveal>
              <div className="mt-28">
                <div className="text-center mb-8">
                  <div className="text-[#e8c07d] text-[11px] font-bold uppercase tracking-[0.3em] mb-3">Drag the timeline</div>
                  <h3 className="headline text-4xl md:text-5xl text-white">Watch your equity fill up.</h3>
                  <p className="font-serif-ed text-lg text-white/50 max-w-xl mx-auto mt-4">Every payment lays a brick. Scrub through the months and watch the house become yours — while the owner's idle funds earn lowfi yield.</p>
                </div>
                <EquitySimulator />
              </div>
            </Reveal>

            <Reveal>
              <div className="mt-28">
                <h3 className="headline text-3xl text-white mb-8 text-center">The mechanics, in four numbers</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {TOKENOMICS.map((t, i) => (
                    <Tilt key={i} max={10}>
                      <div className="glass glass-hover rounded-2xl p-6 text-center h-full">
                        <div className="text-[10px] uppercase tracking-widest text-white/35 font-bold mb-2">{t.label}</div>
                        <div className="font-display font-extrabold text-xl text-[#e8c07d] mb-1">{t.value}</div>
                        <div className="text-[11px] text-white/30">{t.note}</div>
                      </div>
                    </Tilt>
                  ))}
                </div>
              </div>
            </Reveal>

            <Reveal>
              <div className="mt-24">
                <h3 className="headline text-3xl text-white mb-10 text-center">The road to a city you can own</h3>
                <div className="space-y-3">
                  {ROADMAP.map((r, i) => (
                    <div key={i} className={`flex items-start gap-5 rounded-2xl p-5 border ${r.done ? 'border-emerald-500/25 bg-emerald-500/[0.04]' : 'border-white/[0.07] bg-white/[0.015]'}`}>
                      <div className={`mt-0.5 w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-sm font-black ${r.done ? 'bg-emerald-400 text-[#0a0a0a]' : 'bg-white/10 text-white/40'}`}>{r.done ? '✓' : i + 1}</div>
                      <div className="flex-1">
                        <div className="flex items-center gap-3 flex-wrap">
                          <span className="text-[10px] font-bold uppercase tracking-widest text-[#e8c07d]">{r.phase}</span>
                          <h4 className="font-display font-bold text-white">{r.title}</h4>
                        </div>
                        <p className="text-white/45 text-sm mt-1">{r.detail}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </Reveal>
          </div>
        </section>

        {/* ── Code & contracts ───────────────────────────── */}
        <section id="code" className="max-w-6xl mx-auto px-5 md:px-8 py-24 md:py-32">
          <Reveal>
            <div className="text-center mb-12">
              <div className="text-[#e8c07d] text-[11px] font-bold uppercase tracking-[0.3em] mb-4">Open Source · No Black Box</div>
              <h2 className="headline text-5xl md:text-7xl text-white">Read the contract.</h2>
              <p className="font-serif-ed text-xl text-white/50 max-w-2xl mx-auto mt-6">
                The whole thing is right here — the Solidity that holds your shares, the logic that splits the rent, the API that serves it. No trust required. Verify.
              </p>
            </div>
          </Reveal>
          <Reveal delay={80}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
              {[
                { k: 'Contract', v: 'OpenHouse.sol' },
                { k: 'Network', v: 'Base Sepolia · 84532' },
                { k: 'License', v: 'MIT' },
                { k: 'Files shown', v: `${sources.length}` },
              ].map((s, i) => (
                <div key={i} className="glass rounded-xl p-4">
                  <div className="text-[10px] uppercase tracking-widest text-white/35 font-bold mb-1">{s.k}</div>
                  <div className="font-mono text-sm text-[#e8c07d] truncate">{s.v}</div>
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
              <div className="text-[#e8c07d] text-[11px] font-bold uppercase tracking-[0.3em] mb-4">Radical Transparency</div>
              <h2 className="headline text-5xl md:text-7xl text-white">The cap table is public.</h2>
            </div>
          </Reveal>
          <div className="grid lg:grid-cols-2 gap-6">
            <Reveal delay={60}>
              <div className="glass rounded-3xl overflow-hidden h-full">
                <div className="px-6 py-4 border-b border-white/[0.07] flex items-center justify-between">
                  <h3 className="font-display font-bold text-white uppercase tracking-wider text-sm">Owners</h3>
                  <span className="text-[11px] text-white/35">{shareholders.length} on the cap table</span>
                </div>
                {shareholders.length === 0 ? (
                  <div className="py-20 text-center text-white/25 text-sm uppercase tracking-widest">Be the first owner</div>
                ) : (
                  <div className="divide-y divide-white/[0.04] max-h-[28rem] overflow-y-auto">
                    {shareholders.map(sh => (
                      <button key={sh.address} onClick={() => { navigator.clipboard.writeText(sh.address); toast.success('Address copied') }} className="w-full grid grid-cols-[1fr_auto_auto] gap-4 items-center px-6 py-4 hover:bg-white/[0.03] transition-colors text-left">
                        <span className="font-mono text-xs text-white/55 truncate" title={sh.address}>{sh.address}</span>
                        <span className="text-xs text-[#e8c07d] font-bold tabular-nums">{formatNum(sh.shares, 0)} sh</span>
                        <span className="text-xs text-white/40 tabular-nums w-14 text-right">{sh.ownership_pct}%</span>
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
                  <span className="text-[11px] text-white/35">{formatNum(status?.total_dividends_distributed ?? 0)} Ξ paid</span>
                </div>
                {dividends.length === 0 ? (
                  <div className="py-20 text-center text-white/25 text-sm uppercase tracking-widest">Rent flows here</div>
                ) : (
                  <div className="divide-y divide-white/[0.04] max-h-[28rem] overflow-y-auto">
                    {[...dividends].reverse().map((d, i) => (
                      <div key={i} className="grid grid-cols-[1fr_auto_auto] gap-4 items-center px-6 py-4">
                        <span className="text-xs text-white/45">{timeAgo(d.timestamp)}</span>
                        <span className="text-xs text-emerald-400 font-bold tabular-nums">{formatNum(d.total_amount)} Ξ</span>
                        <span className="text-[11px] text-white/35 w-20 text-right">{d.recipients} owners</span>
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
            <h2 className="headline text-6xl md:text-9xl text-white leading-[0.9] glow-gold">THE DOOR<br />IS <span className="text-gold-grad">OPEN.</span></h2>
            <p className="font-serif-ed text-xl text-white/50 max-w-lg mx-auto mt-8">Stop renting the dream. Own the building it lives in.</p>
            <a href="#invest" className="btn-shine inline-block mt-10 px-10 py-4 rounded-full bg-gradient-to-r from-[#f5d9a8] to-[#e8c07d] text-[#0a0a0a] font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-[#e8c07d]/40 transition-shadow">Start owning →</a>
          </Reveal>
        </section>

        {/* ── Footer ─────────────────────────────────────── */}
        <footer className="border-t border-white/[0.07]">
          <div className="max-w-6xl mx-auto px-5 md:px-8 py-10 flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2.5">
              <span className="w-7 h-7 rounded-md bg-gradient-to-br from-[#e8c07d] to-[#b8893f] flex items-center justify-center text-[#0a0a0a] font-black text-xs">⌂</span>
              <span className="font-display font-extrabold text-white text-sm">OpenHouse</span>
            </div>
            <p className="text-white/30 text-xs text-center">Collective asset ownership · Fractional property via smart contracts · Base</p>
            <p className="text-white/20 text-[11px] uppercase tracking-widest">Equity for everybody</p>
          </div>
        </footer>
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(OpenHousePageInner), { ssr: false })
