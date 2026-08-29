/* /captable — who owns what, and what has been paid out.

   The two lists used to sit in a pair of max-h-[28rem] scroll boxes at the
   bottom of a page that was itself a scroll. On their own page they get
   the room to just be lists. */

"use client";

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { toast } from 'react-toastify'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal } from '../../components/motion'
import { DividendRecord, Shareholder, StatusData, formatNum, timeAgo, useResource } from '../../lib/api'

function CapTableInner() {
  const { data: shareholders } = useResource<Shareholder[]>('shareholders', [])
  const { data: dividends } = useResource<DividendRecord[]>('dividends', [])
  const { data: status } = useResource<StatusData | null>('status', null)

  const holders = Array.isArray(shareholders) ? shareholders : []
  const payouts = Array.isArray(dividends) ? dividends : []

  return (
    <Shell>
      <PageHead kicker="Radical Transparency" title="The cap table is public.">
        Every holder, every share, every distribution — read straight off the contract's mirror.
        There is no private ledger behind this one.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8">
        <div className="grid lg:grid-cols-2 gap-6">
          <Reveal>
            <div className="glass rounded-3xl overflow-hidden h-full">
              <div className="px-6 py-4 border-b border-white/[0.07] flex items-center justify-between">
                <h2 className="font-display font-bold text-white uppercase tracking-wider text-sm">Owners</h2>
                <span className="text-[11px] text-white/58">{holders.length} on the cap table</span>
              </div>
              {holders.length === 0 ? (
                <div className="py-20 px-6 text-center">
                  <div className="text-white/50 text-sm uppercase tracking-widest">Be the first owner</div>
                  <Link href="/invest" className="inline-block mt-4 text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">Take a position →</Link>
                </div>
              ) : (
                <div className="divide-y divide-white/[0.04]">
                  {holders.map(sh => (
                    <button key={sh.address} onClick={() => { navigator.clipboard.writeText(sh.address); toast.success('Address copied') }} className="w-full grid grid-cols-[1fr_auto_auto] gap-4 items-center px-6 py-4 hover:bg-white/[0.03] transition-colors text-left">
                      <span className="font-mono text-xs text-white/72 truncate" title={sh.address}>{sh.address}</span>
                      <span className="text-xs text-coral font-bold tabular-nums">{formatNum(sh.shares, 0)} sh</span>
                      <span className="text-xs text-white/60 tabular-nums w-14 text-right">{sh.ownership_pct}%</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Reveal>

          <Reveal delay={80}>
            <div className="glass rounded-3xl overflow-hidden h-full">
              <div className="px-6 py-4 border-b border-white/[0.07] flex items-center justify-between">
                <h2 className="font-display font-bold text-white uppercase tracking-wider text-sm">Distributions</h2>
                <span className="text-[11px] text-white/58">{formatNum(status?.total_dividends_distributed ?? 0)} Ξ paid</span>
              </div>
              {payouts.length === 0 ? (
                <div className="py-20 px-6 text-center">
                  <div className="text-white/50 text-sm uppercase tracking-widest">Rent flows here</div>
                  <Link href="/split" className="inline-block mt-4 text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">See the split →</Link>
                </div>
              ) : (
                <div className="divide-y divide-white/[0.04]">
                  {[...payouts].reverse().map((d, i) => (
                    <div key={i} className="grid grid-cols-[1fr_auto_auto] gap-4 items-center px-6 py-4">
                      <span className="text-xs text-white/65">{timeAgo(d.timestamp)}</span>
                      <span className="text-xs text-emerald-400 font-bold tabular-nums">{formatNum(d.total_amount)} Ξ</span>
                      <span className="text-[11px] text-white/58 w-20 text-right">{d.recipients} owners</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Reveal>
        </div>

        {status?.deployed && (
          <Reveal delay={120}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6">
              {[
                { k: 'Shares sold', v: formatNum(status.shares_sold), c: 'text-coral' },
                { k: 'Available', v: formatNum(status.available_shares), c: 'text-emerald-400' },
                { k: 'Contributed', v: `${formatNum(status.total_contributed, 3)} Ξ`, c: 'text-white' },
                { k: 'Distributions', v: formatNum(status.dividend_count, 0), c: 'text-pink' },
              ].map(s => (
                <div key={s.k} className="glass rounded-2xl p-5">
                  <div className={`text-2xl font-display font-extrabold tabular-nums ${s.c}`}>{s.v}</div>
                  <div className="text-[10px] uppercase tracking-widest text-white/58 mt-1 font-bold">{s.k}</div>
                </div>
              ))}
            </div>
          </Reveal>
        )}
      </div>

      <NextUp here="/captable" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(CapTableInner), { ssr: false })
