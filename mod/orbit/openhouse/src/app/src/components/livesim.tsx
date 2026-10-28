/* The front door's proof: one simulation, played while you watch.

   On mount it asks the node to run the default deal (POST /simulate — the
   same throwaway-store engine the /simulator page drives) and then plays the
   answer back month by month: the house fills brick by brick, the curve
   draws itself, the ticker reads out what the protocol did. Nothing here is
   arithmetic in the browser; every number came back from the real code. */

"use client";

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { SimResult, api } from '../lib/api'
import { useCurrency } from '../lib/currency'

/** Walk an index from 1 to `n` over `ms`, on demand. Returns the index, a
 *  play() that restarts it, a stop(), and whether it's running. Respects
 *  prefers-reduced-motion by jumping straight to the end. */
export function usePlayhead(n: number, ms = 4200) {
  const [i, setI] = useState(n)
  const [playing, setPlaying] = useState(false)
  const raf = useRef(0)

  const stop = useCallback(() => { cancelAnimationFrame(raf.current); setPlaying(false) }, [])
  const play = useCallback((from = 1) => {
    cancelAnimationFrame(raf.current)
    if (n < 1) return
    if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) { setI(n); return }
    const start = performance.now()
    const span = Math.max(ms * (1 - (from - 1) / n), 300)
    setPlaying(true)
    const tick = (now: number) => {
      const k = Math.min((now - start) / span, 1)
      const eased = 1 - Math.pow(1 - k, 2.2)
      setI(Math.max(1, Math.round(from + (n - from) * eased)))
      if (k < 1) raf.current = requestAnimationFrame(tick)
      else setPlaying(false)
    }
    raf.current = requestAnimationFrame(tick)
  }, [n, ms])

  useEffect(() => () => cancelAnimationFrame(raf.current), [])
  return { i, setI, play, stop, playing }
}

const DEAL = { years: 20, home_price: 120, monthly_rent: 0.62, model: 'full_credit', fee_pct: 2.5, renters: 1, apy: 5, bank: true }

/** 100 bricks; the filled ones are the share of the house you own. */
export function Bricks({ pct, size = 'w-[7.5rem]' }: { pct: number; size?: string }) {
  const filled = Math.round(pct)
  return (
    <div className={`grid grid-cols-10 gap-[3px] ${size}`} aria-hidden>
      {Array.from({ length: 100 }, (_, k) => {
        const r = 9 - Math.floor(k / 10), c = k % 10, idx = r * 10 + c   // fill from the ground up
        return (
          <div key={k} className="aspect-[3/2] rounded-[2px] transition-colors duration-200"
            style={{ background: idx < filled ? 'linear-gradient(135deg,var(--peach),var(--coral))' : 'rgb(var(--ink-rgb) / 0.07)' }} />
        )
      })}
    </div>
  )
}

function Spark({ sim, at }: { sim: SimResult; at: number }) {
  const W = 320, H = 92, rows = sim.months, n = rows.length
  const x = (m: number) => (n <= 1 ? 0 : ((m - 1) / (n - 1)) * W)
  const y = (p: number) => H - 4 - (Math.min(p, 100) / 100) * (H - 10)
  const upto = rows.slice(0, at)
  const line = upto.map((r, k) => `${k ? 'L' : 'M'}${x(r.m).toFixed(1)},${y(r.owned_pct).toFixed(1)}`).join('')
  const area = upto.length ? `${line}L${x(upto[upto.length - 1].m).toFixed(1)},${H}L0,${H}Z` : ''
  const cur = upto[upto.length - 1]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto overflow-visible" aria-label="Share of the home owned, month by month">
      <defs>
        <linearGradient id="ls-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--coral)" stopOpacity="0.32" />
          <stop offset="100%" stopColor="var(--coral)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[25, 50, 75, 100].map(p => (
        <line key={p} x1={0} x2={W} y1={y(p)} y2={y(p)} stroke="rgb(var(--ink-rgb) / 0.08)" strokeDasharray={p === 100 ? '0' : '3 4'} />
      ))}
      <text x={W} y={y(100) - 4} textAnchor="end" className="fill-current text-white/45" fontSize="9" fontWeight="700" letterSpacing="1">100% YOURS</text>
      {area && <path d={area} fill="url(#ls-fill)" />}
      {line && <path d={line} fill="none" stroke="var(--coral)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />}
      {cur && <circle cx={x(cur.m)} cy={y(cur.owned_pct)} r="5" fill="var(--coral)" stroke="var(--paper)" strokeWidth="2" />}
    </svg>
  )
}

export function LiveSim() {
  const [sim, setSim] = useState<SimResult | null>(null)
  const [err, setErr] = useState('')
  const { fiat } = useCurrency()
  const n = sim?.months.length ?? 0
  const head = usePlayhead(n, 5200)

  useEffect(() => {
    let live = true
    api('simulate', { method: 'POST', body: DEAL })
      .then((r: SimResult) => { if (live) setSim(r) })
      .catch((e: any) => { if (live) setErr(e?.message || 'the node did not answer') })
    return () => { live = false }
  }, [])
  // play it once, as soon as it lands
  useEffect(() => { if (n) { head.setI(1); head.play(1) } }, [n]) // eslint-disable-line react-hooks/exhaustive-deps

  const cur = sim?.months[Math.min(Math.max(head.i, 1), n) - 1]
  const said = sim?.events.filter(e => cur && e.month <= cur.m && e.kind !== 'quarter').slice(-1)[0]
  const quarters = sim ? sim.quarters.filter(q => cur && q.month <= cur.m).length : 0
  const yours = (cur?.owned_pct ?? 0) >= 100

  return (
    <div className="glass rounded-3xl p-6 md:p-7 shadow-2xl shadow-coral/10 relative overflow-hidden">
      <div className="flex items-center justify-between gap-3 mb-5">
        <span className="inline-flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.22em] text-coral">
          <span className={`w-1.5 h-1.5 rounded-full bg-coral ${head.playing ? 'animate-pulse' : ''}`} />
          Live simulation
        </span>
        <span className="font-mono text-[11px] text-white/55 tabular-nums">
          {cur ? `year ${Math.ceil(cur.m / 12)} · month ${cur.m}` : err ? 'offline' : 'running…'}
        </span>
      </div>

      {err ? (
        <p className="text-sm text-white/65 leading-relaxed">The node isn&apos;t answering right now ({err}). The simulator page runs the same engine.</p>
      ) : (
        <>
          <div className="flex items-end justify-between gap-5">
            <div>
              <div className="headline text-surf-grad leading-none text-6xl tabular-nums">
                {(cur?.owned_pct ?? 0).toFixed(0)}<span className="text-3xl">%</span>
              </div>
              <div className="text-[11px] font-bold uppercase tracking-widest text-white/60 mt-2">
                {yours ? 'The house is yours' : 'of the home is yours'}
              </div>
            </div>
            <Bricks pct={cur?.owned_pct ?? 0} />
          </div>

          <div className="mt-5">{sim && <Spark sim={sim} at={Math.max(head.i, 1)} />}</div>

          <div className="grid grid-cols-3 gap-2 mt-4">
            {[
              { k: 'rent paid', v: cur?.paid ?? 0, c: 'text-white' },
              { k: 'your equity', v: cur?.equity ?? 0, c: 'text-coral' },
              { k: `fee · ${sim?.terms.fee_pct ?? 2.5}%`, v: cur?.fees ?? 0, c: 'text-pink' },
            ].map(t => (
              <div key={t.k} className="rounded-xl bg-white/[0.03] border border-white/[0.06] px-3 py-2.5">
                <div className={`font-display font-extrabold text-[15px] tabular-nums ${t.c}`}>{t.v.toFixed(1)} Ξ</div>
                <div className="text-[10px] text-white/50 font-mono tabular-nums truncate">{fiat(t.v) ? `≈ ${fiat(t.v)}` : ' '}</div>
                <div className="text-[9px] uppercase tracking-widest text-white/55 mt-0.5">{t.k}</div>
              </div>
            ))}
          </div>

          <div className="mt-4 min-h-[2.75rem] text-[13px] leading-snug text-white/75 border-l-2 border-coral/50 pl-3">
            {said ? said.says : `Rent arrives by bank transfer; the node books it to the renter. ${quarters ? `${quarters} quarter${quarters > 1 ? 's' : ''} closed so far.` : ''}`}
          </div>

          <div className="text-[10px] text-white/45 font-mono tabular-nums mt-3">
            {sim ? `20 years · ${sim.bank?.booked ?? sim.end.payments} bank transfers booked · ${sim.end.quarters_closed} quarters closed · ran in ${sim.elapsed_ms} ms` : ''}
          </div>

          <div className="flex items-center justify-between gap-3 mt-4 pt-4 border-t border-white/[0.07] whitespace-nowrap">
            <button onClick={() => head.play(1)} disabled={!sim || head.playing}
              className="text-[11px] font-bold uppercase tracking-widest text-white/60 hover:text-coral disabled:opacity-40 transition-colors">
              {head.playing ? 'Playing…' : '↻ Replay'}
            </button>
            <Link href="/simulator" className="text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">Try your numbers →</Link>
          </div>
        </>
      )}
    </div>
  )
}
