/* /landscape — every other on-chain housing project, honestly.

   This is the page that most wanted to stop being a section. It reaches
   RealT's community API and CoinGecko through the backend cache; on the
   old single page every visitor paid that latency to read anything at all.
   Now you pay it only if you came to check our claims. */

"use client";

import dynamic from 'next/dynamic'
import { useState } from 'react'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal } from '../../components/motion'
import { CompareData, Peer, formatNum, timeAgo, useResource } from '../../lib/api'

const STATUS_STYLE: Record<string, string> = {
  live: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10',
  testnet: 'text-coral border-coral/30 bg-coral/10',
  liquidating: 'text-pink border-pink/30 bg-pink/10',
  acquired: 'text-pink border-pink/30 bg-pink/10',
  quiet: 'text-white/60 border-white/15 bg-white/[0.03]',
}

function PeerCard({ p, ours = false }: { p: Peer; ours?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`rounded-2xl border p-5 transition-colors ${ours
      ? 'border-coral/40 bg-gradient-to-br from-coral/[0.09] to-transparent'
      : 'border-white/[0.07] bg-white/[0.02] hover:border-white/15'}`}>
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className={`font-display font-extrabold text-lg truncate ${ours ? 'text-coral' : 'text-white'}`}>{p.name}</h3>
            {p.occupant_equity && (
              <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.14em] text-emerald-400" title="The person living there accrues ownership">⌂ resident</span>
            )}
          </div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-white/55 font-bold mt-1">{p.chain} · {p.category_label}</div>
        </div>
        <span className={`shrink-0 text-[9px] font-bold uppercase tracking-widest px-2 py-1 rounded-full border ${STATUS_STYLE[p.status] || STATUS_STYLE.quiet}`}>{p.status}</span>
      </div>

      <p className="text-white/72 text-[13px] leading-relaxed">{p.thesis}</p>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 mt-4 text-[11px]">
        {[
          { k: 'Equity goes to', v: p.equity_to },
          { k: 'Minimum', v: p.min_ticket },
          { k: 'Wrapper', v: p.wrapper },
          { k: 'The take', v: p.take },
        ].map(f => (
          <div key={f.k}>
            <dt className="text-white/50 uppercase tracking-[0.14em] font-bold text-[9px]">{f.k}</dt>
            <dd className="text-white/75 leading-snug mt-0.5">{f.v || '—'}</dd>
          </div>
        ))}
      </dl>

      {/* Live rails: only the projects that publish openly have anything here. */}
      {(p.token || p.live) && (
        <div className="flex flex-wrap gap-2 mt-4">
          {p.live?.tokens != null && (
            <span className="text-[10px] px-2 py-1 rounded-md bg-white/[0.04] border border-white/[0.07] text-white/68 tabular-nums">
              {formatNum(p.live.tokens)} property tokens
              {p.live.median_token_price != null && ` · $${p.live.median_token_price} median`}
            </span>
          )}
          {p.token && (
            <span className="text-[10px] px-2 py-1 rounded-md bg-white/[0.04] border border-white/[0.07] text-white/68 tabular-nums">
              {p.token.symbol} ${p.token.price_usd < 0.01 ? p.token.price_usd.toFixed(6) : p.token.price_usd.toFixed(4)}
              <span className={p.token.ath_change_pct < -80 ? 'text-pink/70' : 'text-white/58'}> · {p.token.ath_change_pct.toFixed(0)}% from ATH</span>
            </span>
          )}
        </div>
      )}

      {p.status_note && (
        <>
          <button onClick={() => setOpen(o => !o)}
            className="mt-4 text-[10px] font-bold uppercase tracking-widest text-white/58 hover:text-coral transition-colors">
            {open ? '− What happened' : '+ What happened'}
          </button>
          {open && (
            <div className="mt-3 pt-3 border-t border-white/[0.07]">
              <p className="text-white/68 text-[12px] leading-relaxed">{p.status_note}</p>
              {p.sources.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-3">
                  {p.sources.map((s, i) => (
                    <a key={s} href={s} target="_blank" rel="noopener noreferrer"
                      className="text-[10px] px-2 py-0.5 rounded border border-white/10 text-white/58 hover:text-coral hover:border-coral/30 transition-colors">
                      source {i + 1} ↗
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function LandscapeInner() {
  const { data, loading, loaded, reload } = useResource<CompareData | null>('compare', null)
  const [onlyResident, setOnlyResident] = useState(false)

  return (
    <Shell>
      <PageHead kicker="The Landscape" title={<>Everyone else<br />tokenized the <span className="text-surf-grad">landlord.</span></>}>
        Twelve projects that put housing on a chain, sorted by the only question that
        matters: who ends up owning the house. Live numbers pulled from public endpoints;
        every editorial claim carries its source.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8">
        {!data ? (
          <p className="text-white/55 text-sm">
            {loading || !loaded ? 'Reading the landscape…' : 'Comparison unavailable — the API is not responding.'}
          </p>
        ) : (() => {
          // Us first, then the field: resident-side projects lead, then by category.
          const shown = data.peers.filter(p => !onlyResident || p.occupant_equity)
          const order = ['occupant-equity', 'offchain-rto', 'investor-fractional', 'homeowner-liquidity', 'title', 'infrastructure', 'synthetic']
          const sorted = [...shown].sort((a, b) => order.indexOf(a.category) - order.indexOf(b.category))
          return (
            <>
              <Reveal>
                <div className="glass rounded-3xl p-7 md:p-9 mb-8">
                  <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mb-4">The finding</div>
                  <p className="font-serif-ed text-3xl md:text-4xl text-white leading-tight">
                    {data.headline.occupant_side} of {data.headline.total} comparable projects give the resident equity —
                    <span className="text-surf-grad"> and none of those are on-chain.</span>
                  </p>
                  <p className="text-white/65 text-sm leading-relaxed mt-5 max-w-3xl">
                    Everyone else tokenized the landlord. Investor-side platforms sell slices of a rental to
                    people who will never live in it, and the tenant is the yield. The two projects that do
                    credit the resident — Divvy and Landis — hold that credit on a company balance sheet.
                    OpenHouse is the same promise, written into a contract instead.
                  </p>
                </div>
              </Reveal>

              <Reveal delay={60}>
                <div className="flex items-center justify-between flex-wrap gap-3 mb-6">
                  <div className="flex items-center gap-2">
                    <button onClick={() => setOnlyResident(o => !o)}
                      className={`px-3 py-1.5 rounded-full border text-[10px] font-bold uppercase tracking-widest transition-colors ${onlyResident
                        ? 'border-emerald-500/40 text-emerald-400 bg-emerald-500/10'
                        : 'border-white/12 text-white/65 hover:text-white hover:border-white/30'}`}>
                      ⌂ Resident gets equity {onlyResident ? '· on' : '· off'}
                    </button>
                    <span className="text-[10px] text-white/50 tabular-nums">{sorted.length} of {data.peers.length} peers</span>
                  </div>
                  <div className="flex items-center gap-3">
                    {data.fetched > 0 && (
                      <span className="text-[10px] text-white/50">
                        live data {timeAgo(data.fetched)}{data.cached ? ' · cached' : ''}
                      </span>
                    )}
                    <button onClick={() => reload('compare?refresh=true')} disabled={loading}
                      className="px-4 py-2 rounded-full border border-white/12 text-[11px] font-bold uppercase tracking-widest text-white/68 hover:text-white hover:border-white/30 disabled:opacity-30 transition-colors">
                      {loading ? 'Syncing…' : '↻ Refresh'}
                    </button>
                  </div>
                </div>
              </Reveal>

              <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                <Reveal delay={100}><PeerCard p={data.openhouse} ours /></Reveal>
                {sorted.map((p, i) => (
                  <Reveal key={p.id} delay={140 + i * 40}><PeerCard p={p} /></Reveal>
                ))}
              </div>

              {/* The receipts for the model — and the receipts against us. */}
              <div className="grid lg:grid-cols-2 gap-6 mt-10">
                <Reveal>
                  <div className="glass rounded-3xl p-7 h-full">
                    <div className="text-pink text-[11px] font-bold uppercase tracking-[0.25em] mb-5">What the field has already proved</div>
                    <div className="space-y-6">
                      {data.evidence.map(e => (
                        <div key={e.claim}>
                          <p className="text-white font-display font-bold text-[15px] leading-snug">{e.claim}</p>
                          <p className="text-white/65 text-[13px] leading-relaxed mt-2">{e.detail}</p>
                          <a href={e.source} target="_blank" rel="noopener noreferrer"
                            className="inline-block mt-2 text-[10px] font-bold uppercase tracking-widest text-white/55 hover:text-coral transition-colors">source ↗</a>
                        </div>
                      ))}
                    </div>
                  </div>
                </Reveal>
                <Reveal delay={80}>
                  <div className="glass rounded-3xl p-7 h-full">
                    <div className="text-white/68 text-[11px] font-bold uppercase tracking-[0.25em] mb-2">Where they're ahead of us</div>
                    <p className="text-white/58 text-xs leading-relaxed mb-5">
                      A better model on testnet loses to a worse model with a deed. Here's the honest gap.
                    </p>
                    <ul className="space-y-3">
                      {data.behind.map(b => (
                        <li key={b} className="flex gap-3 text-[13px] text-white/72 leading-relaxed">
                          <span className="text-pink/60 shrink-0 mt-0.5">✕</span>{b}
                        </li>
                      ))}
                    </ul>
                  </div>
                </Reveal>
              </div>
            </>
          )
        })()}
      </div>

      <NextUp here="/landscape" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(LandscapeInner), { ssr: false })
