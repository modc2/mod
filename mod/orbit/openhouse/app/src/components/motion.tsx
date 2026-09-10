/* The three bits of motion the pages share. Client-only by nature — each
   one measures the viewport or the pointer. */

"use client";

import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { formatNum } from '../lib/api'

/** Fade-and-rise once, the first time it comes into view. */
export function Reveal({ children, delay = 0, className = '' }: { children: ReactNode; delay?: number; className?: string }) {
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

/** Count-up that re-animates when the async value lands. */
export function Counter({ value, decimals = 0, suffix = '' }: { value: number; decimals?: number; suffix?: string }) {
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

/** 3D tilt toward the pointer (vanilla — no library, no re-render). */
export function Tilt({ children, className = '', max = 6 }: { children: ReactNode; className?: string; max?: number }) {
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

/** Procedural NYC skyline — deterministic, so it doesn't reshuffle on nav. */
export function Skyline({ shift = 0 }: { shift?: number }) {
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

/** One payment, cut three ways — the shape the whole protocol argues about. */
export function SplitBar({ amount, fee, credit, owner, className = '', big = false }: {
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
