/* /invest — the building, the float, and the mint form.

   The one page that writes: a purchase reloads status + property so the
   float bar and the cap-table count move under you. */

"use client";

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useState } from 'react'
import { toast } from 'react-toastify'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal, Tilt } from '../../components/motion'
import { PropertyData, StatusData, api, formatNum, useResource } from '../../lib/api'
import { LAUNCH } from '../../lib/whitepaper'

function InvestInner() {
  const { data: status, loading: sLoading, reload: reloadStatus } = useResource<StatusData | null>('status', null)
  const { data: property, reload: reloadProperty } = useResource<PropertyData | null>('property', null)
  const [buyAddr, setBuyAddr] = useState('')
  const [buyShares, setBuyShares] = useState('')
  const [purchasing, setPurchasing] = useState(false)

  const refresh = () => { reloadStatus(); reloadProperty() }

  const deployed = !!(property?.deployed && status?.deployed && status.total_shares > 0)
  const soldPct = deployed && status ? (status.shares_sold / status.total_shares) * 100 : 0
  const price = deployed && property ? parseFloat(property.share_price || '0') : 0
  const cost = buyShares ? parseInt(buyShares || '0') * price : 0

  const handlePurchase = async () => {
    if (!buyAddr.trim() || !buyShares.trim()) { toast.error('Address and share count required'); return }
    setPurchasing(true)
    try {
      const result = await api('purchase', { method: 'POST', body: { buyer: buyAddr.trim(), share_count: parseInt(buyShares), payment: 0 } })
      if (result.success) {
        toast.success(`You own ${result.shares_purchased} more shares. Welcome to the building.`)
        setBuyAddr(''); setBuyShares(''); refresh()
      }
    } catch (err: any) { toast.error(err?.message || 'Purchase failed') }
    setPurchasing(false)
  }

  return (
    <Shell>
      <PageHead
        kicker="The Building"
        title="Rent toward ownership."
        aside={
          <button onClick={refresh} disabled={sLoading}
            className="px-4 py-2 rounded-full border border-white/12 text-[11px] font-bold uppercase tracking-widest text-white/68 hover:text-white hover:border-white/30 disabled:opacity-30 transition-colors">
            {sLoading ? 'Syncing…' : '↻ Live data'}
          </button>
        } />

      <div className="max-w-6xl mx-auto px-5 md:px-8">
        <div className="grid lg:grid-cols-5 gap-6">
          <Reveal className="lg:col-span-3">
            <Tilt className="h-full">
              <div className="glass glass-hover rounded-3xl p-7 md:p-9 h-full">
                <div className="flex items-start justify-between mb-7">
                  <div>
                    <h2 className="font-serif-ed text-3xl text-white mb-1">{deployed ? property!.description : 'No property deployed yet'}</h2>
                    <p className="text-white/60 text-sm">{deployed ? (property!.contract || 'On-chain') : 'Deploy a property to open the float'}</p>
                  </div>
                  <span className={`text-[11px] font-bold uppercase tracking-widest px-3 py-1.5 rounded-full border ${deployed && property!.is_active ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10' : 'text-white/60 border-white/15'}`}>
                    {deployed ? (property!.is_active ? '● Active' : 'Paused') : `${LAUNCH.stage} · not deployed`}
                  </span>
                </div>
                {deployed ? (
                  <>
                    <div className="mb-8">
                      <div className="flex justify-between text-xs mb-2">
                        <span className="text-white/60 uppercase tracking-widest font-bold">Float sold</span>
                        <span className="text-coral font-bold">{soldPct.toFixed(1)}%</span>
                      </div>
                      <div className="h-3 rounded-full bg-white/[0.06] overflow-hidden">
                        <div className="h-full rounded-full bg-gradient-to-r from-ember via-coral to-peach transition-all duration-1000" style={{ width: `${Math.max(soldPct, 1)}%` }} />
                      </div>
                      <div className="flex justify-between text-[11px] text-white/55 mt-2">
                        <span>{formatNum(status!.shares_sold)} sold</span>
                        <span>{formatNum(status!.total_shares)} total supply</span>
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-4">
                      {[
                        { k: 'Share price', v: `${property!.share_price} Ξ`, c: 'text-coral' },
                        { k: 'Available', v: formatNum(status!.available_shares), c: 'text-emerald-400' },
                        { k: 'Owners', v: formatNum(status!.shareholders), c: 'text-white' },
                      ].map((s, i) => (
                        <div key={i} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4">
                          <div className={`text-2xl font-display font-extrabold ${s.c} tabular-nums`}>{s.v}</div>
                          <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <div className="rounded-2xl border border-dashed border-white/12 bg-white/[0.015] p-8 text-center">
                    <div className="text-4xl mb-3 float">⌂</div>
                    <p className="text-white/72 text-sm leading-relaxed max-w-sm mx-auto">
                      No building has been fractionalized on this contract yet. Once a property is deployed,
                      its float, share price, and live ownership show up here — straight from chain, no placeholders.
                    </p>
                    <p className="text-white/58 text-xs leading-relaxed max-w-sm mx-auto mt-3">
                      On {LAUNCH.chain} you can deploy one yourself and drive the whole loop with test ETH.
                      The first real home lands with the mainnet launch — date {LAUNCH.date.toLowerCase()}.
                    </p>
                    <code className="inline-block mt-4 text-[11px] font-mono text-coral/80 bg-coral/5 border border-coral/15 rounded-lg px-3 py-1.5">openhouse deploy property_details=… total_shares=… share_price=…</code>
                  </div>
                )}
              </div>
            </Tilt>
          </Reveal>

          <Reveal className="lg:col-span-2" delay={80}>
            <div className="glass rounded-3xl p-7 md:p-9 h-full flex flex-col border-coral/20">
              <div className="flex items-center justify-between gap-3 mb-2">
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-coral">Take a position</div>
                <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.15em] text-onaccent bg-coral rounded px-1.5 py-0.5">{LAUNCH.stage}</span>
              </div>
              <h2 className="font-serif-ed text-2xl text-white mb-2">Mint your shares</h2>
              <p className="text-[11px] text-white/60 leading-relaxed mb-6">
                {deployed
                  ? `Settles on ${LAUNCH.chain} with test ETH — the mainnet sale opens at launch, date ${LAUNCH.date.toLowerCase()}.`
                  : `The mainnet sale hasn't opened. Launch date: ${LAUNCH.date.toLowerCase()}.`}
              </p>
              <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Your wallet</label>
              <input type="text" placeholder="0x…" value={buyAddr} onChange={e => setBuyAddr(e.target.value)} className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors mb-4" />
              <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Shares</label>
              <input type="number" min="1" placeholder="100" value={buyShares} onChange={e => setBuyShares(e.target.value)} className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors mb-5" />
              <div className="flex justify-between items-baseline mb-5 pb-5 border-b border-white/10">
                <span className="text-white/60 text-sm uppercase tracking-widest font-bold">Total</span>
                <span className="headline text-3xl text-coral tabular-nums">{deployed ? (cost ? cost.toFixed(4) : '0.00') : '—'} {deployed && <span className="text-lg">Ξ</span>}</span>
              </div>
              <button onClick={handlePurchase} disabled={!deployed || purchasing || !buyAddr.trim() || !buyShares.trim()} className="btn-shine w-full py-4 rounded-xl bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-xl hover:shadow-coral/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all mt-auto">
                {!deployed ? 'Sale not open · launch TBA' : purchasing ? 'Minting…' : 'Own it (testnet) →'}
              </button>
              <p className="text-[10px] text-white/50 text-center mt-3 leading-relaxed">
                {deployed
                  ? `Shares are pro-rata claims on the wrapped entity. On ${LAUNCH.chain} they settle in test ETH and carry no real-world claim.`
                  : 'The primary sale opens once a property is deployed to the contract — on testnet today, on mainnet at launch.'}
              </p>
            </div>
          </Reveal>
        </div>

        <p className="text-white/55 text-sm mt-10 text-center">
          Every mint lands on the <Link href="/captable" className="text-coral hover:underline">public cap table →</Link>
        </p>
      </div>

      <NextUp here="/invest" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(InvestInner), { ssr: false })
