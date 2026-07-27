'use client'

// /mine — your saved buildings. Private by default; publish to put one in the city.

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api, ownerId, FALLBACK_STYLES, Design, StyleSpec } from '@/lib/modcity'
import { Reveal, DesignGlyph, fmtUSD, useDesignActions } from '@/components/site'

export default function MinePage() {
  const [owner, setOwner] = useState('')
  const [styles, setStyles] = useState<StyleSpec[]>(FALLBACK_STYLES)
  const [mine, setMine] = useState<Design[]>([])
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(() => {
    const o = ownerId()
    api(`my/designs?owner=${o}`).then((d) => { setMine(d); setLoaded(true) }).catch(() => setLoaded(true))
  }, [])

  useEffect(() => {
    setOwner(ownerId())
    api('styles').then((s) => { if (Array.isArray(s) && s.length) setStyles(s) }).catch(() => {})
    refresh()
  }, [refresh])

  const { remix, togglePublish, del, share, download } = useDesignActions(owner, refresh)

  return (
    <section className="max-w-7xl mx-auto px-5 py-16">
      <Reveal className="mb-6"><div className="text-[11px] uppercase tracking-[0.25em] text-emerald-300/80 mb-2">your buildings · private by default</div><h2 className="text-3xl md:text-4xl font-bold tracking-tight">Your portfolio</h2></Reveal>
      {mine.length === 0 ? (
        <div className="glass rounded-2xl p-12 text-center">
          <div className="text-white/40">{loaded ? 'Nothing here yet — your saved buildings land on this page.' : 'Loading your buildings…'}</div>
          {loaded && <Link href="/build" className="inline-block mt-5 px-5 py-2.5 rounded-xl font-semibold text-black bg-gradient-to-r from-emerald-300 to-cyan-300 hover:scale-[1.03] transition-transform">Open the foundry →</Link>}
        </div>
      ) : (
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
      )}
    </section>
  )
}
