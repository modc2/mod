'use client'

import { Section, Stat, KV, Loading, Note } from '@/components/chrome'
import { useResource, fmt, pct, short, ContractDescribe, OnchainPool, DEMO_POOL } from '@/lib/api'

export default function ContractPage() {
  const contract = useResource<ContractDescribe>('/contract')
  const live = useResource<OnchainPool>(DEMO_POOL ? `/onchain?address=${DEMO_POOL}` : null)

  return (
    <>
      <div className="hero">
        <h1>The contract is the policy</h1>
        <p>
          Three Solidity files — the mutual, a factory that clones it, and an optional signed
          oracle. One audited bytecode, cloned per pool (EIP-1167), fee capped by a constant
          the owner cannot change. Everything below is read live off the chain.
        </p>
      </div>

      <Section title="Guarantees" sub="Enforced by the bytecode, frozen per claim at filing.">
        {contract.loading && <Loading />}
        {contract.error && <Note tone="error">{contract.error}</Note>}
        {contract.data && (
          <ul className="guarantees">
            {contract.data.guarantees.map((g) => <li key={g}>{g}</li>)}
          </ul>
        )}
      </Section>

      {DEMO_POOL && (
        <Section title="A live pool, read off the chain"
          sub={live.data ? `${live.data.name} · ${short(live.data.address)} · ${live.data.network} network · oracle ${live.data.oracle_mode}` : undefined}>
          {live.loading && <Loading />}
          {live.error && <Note tone="error">The local chain is not answering: {live.error}</Note>}
          {live.data && (
            <>
              <div className="stats" style={{ marginBottom: 16 }}>
                <Stat label="Members" value={fmt(live.data.counts.members)} />
                <Stat label="Agents" value={fmt(live.data.counts.agents)} />
                <Stat label="Claims" value={fmt(live.data.counts.claims)} />
                <Stat label="Solvent" tone={live.data.solvency.solvent ? 'good' : 'bad'}
                  value={live.data.solvency.solvent ? 'yes' : 'NO'}
                  sub={live.data.solvency.reconciles ? 'books reconcile on chain' : 'books do NOT reconcile'} />
              </div>
              <Note>{live.data.solvency.verdict}</Note>
              <div className="grid-2" style={{ marginTop: 16 }}>
                <div>
                  <h3>Money ({live.data.symbol})</h3>
                  <KV rows={[
                    ['Premiums in', fmt(live.data.money.premiums_in as string, 2)],
                    ['Paid in claims', fmt(live.data.money.paid_in_claims as string, 2)],
                    ['Returned to members', fmt(live.data.money.returned_to_members as string, 2)],
                    ['Balance', fmt(live.data.money.balance as string, 2)],
                    ['Open exposure', fmt(live.data.money.open_exposure as string, 2)],
                    ['Unfunded claims', fmt(live.data.money.unfunded_claims as string, 2)],
                    ['Reserve floor', fmt(live.data.money.reserve_floor as string, 2)],
                    ['Loss ratio', pct(Number(live.data.money.loss_ratio))],
                  ]} />
                </div>
                <div>
                  <h3>The provider&apos;s profit, public</h3>
                  <KV rows={[
                    ['Fee now', `${live.data.provider.fee_pct_now}%`],
                    ['Fee cap (constant)', `${live.data.provider.fee_cap_bps / 100}%`],
                    ['Profit accrued', fmt(live.data.provider.profit_accrued, 2)],
                    ['Profit withdrawn', fmt(live.data.provider.profit_withdrawn, 2)],
                    ['Provider share of premium', pct(live.data.provider.profit_share_of_premium)],
                    ['Member share of premium', pct(live.data.provider.member_share_of_premium)],
                    ['Pending fee raise', live.data.provider.pending_fee_bps === null ? 'none' : `${live.data.provider.pending_fee_bps} bps`],
                    ['Raise notice', live.data.provider.notice],
                  ]} />
                </div>
              </div>
            </>
          )}
        </Section>
      )}

      {contract.data && (
        <Section title="Who can call what" sub="The full surface, by role.">
          <div className="grid-2">
            {Object.entries(contract.data.calls).map(([role, calls]) => (
              <div key={role}>
                <h3 style={{ textTransform: 'capitalize' }}>{role}</h3>
                <KV rows={Object.entries(calls).map(([sig, what]) => [sig.split('(')[0], what] as [string, string])} />
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="Source" sub="Read the code this page is describing — served by this node, not a mirror.">
        {contract.data && (
          <div className="cards">
            {contract.data.source_files.map((f) => {
              const name = f.replace('oracles/', '').replace('.sol', '')
              return (
                <a key={f} className="card" href={`/selfinsure/api/contract/source?contract=${name}`}>
                  <h3 className="mono">{f}</h3>
                  <p>{contract.data!.contracts[name] || 'Solidity source'}</p>
                </a>
              )
            })}
          </div>
        )}
        {contract.data?.compiler && (
          <p className="sub" style={{ marginTop: 10 }}>compiler {contract.data.compiler}</p>
        )}
      </Section>
    </>
  )
}
