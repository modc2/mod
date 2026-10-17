'use client'

import Link from 'next/link'
import { useState } from 'react'
import { Check, Created, Draft, PoolTerms, fmt } from '@/lib/api'

// Pieces shared by /ask and /create, so a draft and a freshly opened pool
// look the same wherever they appear.

export function draftHref(terms: PoolTerms, members?: number): string {
  return `/create?draft=${encodeURIComponent(JSON.stringify(terms))}` + (members ? `&members=${members}` : '')
}

export function CheckCard({ check }: { check: Check }) {
  const tone = check.level === 'bad' ? 'check-bad' : check.level === 'warn' ? 'check-warn' : 'check-ok'
  const label = check.level === 'bad' ? 'Will struggle to pay' : check.level === 'warn' ? 'Workable, with caveats' : 'Looks payable'
  return (
    <div className={`check ${tone}`}>
      <div className="check-head"><span className="check-dot" /> {label}</div>
      <p>{check.headline}</p>
      <ul>{check.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </div>
  )
}

export function TermsList({ t }: { t: PoolTerms }) {
  const u = t.unit || 'USD'
  const rows: [string, string][] = [
    ['Premium', `${fmt(t.premium, 2)} ${u} every ${fmt(t.period_days)} days`],
    ['Pays up to', `${fmt(t.coverage, 2)} ${u} per claim` + (t.annual_cap ? `, ${fmt(t.annual_cap)} ${u} a year` : '')],
    ['Deductible', `${fmt(t.deductible, 2)} ${u}`],
    ['Waiting period', `${fmt(t.waiting_days)} days`],
    ['Claims decided by', `${t.quorum} vote${t.quorum === 1 ? '' : 's'}, ${Math.round(t.threshold * 100)}% must accept`],
    ['Operator fee', `${(t.fee_bps || 0) / 100}%`],
  ]
  return (
    <dl className="kv">
      {rows.map(([k, v]) => <div key={k} className="kv-row"><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>
  )
}

export function DraftCard({ draft }: { draft: Draft }) {
  const t = draft.create_args
  return (
    <div className="draft">
      <div className="draft-head">
        <span className="pill">draft — not created</span>
        <strong>{t.name}</strong>
      </div>
      <p className="draft-about">{t.about}</p>
      <TermsList t={t} />
      <CheckCard check={draft.check} />
      <div className="row">
        <Link className="btn" href={draftHref(t, draft.check.members_assumed ? undefined : draft.check.members)}>Review and create</Link>
        {draft.assumed.length > 0 && (
          <span className="sub">Filled in for you: {draft.assumed.join(', ').replace(/_/g, ' ')}</span>
        )}
      </div>
    </div>
  )
}

export function OwnerKeyCard({ created }: { created: Created }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(created.owner_key).then(() => setCopied(true)).catch(() => setCopied(false))
  }
  return (
    <div className="created">
      <h3>Your pool is open: {created.name}</h3>
      <p>Pool id <span className="mono">{created.id}</span> — share this with the people who should join.</p>
      <div className="keybox">
        <span className="mono">{created.owner_key}</span>
        <button className="btn" onClick={copy}>{copied ? 'Copied' : 'Copy key'}</button>
      </div>
      <p className="warn-text">
        This is your owner key. It is shown only this once and nobody — not even this
        server — can recover it. Save it somewhere safe: you need it to change terms or
        return surplus to members.
      </p>
      <div className="row">
        <Link className="btn btn-ghost" href="/pools">See it in Pools</Link>
        <Link className="btn btn-ghost" href="/ask">Ask what to do next</Link>
      </div>
    </div>
  )
}
