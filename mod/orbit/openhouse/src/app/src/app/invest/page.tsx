/* /invest — the house itself: who owns how much of it, a rent check you can
   write, and where the last ones went.

   The whole pitch is "every check buys the house", so the page leads with
   the renter's side — the ownership bar, a pay-rent form that previews the
   split before it books, and the ledger. The investor float (shares) still
   lives here, folded below, because the building needs both seats.

   Everything that writes reloads terms + stats + ledger, so the bar and the
   table move under you. */

"use client";

import dynamic from 'next/dynamic'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'react-toastify'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal, SplitBar } from '../../components/motion'
import { PropertyData, RentStats, StatusData, TermsData, api, formatNum, timeAgo, useResource } from '../../lib/api'
import { useCurrency } from '../../lib/currency'
import { STAGE } from '../../lib/whitepaper'

interface LedgerEntry {
  timestamp: number; renter: string; amount: number; fee: number
  credit: number; owner_income: number; kind: string; source?: { bank?: string }
}
interface Quote { amount: number; fee: number; credit: number; owner_income: number }
interface Equity { payments: number; rent_paid: number; principal: number; equity_pct: number; remaining: number; fully_owned: boolean }

const short = (a: string) => (a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a)
const isAddr = (a: string) => /^0x[0-9a-fA-F]{40}$/.test(a.trim())

/** "DEMO — 128 Maple Ave, Cleveland OH · 3-bed brick rowhouse. Fictional…"
 *  → title "128 Maple Ave, Cleveland OH", sub "3-bed brick rowhouse. Fictional…".
 *  The description is one free-text field; a paragraph is not a headline. */
function splitDescription(d: string) {
  const demo = /^DEMO\s*[—-]\s*/i.test(d)
  const body = d.replace(/^DEMO\s*[—-]\s*/i, '')
  const cut = body.search(/ · |\. /)
  if (cut < 0) return { demo, title: body, sub: '' }
  return { demo, title: body.slice(0, cut).replace(/\.$/, ''), sub: body.slice(cut).replace(/^( · |\. )/, '') }
}

/** A fresh, valid-looking address for "try it as a new renter". */
function randomAddr() {
  const b = new Uint8Array(20); crypto.getRandomValues(b)
  return '0x' + Array.from(b, x => x.toString(16).padStart(2, '0')).join('')
}

function InvestInner() {
  const { data: status, reload: reloadStatus } = useResource<StatusData | null>('status', null)
  const { data: property, reload: reloadProperty } = useResource<PropertyData | null>('property', null)
  const { data: terms, reload: reloadTerms } = useResource<TermsData | null>('terms', null)
  const { data: stats, reload: reloadStats } = useResource<RentStats | null>('rent_stats', null)
  const { data: ledger, loading, reload: reloadLedger } = useResource<LedgerEntry[]>('rent_ledger', [])
  const { fiat } = useCurrency()

  const refresh = () => { reloadStatus(); reloadProperty(); reloadTerms(); reloadStats(); reloadLedger() }

  const price = stats?.home_price || terms?.home_price || 0
  const ownedPct = stats?.owned_pct ?? 0
  const paidOff = price > 0 && ownedPct >= 99.9999

  // Every renter's stake, biggest first — the cap table the rent wrote.
  const holders = useMemo(() => {
    const m = new Map<string, { principal: number; payments: number }>()
    for (const r of ledger) {
      const h = m.get(r.renter) || { principal: 0, payments: 0 }
      h.principal += r.credit; h.payments += 1; m.set(r.renter, h)
    }
    return [...m.entries()].map(([addr, h]) => ({ addr, ...h, pct: price > 0 ? (h.principal / price) * 100 : 0 }))
      .sort((a, b) => b.principal - a.principal)
  }, [ledger, price])

  const desc = property?.deployed ? splitDescription(property.description || '') : null

  /* ── pay rent ── */
  const [renter, setRenter] = useState('')
  const [amount, setAmount] = useState('')
  const [quote, setQuote] = useState<Quote | null>(null)
  const [paying, setPaying] = useState(false)
  const [mine, setMine] = useState<Equity | null>(null)

  // Default the amount to this house's rent once terms land.
  useEffect(() => { if (terms && !amount) setAmount(String(terms.monthly_rent)) }, [terms]) // eslint-disable-line react-hooks/exhaustive-deps

  // Preview the split the server would book — debounced, never writes.
  useEffect(() => {
    const n = parseFloat(amount)
    if (!(n > 0)) { setQuote(null); return }
    const t = setTimeout(() => {
      api('quote', { method: 'POST', body: { amount: n } }).then(setQuote).catch(() => setQuote(null))
    }, 250)
    return () => clearTimeout(t)
  }, [amount, stats?.renter_equity])

  // Show the payer's stake as soon as the address is a real one.
  useEffect(() => {
    if (!isAddr(renter)) { setMine(null); return }
    api(`equity/${renter.trim()}`).then(setMine).catch(() => setMine(null))
  }, [renter, stats?.renter_equity])

  const pay = async () => {
    if (!isAddr(renter)) { toast.error('Paste a 0x address (40 hex characters), or pick a renter below'); return }
    setPaying(true)
    try {
      const r = await api('pay_rent', { method: 'POST', body: { renter: renter.trim(), amount: parseFloat(amount) } })
      toast.success(`Booked. ${r.credit.toFixed(4)} Ξ became equity — you now own ${r.equity.equity_pct.toFixed(2)}% of the house.`)
      refresh()
    } catch (e: any) { toast.error(e?.message || 'Payment refused') }
    setPaying(false)
  }

  /* ── shares (investor side) ── */
  const [buyAddr, setBuyAddr] = useState('')
  const [buyShares, setBuyShares] = useState('')
  const [purchasing, setPurchasing] = useState(false)
  const floatOpen = !!(property?.deployed && status?.deployed && status.total_shares > 0)
  const soldPct = floatOpen && status ? (status.shares_sold / status.total_shares) * 100 : 0
  const sharePrice = floatOpen && property ? parseFloat(property.share_price || '0') : 0
  const cost = (parseInt(buyShares || '0') || 0) * sharePrice

  const purchase = async () => {
    setPurchasing(true)
    try {
      const r = await api('purchase', { method: 'POST', body: { buyer: buyAddr.trim(), share_count: parseInt(buyShares), payment: 0 } })
      if (r.success) { toast.success(`You hold ${r.shares_purchased} more shares.`); setBuyAddr(''); setBuyShares(''); refresh() }
    } catch (e: any) { toast.error(e?.message || 'Purchase failed') }
    setPurchasing(false)
  }

  const input = 'w-full text-sm px-4 py-3 rounded-xl border border-white/10 bg-white/5 text-white placeholder:text-white/45 focus:outline-none focus:border-coral/50 font-mono transition-colors'
  const label = 'text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block'

  return (
    <Shell>
      <PageHead kicker={desc?.demo ? 'The house · demo' : 'The house'} title={desc?.title || 'Rent toward ownership.'}
        aside={
          <button onClick={refresh} disabled={loading}
            className="px-4 py-2 rounded-full border border-white/12 text-[11px] font-bold uppercase tracking-widest text-white/68 hover:text-white hover:border-white/30 disabled:opacity-30 transition-colors">
            {loading ? 'Syncing…' : '↻ Refresh'}
          </button>
        }>
        {desc?.sub}
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8 space-y-6">
        <div className="grid lg:grid-cols-5 gap-6">
          {/* ── who owns it ── */}
          <Reveal className="lg:col-span-3">
            <div className="glass rounded-3xl p-7 md:p-9 h-full">
              <div className="flex items-start justify-between gap-4 mb-6">
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-coral mb-2">Owned by its renters</div>
                  <div className="flex items-baseline flex-wrap gap-x-3">
                    <span className="headline text-6xl text-white tabular-nums">{ownedPct.toFixed(1)}<span className="text-3xl">%</span></span>
                    <span className="text-white/60 text-sm">of {formatNum(price)} Ξ{fiat(price) ? ` · ≈ ${fiat(price)}` : ''}</span>
                  </div>
                </div>
                {property && (
                  <span className={`shrink-0 text-[11px] font-bold uppercase tracking-widest px-3 py-1.5 rounded-full border ${paidOff ? 'text-coral border-coral/30 bg-coral/10' : property.is_active ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10' : 'text-white/60 border-white/15'}`}>
                    {paidOff ? 'Paid off' : property.is_active ? '● Taking rent' : 'Paused'}
                  </span>
                )}
              </div>

              {/* one segment per renter, the rest still the owner's */}
              <div className="flex h-5 rounded-full overflow-hidden bg-white/[0.06] mb-2">
                {holders.map((h, i) => (
                  <div key={h.addr} title={`${short(h.addr)}: ${h.pct.toFixed(2)}%`} className="transition-all duration-700"
                    style={{ width: `${h.pct}%`, background: i % 2 ? 'var(--peach)' : 'var(--coral)', marginRight: 2 }} />
                ))}
              </div>
              <div className="flex justify-between text-[11px] text-white/55 mb-7">
                <span>{formatNum(stats?.renter_equity ?? 0, 4)} Ξ paid in as equity</span>
                <span>{formatNum(Math.max(price - (stats?.renter_equity ?? 0), 0), 4)} Ξ left to own</span>
              </div>

              {holders.length ? (
                <div className="space-y-2 mb-7">
                  {holders.map((h, i) => (
                    <button key={h.addr} onClick={() => setRenter(h.addr)} title="Pay as this renter"
                      className="w-full flex items-center gap-3 rounded-xl px-3 py-2 bg-white/[0.03] border border-white/[0.06] hover:border-coral/40 transition-colors text-left">
                      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: i % 2 ? 'var(--peach)' : 'var(--coral)' }} />
                      <span className="font-mono text-xs text-white/80 flex-1">{short(h.addr)}</span>
                      <span className="text-[11px] text-white/55">{h.payments} payment{h.payments === 1 ? '' : 's'}</span>
                      <span className="text-sm font-bold text-coral tabular-nums w-20 text-right">{h.pct.toFixed(2)}%</span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-white/60 text-sm mb-7">No rent booked yet. The first check below starts the cap table.</p>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {[
                  { k: 'Rent paid', v: `${formatNum(stats?.gross_rent ?? 0, 3)} Ξ`, c: 'text-white' },
                  { k: 'Stayed with the home', v: `${formatNum(stats?.to_property_pct ?? 0, 1)}%`, c: 'text-emerald-400' },
                  { k: 'Protocol took', v: `${formatNum(stats?.protocol_fees ?? 0, 4)} Ξ`, c: 'text-pink' },
                ].map(s => (
                  <div key={s.k} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4">
                    <div className={`text-lg md:text-xl font-display font-extrabold ${s.c} tabular-nums`}>{s.v}</div>
                    <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                  </div>
                ))}
              </div>
            </div>
          </Reveal>

          {/* ── pay rent ── */}
          <Reveal className="lg:col-span-2" delay={80}>
            <div className="glass rounded-3xl p-7 md:p-9 h-full flex flex-col border-coral/20">
              <div className="flex items-center justify-between gap-3 mb-2">
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-coral">Pay this month</div>
                <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.15em] text-onaccent bg-coral rounded px-1.5 py-0.5">{STAGE.stage}</span>
              </div>
              <h2 className="font-serif-ed text-2xl text-white mb-2">Write a rent check</h2>
              <p className="text-[11px] text-white/60 leading-relaxed mb-5">
                Booked on this node's ledger through the real code — no signature, no real money.
                {terms ? ` This house runs ${terms.model_name.toLowerCase()} at a ${terms.fee_pct}% fee.` : ''}
              </p>

              <label className={label}>Renter</label>
              <input type="text" placeholder="0x…" value={renter} onChange={e => setRenter(e.target.value)} className={input} />
              <div className="flex gap-3 mt-1.5 mb-4 text-[10px] uppercase tracking-widest font-bold">
                <button onClick={() => setRenter(randomAddr())} className="text-coral hover:underline">+ new renter</button>
                {holders[0] && <button onClick={() => setRenter(holders[0].addr)} className="text-white/55 hover:text-white">top renter</button>}
              </div>

              <label className={label}>Amount (Ξ){terms ? ` · rent is ${terms.monthly_rent}` : ''}</label>
              <input type="number" min="0" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} className={input} />
              {fiat(parseFloat(amount) || 0) && <div className="text-[11px] text-white/50 mt-1.5">≈ {fiat(parseFloat(amount) || 0)}</div>}

              <div className="my-5">
                {quote ? (
                  <>
                    <SplitBar amount={quote.amount} fee={quote.fee} credit={quote.credit} owner={quote.owner_income} />
                    <p className="text-[11px] text-white/60 mt-2">
                      {quote.credit > 0
                        ? <><b className="text-coral">{quote.credit.toFixed(4)} Ξ</b> of this check becomes equity.</>
                        : 'Nothing left to buy — the ledger will refuse this payment.'}
                    </p>
                  </>
                ) : <div className="h-10" />}
              </div>

              {mine && mine.payments > 0 && (
                <div className="rounded-xl bg-coral/[0.06] border border-coral/20 px-4 py-3 mb-5 text-xs text-white/75">
                  This renter owns <b className="text-coral">{mine.equity_pct.toFixed(2)}%</b> after {mine.payments} payment{mine.payments === 1 ? '' : 's'}
                  {' '}({formatNum(mine.principal, 4)} Ξ of {formatNum(mine.rent_paid, 4)} Ξ paid).
                </div>
              )}

              <button onClick={pay} disabled={paying || paidOff || !renter.trim() || !(parseFloat(amount) > 0) || property?.is_active === false}
                className="btn-shine w-full py-4 rounded-xl bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-xl hover:shadow-coral/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all mt-auto">
                {paidOff ? 'House is paid off' : paying ? 'Booking…' : 'Pay rent (simulated) →'}
              </button>
            </div>
          </Reveal>
        </div>

        {/* ── the ledger ── */}
        <Reveal>
          <div className="glass rounded-3xl p-7 md:p-9">
            <div className="flex items-baseline justify-between mb-4">
              <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-coral">The ledger</div>
              <span className="text-[11px] text-white/55">{stats?.payments ?? 0} payments · {stats?.renters ?? 0} renters</span>
            </div>
            {ledger.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-xs tabular-nums whitespace-nowrap">
                  <thead className="text-[10px] uppercase tracking-widest text-white/55">
                    <tr>{['when', 'renter', 'paid', 'equity', 'owner', 'fee', 'via'].map(h =>
                      <th key={h} className="text-right first:text-left [&:nth-child(2)]:text-left py-2 pr-4 font-bold">{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {ledger.slice(0, 12).map((r, i) => (
                      <tr key={i} className="border-t border-white/[0.06]">
                        <td className="py-2 pr-4 text-white/60">{timeAgo(r.timestamp)}</td>
                        <td className="py-2 pr-4 font-mono text-white/80">{short(r.renter)}</td>
                        <td className="py-2 pr-4 text-right text-white">{r.amount.toFixed(4)}</td>
                        <td className="py-2 pr-4 text-right text-coral">{r.credit.toFixed(4)}</td>
                        <td className="py-2 pr-4 text-right text-emerald-400">{r.owner_income.toFixed(4)}</td>
                        <td className="py-2 pr-4 text-right text-pink">{r.fee.toFixed(4)}</td>
                        <td className="py-2 pr-4 text-right text-white/55">{r.source?.bank ? 'bank' : r.kind === 'option' ? 'option' : 'direct'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {ledger.length > 12 && <div className="text-[11px] text-white/50 mt-3">+ {ledger.length - 12} older</div>}
              </div>
            ) : <p className="text-white/60 text-sm">Empty. Pay the first month above.</p>}
          </div>
        </Reveal>

        {/* ── the investor seat ── */}
        <Reveal>
          <details className="glass rounded-3xl p-7 md:p-9 group">
            <summary className="cursor-pointer list-none flex items-center justify-between gap-4">
              <div>
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-emerald-400 mb-1">The other seat</div>
                <h2 className="font-serif-ed text-2xl text-white">Back the building — {floatOpen ? `${soldPct.toFixed(0)}% of the float sold` : 'float not open'}</h2>
              </div>
              <span className="text-white/55 text-xl transition-transform group-open:rotate-45">+</span>
            </summary>
            <div className="grid md:grid-cols-2 gap-8 mt-6">
              <div>
                <p className="text-sm text-white/70 leading-relaxed mb-5">
                  Owners fund the purchase and earn the owner-income leg of each check plus yield on pooled funds.
                  Shares are pro-rata claims on the wrapped entity; in simulation they are ledger entries with no real-world claim.
                </p>
                {floatOpen && status && (
                  <div className="grid grid-cols-3 gap-3">
                    {[
                      { k: 'Share price', v: `${property!.share_price} Ξ` },
                      { k: 'Available', v: formatNum(status.available_shares) },
                      { k: 'Holders', v: formatNum(status.shareholders) },
                    ].map(s => (
                      <div key={s.k} className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-3">
                        <div className="text-lg font-display font-extrabold text-white tabular-nums">{s.v}</div>
                        <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <label className={label}>Wallet</label>
                <input type="text" placeholder="0x…" value={buyAddr} onChange={e => setBuyAddr(e.target.value)} className={`${input} mb-4`} />
                <label className={label}>Shares</label>
                <input type="number" min="1" placeholder="10" value={buyShares} onChange={e => setBuyShares(e.target.value)} className={`${input} mb-4`} />
                <div className="flex justify-between items-baseline mb-4">
                  <span className="text-white/60 text-xs uppercase tracking-widest font-bold">Total</span>
                  <span className="text-xl font-bold text-white tabular-nums">{cost ? `${cost.toFixed(4)} Ξ` : '—'}</span>
                </div>
                <button onClick={purchase} disabled={!floatOpen || purchasing || !isAddr(buyAddr) || !(parseInt(buyShares) > 0)}
                  className="w-full py-3 rounded-xl border border-emerald-500/40 text-emerald-400 font-bold uppercase tracking-widest text-xs hover:bg-emerald-500/10 disabled:opacity-30 disabled:cursor-not-allowed transition-all">
                  {!floatOpen ? 'Float not open' : purchasing ? 'Minting…' : 'Buy shares (simulated)'}
                </button>
              </div>
            </div>
          </details>
        </Reveal>
      </div>

      <NextUp here="/invest" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(InvestInner), { ssr: false })
