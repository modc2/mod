'use client'

import { Section, Stat, Loading, Note } from '@/components/chrome'
import { useResource, fmt, pct, Stats, PoolsResponse } from '@/lib/api'

export default function PoolsPage() {
  const stats = useResource<Stats>('/stats')
  const pools = useResource<PoolsResponse>('/pools')
  const ledger = useResource<any>('/ledger?limit=20')
  const entries: any[] = Array.isArray(ledger.data) ? ledger.data : (ledger.data?.entries ?? ledger.data?.ledger ?? [])

  return (
    <>
      <div className="hero">
        <h1>The pools on this node</h1>
        <p>
          Off-chain pools live in a local JSON ledger — money in integer minor units, every
          movement logged. Anyone can open one; the terms are set at creation and every
          member sees the same books you see here.
        </p>
      </div>

      <Section title="Totals">
        {stats.loading && <Loading />}
        {stats.error && <Note tone="error">{stats.error}</Note>}
        {stats.data && (
          <div className="stats">
            <Stat label="Pools" value={fmt(stats.data.pools)} sub={`${fmt(stats.data.open_pools)} open`} />
            <Stat label="Held in pools" value={fmt(stats.data.held_in_pools, 2)} />
            <Stat label="Accept rate" value={stats.data.accept_rate === null ? '—' : pct(stats.data.accept_rate)} sub="of settled claims" />
            <Stat label="Operator fees" value={fmt(stats.data.operator_fees, 2)} />
          </div>
        )}
      </Section>

      <Section title="Pools">
        {pools.loading && <Loading />}
        {pools.error && <Note tone="error">{pools.error}</Note>}
        {pools.data && pools.data.count === 0 && (
          <Note>
            No pools yet. {pools.data.note || 'Anyone can open one.'} Agents can do it over
            MCP (<span className="mono">si_create_pool</span>) or REST
            (<span className="mono">POST /selfinsure/api/tools/si_create_pool</span>).
          </Note>
        )}
        {pools.data && pools.data.count > 0 && (
          <table>
            <thead>
              <tr><th>Pool</th><th>State</th><th className="num">Members</th><th className="num">Balance</th></tr>
            </thead>
            <tbody>
              {pools.data.pools.map((p) => (
                <tr key={p.id}>
                  <td>{p.name || p.id}</td>
                  <td><span className="pill">{p.state || '—'}</span></td>
                  <td className="num">{fmt(p.members)}</td>
                  <td className="num">{fmt(p.balance, 2)} {p.unit || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Recent ledger entries" sub="The last movements across all pools — premiums, claims, distributions, fees.">
        {ledger.loading && <Loading />}
        {ledger.error && <Note tone="error">{ledger.error}</Note>}
        {!ledger.loading && !ledger.error && entries.length === 0 && <Note>The ledger is empty — nothing has moved yet.</Note>}
        {entries.length > 0 && (
          <table>
            <thead>
              <tr><th>Kind</th><th>Pool</th><th>Who</th><th className="num">Amount</th></tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={i}>
                  <td><span className="pill">{e.kind || e.type || '—'}</span></td>
                  <td>{e.pool || '—'}</td>
                  <td className="mono">{e.member || e.who || e.agent || '—'}</td>
                  <td className="num">{fmt(e.amount, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </>
  )
}
