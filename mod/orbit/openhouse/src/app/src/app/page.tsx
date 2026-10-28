/* / — the front door.

   One clear hero, one quiet line of the manifesto, one calm directory, one
   close. Everything that used to live below the fold is a route now, so this
   page's only job is to make the case once and point at the pages that back
   it up — without shouting it twice. */

"use client";

import Link from 'next/link'
import dynamic from 'next/dynamic'
import { useRef } from 'react'
import { Shell, ROUTES } from '../components/chrome'
import { Reveal, Skyline } from '../components/motion'
import { LiveSim } from '../components/livesim'
import { MANIFESTO, TICKER, STAGE } from '../lib/whitepaper'

// The four places worth leading with. The full eight still live in the footer
// and the directory below — the home hero just doesn't open with all of them.
const FEATURED = ['/manifesto', '/simulator', '/invest', '/paper']

function HomeInner() {
  const heroRef = useRef<HTMLElement>(null)

  const onHeroMove = (e: React.MouseEvent) => {
    const el = heroRef.current; if (!el) return
    const r = el.getBoundingClientRect()
    el.style.setProperty('--mx', `${e.clientX - r.left}px`)
    el.style.setProperty('--my', `${e.clientY - r.top}px`)
  }

  const featured = FEATURED
    .map(h => ROUTES.find(r => r.href === h))
    .filter((r): r is (typeof ROUTES)[number] => !!r)

  return (
    <Shell>
      {/* ── Hero: the case on the left, the proof running on the right ── */}
      <header ref={heroRef} onMouseMove={onHeroMove} className="relative overflow-hidden">
        <div className="spotlight" />
        <div className="relative z-10 px-5 md:px-8 pt-14 md:pt-20 pb-10 max-w-6xl mx-auto w-full grid gap-12 lg:gap-14 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] items-center">
          <div>
            <Reveal>
              <div className="inline-flex flex-wrap items-center gap-2.5 px-3 py-1.5 rounded-full glass text-[11px] font-bold uppercase tracking-[0.2em] text-coral mb-7">
                <span className="w-1.5 h-1.5 rounded-full bg-coral animate-pulse" />
                {STAGE.short}
              </div>
            </Reveal>
            <Reveal delay={80}>
              <h1 className="headline text-[17vw] sm:text-[6.5rem] xl:text-[7.25rem] leading-[0.86] text-white glow-warm">
                OWN THE<br /><span className="text-surf-grad">SKYLINE.</span>
              </h1>
            </Reveal>
            <Reveal delay={180}>
              <p className="font-serif-ed text-xl md:text-[1.6rem] text-white/75 max-w-xl mt-7 leading-snug">
                You&apos;ve paid someone else&apos;s mortgage long enough.
                <span className="text-white"> OpenHouse is rent-to-own</span> — every
                payment becomes equity in the home you live in.
              </p>
            </Reveal>
            <Reveal delay={260}>
              <div className="flex flex-wrap items-center gap-3 mt-9">
                <Link href="/simulator" className="btn-shine px-7 py-3.5 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
                  Run your numbers →
                </Link>
                <Link href="/split" className="px-7 py-3.5 rounded-full glass text-white/80 font-bold uppercase tracking-widest text-sm hover:text-coral transition-colors">
                  How the split works
                </Link>
              </div>
            </Reveal>
            <Reveal delay={340}>
              <dl className="grid grid-cols-3 gap-4 max-w-lg mt-10">
                {[
                  ['95–100%', 'stays with the home', 'text-emerald-400'],
                  ['0–5%', 'protocol take, capped', 'text-pink'],
                  ['$0', 'to try it', 'text-coral'],
                ].map(([v, k, c]) => (
                  <div key={k}>
                    <dt className={`headline text-2xl ${c}`}>{v}</dt>
                    <dd className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/70 mt-1">{k}</dd>
                  </div>
                ))}
              </dl>
            </Reveal>
          </div>
          <Reveal delay={200}>
            <LiveSim />
          </Reveal>
        </div>
        {/* the city sits under the hero as a band — never behind the type */}
        <div className="relative h-[150px] md:h-[190px] pointer-events-none" aria-hidden>
          <Skyline scale={0.5} className="skyline-band" />
          <div className="absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-paper to-transparent z-[2]" />
        </div>
      </header>

      {/* ── One quiet ticker ───────────────────────────── */}
      <div className="border-y border-white/10 bg-white/[0.02] py-4 overflow-hidden">
        <div className="marquee">
          {[...TICKER, ...TICKER].map((t, i) => (
            <span key={i} className="headline text-xl md:text-2xl text-white/40 px-8 whitespace-nowrap flex items-center gap-8">
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
            <span className="text-white/70 text-[11px] font-bold uppercase tracking-[0.2em] pb-1">Eight doors in</span>
          </div>
        </Reveal>

        {/* Four featured paths, then the rest as a quiet row of links. */}
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {featured.map((r, i) => (
            <Reveal key={r.href} delay={40 + i * 50}>
              <Link href={r.href} className="glass glass-hover rounded-2xl p-6 h-full flex flex-col group">
                <div className="headline text-3xl text-white/35 leading-none group-hover:text-coral transition-colors">
                  {String(i + 1).padStart(2, '0')}
                </div>
                <h3 className="font-display font-extrabold text-white text-lg mt-3">{r.label}</h3>
                <p className="text-white/60 text-[13px] leading-relaxed mt-2 flex-1">{r.blurb}</p>
                <span className="text-[10px] font-bold uppercase tracking-widest text-white/70 group-hover:text-coral transition-colors mt-5">Open →</span>
              </Link>
            </Reveal>
          ))}
        </div>

        <Reveal delay={240}>
          <div className="flex flex-wrap gap-x-6 gap-y-2 mt-6">
            {ROUTES.filter(r => !FEATURED.includes(r.href)).map(r => (
              <Link key={r.href} href={r.href}
                className="text-[11px] font-bold uppercase tracking-widest text-white/70 hover:text-coral transition-colors">
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
              <Link href="/simulator" className="btn-shine inline-block px-8 py-4 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
                Run the simulation →
              </Link>
              <p className="text-white/50 text-[10px] uppercase tracking-[0.2em] font-bold mt-4 md:text-right">
                {STAGE.short}
              </p>
            </div>
          </div>
        </Reveal>
      </section>
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(HomeInner), { ssr: false })
