'use client'

import { useCallback, useEffect, useState } from 'react'
import { api, money, type Booking, type Hook, type Listing, type PolicyField, type Rule, type Status } from '@/lib/api'

type OwnerState = {
  status: Status
  policy: Record<string, any>
  policy_schema: Record<string, PolicyField>
  rules: Rule[]
  facts: Record<string, string>
  effects: Record<string, string>
  hooks: Hook[]
  hook_events: string[]
  deliveries: { hook: string; event: string; ts: number; status: number; error?: string }[]
  listings: Listing[]
  bookings: Booking[]
}

type Section = 'policy' | 'rules' | 'hooks' | 'data'

/** OWNER — the whole market is data, and this is the editor for it.
 *  Every control here is one owner-gated call; the same calls work from curl
 *  or `m openbnb/<fn>`, so nothing in this console is UI-only. */
export function OwnerTab({ onChange }: { onChange: () => void }) {
  const [key, setKey] = useState('')
  const [unlocked, setUnlocked] = useState(false)
  const [state, setState] = useState<OwnerState | null>(null)
  const [section, setSection] = useState<Section>('policy')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  useEffect(() => { setKey(localStorage.getItem('obnb_owner') || '') }, [])

  const load = useCallback(async (k: string) => {
    const s = await api('owner/state', { body: {}, ownerKey: k })
    setState(s); setUnlocked(true)
  }, [])

  useEffect(() => { if (key && !unlocked) load(key).catch(() => {}) }, [key, unlocked, load])

  const unlock = async () => {
    setErr('')
    try {
      await load(key)
      localStorage.setItem('obnb_owner', key)
    } catch (e: any) { setErr(e.message); setUnlocked(false) }
  }

  /** Run an owner call, then refresh both the console and the public view. */
  const run = async (fn: () => Promise<any>, ok: string) => {
    setErr(''); setNote('')
    try { await fn(); await load(key); onChange(); setNote(ok) }
    catch (e: any) { setErr(e.message) }
  }

  if (!unlocked || !state) {
    return (
      <div className="card pad stack" style={{ maxWidth: 560 }}>
        <h2>Owner console</h2>
        <p className="muted">
          The market&apos;s rules live behind one key. It was minted on first run into{' '}
          <span className="mono">~/.mod/openbnb/owner.json</span> — or set{' '}
          <span className="mono">$OPENBNB_OWNER_KEY</span> before serving.
        </p>
        <input className="mono" type="password" placeholder="owner key" value={key}
               onChange={(e) => setKey(e.target.value.trim())}
               onKeyDown={(e) => e.key === 'Enter' && unlock()} />
        {err && <div className="banner err">{err}</div>}
        <div><button className="btn" onClick={unlock} disabled={!key}>Unlock</button></div>
      </div>
    )
  }

  return (
    <div className="stack">
      <div className="owner-bar spread">
        <div>
          <div className="eyebrow">owner console</div>
          <div className="muted">
            {state.status.listings} listing(s) · {state.status.bookings} booking(s) ·{' '}
            {state.status.active_rules}/{state.status.rules} rule(s) active · {state.status.hooks} hook(s) ·{' '}
            volume {money(state.status.volume, state.status.currency)}
          </div>
        </div>
        <button className="btn ghost small" onClick={() => {
          localStorage.removeItem('obnb_owner'); setUnlocked(false); setKey('')
        }}>lock</button>
      </div>

      <nav className="tabs" style={{ alignSelf: 'flex-start' }}>
        {(['policy', 'rules', 'hooks', 'data'] as Section[]).map((s) => (
          <button key={s} className={`tab owner ${section === s ? 'on' : ''}`}
                  onClick={() => setSection(s)}>{s}</button>
        ))}
      </nav>

      {err && <div className="banner err">{err}</div>}
      {note && <div className="banner ok">{note}</div>}

      {section === 'policy' && <PolicyEditor state={state} ownerKey={key} run={run} />}
      {section === 'rules' && <RulesEditor state={state} ownerKey={key} run={run} />}
      {section === 'hooks' && <HooksEditor state={state} ownerKey={key} run={run} />}
      {section === 'data' && <DataEditor state={state} ownerKey={key} run={run} />}
    </div>
  )
}

/* ── policy ──────────────────────────────────────────────────── */
function PolicyEditor({ state, ownerKey, run }: {
  state: OwnerState; ownerKey: string; run: (fn: () => Promise<any>, ok: string) => Promise<void>
}) {
  const [patch, setPatch] = useState<Record<string, any>>({})
  const dirty = Object.keys(patch).length

  const value = (k: string) => (k in patch ? patch[k] : state.policy[k])
  const edit = (k: string, v: any) => setPatch({ ...patch, [k]: v })

  return (
    <section className="card pad stack">
      <div className="spread">
        <h2>Policy</h2>
        <div className="row">
          <button className="btn clay small" disabled={!dirty}
                  onClick={() => run(
                    () => api('owner/policy', { body: { patch }, ownerKey }).then(() => setPatch({})),
                    `${dirty} setting(s) applied — live now.`)}>
            {dirty ? `apply ${dirty} change(s)` : 'no changes'}
          </button>
          <button className="btn ghost small" disabled={!dirty} onClick={() => setPatch({})}>discard</button>
          <button className="btn ghost small danger"
                  onClick={() => run(() => api('owner/policy/reset', { body: {}, ownerKey }), 'Policy reset to defaults.')}>
            reset all
          </button>
        </div>
      </div>
      <p className="muted">
        Every knob the market runs on. Changes take effect on the next quote — no restart.
      </p>

      <div className="kv">
        {Object.entries(state.policy_schema).map(([k, f]) => (
          <PolicyRow key={k} name={k} field={f} value={value(k)} changed={k in patch}
                     onChange={(v) => edit(k, v)} />
        ))}
      </div>
    </section>
  )
}

function PolicyRow({ name, field, value, changed, onChange }: {
  name: string; field: PolicyField; value: any; changed: boolean; onChange: (v: any) => void
}) {
  const label = (
    <div>
      <div style={{ fontWeight: 600, fontSize: 13, color: changed ? 'var(--clay)' : 'var(--ink)' }}>
        {name}{changed ? ' •' : ''}
      </div>
      <div className="note">{field.note}</div>
    </div>
  )
  let control
  if (field.type === 'bool') {
    control = <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
  } else if (field.type === 'int' || field.type === 'num') {
    control = <input type="number" value={value ?? 0}
                     onChange={(e) => onChange(field.type === 'int' ? parseInt(e.target.value || '0', 10) : Number(e.target.value))} />
  } else if (field.type === 'list') {
    control = <input value={(value || []).join(', ')}
                     placeholder="comma separated"
                     onChange={(e) => onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} />
  } else if (field.type === 'dict') {
    control = <input className="mono" value={JSON.stringify(value ?? {})}
                     onChange={(e) => { try { onChange(JSON.parse(e.target.value)) } catch { /* keep typing */ } }} />
  } else {
    control = <input value={value ?? ''} onChange={(e) => onChange(e.target.value)} />
  }
  return <>{label}<div>{control}</div></>
}

/* ── rules ───────────────────────────────────────────────────── */
const EFFECT_HINTS: Record<string, string> = {
  pct: '-15', flat: '25', deny: 'Sorry — not this week', min_nights: '3', tag: 'long-stay',
}

function RulesEditor({ state, ownerKey, run }: {
  state: OwnerState; ownerKey: string; run: (fn: () => Promise<any>, ok: string) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [when, setWhen] = useState('')
  const [effects, setEffects] = useState<Record<string, any>>({ pct: -10 })
  const [test, setTest] = useState<{ matches?: boolean; error?: string } | null>(null)

  const toggleEffect = (k: string) => {
    const next = { ...effects }
    if (k in next) delete next[k]
    else next[k] = k === 'review' ? true : EFFECT_HINTS[k]
    setEffects(next)
  }

  const add = () => run(
    () => api('owner/rules', { body: { name: name || 'rule', when, then: effects }, ownerKey })
      .then(() => { setName(''); setWhen(''); setEffects({ pct: -10 }); setTest(null) }),
    'Rule added — it applies to the next quote.')

  return (
    <div className="grid cols-2" style={{ alignItems: 'start' }}>
      <section className="card pad stack">
        <h2>House rules</h2>
        <p className="muted">
          Rules run top to bottom on every quote. Effects stack; the first{' '}
          <span className="mono">deny</span> wins.
        </p>
        {state.rules.length === 0 && <p className="muted">No rules yet — the market runs on policy alone.</p>}
        {state.rules.map((r, i) => (
          <div key={r.id} className={`rule ${r.enabled ? '' : 'off'}`}>
            <div className="spread">
              <h3>{r.name}</h3>
              <span className="muted mono">{r.hits} hit(s)</span>
            </div>
            <div className="when mono">{r.when}</div>
            <div className="chips">
              {Object.entries(r.then).map(([k, v]) => (
                <span key={k} className="chip clay">{k}: {String(v)}</span>
              ))}
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn ghost small"
                      onClick={() => run(() => api(`owner/rules/${r.id}`, { body: { enabled: !r.enabled }, ownerKey }),
                                         r.enabled ? 'Rule paused.' : 'Rule live.')}>
                {r.enabled ? 'disable' : 'enable'}
              </button>
              <button className="btn ghost small" disabled={i === 0}
                      onClick={() => run(() => api(`owner/rules/${r.id}/move`, { body: { direction: 'up' }, ownerKey }), 'Reordered.')}>↑</button>
              <button className="btn ghost small" disabled={i === state.rules.length - 1}
                      onClick={() => run(() => api(`owner/rules/${r.id}/move`, { body: { direction: 'down' }, ownerKey }), 'Reordered.')}>↓</button>
              <button className="btn ghost small danger"
                      onClick={() => run(() => api(`owner/rules/${r.id}/delete`, { body: {}, ownerKey }), 'Rule deleted.')}>
                delete
              </button>
            </div>
          </div>
        ))}
      </section>

      <section className="card pad stack">
        <h2>Write a rule</h2>
        <div>
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Long stay discount" />
        </div>
        <div>
          <label>When</label>
          <input className="mono" value={when} onChange={(e) => { setWhen(e.target.value); setTest(null) }}
                 placeholder="nights >= 7 and city == 'toronto'" />
        </div>

        <div>
          <label>Then</label>
          <div className="chips">
            {Object.keys(state.effects).map((k) => (
              <button key={k} type="button" className={`chip ${k in effects ? 'clay' : ''}`}
                      style={{ cursor: 'pointer' }} onClick={() => toggleEffect(k)}>{k}</button>
            ))}
          </div>
          <div className="stack" style={{ gap: 8, marginTop: 10 }}>
            {Object.keys(effects).filter((k) => k !== 'review').map((k) => (
              <div className="row" key={k}>
                <span className="mono" style={{ width: 90 }}>{k}</span>
                <input value={effects[k]} onChange={(e) => setEffects({ ...effects, [k]: e.target.value })} />
              </div>
            ))}
            {'review' in effects && <div className="muted">review: this booking always needs host approval.</div>}
          </div>
        </div>

        <div className="row">
          <button className="btn ghost" disabled={!when}
                  onClick={async () => {
                    try {
                      const r = await api('owner/rules/test', { body: { when }, ownerKey })
                      setTest({ matches: r.matches })
                    } catch (e: any) { setTest({ error: e.message }) }
                  }}>
            test against sample
          </button>
          <button className="btn clay" disabled={!when || !Object.keys(effects).length} onClick={add}>
            add rule
          </button>
        </div>
        {test?.error && <div className="banner err">{test.error}</div>}
        {test && !test.error && (
          <div className={`banner ${test.matches ? 'ok' : 'info'}`}>
            Sample stay (3 nights, 2 guests, Toronto, 14 days out): {test.matches ? 'matches' : 'does not match'}.
          </div>
        )}

        <details>
          <summary className="eyebrow" style={{ cursor: 'pointer' }}>facts you can use</summary>
          <div className="stack" style={{ gap: 4, marginTop: 8 }}>
            {Object.entries(state.facts).map(([k, v]) => (
              <div key={k} style={{ fontSize: 12 }}>
                <span className="mono" style={{ color: 'var(--slate)' }}>{k}</span>
                <span className="muted"> — {v}</span>
              </div>
            ))}
          </div>
        </details>
      </section>
    </div>
  )
}

/* ── hooks ───────────────────────────────────────────────────── */
function HooksEditor({ state, ownerKey, run }: {
  state: OwnerState; ownerKey: string; run: (fn: () => Promise<any>, ok: string) => Promise<void>
}) {
  const [url, setUrl] = useState('')
  const [events, setEvents] = useState<string[]>([])

  return (
    <div className="grid cols-2" style={{ alignItems: 'start' }}>
      <section className="card pad stack">
        <h2>Webhooks</h2>
        <p className="muted">Every event POSTs to your URL, so the market can drive anything else you run.</p>
        <div>
          <label>Endpoint</label>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/openbnb" />
        </div>
        <div>
          <label>Events <span className="muted">(none selected = all)</span></label>
          <div className="chips">
            {state.hook_events.map((e) => (
              <button key={e} type="button" className={`chip ${events.includes(e) ? 'clay' : ''}`}
                      style={{ cursor: 'pointer' }}
                      onClick={() => setEvents(events.includes(e) ? events.filter((x) => x !== e) : [...events, e])}>
                {e}
              </button>
            ))}
          </div>
        </div>
        <div>
          <button className="btn clay" disabled={!url}
                  onClick={() => run(() => api('owner/hooks', {
                    body: { url, events: events.length ? events : ['*'] }, ownerKey,
                  }).then(() => { setUrl(''); setEvents([]) }), 'Hook added.')}>
            add hook
          </button>
        </div>

        {state.hooks.map((h) => (
          <div key={h.id} className="rule">
            <div className="spread">
              <span className="mono">{h.url}</span>
              <button className="btn ghost small danger"
                      onClick={() => run(() => api(`owner/hooks/${h.id}/delete`, { body: {}, ownerKey }), 'Hook removed.')}>
                delete
              </button>
            </div>
            <div className="chips" style={{ marginTop: 8 }}>
              {h.events.map((e) => <span key={e} className="chip">{e}</span>)}
            </div>
          </div>
        ))}
      </section>

      <section className="card pad stack">
        <h2>Recent deliveries</h2>
        {state.deliveries.length === 0 ? (
          <p className="muted">Nothing delivered yet.</p>
        ) : (
          <div className="scroll">
            <table>
              <thead><tr><th>event</th><th>hook</th><th>status</th></tr></thead>
              <tbody>
                {[...state.deliveries].reverse().map((d, i) => (
                  <tr key={i}>
                    <td className="mono">{d.event}</td>
                    <td className="mono">{d.hook}</td>
                    <td className={d.status >= 200 && d.status < 300 ? '' : 'muted'}>
                      {d.status || d.error?.slice(0, 40) || 'failed'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

/* ── data ────────────────────────────────────────────────────── */
function DataEditor({ state, ownerKey, run }: {
  state: OwnerState; ownerKey: string; run: (fn: () => Promise<any>, ok: string) => Promise<void>
}) {
  const exportState = async () => {
    const data = await api('owner/export', { body: {}, ownerKey })
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'openbnb-state.json'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="stack">
      <section className="card pad stack">
        <div className="spread">
          <h2>Listings</h2>
          <div className="row">
            <button className="btn ghost small"
                    onClick={() => run(() => api('owner/seed_demo', { body: {}, ownerKey }), 'Demo listings added.')}>
              seed demo
            </button>
            <button className="btn ghost small danger"
                    onClick={() => run(() => api('owner/wipe_demo', { body: {}, ownerKey }), 'Demo data removed.')}>
              wipe demo
            </button>
            <button className="btn ghost small" onClick={exportState}>export json</button>
          </div>
        </div>
        <div className="scroll">
          <table>
            <thead><tr><th>place</th><th>host</th><th>city</th><th>price</th><th>status</th><th /></tr></thead>
            <tbody>
              {state.listings.map((l) => (
                <tr key={l.id}>
                  <td>{l.title}</td>
                  <td className="mono">{l.host}</td>
                  <td>{l.city}</td>
                  <td>{l.price}</td>
                  <td>
                    <select value={l.status} style={{ width: 120 }}
                            onChange={(e) => run(() => api(`listing/${l.id}/status`, {
                              body: { host_key: ownerKey, status: e.target.value },
                            }), 'Listing updated.')}>
                      {['live', 'paused', 'pending', 'removed'].map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </td>
                  <td>
                    <button className="btn ghost small danger"
                            onClick={() => run(() => api(`owner/listing/${l.id}/delete`, { body: {}, ownerKey }), 'Listing deleted.')}>
                      delete
                    </button>
                  </td>
                </tr>
              ))}
              {state.listings.length === 0 && <tr><td colSpan={6} className="muted">No listings.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card pad stack">
        <h2>Bookings</h2>
        <div className="scroll">
          <table>
            <thead><tr><th>ref</th><th>place</th><th>guest</th><th>dates</th><th>total</th><th>status</th><th /></tr></thead>
            <tbody>
              {state.bookings.map((b) => (
                <tr key={b.id}>
                  <td className="mono">{b.id}</td>
                  <td>{b.listing_title}</td>
                  <td className="mono">{b.guest}</td>
                  <td className="mono">{b.checkin} → {b.checkout}</td>
                  <td>{money(b.quote.total, b.quote.currency)}</td>
                  <td>
                    <select value={b.status} style={{ width: 130 }}
                            onChange={(e) => run(() => api(`owner/booking/${b.id}/status`, {
                              body: { status: e.target.value }, ownerKey,
                            }), 'Booking updated.')}>
                      {['pending', 'confirmed', 'declined', 'cancelled'].map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </td>
                  <td>
                    <button className="btn ghost small danger"
                            onClick={() => run(() => api(`owner/booking/${b.id}/delete`, { body: {}, ownerKey }), 'Booking deleted.')}>
                      delete
                    </button>
                  </td>
                </tr>
              ))}
              {state.bookings.length === 0 && <tr><td colSpan={7} className="muted">No bookings.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
