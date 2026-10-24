'use client'

import Link from 'next/link'
import { ROUTES, Section, Stat, Loading, Note } from '@/components/chrome'
import { useResource, fmt, pct, Stats, ContractDescribe } from '@/lib/api'

export default function Home() {
  const stats = useResource<Stats>('/stats')
  const contract = useResource<ContractDescribe>('/contract')

  return (
    <>
      <div className="hero">
        <h1>Insurance that is <span className="accent">owned by the people it covers.</span></h1>
        <p>
          A selfinsure pool is a mutual: every premium goes into a shared pot, claims are
          adjudicated by named agents who must give a reason, surplus flows back to members
          pro rata, and the operator&apos;s cut is hard-capped at 10% and published on chain.
          No underwriter, no black box — the contract is the policy.
        </p>
      </div>

      <Section title="This node, live" sub="Read straight from the local ledger — zeros mean no pools have opened here yet, not a broken page.">
        {stats.loading && <Loading />}
        {stats.error && <Note tone="error">API unreachable: {stats.error}</Note>}
        {stats.data && (
          <div className="stats">
            <Stat label="Pools" value={fmt(stats.data.pools)} sub={`${fmt(stats.data.open_pools)} open`} />
            <Stat label="Members" value={fmt(stats.data.members)} />
            <Stat label="Agents" value={fmt(stats.data.agents)} sub="adjudicators" />
            <Stat label="Claims" value={fmt(stats.data.claims)} sub={`${fmt(stats.data.open_claims)} open`} />
            <Stat label="Premiums in" value={fmt(stats.data.premiums_in, 2)} />
            <Stat label="Paid in claims" value={fmt(stats.data.paid_in_claims, 2)} tone="good" />
            <Stat label="Returned to members" value={fmt(stats.data.returned_to_members, 2)} tone="good" />
            <Stat label="Operator share" value={pct(stats.data.operator_share)} sub="of gross premium" />
          </div>
        )}
      </Section>

      <Section title="What the contract guarantees" sub="These are properties of the Solidity, not promises in a brochure.">
        {contract.loading && <Loading />}
        {contract.error && <Note tone="error">{contract.error}</Note>}
        {contract.data && (
          <ul className="guarantees">
            {contract.data.guarantees.map((g) => <li key={g}>{g}</li>)}
          </ul>
        )}
      </Section>

      <Section title="Explore">
        <div className="cards">
          {ROUTES.filter((r) => r.href !== '/').map((r) => (
            <Link key={r.href} href={r.href} className="card">
              <h3>{r.label}</h3>
              <p>{r.blurb}</p>
            </Link>
          ))}
        </div>
      </Section>
    </>
  )
}
