'use client'

import { useEffect, useMemo, useState } from 'react'
import { api, money, type Booking, type Kind, type Listing } from '@/lib/api'
import { loadKeys, saveKey } from '@/lib/keys'

/** HOST — list a place, run its calendar, answer requests.
 *  Auth is the per-listing host_key handed back once at creation and kept in
 *  this browser; the owner's key works everywhere too. */
export function HostTab({ listings, kinds, handle, onChange }: {
  listings: Listing[]; kinds: Kind[]; handle: string; onChange: () => void
}) {
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [mine, setMine] = useState<Listing[]>([])
  const [requests, setRequests] = useState<Booking[]>([])
  const [amenities, setAmenities] = useState<string[]>([])
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  useEffect(() => { setKeys(loadKeys()) }, [])
  useEffect(() => { api('amenities').then(setAmenities).catch(() => {}) }, [])

  const reload = useMemo(() => async () => {
    if (!handle) { setMine([]); setRequests([]); return }
    try {
      const [ls, bs] = await Promise.all([
        api(`listings?host=${encodeURIComponent(handle)}&include_all=true`),
        api(`bookings?host=${encodeURIComponent(handle)}`),
      ])
      setMine(ls); setRequests(bs)
    } catch (e: any) { setErr(e.message) }
  }, [handle])

  useEffect(() => { reload() }, [reload, listings])

  const act = async (fn: () => Promise<any>, ok: string) => {
    setErr(''); setNote('')
    try { await fn(); setNote(ok); await reload(); onChange() }
    catch (e: any) { setErr(e.message) }
  }

  if (!handle) {
    return (
      <div className="card empty">
        <div className="big">Who are you?</div>
        <div>Type a handle up in the header — that&apos;s your host identity here.</div>
      </div>
    )
  }

  return (
    <div className="grid cols-2" style={{ alignItems: 'start' }}>
      <NewListing
        handle={handle} kinds={kinds} amenities={amenities}
        onCreated={(l) => { saveKey(l.id, l.host_key!); setKeys(loadKeys()); reload(); onChange() }}
      />

      <div className="stack">
        {err && <div className="banner err">{err}</div>}
        {note && <div className="banner ok">{note}</div>}

        <section className="card pad stack">
          <h2>Your places</h2>
          {mine.length === 0 ? (
            <p className="muted">Nothing listed under “{handle}” yet.</p>
          ) : mine.map((l) => (
            <div key={l.id} className="rule">
              <div className="spread">
                <h3>{l.title}</h3>
                <span className={`chip ${l.status === 'live' ? 'live' : l.status === 'pending' ? 'pending' : 'off'}`}>
                  {l.status}
                </span>
              </div>
              <div className="muted" style={{ margin: '6px 0' }}>
                {l.price} / night · sleeps {l.guests} · {l.instant_book ? 'instant book' : 'you approve each stay'}
              </div>
              {!keys[l.id] && (
                <div className="banner info" style={{ marginBottom: 8 }}>
                  This browser doesn&apos;t hold the host key for this listing — paste it to manage it.
                  <input
                    className="mono" style={{ marginTop: 8 }} placeholder="host_key"
                    onChange={(e) => { if (e.target.value.length > 8) { saveKey(l.id, e.target.value.trim()); setKeys(loadKeys()) } }}
                  />
                </div>
              )}
              <div className="row">
                <button className="btn ghost small" disabled={!keys[l.id]}
                        onClick={() => act(() => api(`listing/${l.id}/status`, {
                          body: { host_key: keys[l.id], status: l.status === 'live' ? 'paused' : 'live' },
                        }), l.status === 'live' ? 'Paused.' : 'Live again.')}>
                  {l.status === 'live' ? 'pause' : 'go live'}
                </button>
                <BlockNight listingId={l.id} hostKey={keys[l.id]} onDone={() => act(async () => {}, 'Calendar updated.')} />
                <span className="mono muted">{l.id}</span>
              </div>
            </div>
          ))}
        </section>

        <section className="card pad stack">
          <h2>Requests & stays</h2>
          {requests.length === 0 ? (
            <p className="muted">No one has booked you yet.</p>
          ) : requests.map((b) => (
            <div key={b.id} className="rule">
              <div className="spread">
                <h3>{b.listing_title}</h3>
                <span className={`chip ${b.status === 'confirmed' ? 'live' : b.status === 'pending' ? 'pending' : 'off'}`}>
                  {b.status}
                </span>
              </div>
              <div className="muted" style={{ margin: '6px 0' }}>
                {b.guest} · {b.checkin} → {b.checkout} ({b.nights} night(s), {b.guests} guest(s)) ·{' '}
                {money(b.quote.total, b.quote.currency)}
              </div>
              {b.status === 'pending' && (
                <div className="row">
                  <button className="btn small" disabled={!keys[b.listing_id]}
                          onClick={() => act(() => api(`booking/${b.id}/approve`, { body: { host_key: keys[b.listing_id] } }), 'Confirmed.')}>
                    approve
                  </button>
                  <button className="btn ghost small danger" disabled={!keys[b.listing_id]}
                          onClick={() => act(() => api(`booking/${b.id}/decline`, { body: { host_key: keys[b.listing_id] } }), 'Declined.')}>
                    decline
                  </button>
                </div>
              )}
            </div>
          ))}
        </section>
      </div>
    </div>
  )
}

function BlockNight({ listingId, hostKey, onDone }: {
  listingId: string; hostKey?: string; onDone: () => void
}) {
  const [d, setD] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <span className="row" style={{ gap: 6 }}>
      <input type="date" value={d} onChange={(e) => setD(e.target.value)}
             style={{ width: 150 }} aria-label="block a night" />
      <button className="btn ghost small" disabled={!hostKey || !d || busy}
              onClick={async () => {
                setBusy(true)
                try { await api(`listing/${listingId}/block`, { body: { host_key: hostKey, dates: [d] } }); onDone() } catch {}
                setBusy(false); setD('')
              }}>
        block
      </button>
    </span>
  )
}

function NewListing({ handle, kinds, amenities, onCreated }: {
  handle: string; kinds: Kind[]; amenities: string[]; onCreated: (l: Listing) => void
}) {
  const [f, setF] = useState({
    title: '', city: '', price: 100, kind: 'entire_place', guests: 2, bedrooms: 1,
    beds: 1, baths: 1, cleaning_fee: 0, min_nights: 0, notes: '', instant_book: true,
  })
  const [picked, setPicked] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [created, setCreated] = useState<Listing | null>(null)

  const set = (k: string, v: any) => setF({ ...f, [k]: v })

  const submit = async () => {
    setBusy(true); setErr('')
    try {
      const l = await api('listings', { body: { ...f, host: handle, amenities: picked } })
      setCreated(l); onCreated(l)
      setF({ ...f, title: '', notes: '' }); setPicked([])
    } catch (e: any) { setErr(e.message) }
    setBusy(false)
  }

  return (
    <section className="card pad stack">
      <h2>List a place</h2>
      {created && (
        <div className="banner ok">
          <div><strong>{created.title}</strong> is {created.status}.</div>
          <div style={{ marginTop: 6 }}>Host key (saved in this browser, shown once):</div>
          <div className="mono" style={{ wordBreak: 'break-all' }}>{created.host_key}</div>
        </div>
      )}
      {err && <div className="banner err">{err}</div>}

      <div className="row">
        <div className="field" style={{ flexBasis: '100%' }}>
          <label>Title</label>
          <input value={f.title} onChange={(e) => set('title', e.target.value)}
                 placeholder="Loft over the bakery" />
        </div>
      </div>
      <div className="row">
        <div className="field">
          <label>City</label>
          <input value={f.city} onChange={(e) => set('city', e.target.value)} placeholder="toronto" />
        </div>
        <div className="field">
          <label>Type</label>
          <select value={f.kind} onChange={(e) => set('kind', e.target.value)}>
            {kinds.filter((k) => k.allowed).map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
        </div>
        <div className="field" style={{ maxWidth: 120 }}>
          <label>Per night</label>
          <input type="number" min={1} value={f.price} onChange={(e) => set('price', Number(e.target.value))} />
        </div>
      </div>
      <div className="row">
        {(['guests', 'bedrooms', 'beds', 'baths'] as const).map((k) => (
          <div className="field" key={k} style={{ maxWidth: 100 }}>
            <label>{k}</label>
            <input type="number" min={k === 'guests' ? 1 : 0} value={f[k]}
                   onChange={(e) => set(k, Number(e.target.value))} />
          </div>
        ))}
        <div className="field" style={{ maxWidth: 120 }}>
          <label>Cleaning</label>
          <input type="number" min={0} value={f.cleaning_fee}
                 onChange={(e) => set('cleaning_fee', Number(e.target.value))} />
        </div>
        <div className="field" style={{ maxWidth: 120 }}>
          <label>Min nights</label>
          <input type="number" min={0} value={f.min_nights}
                 onChange={(e) => set('min_nights', Number(e.target.value))} />
        </div>
      </div>
      <div>
        <label>Amenities</label>
        <div className="chips">
          {amenities.map((a) => (
            <button key={a} type="button"
                    className={`chip ${picked.includes(a) ? 'clay' : ''}`}
                    style={{ cursor: 'pointer' }}
                    onClick={() => setPicked(picked.includes(a) ? picked.filter((x) => x !== a) : [...picked, a])}>
              {a}
            </button>
          ))}
        </div>
      </div>
      <div>
        <label>Notes for guests</label>
        <textarea value={f.notes} onChange={(e) => set('notes', e.target.value)}
                  placeholder="What should a guest know before they arrive?" />
      </div>
      <label className="row" style={{ gap: 8 }}>
        <input type="checkbox" checked={f.instant_book}
               onChange={(e) => set('instant_book', e.target.checked)} />
        <span>Let guests book instantly</span>
      </label>
      <div>
        <button className="btn clay" onClick={submit} disabled={busy || !f.title || !f.city}>
          {busy ? 'listing…' : 'Publish listing'}
        </button>
      </div>
    </section>
  )
}
