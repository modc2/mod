/* /near — OpenHouse on NEAR.

   Two things, both read live from the chain / indexer, nothing written here:
     1. The verifier — the WASM contract that checks the bank's proof of rent
        paid. Program hash vs this node's, who the seated banks are, whether
        this month is paid.
     2. The rent rail — a NEAR account that takes rent in native NEAR. Each
        renter links the account they pay from; the node books every transfer
        from it as their rent, once. */

"use client";

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal } from '../../components/motion'
import { NearStatus, timeAgo, useResource } from '../../lib/api'

const short = (h?: string | null, n = 12) => (h ? (h.length > n + 8 ? `${h.slice(0, n)}…${h.slice(-6)}` : h) : '—')
const near = (n?: number | null) => (n == null ? '—' : `${n.toLocaleString(undefined, { maximumFractionDigits: 4 })} Ⓝ`)

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2 border-b border-white/[0.06] last:border-0">
      <span className="text-[11px] uppercase tracking-widest text-white/45 shrink-0">{k}</span>
      <span className="text-sm text-white/85 text-right min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </div>
  )
}

function Pill({ ok, children }: { ok: boolean | null | undefined; children: React.ReactNode }) {
  const tone = ok ? 'text-emerald-400' : ok === false ? 'text-amber-300' : 'text-white/45'
  return <span className={`text-[10px] font-bold uppercase tracking-widest ${tone}`}>{children}</span>
}

function NearInner() {
  const { data, loaded, error } = useResource<NearStatus | null>('near', null)
  const v = data?.verifier
  const r = data?.rent_rail
  const bank = v?.banks?.[0]
  const period = v?.this_month?.period
  const explorer = v?.explorer && v.account ? `${v.explorer}/address/${v.account}` : ''

  return (
    <Shell>
      <PageHead kicker="NEAR · verifier + rent rail" title="Rent in NEAR. Proof on NEAR.">
        A WASM contract on NEAR checks that this home, this month, was paid — and a NEAR account can take the rent itself,
        booked as the renter&rsquo;s equity the moment it lands. Everything on this page is read live from the chain.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8 space-y-16">
        {error && <p className="text-sm text-amber-300">The API did not answer — {error}.</p>}
        {!loaded && <p className="text-sm text-white/50">Reading NEAR…</p>}

        {data && v && r && (
          <>
            <section className="grid gap-4 md:grid-cols-2">
              <Reveal>
                <div className="glass rounded-2xl p-6 h-full">
                  <div className="flex items-baseline justify-between gap-3 mb-3">
                    <div className="text-white font-bold">The verifier</div>
                    <Pill ok={v.live}>{v.live ? `live on ${v.network}` : 'unreachable'}</Pill>
                  </div>
                  <Row k="Contract">{explorer ? <a href={explorer} target="_blank" rel="noreferrer" className="font-mono text-[12px] text-coral hover:underline">{v.account}</a> : (v.account || '—')}</Row>
                  <Row k="Program hash">
                    <span className="font-mono text-[11px]" title={v.program_hash}>{short(v.program_hash)}</span>{' '}
                    {v.program_hash_matches_local != null && <Pill ok={v.program_hash_matches_local}>{v.program_hash_matches_local ? 'matches this node' : 'differs from this node'}</Pill>}
                  </Row>
                  <Row k="Seated bank">{bank ? <><span>{bank.name}</span> <span className="font-mono text-[11px] text-white/50">{short(bank.key, 14)}</span></> : '—'}</Row>
                  <Row k="Properties">{v.properties ?? '—'}</Row>
                  <Row k={period ? `Paid ${String(period).slice(0, 4)}-${String(period).slice(4)}` : 'This month'}>
                    {v.this_month ? <Pill ok={v.this_month.paid}>{v.this_month.paid ? 'paid' : 'not yet'}</Pill> : '—'}
                  </Row>
                  <Row k="Post-quantum door">{v.pq_door === false ? <Pill ok={false}>this deployment predates it</Pill> : <Pill ok={v.pq_required}>{v.pq_required ? 'classical door shut' : 'available'}</Pill>}</Row>
                  <Row k="Relayed by this node">{v.receipts_on_chain} receipt{v.receipts_on_chain === 1 ? '' : 's'}</Row>
                  {v.error && <p className="text-[12px] text-amber-300 mt-3">{v.error}</p>}
                  <Link href="/proofs" className="inline-block mt-4 text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">How the proof works →</Link>
                </div>
              </Reveal>

              <Reveal delay={60}>
                <div className="glass rounded-2xl p-6 h-full">
                  <div className="flex items-baseline justify-between gap-3 mb-3">
                    <div className="text-white font-bold">The rent rail</div>
                    <Pill ok={r.connected}>{r.connected ? `reading ${r.network}` : 'not connected'}</Pill>
                  </div>
                  <Row k="Rent account">{r.account_id ? <span className="font-mono text-[12px]">{r.account_id}</span> : '—'}</Row>
                  {r.connected && <Row k="Balance">{near(r.balance)}</Row>}
                  <Row k="Monthly rent">
                    {r.monthly_rent_near ? <>{near(r.monthly_rent_near)} <span className="text-white/45 text-[12px]">({r.monthly_rent_eth} Ξ)</span></> : '—'}
                  </Row>
                  <Row k="Rate">
                    {r.rate.near_per_eth ? <>1 Ξ ≈ {r.rate.near_per_eth.toLocaleString(undefined, { maximumFractionDigits: 1 })} Ⓝ <Pill ok={r.rate.source === 'live' || r.rate.source === 'cache'}>{r.rate.source}</Pill></> : '—'}
                  </Row>
                  {r.connected && <Row k="Booked">{r.booked_transfers ?? 0} transfer{r.booked_transfers === 1 ? '' : 's'} · {r.booked_units ?? 0} Ξ · {r.linked_payers ?? 0} linked payer{r.linked_payers === 1 ? '' : 's'}</Row>}
                  {r.error && <p className="text-[12px] text-amber-300 mt-3">{r.error}</p>}
                  <p className="text-[12px] text-white/55 leading-snug mt-4">
                    A native NEAR transfer has no memo, so a renter is recognised by the account they pay <em>from</em>: link it once and every transfer from it is their rent, converted to Ξ at the live rate and booked exactly once.
                  </p>
                </div>
              </Reveal>
            </section>

            {r.connected && (
              <section>
                <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-4">Recent transfers in</div>
                {r.recent.length === 0 ? <p className="text-sm text-white/55">Nothing has arrived at {r.account_id} yet.</p> : (
                  <div className="glass rounded-2xl overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead><tr className="text-left text-[10px] uppercase tracking-widest text-white/40">
                        <th className="p-4">From</th><th className="p-4">Amount</th><th className="p-4">When</th><th className="p-4">Status</th>
                      </tr></thead>
                      <tbody>
                        {r.recent.map(t => (
                          <tr key={t.id} className="border-t border-white/[0.06]">
                            <td className="p-4 font-mono text-[12px] text-white/85">{t.counterparty}</td>
                            <td className="p-4 text-white">{near(t.amount)}</td>
                            <td className="p-4 text-white/55">{timeAgo(t.date)}</td>
                            <td className="p-4"><Pill ok={t.booked ? true : t.linked ? null : false}>{t.booked ? 'booked as rent' : t.linked ? 'linked · waiting' : 'unlinked payer'}</Pill></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            )}

            <section className="grid gap-4 md:grid-cols-2">
              <div className="glass rounded-2xl p-6">
                <div className="text-white font-bold mb-2">Turn it on</div>
                <ol className="text-sm text-white/70 space-y-1.5 list-decimal pl-5">
                  <li>Connect the rent account: <code className="text-coral">openhouse_bank_connect</code> with <code>kind=near</code> and <code>account_id</code> (operator key).</li>
                  <li>Link each renter: <code className="text-coral">openhouse_bank_link</code> with their address and <code>payer_name</code> = the NEAR account they pay from.</li>
                  <li><code className="text-coral">openhouse_bank_reconcile</code> books every transfer from a linked account once. Add <code>issue_proofs=true</code> and each booking also gets a receipt for the verifier.</li>
                </ol>
              </div>
              <div className="glass rounded-2xl p-6">
                <div className="text-white font-bold mb-2">Over MCP</div>
                <pre className="text-[11px] text-white/75 bg-black/30 rounded-lg p-3 overflow-x-auto">{`openhouse_near           this page, as JSON
openhouse_bank_kinds     lists the near adapter
openhouse_bank_connect   kind = near
openhouse_bank_link      payer_name = NEAR account
openhouse_bank_reconcile issue_proofs = true
openhouse_proof_submit   chain = near`}</pre>
                <p className="text-[12px] text-white/50 mt-3">Read-only: this node never holds a renter&rsquo;s NEAR key. Rent is paid by the renter&rsquo;s own wallet.</p>
              </div>
            </section>
          </>
        )}
      </div>

      <NextUp here="/near" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(NearInner), { ssr: false })
