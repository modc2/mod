'use client'

// /city — the shared city: everyone's published buildings, copy & remix any.

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api, ownerId, FALLBACK_STYLES, Design, StyleSpec } from '@/lib/modcity'
import { Reveal, DesignGlyph, Mini, fmtUSD, useDesignActions } from '@/components/site'

export default function CityPage() {
  const [owner, setOwner] = useState('')
  const [styles, setStyles] = useState<StyleSpec[]>(FALLBACK_STYLES)
  const [community, setCommunity] = useState<Design[]>([])
  const [status, setStatus] = useState<any>(null)

  const refresh = useCallback(() => {
    api('designs?scope=public&limit=24').then(setCommunity).catch(() => {})
    api('status').then(setStatus).catch(() => {})
  }, [])

  useEffect(() => {
    setOwner(ownerId())
    api('styles').then((s) => { if (Array.isArray(s) && s.length) setStyles(s) }).catch(() => {})
    refresh()
  }, [refresh])

  const { remix } = useDesignActions(owner, refresh)

  return (
    <section className="max-w-7xl mx-auto px-5 py-16">
      <Reveal className="mb-8 flex items-end justify-between flex-wrap gap-3">
        <div><div className="text-[11px] uppercase tracking-[0.25em] text-cyan-300/80 mb-2">the shared city</div><h2 className="text-3xl md:text-4xl font-bold tracking-tight">Published buildings — copy & remix any</h2></div>
        {status && <div className="flex gap-5 text-right">{[['public', status.public_designs], ['panels', status.bricks_placed], ['shared panels', status.public_bricks], ['value', fmtUSD(status.total_design_value_usd)]].map(([l, v]) => <div key={l as string}><div className="text-xl font-bold grad">{v}</div><div className="text-[10px] uppercase tracking-wider text-white/40">{l}</div></div>)}</div>}
      </Reveal>
      {community.length === 0 ? (
        <div className="glass rounded-2xl p-12 text-center">
          <div className="text-white/40">No public buildings yet — be the first to publish.</div>
          <Link href="/build" className="inline-block mt-5 px-5 py-2.5 rounded-xl font-semibold text-black bg-gradient-to-r from-cyan-300 to-purple-300 hover:scale-[1.03] transition-transform">Build something →</Link>
        </div>
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
  )
}
