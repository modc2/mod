'use client'

import { useState } from 'react'
import { Section, KV, Loading, Note } from '@/components/chrome'
import { useResource, fmt, Preset } from '@/lib/api'

const PRESETS = [
  { id: 'health', label: 'US health mutual' },
  { id: 'parametric', label: 'Parametric cover' },
  { id: 'mutual', label: 'Plain mutual' },
]

const TERM_LABELS: Record<string, string> = {
  premium: 'Premium per period',
  period_days: 'Period (days)',
  coverage: 'Coverage per claim',
  deductible: 'Deductible',
  annual_cap: 'Annual cap per member',
  waiting_days: 'Waiting period (days)',
  reserve_floor: 'Reserve floor',
  fee_bps: 'Operator fee (bps)',
  quorum: 'Adjudicator quorum',
  threshold_bps: 'Approval threshold (bps)',
  approved_agents_only: 'Approved agents only',
}

export default function PresetPage() {
  const [preset, setPreset] = useState('health')
  const res = useResource<Preset>(`/preset?preset=${preset}`)

  return (
    <>
      <div className="hero">
        <h1>Templates you can deploy today</h1>
        <p>
          A preset is a full set of terms, scaled to whatever asset the pool settles in.
          The health template is the point of the whole module: a US health mutual with a
          0% operator fee, deployable by anyone with <span className="mono">openHealth()</span>.
        </p>
      </div>

      <Section title="Preset">
        <div className="cards" style={{ marginBottom: 20 }}>
          {PRESETS.map((p) => (
            <button key={p.id} onClick={() => setPreset(p.id)} className="card"
              style={{ cursor: 'pointer', textAlign: 'left', font: 'inherit', borderColor: preset === p.id ? 'var(--mint)' : undefined }}>
              <h3>{p.label}</h3>
              <p>{preset === p.id ? 'showing below' : 'view terms'}</p>
            </button>
          ))}
        </div>

        {res.loading && <Loading />}
        {res.error && <Note tone="error">{res.error}</Note>}
        {res.data && (
          <>
            <h3>{res.data.title}</h3>
            <Note>{res.data.about}</Note>
            <div className="grid-2" style={{ marginTop: 16 }}>
              <div>
                <h3>Terms</h3>
                <KV rows={Object.entries(res.data.terms_human).map(([k, v]) => [
                  TERM_LABELS[k] || k,
                  typeof v === 'boolean' ? (v ? 'yes' : 'no') : fmt(v),
                ] as [string, string])} />
              </div>
              <div>
                <h3>Why these numbers</h3>
                <KV rows={Object.entries(res.data.why).map(([k, v]) => [TERM_LABELS[k] || k, v] as [string, string])} />
              </div>
            </div>
            <p className="sub" style={{ marginTop: 12 }}>
              Deploy it: <span className="mono">si_deploy</span> over MCP, or call{' '}
              <span className="mono">openHealth()</span> on the factory directly. Terms above
              are scaled to {res.data.decimals} decimals on chain.
            </p>
          </>
        )}
      </Section>
    </>
  )
}
