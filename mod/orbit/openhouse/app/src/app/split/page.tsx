/* /split — where every payment goes, and the dial that sets it.

   The owner console plus the consequence of whatever it's set to. Reads
   terms + models + rent_stats; nothing else on the site needs those three
   together, which is exactly why this is its own page. */

"use client";

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'react-toastify'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal, SplitBar } from '../../components/motion'
import { ModelPreset, RentStats, TermsData, api, formatNum, useResource } from '../../lib/api'
import { BENCHMARKS } from '../../lib/whitepaper'

/* ── What everyone else takes off the top ────────────────── */
function TakeComparison({ ourTake }: { ourTake: number }) {
  const rows = BENCHMARKS.map(b => (b.ours ? { ...b, take: ourTake } : b))
  const max = Math.max(...rows.map(r => r.take), 1)
  return (
    <div className="space-y-3">
      {rows.map(r => (
        <div key={r.name} className="grid grid-cols-[8.5rem_1fr_3.5rem] items-center gap-3">
          <span className={`text-xs truncate ${r.ours ? 'text-coral font-bold' : 'text-white/68'}`} title={r.note}>{r.name}</span>
          <div className="h-2.5 rounded-full bg-white/[0.05] overflow-hidden">
            <div className="h-full rounded-full transition-all duration-700"
              style={{
                width: `${Math.max((r.take / max) * 100, 1.5)}%`,
                background: r.ours ? 'linear-gradient(90deg,var(--peach),var(--coral))' : 'rgb(var(--pink-rgb) / 0.5)',
              }} />
          </div>
          <span className={`text-xs font-bold tabular-nums text-right ${r.ours ? 'text-coral' : 'text-white/60'}`}>
            {r.take % 1 === 0 ? r.take : r.take.toFixed(1)}%
          </span>
        </div>
      ))}
      <p className="text-[10px] text-white/50 leading-relaxed pt-1">
        Published headline rates for comparison. Only the OpenHouse number is enforced by this contract —
        and only inside the 1–5% band written into it.
      </p>
    </div>
  )
}

/* ── Owner's terms desk — the dial, live ─────────────────── */
function TermsDesk({ terms, models, onSaved }: {
  terms: TermsData | null; models: ModelPreset[]; onSaved: () => void
}) {
  const band = terms?.fee_band ?? { min_pct: 1, max_pct: 5 }
  const [model, setModel] = useState('full_credit')
  const [feePct, setFeePct] = useState(2.5)
  const [creditPct, setCreditPct] = useState(100)
  const [rent, setRent] = useState('')
  const [price, setPrice] = useState('')
  const [ownerAddr, setOwnerAddr] = useState('')
  const [saving, setSaving] = useState(false)
  const loaded = useRef(false)

  // Seed the desk from the live deal once it lands, then leave the owner alone.
  useEffect(() => {
    if (!terms || loaded.current) return
    loaded.current = true
    setModel(terms.model); setFeePct(terms.fee_pct); setCreditPct(terms.credit_pct)
    setRent(terms.monthly_rent ? String(terms.monthly_rent) : '')
    setPrice(terms.home_price ? String(terms.home_price) : '')
    setOwnerAddr(terms.owner || '')
  }, [terms])

  const pickModel = (m: ModelPreset) => { setModel(m.id); setCreditPct(m.credit_pct) }

  const monthly = parseFloat(rent) || 1
  const fee = monthly * (feePct / 100)
  const credit = (monthly - fee) * (creditPct / 100)
  const ownerCut = monthly - fee - credit
  const preset = models.find(m => m.id === model)
  const isCustom = !!preset && Math.abs(preset.credit_pct - creditPct) > 1e-9

  const save = async () => {
    setSaving(true)
    try {
      const body: any = { model, fee_pct: feePct, credit_pct: creditPct }
      if (rent.trim()) body.monthly_rent = parseFloat(rent)
      if (price.trim()) body.home_price = parseFloat(price)
      if (ownerAddr.trim()) body.owner = ownerAddr.trim()
      await api('terms', { method: 'POST', body })
      toast.success(`Terms live — protocol takes ${feePct}%, ${(100 - feePct).toFixed(1)}% stays with the home`)
      onSaved()
    } catch (err: any) {
      toast.error(err?.message || 'Could not set terms')
    }
    setSaving(false)
  }

  return (
    <div className="grid lg:grid-cols-2 gap-6">
      {/* ── The dial ── */}
      <div className="glass rounded-3xl p-6 md:p-8 border-coral/15">
        <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-coral mb-2">Owner console</div>
        <h3 className="font-serif-ed text-2xl text-white mb-6">Set the deal.</h3>

        <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-2 block">Rent-to-own model</label>
        <div className="grid sm:grid-cols-2 gap-2.5 mb-7">
          {models.map(m => (
            <button key={m.id} onClick={() => pickModel(m)}
              className={`text-left rounded-2xl border p-3.5 transition-colors ${model === m.id ? 'border-coral/50 bg-coral/[0.07]' : 'border-white/[0.08] bg-white/[0.02] hover:border-white/20'}`}>
              <div className="flex items-baseline justify-between gap-2">
                <span className={`text-sm font-bold ${model === m.id ? 'text-coral' : 'text-white/80'}`}>{m.name}</span>
                <span className="text-[11px] font-mono text-white/60 tabular-nums">{m.credit_pct}%</span>
              </div>
              <p className="text-[11px] text-white/60 mt-1 leading-snug">{m.headline}</p>
            </button>
          ))}
        </div>

        {/* Protocol fee — bounded by the contract, not by us */}
        <div className="flex items-baseline justify-between mb-1.5">
          <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold">Protocol fee</label>
          <span className="text-pink font-bold tabular-nums text-sm">{feePct.toFixed(1)}%</span>
        </div>
        <input type="range" min={band.min_pct} max={band.max_pct} step={0.1} value={feePct}
          onChange={e => setFeePct(parseFloat(e.target.value))}
          className="w-full accent-pink cursor-pointer" />
        <div className="flex justify-between text-[10px] text-white/50 font-mono mb-6">
          <span>{band.min_pct}% floor</span>
          <span className="text-white/60">Airbnb takes ~15%</span>
          <span>{band.max_pct}% ceiling — hard-capped in the contract</span>
        </div>

        {/* Rent credit — the model dial */}
        <div className="flex items-baseline justify-between mb-1.5">
          <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold">Rent credit → equity</label>
          <span className="text-coral font-bold tabular-nums text-sm">
            {creditPct.toFixed(0)}%{isCustom && <span className="text-white/55 font-normal"> · custom</span>}
          </span>
        </div>
        <input type="range" min={0} max={100} step={1} value={creditPct}
          onChange={e => setCreditPct(parseFloat(e.target.value))}
          className="w-full accent-coral cursor-pointer" />
        <div className="flex justify-between text-[10px] text-white/50 font-mono mb-6">
          <span>0% — plain lease</span>
          <span>100% — every net dollar buys the house</span>
        </div>

        <div className="grid sm:grid-cols-2 gap-3 mb-5">
          <div>
            <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Monthly payment</label>
            <input type="number" min="0" step="0.1" placeholder="2.0" value={rent} onChange={e => setRent(e.target.value)}
              className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors" />
          </div>
          <div>
            <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Home price</label>
            <input type="number" min="0" step="1" placeholder="120" value={price} onChange={e => setPrice(e.target.value)}
              className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors" />
          </div>
        </div>

        <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">
          Owner address {terms?.owner && <span className="text-white/50 normal-case tracking-normal">· claimed</span>}
        </label>
        <input type="text" placeholder="0x…" value={ownerAddr} onChange={e => setOwnerAddr(e.target.value)}
          className="w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors mb-5" />

        <button onClick={save} disabled={saving}
          className="btn-shine w-full py-4 rounded-xl bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-xl hover:shadow-coral/30 disabled:opacity-30 transition-all">
          {saving ? 'Setting…' : 'Set the terms →'}
        </button>
        <p className="text-[10px] text-white/50 text-center mt-3 leading-relaxed">
          Once an owner address is recorded, only that address can change these terms — the same rule
          <code className="text-white/60"> onlyOwner </code> enforces on-chain.
        </p>
      </div>

      {/* ── The consequence ── */}
      <div className="space-y-6">
        <div className="glass rounded-3xl p-6 md:p-8">
          <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/60 mb-5">
            Every {rent.trim() ? `${monthly} Ξ` : '1 Ξ'} paid
          </div>
          <SplitBar amount={monthly} fee={fee} credit={credit} owner={ownerCut} big className="mb-6" />
          <div className="grid grid-cols-3 gap-3">
            {[
              { k: 'Your equity', v: credit, c: 'text-coral' },
              { k: 'Owner income', v: ownerCut, c: 'text-emerald-400' },
              { k: 'Protocol fee', v: fee, c: 'text-pink' },
            ].map(s => (
              <div key={s.k} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4">
                <div className={`text-xl font-display font-extrabold tabular-nums ${s.c}`}>{s.v.toFixed(3)}</div>
                <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
              </div>
            ))}
          </div>
          <div className="mt-6 pt-5 border-t border-white/[0.07] flex items-baseline justify-between">
            <span className="text-[11px] uppercase tracking-widest text-white/60 font-bold">Stays with the property</span>
            <span className="headline text-3xl text-surf-grad tabular-nums">{(100 - feePct).toFixed(1)}%</span>
          </div>
        </div>

        <div className="glass rounded-3xl p-6 md:p-8">
          <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/60 mb-5">Skimmed off the top</div>
          <TakeComparison ourTake={feePct} />
        </div>
      </div>
    </div>
  )
}

function SplitPageInner() {
  const { data: terms, reload } = useResource<TermsData | null>('terms', null)
  const { data: modelsData } = useResource<{ models: ModelPreset[] } | null>('models', null)
  const { data: rentStats, reload: reloadStats } = useResource<RentStats | null>('rent_stats', null)
  const models = modelsData?.models ?? []

  const refresh = () => { reload(); reloadStats() }

  return (
    <Shell>
      <PageHead
        kicker="Where the rent goes"
        title={<>THEY TAKE 15%.<br /><span className="text-surf-grad">WE TAKE {terms ? terms.fee_pct : '1–5'}%.</span></>}>
        {terms ? `${terms.to_property_pct}%` : '95–99%'} of every payment stays with the property —
        split between the renter's equity and the owner's income by whichever rent-to-own model the owner picked.
        The 1–5% band is a constant in the contract, not a promise on a pricing page.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8">
        {/* Live deal summary — only once someone has actually set terms */}
        {terms && terms.updated > 0 && (
          <Reveal>
            <div className="glass rounded-3xl p-6 md:p-7 mb-8 grid sm:grid-cols-2 lg:grid-cols-4 gap-5">
              {[
                { k: 'Live model', v: terms.custom ? `${terms.model_name} · tuned` : terms.model_name, c: 'text-white' },
                { k: 'Protocol take', v: `${terms.fee_pct}%`, c: 'text-pink' },
                { k: 'To renter equity', v: `${terms.equity_pct_of_rent}%`, c: 'text-coral' },
                { k: 'To owner income', v: `${terms.owner_pct_of_rent}%`, c: 'text-emerald-400' },
              ].map(s => (
                <div key={s.k}>
                  <div className={`text-2xl font-display font-extrabold tabular-nums ${s.c}`}>{s.v}</div>
                  <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                </div>
              ))}
            </div>
          </Reveal>
        )}

        <Reveal delay={60}>
          <TermsDesk terms={terms} models={models} onSaved={refresh} />
        </Reveal>

        {/* What has actually been paid, if anything has */}
        {rentStats && rentStats.payments > 0 && (
          <Reveal delay={100}>
            <div className="glass rounded-3xl p-6 md:p-8 mt-8">
              <div className="flex items-baseline justify-between flex-wrap gap-3 mb-6">
                <h2 className="font-display font-bold text-white uppercase tracking-wider text-sm">Rent recorded so far</h2>
                <span className="text-[11px] text-white/58">
                  {rentStats.payments} payment{rentStats.payments === 1 ? '' : 's'} · {rentStats.renters} renter{rentStats.renters === 1 ? '' : 's'}
                </span>
              </div>
              <SplitBar amount={rentStats.gross_rent} fee={rentStats.protocol_fees}
                credit={rentStats.renter_equity} owner={rentStats.owner_income} big className="mb-6" />
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                  { k: 'Gross rent', v: `${formatNum(rentStats.gross_rent, 3)} Ξ`, c: 'text-white' },
                  { k: 'Renter equity', v: `${formatNum(rentStats.renter_equity, 3)} Ξ`, c: 'text-coral' },
                  { k: 'Owner income', v: `${formatNum(rentStats.owner_income, 3)} Ξ`, c: 'text-emerald-400' },
                  { k: 'Protocol fees', v: `${formatNum(rentStats.protocol_fees, 3)} Ξ`, c: 'text-pink' },
                ].map(s => (
                  <div key={s.k} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4">
                    <div className={`text-lg font-display font-extrabold tabular-nums ${s.c}`}>{s.v}</div>
                    <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-white/55 mt-5">
                {rentStats.to_property_pct}% of everything paid stayed with the property.
                An Airbnb-rate platform would have taken {formatNum(rentStats.gross_rent * 0.15, 3)} Ξ instead of {formatNum(rentStats.protocol_fees, 3)} Ξ.
              </p>
            </div>
          </Reveal>
        )}

        <p className="text-white/55 text-sm mt-10 text-center">
          Want to see what that split does over ten years? <Link href="/simulator" className="text-coral hover:underline">Run the simulator →</Link>
        </p>
      </div>

      <NextUp here="/split" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(SplitPageInner), { ssr: false })
