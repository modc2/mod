/* / — the front door.

   Hero, ticker, and the directory. Everything that used to live below the
   fold is a route now, so this page's only job is to make the case and
   point at the eight places that back it up. */

"use client";

import Link from 'next/link'
import dynamic from 'next/dynamic'
import { ReactNode, useEffect, useRef, useState } from 'react'
import { Shell, ROUTES } from '../components/chrome'
import { Counter, Reveal, Skyline, Tilt } from '../components/motion'
import { StatusData, PropertyData, useResource } from '../lib/api'
import { MANIFESTO, TICKER, LAUNCH } from '../lib/whitepaper'

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
    <Shell>
      {/* ── Hero ───────────────────────────────────────── */}
      <header ref={heroRef} onMouseMove={onHeroMove}
        className="relative flex flex-col justify-center overflow-hidden min-h-[calc(100vh-5.5rem)]">
        <Skyline shift={scrollY * 0.25} />
        <div className="spotlight" />
        {/* let the pastel city keep its color — just enough haze to seat the type */}
        <div className="absolute inset-x-0 bottom-0 h-[46vh] bg-gradient-to-t from-paper/80 via-paper/25 to-transparent z-[1]" />

        <div className="relative z-10 px-5 md:px-8 pt-16 pb-16 max-w-6xl mx-auto w-full">
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
              <Link href="/invest" className="btn-shine px-7 py-3.5 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
                {deployed ? 'Start owning →' : 'Try it on testnet →'}
              </Link>
              <Link href="/paper" className="px-7 py-3.5 rounded-full border border-white/15 text-white/80 font-bold uppercase tracking-widest text-sm hover:border-coral/50 hover:text-white transition-colors">Read the whitepaper</Link>
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

      {/* ── The pitch, in three lines ──────────────────── */}
      <section className="max-w-5xl mx-auto px-5 md:px-8 py-24 md:py-32">
        {MANIFESTO.slice(0, 3).map((line, i) => (
          <Reveal key={i} delay={i * 120}>
            <p className="font-serif-ed text-4xl md:text-6xl leading-[1.05] mb-3 text-white/60">{line}</p>
          </Reveal>
        ))}
        <Reveal delay={400}>
          <Link href="/manifesto" className="inline-block mt-8 text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">
            The whole manifesto →
          </Link>
        </Reveal>
      </section>

      {/* ── The directory ──────────────────────────────── */}
      <section className="max-w-6xl mx-auto px-5 md:px-8 pb-8">
        <Reveal>
          <div className="text-center mb-12">
            <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-4">Eight pages, no scroll</div>
            <h2 className="headline text-5xl md:text-7xl text-white">Start anywhere.</h2>
          </div>
        </Reveal>
        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-4">
          {ROUTES.map((r, i) => (
            <Reveal key={r.href} delay={40 + i * 40}>
              <Tilt max={8} className="h-full">
                <Link href={r.href} className="glass glass-hover rounded-2xl p-6 h-full flex flex-col group">
                  <div className="headline text-4xl text-white/[0.16] leading-none group-hover:text-coral/30 transition-colors">
                    {String(i + 1).padStart(2, '0')}
                  </div>
                  <h3 className="font-display font-extrabold text-white text-lg mt-3">{r.label}</h3>
                  <p className="text-white/65 text-[13px] leading-relaxed mt-2 flex-1">{r.blurb}</p>
                  <span className="text-[10px] font-bold uppercase tracking-widest text-white/45 group-hover:text-coral transition-colors mt-5">Open →</span>
                </Link>
              </Tilt>
            </Reveal>
          ))}
        </div>
      </section>

      {/* ── Closing CTA ────────────────────────────────── */}
      <section className="max-w-5xl mx-auto px-5 md:px-8 pt-24 pb-8 text-center">
        <Reveal>
          <h2 className="headline text-6xl md:text-9xl text-white leading-[0.9] glow-warm">THE DOOR<br />IS <span className="text-surf-grad">OPEN.</span></h2>
          <p className="font-serif-ed text-xl text-white/68 max-w-lg mx-auto mt-8">Stop renting the dream. Own the building it lives in.</p>
          <Link href="/invest" className="btn-shine inline-block mt-10 px-10 py-4 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
            {deployed ? 'Start owning →' : 'Open the testnet →'}
          </Link>
          <p className="text-white/58 text-xs uppercase tracking-[0.2em] font-bold mt-6">
            {LAUNCH.stage} today on {LAUNCH.chain} · Mainnet launch {LAUNCH.date.toLowerCase()}
          </p>
        </Reveal>
      </section>
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(HomeInner), { ssr: false })
