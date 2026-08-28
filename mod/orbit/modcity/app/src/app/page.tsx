'use client'

// Landing — the pitch. Building, browsing and managing live on their own
// pages now: /build, /panels, /mine, /city.

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { api, FALLBACK_CATALOG, FALLBACK_STYLES, MANIFESTO, SECTIONS, ModuleSpec, StyleSpec } from '@/lib/modcity'
import { Reveal } from '@/components/site'

export default function Page() {
  const router = useRouter()
  const [catalog, setCatalog] = useState<ModuleSpec[]>(FALLBACK_CATALOG)
  const [styles, setStyles] = useState<StyleSpec[]>(FALLBACK_STYLES)

  useEffect(() => {
    // legacy shared links pointed at the root — forward them to the foundry
    const q = new URLSearchParams(window.location.search)
    const cid = q.get('cid'), d = q.get('d')
    if (cid) { router.replace(`/build?cid=${cid}`); return }
    if (d) { router.replace(`/build?d=${d}`); return }
    api('catalog').then((c) => { if (Array.isArray(c) && c.length) setCatalog(c) }).catch(() => {})
    api('styles').then((s) => { if (Array.isArray(s) && s.length) setStyles(s) }).catch(() => {})
  }, [router])

  const HERO_SPECS: Array<[string, string]> = [
    ['3×3×3 m', 'panel module'],
    [`${catalog.length}`, 'prefab modules'],
    [`${styles.length}`, 'architecture styles'],
    ['private', 'by default'],
  ]

  return (
    <>
      {/* hero */}
      <section className="relative dotgrid">
        <div className="absolute inset-0 hero-glow pointer-events-none" />
        <div className="relative max-w-7xl mx-auto px-5 pt-20 pb-12 text-center">
          <Reveal><div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.25em] text-white/50 border border-white/10 rounded-full px-3 py-1 mb-7"><span className="w-1.5 h-1.5 rounded-full bg-emerald-300 animate-pulse" /> modular housing protocol</div></Reveal>
          <Reveal delay={60}><h1 className="text-[12vw] sm:text-7xl md:text-8xl font-black tracking-[-0.04em] leading-[0.92]">Build a city<br /><span className="grad">panel by panel.</span></h1></Reveal>
          <Reveal delay={140}><p className="mt-7 max-w-2xl mx-auto text-lg md:text-xl text-white/65 leading-relaxed">Assemble factory-built panels — floors, walls, curtain-wall glazing — into real architecture on a lot. Forge your own panels and share them. Set your budget, height and carbon caps. Re-skin from NYC brownstone to neon spire — and raise a tower in a season.</p></Reveal>
          <Reveal delay={220}><div className="mt-9 flex items-center justify-center gap-3"><Link href="/build" className="px-6 py-3 rounded-xl font-semibold text-black bg-gradient-to-r from-emerald-300 to-cyan-300 hover:scale-[1.03] transition-transform shadow-xl shadow-cyan-500/20">Open the foundry</Link><a href="#how" className="px-6 py-3 rounded-xl font-semibold text-white/80 glass hover:bg-white/10 transition">How it works</a></div></Reveal>
          <Reveal delay={300}><div className="mt-14 grid grid-cols-2 sm:grid-cols-4 gap-3 max-w-3xl mx-auto">{HERO_SPECS.map(([v, l]) => <div key={l} className="glass rounded-xl p-4"><div className="text-2xl font-bold grad">{v}</div><div className="text-[11px] text-white/45 mt-1">{l}</div></div>)}</div></Reveal>
        </div>
      </section>

      {/* manifesto */}
      <section id="how" className="max-w-5xl mx-auto px-5 py-20">
        <Reveal><p className="text-2xl md:text-4xl font-semibold tracking-tight leading-[1.25] text-white/90">{MANIFESTO.split('panel by panel').map((part, i, arr) => <span key={i}>{part}{i < arr.length - 1 && <span className="grad font-bold">panel by panel</span>}</span>)}</p></Reveal>
        <div className="mt-16 grid md:grid-cols-2 gap-5">
          {SECTIONS.map((s, i) => (
            <Reveal key={s.k} delay={i * 60}><div className="glass rounded-2xl p-7 h-full hover:bg-white/[0.06] transition"><div className="text-[11px] uppercase tracking-[0.2em] text-cyan-300/70 mb-3">{s.k}</div><h3 className="text-xl font-bold mb-3 tracking-tight">{s.t}</h3><p className="text-white/55 text-[15px] leading-relaxed">{s.b}</p></div></Reveal>
          ))}
        </div>
      </section>

      {/* explore */}
      <section className="max-w-7xl mx-auto px-5 py-4">
        <div className="grid sm:grid-cols-3 gap-3">
          {([
            ['/build', 'The foundry', 'Design a building in 3D — panels, constraints, styles.'],
            ['/panels', 'Panel library', `${catalog.length} prefab modules and ${styles.length} architecture styles.`],
            ['/city', 'The shared city', 'Published buildings — copy & remix any of them.'],
          ] as Array<[string, string, string]>).map(([href, t, b], i) => (
            <Reveal key={href} delay={i * 60}>
              <Link href={href} className="block glass rounded-2xl p-6 h-full hover:bg-white/[0.07] hover:-translate-y-0.5 transition group">
                <div className="font-bold text-lg tracking-tight">{t} <span className="inline-block group-hover:translate-x-1 transition-transform">→</span></div>
                <p className="text-sm text-white/50 mt-1.5 leading-relaxed">{b}</p>
              </Link>
            </Reveal>
          ))}
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-5 py-20">
        <Reveal><div className="rounded-3xl p-10 md:p-16 text-center relative overflow-hidden border border-white/10" style={{ background: 'radial-gradient(70% 120% at 50% 0%, rgba(0,245,212,0.16), rgba(199,125,255,0.10) 50%, transparent)' }}>
          <h2 className="text-3xl md:text-5xl font-black tracking-tight">Housing is a build problem.<br /><span className="grad">We made it a build button.</span></h2>
          <p className="mt-5 text-white/60 max-w-xl mx-auto">Forge a panel, set your constraints, stack a brownstone, save it private, share the CID. The whole protocol runs over the mod gateway.</p>
          <Link href="/build" className="inline-block mt-8 px-7 py-3.5 rounded-xl font-semibold text-black bg-white hover:scale-[1.03] transition-transform">Build something →</Link>
        </div></Reveal>
      </section>
    </>
  )
}
