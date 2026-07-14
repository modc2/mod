'use client'

import { Component, useEffect, useRef, useState, ReactNode, useCallback } from 'react'
import dynamic from 'next/dynamic'
import {
  api, ownerId, FALLBACK_CATALOG, FALLBACK_STYLES, MANIFESTO, SECTIONS,
  ModuleSpec, StyleSpec, Design, PortableDoc,
} from '@/lib/modcity'

const Configurator = dynamic(() => import('@/components/Configurator'), {
  ssr: false,
  loading: () => <div className="w-full h-[72vh] min-h-[560px] rounded-2xl border border-white/10 bg-black/40 grid place-items-center text-white/40 text-sm">booting the 3D foundry…</div>,
})

// the 3D foundry must never take the page down with it — if anything inside
// throws (driver quirks, exotic embeds), degrade to a quiet card instead.
class FoundryBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (this.state.failed) return (
      <div className="w-full h-[72vh] min-h-[560px] rounded-2xl border border-white/10 bg-black/40 grid place-items-center text-center px-6">
        <div>
          <div className="text-lg font-bold tracking-tight">The 3D foundry couldn&apos;t start here</div>
          <p className="mt-2 text-sm text-white/50 max-w-md mx-auto leading-relaxed">Your browser or this embed blocked 3D rendering. The catalog, styles and gallery below still work — open the page in a regular browser to build.</p>
        </div>
      </div>
    )
    return this.props.children
  }
}

function fmtUSD(n: number) {
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M'
  if (n >= 1e3) return '$' + (n / 1e3).toFixed(0) + 'k'
  return '$' + (n || 0)
}
const fmt = (n: number) => (n || 0).toLocaleString()

function Reveal({ children, className = '', delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current; if (!el) return
    const ob = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); ob.disconnect() } }, { threshold: 0.1 })
    ob.observe(el); return () => ob.disconnect()
  }, [])
  return <div ref={ref} className={className} style={{ opacity: seen ? 1 : 0, transform: seen ? 'none' : 'translateY(26px)', transition: `all .8s cubic-bezier(.16,1,.3,1) ${delay}ms` }}>{children}</div>
}

export default function Page() {
  const [owner, setOwner] = useState('')
  const [catalog, setCatalog] = useState<ModuleSpec[]>(FALLBACK_CATALOG)
  const [styles, setStyles] = useState<StyleSpec[]>(FALLBACK_STYLES)
  const [community, setCommunity] = useState<Design[]>([])
  const [mine, setMine] = useState<Design[]>([])
  const [bricks, setBricks] = useState<ModuleSpec[]>([])
  const [status, setStatus] = useState<any>(null)
  const [loadDoc, setLoadDoc] = useState<PortableDoc | null>(null)

  const loadCatalog = useCallback((o: string) => {
    api('catalog' + (o ? `?owner=${o}` : '')).then((c) => { if (Array.isArray(c) && c.length) setCatalog(c) }).catch(() => {})
  }, [])
  const loadGallery = useCallback((o: string) => {
    api('designs?scope=public&limit=24').then(setCommunity).catch(() => {})
    api('bricks?limit=18' + (o ? `&owner=${o}` : '')).then((b) => Array.isArray(b) && setBricks(b)).catch(() => {})
    api('status').then(setStatus).catch(() => {})
    if (o) api(`my/designs?owner=${o}`).then(setMine).catch(() => {})
  }, [])

  useEffect(() => {
    const o = ownerId(); setOwner(o)
    api('styles').then((s) => { if (Array.isArray(s) && s.length) setStyles(s) }).catch(() => {})
    loadCatalog(o); loadGallery(o)
    const onSaved = () => loadGallery(o)
    window.addEventListener('modcity:saved', onSaved)
    // load a shared building from the URL (?cid= or ?d=)
    const q = new URLSearchParams(window.location.search)
    const cid = q.get('cid'), d = q.get('d')
    if (cid) api(`load/${cid}`).then(setLoadDoc).catch(() => {})
    else if (d) api(`design/${d}/export`).then((e) => setLoadDoc({ ...e.doc, cid: e.cid })).catch(() => {})
    return () => window.removeEventListener('modcity:saved', onSaved)
  }, [loadCatalog, loadGallery])

  const remix = useCallback(async (d: Design) => {
    try {
      const copy = await api(`design/${d.id}/copy`, { method: 'POST', body: { owner } })
      const exp = await api(`design/${copy.id}/export`)
      setLoadDoc({ ...exp.doc, cid: exp.cid })
      loadGallery(owner)
    } catch {}
  }, [owner, loadGallery])

  const togglePublish = useCallback(async (d: Design) => {
    try { await api(`design/${d.id}/publish`, { method: 'POST', body: { owner, public: !d.public } }); loadGallery(owner) } catch {}
  }, [owner, loadGallery])
  const del = useCallback(async (d: Design) => {
    try { await api(`design/${d.id}?owner=${owner}`, { method: 'DELETE' }); loadGallery(owner) } catch {}
  }, [owner, loadGallery])
  const share = useCallback((d: Design) => {
    const link = `${window.location.origin}/modcity/?${d.cid ? 'cid=' + d.cid : 'd=' + d.id}`
    navigator.clipboard?.writeText(link)
  }, [])
  const download = useCallback(async (d: Design) => {
    try {
      const exp = await api(`design/${d.id}/export`)
      const blob = new Blob([JSON.stringify(exp.doc, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob); const a = document.createElement('a')
      a.href = url; a.download = exp.filename; a.click(); URL.revokeObjectURL(url)
    } catch {}
  }, [])

  const HERO_SPECS: Array<[string, string]> = [
    ['3×3×3 m', 'panel module'],
    [`${catalog.length}`, 'prefab modules'],
    [`${styles.length}`, 'architecture styles'],
    ['private', 'by default'],
  ]

  return (
    <main className="min-h-screen relative overflow-x-hidden">
      <nav className="sticky top-0 z-50 backdrop-blur-xl bg-black/40 border-b border-white/5">
        <div className="max-w-7xl mx-auto px-5 h-14 flex items-center justify-between">
          <a href="#top" className="flex items-center gap-2 font-bold tracking-tight">
            <span className="inline-grid grid-cols-2 gap-[2px]">{['#00f5d4', '#48cae4', '#c77dff', '#ffd166'].map((c) => <span key={c} className="w-2 h-2 rounded-[2px]" style={{ background: c }} />)}</span>
            ModCity
          </a>
          <div className="hidden sm:flex items-center gap-6 text-[13px] text-white/60">
            <a href="#build" className="hover:text-white transition">Build</a>
            <a href="#catalog" className="hover:text-white transition">Panels</a>
            <a href="#mine" className="hover:text-white transition">Mine</a>
            <a href="#gallery" className="hover:text-white transition">City</a>
          </div>
          <a href="#build" className="text-[13px] font-semibold px-3.5 py-1.5 rounded-lg bg-white text-black hover:bg-white/90 transition">Start building →</a>
        </div>
      </nav>

      {/* hero */}
      <section id="top" className="relative dotgrid">
        <div className="absolute inset-0 hero-glow pointer-events-none" />
        <div className="relative max-w-7xl mx-auto px-5 pt-20 pb-12 text-center">
          <Reveal><div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.25em] text-white/50 border border-white/10 rounded-full px-3 py-1 mb-7"><span className="w-1.5 h-1.5 rounded-full bg-emerald-300 animate-pulse" /> modular housing protocol</div></Reveal>
          <Reveal delay={60}><h1 className="text-[12vw] sm:text-7xl md:text-8xl font-black tracking-[-0.04em] leading-[0.92]">Build a city<br /><span className="grad">panel by panel.</span></h1></Reveal>
          <Reveal delay={140}><p className="mt-7 max-w-2xl mx-auto text-lg md:text-xl text-white/65 leading-relaxed">Assemble factory-built panels — floors, walls, curtain-wall glazing — into real architecture on a lot. Forge your own panels and share them. Set your budget, height and carbon caps. Re-skin from NYC brownstone to neon spire — and raise a tower in a season.</p></Reveal>
          <Reveal delay={220}><div className="mt-9 flex items-center justify-center gap-3"><a href="#build" className="px-6 py-3 rounded-xl font-semibold text-black bg-gradient-to-r from-emerald-300 to-cyan-300 hover:scale-[1.03] transition-transform shadow-xl shadow-cyan-500/20">Open the foundry</a><a href="#how" className="px-6 py-3 rounded-xl font-semibold text-white/80 glass hover:bg-white/10 transition">How it works</a></div></Reveal>
          <Reveal delay={300}><div className="mt-14 grid grid-cols-2 sm:grid-cols-4 gap-3 max-w-3xl mx-auto">{HERO_SPECS.map(([v, l]) => <div key={l} className="glass rounded-xl p-4"><div className="text-2xl font-bold grad">{v}</div><div className="text-[11px] text-white/45 mt-1">{l}</div></div>)}</div></Reveal>
        </div>
      </section>

      {/* configurator */}
      <section id="build" className="max-w-7xl mx-auto px-5 py-10">
        {loadDoc && (
          <div className="mb-3 text-[12px] text-cyan-200 bg-cyan-400/10 border border-cyan-400/20 rounded-lg px-3 py-2">
            Loaded shared building <span className="font-semibold">“{loadDoc.name}”</span> — tweak it and hit Save to make it your own.
          </div>
        )}
        <Reveal className="mb-5">
          <div className="flex items-end justify-between flex-wrap gap-3">
            <div><div className="text-[11px] uppercase tracking-[0.25em] text-emerald-300/80 mb-2">the foundry · live</div><h2 className="text-3xl md:text-4xl font-bold tracking-tight">Design a building in your browser</h2></div>
            <p className="text-sm text-white/50 max-w-sm">Forge panels, set your constraints, stack floors, switch styles, then save — private by default, share by CID.</p>
          </div>
        </Reveal>
        <Reveal delay={80}>
          <FoundryBoundary>
            <Configurator catalog={catalog} styles={styles} owner={owner} loadDoc={loadDoc}
              onSaved={() => loadGallery(owner)} onBrickCreated={() => { loadCatalog(owner); loadGallery(owner) }} />
          </FoundryBoundary>
        </Reveal>
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

      {/* catalog */}
      <section id="catalog" className="max-w-7xl mx-auto px-5 py-16">
        <Reveal className="mb-8"><div className="text-[11px] uppercase tracking-[0.25em] text-emerald-300/80 mb-2">the panel library</div><h2 className="text-3xl md:text-4xl font-bold tracking-tight">{catalog.length} prefab panel modules. One footprint.</h2><p className="text-white/50 mt-2 max-w-2xl">Standardized 3×3×3 m panel assemblies — including true NYC-brownstone parts. Forge your own in the foundry; publish to add it here.</p></Reveal>
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

      {/* your buildings */}
      {mine.length > 0 && (
        <section id="mine" className="max-w-7xl mx-auto px-5 py-16">
          <Reveal className="mb-6"><div className="text-[11px] uppercase tracking-[0.25em] text-emerald-300/80 mb-2">your buildings · private by default</div><h2 className="text-3xl md:text-4xl font-bold tracking-tight">Your portfolio</h2></Reveal>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {mine.map((d, i) => {
              const st = styles.find((s) => s.id === d.style) || styles[0]
              return (
                <Reveal key={d.id} delay={(i % 3) * 50}><div className="glass rounded-xl overflow-hidden">
                  <div className="h-20 relative" style={{ background: `linear-gradient(160deg, ${st?.accent}33, rgba(0,0,0,0.4))` }}><DesignGlyph design={d} style={st} /></div>
                  <div className="p-3.5">
                    <div className="flex items-center justify-between"><div className="font-semibold text-[14px] truncate">{d.name}</div>
                      <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-semibold ${d.public ? 'bg-emerald-400/20 text-emerald-200' : 'bg-white/10 text-white/50'}`}>{d.public ? 'public' : 'private'}</span></div>
                    <div className="text-[11px] text-white/45 mt-0.5">{fmtUSD(d.stats.price_usd)} · {d.stats.floors} fl · {d.stats.module_count} panels{d.copies ? ` · ${d.copies} copies` : ''}</div>
                    <div className="mt-2.5 flex flex-wrap gap-1.5 text-[11px]">
                      <button onClick={() => remix(d)} className="px-2 py-1 rounded bg-white/10 hover:bg-white/20">Edit</button>
                      <button onClick={() => togglePublish(d)} className="px-2 py-1 rounded bg-white/10 hover:bg-white/20">{d.public ? 'Unpublish' : 'Publish'}</button>
                      <button onClick={() => share(d)} className="px-2 py-1 rounded bg-white/10 hover:bg-white/20">Share</button>
                      <button onClick={() => download(d)} className="px-2 py-1 rounded bg-white/10 hover:bg-white/20">Export</button>
                      <button onClick={() => del(d)} className="px-2 py-1 rounded bg-rose-500/15 text-rose-300 hover:bg-rose-500/25">Delete</button>
                    </div>
                  </div>
                </div></Reveal>
              )
            })}
          </div>
        </section>
      )}

      {/* community gallery */}
      <section id="gallery" className="max-w-7xl mx-auto px-5 py-16">
        <Reveal className="mb-8 flex items-end justify-between flex-wrap gap-3">
          <div><div className="text-[11px] uppercase tracking-[0.25em] text-cyan-300/80 mb-2">the shared city</div><h2 className="text-3xl md:text-4xl font-bold tracking-tight">Published buildings — copy & remix any</h2></div>
          {status && <div className="flex gap-5 text-right">{[['public', status.public_designs], ['panels', status.bricks_placed], ['shared panels', status.public_bricks], ['value', fmtUSD(status.total_design_value_usd)]].map(([l, v]) => <div key={l as string}><div className="text-xl font-bold grad">{v}</div><div className="text-[10px] uppercase tracking-wider text-white/40">{l}</div></div>)}</div>}
        </Reveal>
        {community.length === 0 ? (
          <div className="glass rounded-2xl p-12 text-center text-white/40">No public buildings yet — be the first to publish.</div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {community.map((d, i) => {
              const st = styles.find((s) => s.id === d.style) || styles[0]
              return (
                <Reveal key={d.id} delay={(i % 4) * 40}><div className="glass rounded-xl overflow-hidden hover:-translate-y-0.5 transition group">
                  <div className="h-24 relative" style={{ background: `linear-gradient(160deg, ${st?.accent}33, rgba(0,0,0,0.4))` }}>
                    <DesignGlyph design={d} style={st} />
                    {d.featured && <span className="absolute top-2 left-2 text-[8px] px-1.5 py-0.5 rounded-full bg-yellow-400/90 text-black font-bold tracking-wide">FEATURED</span>}
                  </div>
                  <div className="p-3.5">
                    <div className="flex items-center justify-between"><div className="font-semibold text-[14px] truncate">{d.name}</div><span className="text-[9px] px-1.5 py-0.5 rounded-full text-black font-semibold shrink-0" style={{ background: st?.accent }}>{st?.name}</span></div>
                    <div className="mt-2 grid grid-cols-3 gap-1 text-center">
                      <Mini v={fmtUSD(d.stats.price_usd)} l="value" /><Mini v={`${d.stats.floors}`} l="floors" /><Mini v={`${d.copies || 0}`} l="copies" />
                    </div>
                    <button onClick={() => remix(d)} className="mt-2.5 w-full py-1.5 rounded-lg text-[12px] font-semibold text-black bg-gradient-to-r from-cyan-300 to-purple-300 hover:from-cyan-200 hover:to-purple-200 transition">↻ Copy & remix</button>
                  </div>
                </div></Reveal>
              )
            })}
          </div>
        )}
      </section>

      <section className="max-w-7xl mx-auto px-5 py-20">
        <Reveal><div className="rounded-3xl p-10 md:p-16 text-center relative overflow-hidden border border-white/10" style={{ background: 'radial-gradient(70% 120% at 50% 0%, rgba(0,245,212,0.16), rgba(199,125,255,0.10) 50%, transparent)' }}>
          <h2 className="text-3xl md:text-5xl font-black tracking-tight">Housing is a build problem.<br /><span className="grad">We made it a build button.</span></h2>
          <p className="mt-5 text-white/60 max-w-xl mx-auto">Forge a panel, set your constraints, stack a brownstone, save it private, share the CID. The whole protocol runs over the mod gateway.</p>
          <a href="#build" className="inline-block mt-8 px-7 py-3.5 rounded-xl font-semibold text-black bg-white hover:scale-[1.03] transition-transform">Build something →</a>
        </div></Reveal>
      </section>

      <footer className="border-t border-white/5 py-10">
        <div className="max-w-7xl mx-auto px-5 flex flex-col sm:flex-row items-center justify-between gap-3 text-[12px] text-white/40">
          <div>ModCity — modular housing & cities · buildings stored content-addressed via <span className="font-mono text-white/60">localfs</span> · served at <span className="font-mono text-white/60">/modcity</span></div>
          <div className="flex gap-4 font-mono"><a href="/modcity/api" className="hover:text-white">/api</a><a href="/modcity/api/catalog" className="hover:text-white">/catalog</a><a href="/modcity/api/designs" className="hover:text-white">/designs</a></div>
        </div>
      </footer>
    </main>
  )
}

function Mini({ v, l }: { v: string; l: string }) {
  return <div className="bg-white/5 rounded-md py-1"><div className="text-[12px] font-semibold leading-none">{v}</div><div className="text-[8px] uppercase tracking-wider text-white/35 mt-0.5">{l}</div></div>
}

function DesignGlyph({ design, style }: { design: Design; style?: StyleSpec }) {
  const cells = design.cells || []
  return (
    <div className="absolute inset-0 flex items-end justify-center gap-[3px] p-3 opacity-90">
      {cells.slice(0, 10).map((c, i) => (
        <div key={i} className="flex flex-col-reverse gap-[2px]">
          {c.stack.slice(0, 9).map((id, j) => {
            const spec = FALLBACK_CATALOG.find((m) => m.id === id)
            return <span key={j} className="w-2.5 h-2.5 rounded-[1px]" style={{ background: spec?.color || style?.palette[spec?.tone || 'warm'] || '#888' }} />
          })}
        </div>
      ))}
    </div>
  )
}
