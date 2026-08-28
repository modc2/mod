'use client'

// /panels — the prefab panel library and the architecture styles it re-skins into.

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api, ownerId, FALLBACK_CATALOG, FALLBACK_STYLES, ModuleSpec, StyleSpec } from '@/lib/modcity'
import { Reveal, fmtUSD } from '@/components/site'

export default function PanelsPage() {
  const [owner, setOwner] = useState('')
  const [catalog, setCatalog] = useState<ModuleSpec[]>(FALLBACK_CATALOG)
  const [styles, setStyles] = useState<StyleSpec[]>(FALLBACK_STYLES)
  const [bricks, setBricks] = useState<ModuleSpec[]>([])

  useEffect(() => {
    const o = ownerId(); setOwner(o)
    api('catalog' + (o ? `?owner=${o}` : '')).then((c) => { if (Array.isArray(c) && c.length) setCatalog(c) }).catch(() => {})
    api('styles').then((s) => { if (Array.isArray(s) && s.length) setStyles(s) }).catch(() => {})
    api('bricks?limit=18' + (o ? `&owner=${o}` : '')).then((b) => Array.isArray(b) && setBricks(b)).catch(() => {})
  }, [])

  return (
    <>
      {/* catalog */}
      <section className="max-w-7xl mx-auto px-5 py-16">
        <Reveal className="mb-8"><div className="text-[11px] uppercase tracking-[0.25em] text-emerald-300/80 mb-2">the panel library</div><h2 className="text-3xl md:text-4xl font-bold tracking-tight">{catalog.length} prefab panel modules. One footprint.</h2><p className="text-white/50 mt-2 max-w-2xl">Standardized 3×3×3 m panel assemblies — including true NYC-brownstone parts. Forge your own in <Link href="/build" className="underline hover:text-white">the foundry</Link>; publish to add it here.</p></Reveal>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {catalog.map((m, i) => {
            const bs = styles.find((s) => s.id === 'brownstone') || styles[0]
            return (
              <Reveal key={m.id} delay={(i % 4) * 50}><div className="glass rounded-xl p-4 h-full hover:bg-white/[0.06] hover:-translate-y-0.5 transition group">
                <div className="flex items-start justify-between mb-3"><div className="w-9 h-9 rounded-lg border border-black/30 shadow-lg group-hover:scale-110 transition" style={{ background: m.color || bs?.palette[m.tone] || '#888', opacity: m.glass ? 0.6 : 1 }} />
                  <span className="text-[10px] uppercase tracking-wider text-white/35">{m.custom ? 'custom' : m.category}</span></div>
                <div className="font-semibold text-[15px]">{m.name}</div>
                <p className="text-[12px] text-white/45 mt-1 leading-snug min-h-[48px]">{m.blurb}</p>
                <div className="mt-3 pt-3 border-t border-white/5 flex items-center justify-between text-[11px]"><span className="font-semibold text-white/90">{fmtUSD(m.price)}</span><span className="text-white/40">{m.lead_days}d · {m.carbon_kg}kg</span></div>
              </div></Reveal>
            )
          })}
        </div>
        {bricks.length > 0 && (
          <Reveal className="mt-8">
            <div className="text-[11px] uppercase tracking-[0.2em] text-purple-300/70 mb-3">community panel library · reuse anyone's piece</div>
            <div className="flex flex-wrap gap-2">
              {bricks.map((b) => (
                <div key={b.id} className="flex items-center gap-2 glass rounded-full pl-1.5 pr-3 py-1.5">
                  <span className="w-5 h-5 rounded-md border border-black/30" style={{ background: b.color || '#888' }} />
                  <span className="text-[12px] font-medium">{b.name}</span>
                  <span className="text-[10px] text-white/40">{fmtUSD(b.price)}</span>
                  {(b.mine || b.owner === owner) && <span className="text-[8px] px-1 rounded bg-cyan-400/20 text-cyan-200">mine</span>}
                </div>
              ))}
            </div>
          </Reveal>
        )}
      </section>

      {/* styles */}
      <section className="max-w-7xl mx-auto px-5 py-16">
        <Reveal className="mb-8"><div className="text-[11px] uppercase tracking-[0.25em] text-purple-300/80 mb-2">style is a layer</div><h2 className="text-3xl md:text-4xl font-bold tracking-tight">Same panels. {styles.length} architectures.</h2></Reveal>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {styles.map((s, i) => (
            <Reveal key={s.id} delay={(i % 4) * 50}><div className="rounded-xl p-5 h-full border border-white/10 hover:-translate-y-0.5 transition" style={{ background: `linear-gradient(160deg, ${s.accent}22, rgba(255,255,255,0.02))` }}>
              <div className="flex gap-1 mb-3">{Object.values(s.palette).slice(0, 6).map((c, j) => <span key={j} className="w-4 h-7 rounded-[3px]" style={{ background: c }} />)}</div>
              <div className="font-bold text-[15px]">{s.name}</div><p className="text-[12px] text-white/50 mt-1 leading-snug min-h-[40px]">{s.vibe}</p>
              <div className="mt-2 text-[10px] text-white/35">×{s.price_mult.toFixed(2)} cost · ×{s.carbon_mult.toFixed(2)} carbon</div>
            </div></Reveal>
          ))}
        </div>
      </section>
    </>
  )
}
