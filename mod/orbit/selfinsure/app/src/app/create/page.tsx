'use client'

import { ReactNode, useEffect, useState } from 'react'
import { Section, Note, Loading } from '@/components/chrome'
import { CheckCard, OwnerKeyCard } from '@/components/pool'
import { post, useResource, Check, Created, Draft, Guide, PoolTerms } from '@/lib/api'

// Create your own pool: start from a starter or a sentence, set the numbers,
// see whether they can pay (guide.check on the server), then open it.

const BLANK: PoolTerms = {
  name: '', about: '', premium: 10, period_days: 30, coverage: 500, deductible: 0,
  annual_cap: null, waiting_days: 14, quorum: 1, threshold: 0.5, fee_bps: 0,
  agent_policy: 'open', reserve_floor: 0, unit: 'USD',
}

function Field({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      <span className="field-hint">{hint}</span>
    </label>
  )
}

export default function CreatePage() {
  const guide = useResource<Guide>('/guide')
  const [t, setT] = useState<PoolTerms>(BLANK)
  const [members, setMembers] = useState(10)
  const [owner, setOwner] = useState('')
  const [sentence, setSentence] = useState('')
  const [check, setCheck] = useState<Check | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<Created | null>(null)

  // A draft handed over from /ask arrives as ?draft=<json>.
  useEffect(() => {
    const qs = new URLSearchParams(window.location.search)
    const m = Number(qs.get('members'))
    if (m > 0) setMembers(m)
    const raw = qs.get('draft')
    if (!raw) return
    try { setT({ ...BLANK, ...JSON.parse(raw) }) } catch { /* ignore a bad link */ }
  }, [])

  // Re-check the numbers whenever they change.
  useEffect(() => {
    const id = setTimeout(() => {
      post<{ check: Check }>('/tools/si_draft', { terms: t, members })
        .then((r) => setCheck(r.check)).catch(() => setCheck(null))
    }, 300)
    return () => clearTimeout(id)
  }, [t, members])

  const set = <K extends keyof PoolTerms>(k: K, v: PoolTerms[K]) => setT((x) => ({ ...x, [k]: v }))
  const num = (k: keyof PoolTerms) => (e: React.ChangeEvent<HTMLInputElement>) =>
    set(k, (e.target.value === '' ? (k === 'annual_cap' ? null : 0) : Number(e.target.value)) as any)

  const fromSentence = async () => {
    if (!sentence.trim()) return
    setError(null)
    try {
      const d = await post<Draft>('/tools/si_draft', { text: sentence })
      setT({ ...BLANK, ...d.create_args })
      if (d.check?.members && !d.check.members_assumed) setMembers(d.check.members)
    } catch (e: any) { setError(e.message || String(e)) }
  }

  const create = async () => {
    if (!t.name.trim()) { setError('Give the pool a name first.'); return }
    setBusy(true); setError(null)
    try {
      const out = await post<Created & Record<string, any>>('/tools/si_create_pool',
        { ...t, owner: owner.trim() || undefined })
      setCreated({ id: out.id, name: out.name, owner_key: out.owner_key })
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e: any) { setError(e.message || String(e)) }
    finally { setBusy(false) }
  }

  if (created) {
    return (
      <>
        <div className="hero"><h1>Done.</h1></div>
        <Section title="Save your owner key"><OwnerKeyCard created={created} /></Section>
      </>
    )
  }

  return (
    <>
      <div className="hero">
        <h1>Create your own pool</h1>
        <p>
          A pool is a shared pot with one set of rules. Start from a starter or describe it
          in a sentence, adjust the numbers, and check that it can actually pay before you
          open it. Anyone can open one; it takes a minute.
        </p>
      </div>

      <Section title="1. Start from somewhere" sub="Pick a starter, or describe your pool and the guide fills in the form.">
        {guide.loading && <Loading />}
        <div className="chips">
          {guide.data?.templates.map((tpl) => (
            <button key={tpl.id} className="chip" onClick={() => setT({ ...BLANK, ...tpl.terms })}>
              {tpl.title}
            </button>
          ))}
        </div>
        <div className="composer" style={{ marginTop: 12 }}>
          <input value={sentence} onChange={(e) => setSentence(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') fromSentence() }}
            placeholder='e.g. "a pool for 30 freelancers covering sick days, $20 a month, up to $1,000"' />
          <button className="btn" onClick={fromSentence} disabled={!sentence.trim()}>Fill in</button>
        </div>
      </Section>

      <Section title="2. What it covers" sub="Adjudicators judge every claim against this text, so write it like a rule.">
        <div className="form">
          <Field label="Name" hint="What people will see, e.g. Courier bike theft.">
            <input value={t.name} onChange={(e) => set('name', e.target.value)} />
          </Field>
          <Field label="The rule" hint="Covers… Needs (evidence)… Not covered…">
            <textarea rows={4} value={t.about} onChange={(e) => set('about', e.target.value)} />
          </Field>
        </div>
      </Section>

      <Section title="3. The money">
        <div className="form form-grid">
          <Field label={`Premium (${t.unit})`} hint="What each member pays per period.">
            <input type="number" min={0} value={t.premium} onChange={num('premium')} />
          </Field>
          <Field label="Every (days)" hint="30 = monthly.">
            <input type="number" min={1} value={t.period_days} onChange={num('period_days')} />
          </Field>
          <Field label={`Pays up to (${t.unit})`} hint="Most one claim can pay. 0 = no limit (risky).">
            <input type="number" min={0} value={t.coverage} onChange={num('coverage')} />
          </Field>
          <Field label={`Deductible (${t.unit})`} hint="The part of each loss the member pays.">
            <input type="number" min={0} value={t.deductible} onChange={num('deductible')} />
          </Field>
          <Field label={`Yearly limit per member (${t.unit})`} hint="Optional. Blank = none.">
            <input type="number" min={0} value={t.annual_cap ?? ''} onChange={num('annual_cap')} />
          </Field>
          <Field label="Currency" hint="Shown on every amount.">
            <select value={t.unit} onChange={(e) => set('unit', e.target.value)}>
              {['USD', 'EUR', 'GBP', 'USDC', 'DAI', 'ETH'].map((u) => <option key={u}>{u}</option>)}
            </select>
          </Field>
        </div>
      </Section>

      <Section title="4. Fairness and claims">
        <div className="form form-grid">
          <Field label="Waiting period (days)" hint="Stops people joining right after a loss.">
            <input type="number" min={0} value={t.waiting_days} onChange={num('waiting_days')} />
          </Field>
          <Field label="Votes needed" hint="How many adjudicators decide a claim.">
            <input type="number" min={1} value={t.quorum} onChange={num('quorum')} />
          </Field>
          <Field label="Share that must say yes" hint="Half, two thirds, or everyone.">
            <select value={String(t.threshold)} onChange={(e) => set('threshold', Number(e.target.value))}>
              <option value="0.5">Half (simple majority)</option>
              <option value="0.66">Two thirds</option>
              <option value="1">Everyone</option>
            </select>
          </Field>
          <Field label="Who may adjudicate" hint="Open: any AI agent or person. Approved: you admit them.">
            <select value={t.agent_policy} onChange={(e) => set('agent_policy', e.target.value as any)}>
              <option value="open">Anyone</option>
              <option value="approved">Only people I approve</option>
            </select>
          </Field>
          <Field label="Your fee (%)" hint="Your cut of each premium. 0% = all of it stays with members. Max 10%.">
            <input type="number" min={0} max={10} step={0.5} value={t.fee_bps / 100}
              onChange={(e) => set('fee_bps', Math.max(0, Math.min(1000, Math.round(Number(e.target.value || 0) * 100))))} />
          </Field>
          <Field label="Your name (optional)" hint="Shown on the pool as its owner.">
            <input value={owner} onChange={(e) => setOwner(e.target.value)} />
          </Field>
        </div>
      </Section>

      <Section title="5. Could it pay?" sub="Members times premium over a year, against the most one claim can pay.">
        <div className="form" style={{ maxWidth: 260, marginBottom: 12 }}>
          <Field label="Members you expect" hint="Only used for this check.">
            <input type="number" min={1} value={members} onChange={(e) => setMembers(Math.max(1, Number(e.target.value || 1)))} />
          </Field>
        </div>
        {check ? <CheckCard check={check} /> : <Loading />}
      </Section>

      <Section title="6. Open it">
        {error && <Note tone="error">{error}</Note>}
        <div className="row">
          <button className="btn" onClick={create} disabled={busy || !t.name.trim()}>
            {busy ? 'Creating…' : 'Create pool'}
          </button>
          <span className="sub">You will get an owner key once. Save it — it cannot be recovered.</span>
        </div>
      </Section>
    </>
  )
}
