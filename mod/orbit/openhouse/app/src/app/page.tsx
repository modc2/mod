/* / — the front door.

   One clear hero, one quiet line of the manifesto, one calm directory, one
   close. Everything that used to live below the fold is a route now, so this
   page's only job is to make the case once and point at the pages that back
   it up — without shouting it twice. */

"use client";

import Link from 'next/link'
import dynamic from 'next/dynamic'
import { ReactNode, useEffect, useRef, useState } from 'react'
import { Shell, ROUTES } from '../components/chrome'
import { Counter, Reveal, Skyline } from '../components/motion'
import { StatusData, PropertyData, useResource } from '../lib/api'
import { MANIFESTO, TICKER, LAUNCH } from '../lib/whitepaper'

// The four places worth leading with. The full eight still live in the footer
// and the directory below — the home hero just doesn't open with all of them.
const FEATURED = ['/manifesto', '/simulator', '/invest', '/paper']

function HomeInner() {
  const { data: status } = useResource<StatusData | null>('status', null)
  const { data: property } = useResource<PropertyData | null>('property', null)
  const [scrollY, setScrollY] = useState(0)
  const heroRef = useRef<HTMLElement>(null)

  // The skyline parallaxes against the hero, and nothing else on this page
  // reads the scroll position — the pages took the rest of that work away.
  useEffect(() => {
    const onScroll = () => setScrollY(window.scrollY)
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

  const deployed = !!(property?.deployed && status?.deployed && status.total_shares > 0)

  // Before a property is on-chain the live counters are all zero — a wall of goose
  // eggs reads as broken. Show what's actually true about the stage instead.
  const heroStats: { k: string; v: ReactNode; c: string }[] = deployed ? [
    { k: 'Shareholders', v: <Counter value={status!.shareholders} />, c: 'text-white' },
    { k: 'Shares sold', v: <Counter value={status!.shares_sold} />, c: 'text-coral' },
    { k: 'Contributed', v: <Counter value={status!.total_contributed} decimals={2} suffix=" Ξ" />, c: 'text-emerald-400' },
    { k: 'Dividends paid', v: <Counter value={status!.total_dividends_distributed} decimals={2} suffix=" Ξ" />, c: 'text-pink' },
  ] : [
    { k: 'Stage', v: 'Testnet', c: 'text-coral' },
    { k: 'Network', v: LAUNCH.chain, c: 'text-white' },
    { k: 'Mainnet', v: 'TBA', c: 'text-pink' },
    { k: 'To try', v: 'Test ETH', c: 'text-emerald-400' },
  ]

  const featured = FEATURED
    .map(h => ROUTES.find(r => r.href === h))
    .filter((r): r is (typeof ROUTES)[number] => !!r)

  return (
    <Shell>
      {/* ── Hero ───────────────────────────────────────── */}
      <header ref={heroRef} onMouseMove={onHeroMove}
        className="relative flex flex-col justify-center overflow-hidden min-h-[calc(100vh-4rem)]">
        <Skyline shift={scrollY * 0.25} />
        <div className="spotlight" />
        {/* let the pastel city keep its color — just enough haze to seat the type */}
        <div className="absolute inset-x-0 bottom-0 h-[46vh] bg-gradient-to-t from-paper/80 via-paper/25 to-transparent z-[1]" />

        <div className="relative z-10 px-5 md:px-8 py-20 max-w-5xl mx-auto w-full">
          <Reveal>
            <div className="inline-flex flex-wrap items-center gap-2.5 px-3 py-1.5 rounded-full glass text-[11px] font-bold uppercase tracking-[0.2em] text-coral mb-8">
              <span className="w-1.5 h-1.5 rounded-full bg-coral animate-pulse" />
              {LAUNCH.stage} on {LAUNCH.chain}
            </div>
          </Reveal>
          <Reveal delay={80}>
            <h1 className="headline text-[18vw] md:text-[9rem] leading-[0.85] text-white glow-warm">
              OWN THE<br /><span className="text-surf-grad">SKYLINE.</span>
            </h1>
          </Reveal>
          <Reveal delay={180}>
            <p className="font-serif-ed text-2xl md:text-[1.9rem] text-white/80 max-w-xl mt-8 leading-snug">
              You've paid someone else's mortgage long enough.
              <span className="text-white"> OpenHouse is rent-to-own, on-chain</span> — every
              payment becomes equity in the home you live in.
            </p>
          </Reveal>
          <Reveal delay={280}>
            <div className="flex flex-wrap items-center gap-3 mt-10">
              <Link href="/invest" className="btn-shine px-7 py-3.5 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
                {deployed ? 'Start owning →' : 'Try it on testnet →'}
              </Link>
              <Link href="/paper" className="px-7 py-3.5 rounded-full border border-white/15 text-white/80 font-bold uppercase tracking-widest text-sm hover:border-coral/50 hover:text-white transition-colors">
                Read the whitepaper
              </Link>
            </div>
          </Reveal>
          <Reveal delay={400}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-px mt-14 rounded-2xl overflow-hidden glass max-w-3xl">
              {heroStats.map((s, i) => (
                <div key={i} className="p-5 bg-white/[0.015]">
                  <div className={`headline text-2xl md:text-3xl ${s.c} tabular-nums`}>{s.v}</div>
                  <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/55 mt-2">{s.k}</div>
                </div>
              ))}
            </div>
            {!deployed && (
              <p className="text-white/50 text-xs mt-4 max-w-md leading-relaxed">
                Testnet preview — the contract, the math, and the cap table are real code on {LAUNCH.chain}.
                The money isn't. Mainnet launch {LAUNCH.date.toLowerCase()}.
              </p>
            )}
          </Reveal>
        </div>
      </header>

      {/* ── One quiet ticker ───────────────────────────── */}
      <div className="border-y border-white/10 bg-white/[0.02] py-4 overflow-hidden">
        <div className="marquee">
          {[...TICKER, ...TICKER].map((t, i) => (
            <span key={i} className="headline text-xl md:text-2xl text-white/22 px-8 whitespace-nowrap flex items-center gap-8">
              {t} <span className="text-coral/35">✦</span>
            </span>
          ))}
        </div>
      </div>

      {/* ── The pitch, in three lines ──────────────────── */}
      <section className="max-w-4xl mx-auto px-5 md:px-8 py-24 md:py-28">
        {MANIFESTO.slice(0, 3).map((line, i) => (
          <Reveal key={i} delay={i * 120}>
            <p className="font-serif-ed text-3xl md:text-5xl leading-[1.1] mb-3 text-white/60">{line}</p>
          </Reveal>
        ))}
        <Reveal delay={400}>
          <Link href="/manifesto" className="inline-block mt-7 text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">
            The whole manifesto →
          </Link>
        </Reveal>
      </section>

      {/* ── The directory ──────────────────────────────── */}
      <section className="max-w-5xl mx-auto px-5 md:px-8">
        <Reveal>
          <div className="flex items-end justify-between flex-wrap gap-4 mb-10">
            <h2 className="headline text-4xl md:text-6xl text-white">Start anywhere.</h2>
            <span className="text-white/45 text-[11px] font-bold uppercase tracking-[0.2em] pb-1">Eight doors in</span>
          </div>
        </Reveal>

        {/* Four featured paths, then the rest as a quiet row of links. */}
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {featured.map((r, i) => (
            <Reveal key={r.href} delay={40 + i * 50}>
              <Link href={r.href} className="glass glass-hover rounded-2xl p-6 h-full flex flex-col group">
                <div className="headline text-3xl text-white/[0.16] leading-none group-hover:text-coral/40 transition-colors">
                  {String(i + 1).padStart(2, '0')}
                </div>
                <h3 className="font-display font-extrabold text-white text-lg mt-3">{r.label}</h3>
                <p className="text-white/60 text-[13px] leading-relaxed mt-2 flex-1">{r.blurb}</p>
                <span className="text-[10px] font-bold uppercase tracking-widest text-white/45 group-hover:text-coral transition-colors mt-5">Open →</span>
              </Link>
            </Reveal>
          ))}
        </div>

        <Reveal delay={240}>
          <div className="flex flex-wrap gap-x-6 gap-y-2 mt-6">
            {ROUTES.filter(r => !FEATURED.includes(r.href)).map(r => (
              <Link key={r.href} href={r.href}
                className="text-[11px] font-bold uppercase tracking-widest text-white/50 hover:text-coral transition-colors">
                {r.label} →
              </Link>
            ))}
          </div>
        </Reveal>
      </section>

      {/* ── Closing CTA — one calm strip, not a second billboard ─ */}
      <section className="max-w-5xl mx-auto px-5 md:px-8 pt-24">
        <Reveal>
          <div className="glass rounded-3xl p-10 md:p-14 flex flex-col md:flex-row md:items-center gap-8 justify-between">
            <div className="max-w-xl">
              <h2 className="headline text-3xl md:text-5xl text-white leading-[0.95]">The door is open.</h2>
              <p className="font-serif-ed text-lg text-white/65 mt-4 leading-snug">
                Stop renting the dream. Own the building it lives in — the math is public and the split is yours.
              </p>
            </div>
            <div className="shrink-0">
              <Link href="/invest" className="btn-shine inline-block px-8 py-4 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
                {deployed ? 'Start owning →' : 'Open the testnet →'}
              </Link>
              <p className="text-white/50 text-[10px] uppercase tracking-[0.2em] font-bold mt-4 md:text-right">
                {LAUNCH.stage} · Mainnet {LAUNCH.date.toLowerCase()}
              </p>
            </div>
          </div>
        </Reveal>
      </section>
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(HomeInner), { ssr: false })
