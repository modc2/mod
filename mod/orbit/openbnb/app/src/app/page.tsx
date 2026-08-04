'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api, addDays, money, nightsBetween, today,
  type Booking, type Kind, type Listing, type Quote, type Status,
} from '@/lib/api'
import { HostTab } from './Host'
import { OwnerTab } from './Owner'

type Tab = 'stay' | 'host' | 'owner'

export default function Page() {
  const [tab, setTab] = useState<Tab>('stay')
  const [status, setStatus] = useState<Status | null>(null)
  const [listings, setListings] = useState<Listing[]>([])
  const [kinds, setKinds] = useState<Kind[]>([])
  const [handle, setHandle] = useState('')
  const [ready, setReady] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    setHandle(localStorage.getItem('obnb_handle') || '')
    setReady(true)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [s, l, k] = await Promise.all([
        api('status'), api('listings'), api('kinds'),
      ])
      setStatus(s); setListings(l); setKinds(k)
    } catch (e: any) { setErr(e.message) }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const setHandlePersisted = (h: string) => {
    setHandle(h)
    localStorage.setItem('obnb_handle', h)
  }

  return (
    <div className="wrap">
      <header className="masthead">
        <div>
          <div className="logo">Open<em>BnB</em></div>
          <div className="tagline">{status?.tagline || 'Stay anywhere. Rules by the house.'}</div>
        </div>
        <div className="row">
          <div style={{ width: 170 }}>
            <input
              placeholder="your handle"
              value={handle}
              onChange={(e) => setHandlePersisted(e.target.value.trim().toLowerCase())}
              aria-label="your handle"
            />
          </div>
          <nav className="tabs">
            {(['stay', 'host', 'owner'] as Tab[]).map((t) => (
              <button
                key={t}
                className={`tab ${t === 'owner' ? 'owner' : ''} ${tab === t ? 'on' : ''}`}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </nav>
        </div>
      </header>

      {status && (
        <div className="row" style={{ gap: 16, paddingBottom: 18 }}>
          <span className="muted">{status.live_listings} place(s)</span>
          <span className="muted">·</span>
          <span className="muted">{status.bookings} booking(s)</span>
          <span className="muted">·</span>
          <span className="muted">{status.active_rules} house rule(s) live</span>
          <span className="muted">·</span>
          <span className="muted">
            service fee {status.fee_bps ? `${status.fee_bps / 100}%` : 'none'} · settles in {status.currency}
          </span>
        </div>
      )}

      {err && <div className="banner err" style={{ marginBottom: 16 }}>{err}</div>}

      {!ready ? null : tab === 'stay' ? (
        <StayTab listings={listings} kinds={kinds} handle={handle} onChange={refresh} />
      ) : tab === 'host' ? (
        <HostTab listings={listings} kinds={kinds} handle={handle} onChange={refresh} />
      ) : (
        <OwnerTab onChange={refresh} />
      )}
    </div>
  )
}

/* ── STAY — browse, quote, book ──────────────────────────────── */
function StayTab({ listings, kinds, handle, onChange }: {
  listings: Listing[]; kinds: Kind[]; handle: string; onChange: () => void
}) {
  const [city, setCity] = useState('')
  const [kind, setKind] = useState('')
  const [selected, setSelected] = useState<Listing | null>(null)

  const cities = useMemo(
    () => Array.from(new Set(listings.map((l) => l.city))).sort(),
    [listings],
  )
  const shown = listings.filter(
    (l) => (!city || l.city === city) && (!kind || l.kind === kind),
  )

  return (
    <div className="grid" style={{ gridTemplateColumns: selected ? 'minmax(0,1fr) 380px' : '1fr', alignItems: 'start' }}>
      <div className="stack">
        <div className="row">
          <div className="field" style={{ maxWidth: 190 }}>
            <select value={city} onChange={(e) => setCity(e.target.value)} aria-label="city">
              <option value="">Anywhere</option>
              {cities.map((c) => <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>)}
            </select>
          </div>
          <div className="field" style={{ maxWidth: 190 }}>
            <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="place type">
              <option value="">Any type</option>
              {kinds.filter((k) => k.allowed).map((k) => (
                <option key={k.key} value={k.key}>{k.label}</option>
              ))}
            </select>
          </div>
          <span className="muted">{shown.length} place(s)</span>
        </div>

        {shown.length === 0 ? (
          <div className="card empty">
            <div className="big">Nobody has listed yet.</div>
            <div>Open the HOST tab and put the first place on the map.</div>
          </div>
        ) : (
          <div className="grid cols-3">
            {shown.map((l) => (
              <article
                key={l.id}
                className={`card listing ${selected?.id === l.id ? 'on' : ''}`}
                onClick={() => setSelected(l)}
              >
                {/* typographic, not emoji — servers without an emoji font render tofu */}
                <div className="thumb">{kinds.find((k) => k.key === l.kind)?.label || 'Place'}</div>
                <div className="pad stack" style={{ gap: 8 }}>
                  <div className="spread">
                    <h3>{l.title}</h3>
                    <span className="price">{l.price}</span>
                  </div>
                  <div className="muted">
                    {l.city[0].toUpperCase() + l.city.slice(1)} · sleeps {l.guests} · {l.bedrooms} bed(s)
                  </div>
                  <div className="chips">
                    {l.instant_book && <span className="chip clay">instant book</span>}
                    {l.min_nights > 1 && <span className="chip">{l.min_nights}-night min</span>}
                    {l.amenities.slice(0, 3).map((a) => <span key={a} className="chip">{a}</span>)}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      {selected && (
        <BookPanel
          listing={selected}
          handle={handle}
          onClose={() => setSelected(null)}
          onBooked={onChange}
        />
      )}
    </div>
  )
}

function BookPanel({ listing, handle, onClose, onBooked }: {
  listing: Listing; handle: string; onClose: () => void; onBooked: () => void
}) {
  const [checkin, setCheckin] = useState(addDays(today(), 7))
  const [checkout, setCheckout] = useState(addDays(today(), 10))
  const [guests, setGuests] = useState(1)
  const [quote, setQuote] = useState<Quote | null>(null)
  const [booked, setBooked] = useState<Booking | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [taken, setTaken] = useState<string[]>([])

  useEffect(() => {
    setQuote(null); setBooked(null); setErr('')
    api(`listing/${listing.id}/calendar`).then((c) => setTaken(c.booked || [])).catch(() => setTaken([]))
  }, [listing.id])

  const nights = nightsBetween(checkin, checkout)

  const getQuote = async () => {
    setBusy(true); setErr(''); setBooked(null)
    try {
      // explain=true: the guest sees exactly which house rule moved the price
      const q = await api('quote', {
        body: { listing_id: listing.id, checkin, checkout, guests, guest: handle, explain: true },
      })
      setQuote(q)
    } catch (e: any) { setErr(e.message); setQuote(null) }
    setBusy(false)
  }

  const doBook = async () => {
    if (!handle) { setErr('set your handle in the header first'); return }
    setBusy(true); setErr('')
    try {
      const b = await api('book', {
        body: { listing_id: listing.id, guest: handle, checkin, checkout, guests },
      })
      setBooked(b); setQuote(null); onBooked()
      api(`listing/${listing.id}/calendar`).then((c) => setTaken(c.booked || [])).catch(() => {})
    } catch (e: any) { setErr(e.message) }
    setBusy(false)
  }

  return (
    <aside className="card pad stack" style={{ position: 'sticky', top: 20 }}>
      <div className="spread">
        <h2 style={{ fontSize: 22 }}>{listing.title}</h2>
        <button className="btn ghost small" onClick={onClose}>close</button>
      </div>
      <div className="muted">
        Hosted by {listing.host} · {listing.city[0].toUpperCase() + listing.city.slice(1)}
      </div>
      {listing.notes && <p style={{ fontSize: 14, color: 'var(--ink-2)' }}>{listing.notes}</p>}
      <div className="chips">
        {listing.amenities.map((a) => <span key={a} className="chip">{a}</span>)}
      </div>

      <div className="row">
        <div className="field">
          <label>Check in</label>
          <input type="date" min={today()} value={checkin}
                 onChange={(e) => setCheckin(e.target.value)} />
        </div>
        <div className="field">
          <label>Check out</label>
          <input type="date" min={addDays(checkin, 1)} value={checkout}
                 onChange={(e) => setCheckout(e.target.value)} />
        </div>
        <div className="field" style={{ maxWidth: 90 }}>
          <label>Guests</label>
          <input type="number" min={1} max={listing.guests} value={guests}
                 onChange={(e) => setGuests(Number(e.target.value))} />
        </div>
      </div>

      {taken.length > 0 && (
        <div className="muted">
          {taken.length} night(s) already taken — first is {taken[0]}
        </div>
      )}

      <div className="row">
        <button className="btn ghost" onClick={getQuote} disabled={busy || nights < 1}>
          {nights ? `Price ${nights} night(s)` : 'Pick your dates'}
        </button>
        {quote && (
          <button className="btn clay" onClick={doBook} disabled={busy}>
            {quote.instant ? 'Book now' : 'Request to book'}
          </button>
        )}
      </div>

      {err && <div className="banner err">{err}</div>}

      {quote && (
        <div className="stack" style={{ gap: 10 }}>
          <div className="lines">
            {quote.lines.map((l, i) => (
              <div className="line" key={i}>
                <span>
                  {l.label}
                  {l.rules?.length ? <span className="muted"> · {l.rules.join(', ')}</span> : null}
                </span>
                <span>{money(l.amount, quote.currency)}</span>
              </div>
            ))}
            <div className="line total">
              <span>Total</span><span>{money(quote.total, quote.currency)}</span>
            </div>
          </div>
          {quote.tags.length > 0 && (
            <div className="chips">{quote.tags.map((t) => <span key={t} className="chip clay">{t}</span>)}</div>
          )}
          <div className="muted">
            {quote.instant ? 'Confirms instantly.' : 'The host reviews this request before it confirms.'}
          </div>
          {quote.trace && quote.trace.some((t) => t.matched) && (
            <details>
              <summary className="eyebrow" style={{ cursor: 'pointer' }}>why this price</summary>
              <div className="stack" style={{ gap: 6, marginTop: 8 }}>
                {quote.trace.filter((t) => t.matched).map((t) => (
                  <div key={t.id} className="mono" style={{ color: 'var(--ink-2)' }}>
                    {t.rule}: {t.when} → {JSON.stringify(t.then)}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {booked && (
        <div className={`banner ${booked.status === 'confirmed' ? 'ok' : 'info'}`}>
          {booked.status === 'confirmed'
            ? `Confirmed — ${booked.nights} night(s), ${money(booked.quote.total, booked.quote.currency)}. Reference ${booked.id}.`
            : `Requested — waiting on ${booked.host}. Reference ${booked.id}.`}
        </div>
      )}
    </aside>
  )
}
