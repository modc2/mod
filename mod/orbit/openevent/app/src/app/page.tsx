"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { toast } from 'react-toastify'
import {
  api, EventItem, DiscoverResult, SourceReport, Platform, City, Syndication,
} from '@/lib/api'
import { loadKeys, saveKey, loadHandle, saveHandle, loadCity, saveCity } from '@/lib/store'

// ── date helpers ───────────────────────────────────────────────────
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
function stub(ts: number | null) {
  if (!ts) return { mo: 'TBA', day: '·', time: '' }
  const d = new Date(ts * 1000)
  return {
    mo: MONTHS[d.getMonth()], day: String(d.getDate()),
    time: d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }),
  }
}
function fmtWhen(ts: number | null) {
  if (!ts) return 'Date TBA'
  return new Date(ts * 1000).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
function toLocalInput(ts: number) {
  const d = new Date(ts * 1000), off = d.getTimezoneOffset() * 60000
  return new Date(d.getTime() - off).toISOString().slice(0, 16)
}

export default function Page() {
  const [platforms, setPlatforms] = useState<Platform[]>([])
  const [cities, setCities] = useState<City[]>([])
  const [city, setCity] = useState('')
  const [tab, setTab] = useState<'discover' | 'mine'>('discover')
  const [query, setQuery] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const [filter, setFilter] = useState('all')          // platform filter
  const [res, setRes] = useState<DiscoverResult | null>(null)
  const [mine, setMine] = useState<EventItem[]>([])
  const [loading, setLoading] = useState(true)
  const [handle, setHandle] = useState('')
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [showCompose, setShowCompose] = useState(false)
  const [showConn, setShowConn] = useState(false)
  const [showCitySearch, setShowCitySearch] = useState(false)
  const [detail, setDetail] = useState<EventItem | null>(null)
  const [mapReady, setMapReady] = useState(false)

  const mapRef = useRef<any>(null)
  const layerRef = useRef<any>(null)
  const mapElRef = useRef<HTMLDivElement>(null)

  const activeCity = useMemo(() => cities.find(c => c.key === city), [cities, city])
  const platColor = useMemo(() => Object.fromEntries(platforms.map(p => [p.key, p.color])), [platforms])

  useEffect(() => {
    setHandle(loadHandle()); setKeys(loadKeys())
    api('platforms').then(setPlatforms).catch(() => {})
    api('cities').then((cs: City[]) => {
      setCities(cs)
      const saved = loadCity()
      const def = cs.find(c => c.key === saved) || cs.find(c => c.default) || cs[0]
      if (def) setCity(def.key)
    }).catch(() => {})
  }, [])

  const refreshDiscover = useCallback(async (force = false) => {
    if (!city) return
    setLoading(true)
    try {
      const q = new URLSearchParams({ city, limit: '60' })
      if (activeQuery) q.set('query', activeQuery)
      if (force) q.set('refresh', 'true')
      setRes(await api(`discover?${q.toString()}`))
    } catch (e: any) { toast.error(e.message) }
    finally { setLoading(false) }
  }, [city, activeQuery])

  const refreshMine = useCallback(async () => {
    try { setMine(await api('events')) } catch {}
  }, [])

  useEffect(() => { refreshDiscover() }, [refreshDiscover])
  useEffect(() => { refreshMine() }, [refreshMine])

  // ── Leaflet ──────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const L = (await import('leaflet')).default
      if (cancelled || !mapElRef.current || mapRef.current) return
      const map = L.map(mapElRef.current, { zoomControl: true, attributionControl: false }).setView([37.7749, -122.4194], 12)
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 19 }).addTo(map)
      mapRef.current = map; layerRef.current = L.layerGroup().addTo(map); setMapReady(true)
    })()
    return () => { cancelled = true }
  }, [])

  const events = useMemo(() => {
    let list = res?.events || []
    if (filter !== 'all') list = list.filter(e => e.platforms.some(p => p.key === filter))
    return list
  }, [res, filter])
  const mapped = useMemo(() => events.filter(e => e.lat != null && e.lng != null), [events])

  const renderPins = useCallback(async () => {
    if (!mapRef.current) return
    const L = (await import('leaflet')).default
    const layer = layerRef.current; layer.clearLayers()
    const byLoc: Record<string, EventItem[]> = {}
    for (const e of mapped) {
      const k = `${e.lat!.toFixed(4)},${e.lng!.toFixed(4)}`
      ;(byLoc[k] ||= []).push(e)
    }
    Object.values(byLoc).forEach(list => {
      const e = list[0]
      const color = platColor[e.platforms[0]?.key] || '#7c5cff'
      const icon = L.divIcon({ className: '', iconSize: [30, 30], iconAnchor: [15, 30], popupAnchor: [0, -28],
        html: `<div class="pin" style="--c:${color}"><span>${e.mine ? '★' : '◆'}</span></div>` })
      const mk = L.marker([e.lat!, e.lng!], { icon }).addTo(layer)
      const items = list.slice(0, 4).map(o => `<div style="margin:5px 0"><b>${esc(o.title)}</b><br/><span style="opacity:.65;font-size:12px">${fmtWhen(o.starts_at)} · ${o.free ? 'free' : `${o.price?.amount} ${o.price?.currency}`}</span></div>`).join('')
      mk.bindPopup(`<div style="min-width:190px"><div style="font-weight:800;margin-bottom:5px;font-size:14px">${esc(e.venue || e.address || activeCity?.label || '')}</div>${items}${list.length > 4 ? `<div style="opacity:.55;font-size:12px">+${list.length - 4} more</div>` : ''}</div>`)
      mk.on('click', () => openDetail(list[0]))
    })
  }, [mapped, platColor, activeCity])

  useEffect(() => { if (mapReady) renderPins() }, [renderPins, mapReady])
  useEffect(() => {
    if (!mapReady || !mapRef.current || !activeCity) return
    mapRef.current.setView([activeCity.lat, activeCity.lng], activeCity.zoom || 12)
  }, [activeCity, mapReady])

  async function openDetail(e: EventItem) {
    if (e.id) { try { setDetail(await api(`event/${e.id}`)) } catch { setDetail(e) } }
    else setDetail(e)
  }
  function onHandle(v: string) { setHandle(v); saveHandle(v) }
  function pickCity(k: string) { setCity(k); saveCity(k); setShowCitySearch(false); setTab('discover') }

  const okSources = res?.sources.filter(s => s.ok && s.count > 0) || []
  const liveCount = res?.total ?? 0

  return (
    <main>
      {/* Nav */}
      <nav className="nav">
        <div className="wrap" style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '13px 22px' }}>
          <span className="brand"><span className="dot" />Open<span className="ev">Event</span></span>
          <div className="tabs">
            <button className={`tab ${tab === 'discover' ? 'active' : ''}`} onClick={() => setTab('discover')}>◆ Discover</button>
            <button className={`tab ${tab === 'mine' ? 'active' : ''}`} onClick={() => { setTab('mine'); refreshMine() }}>★ Mine</button>
          </div>
          <div style={{ flex: 1 }} />
          <input className="input nav-name" placeholder="your name" value={handle} onChange={e => onHandle(e.target.value)} />
          <button className="btn btn-sm" onClick={() => setShowConn(true)} title="Connect platforms">⚙ Connections</button>
          <button className="btn btn-primary" onClick={() => setShowCompose(true)}>+ Broadcast event</button>
        </div>
      </nav>

      {/* Hero */}
      <section className="hero">
        {['🎟️', '🎪', '🎤', '🪩', '🎸', '✦'].map((g, i) => (
          <span key={i} className="glyph" style={{ left: `${[6, 80, 30, 90, 52, 16][i]}%`, top: `${[20, 12, 70, 58, 88, 46][i]}%`, animationDelay: `${i * 2.3}s`, fontSize: `${[68, 52, 88, 58, 46, 74][i]}px` }}>{g}</span>
        ))}
        <div className="wrap" style={{ position: 'relative' }}>
          <div className="hero-eyebrow">every platform · one place</div>
          <h1>What&rsquo;s on in<br /><span className="ev">{activeCity?.label || 'your city'}</span>.</h1>
          <p className="hero-sub">
            One live feed of every event near you — pulled straight across <b>Luma</b>, <b>Eventbrite</b> and <b>Meetup</b>.
            Then <b>compose once and broadcast everywhere.</b> Stop checking five apps.
          </p>
          <div className="ticker">
            <div className="stat"><span className="num"><span className="dot-live" />{loading ? '—' : liveCount}</span><span className="lab">events on the board</span></div>
            <div className="stat"><span className="num">{okSources.length || '—'}</span><span className="lab">platforms live</span></div>
            <div className="stat"><span className="num">{platforms.filter(p => p.connected).length}</span><span className="lab">accounts connected</span></div>
          </div>
        </div>
        <div className="marquee">
          <div className="marquee-track">
            {[...platforms, ...platforms, ...platforms].map((p, i) => (
              <span key={i} className="marquee-item"><span className="swatch" style={{ background: p.color }} />{p.label} · {p.can_discover ? 'discover' : ''}{p.can_publish ? (p.can_discover ? ' + broadcast' : 'broadcast') : ''}</span>
            ))}
          </div>
        </div>
      </section>

      <div className="wrap" style={{ paddingBottom: 70 }}>
        {tab === 'mine' ? (
          <MineView events={mine} keys={keys} platColor={platColor} onOpen={openDetail} onCompose={() => setShowCompose(true)} />
        ) : (
          <>
            {/* Controls */}
            <div style={{ display: 'flex', gap: 9, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
              <button className="chip active" onClick={() => setShowCitySearch(true)}>📍 {activeCity?.label || 'Pick a city'} ▾</button>
              <form style={{ flex: 1, minWidth: 220, display: 'flex', gap: 8 }} onSubmit={e => { e.preventDefault(); setActiveQuery(query.trim()) }}>
                <input className="input" placeholder="Search events — “AI”, “run club”, “techno”…" value={query} onChange={e => setQuery(e.target.value)} />
                {activeQuery && <button type="button" className="btn btn-sm" onClick={() => { setQuery(''); setActiveQuery('') }}>✕</button>}
              </form>
              <button className="btn btn-sm" onClick={() => refreshDiscover(true)} title="Refresh from platforms">↻ Refresh</button>
            </div>

            {/* Platform filter + source status */}
            <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap', marginBottom: 10 }}>
              <button className={`chip ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>✦ All platforms</button>
              {platforms.map(p => (
                <button key={p.key} className={`chip ${filter === p.key ? 'active' : ''}`} onClick={() => setFilter(p.key)}>
                  <span className="swatch" style={{ background: p.color }} />{p.label}
                </button>
              ))}
            </div>
            <SourceStrip sources={res?.sources || []} cached={res?.cached} age={res?.age} />

            <div className="oe-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.05fr) minmax(0,1fr)', gap: 22, alignItems: 'start', marginTop: 16 }}>
              {/* Feed */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14 }}>
                  <h2 className="section-title">What&rsquo;s on</h2>
                  <span className="muted" style={{ fontSize: 13 }}>{events.length} event{events.length !== 1 ? 's' : ''}{activeQuery ? ` · “${activeQuery}”` : ''}</span>
                </div>
                {loading && <div className="muted">Reading {okSources.length ? '' : 'the'} city across platforms…</div>}
                {!loading && events.length === 0 && (
                  <div className="card empty">
                    <div className="big">🎟️</div>
                    <div className="font-display" style={{ fontSize: 22, marginBottom: 6 }}>Nothing surfaced{activeQuery ? ` for “${activeQuery}”` : ''}.</div>
                    <div className="muted" style={{ fontSize: 15, marginBottom: 20 }}>Try another city, clear the search — or be the one who puts something on.</div>
                    <button className="btn btn-primary" onClick={() => setShowCompose(true)}>+ Broadcast the first event</button>
                  </div>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
                  {events.map((e, i) => <EventCard key={e.uid + i} e={e} idx={i} platColor={platColor} onOpen={() => openDetail(e)} />)}
                </div>
              </div>

              {/* Map */}
              <div className="card map-card map-frame" style={{ height: 580, position: 'sticky', top: 78 }}>
                <div className="map-label">↳ {activeCity?.label || 'the city'} · {mapped.length} pinned</div>
                <div className="map-overlay" /><div className="map-glow" />
                <div id="map" ref={mapElRef} />
              </div>
            </div>
          </>
        )}
      </div>

      {showCitySearch && <CitySearch cities={cities} active={city} onPick={pickCity} onClose={() => setShowCitySearch(false)} />}
      {showConn && <ConnectionsModal platforms={platforms} onClose={() => setShowConn(false)} onChange={() => api('platforms').then(setPlatforms)} />}
      {showCompose && (
        <ComposeModal platforms={platforms} cities={cities} defaultCity={city} handle={handle}
          onClose={() => setShowCompose(false)}
          onCreated={(ev) => { if (ev.id && ev.edit_key) { saveKey(ev.id, ev.edit_key); setKeys(loadKeys()) } refreshMine() }}
          onConnect={() => setShowConn(true)} />
      )}
      {detail && <DetailModal e={detail} editKey={detail.id ? keys[detail.id] : undefined} platforms={platforms}
        onClose={() => setDetail(null)} onChanged={() => { refreshMine(); refreshDiscover(true) }} />}
    </main>
  )
}

function esc(s: string) { return (s || '').replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]!)) }

// ── source status strip ────────────────────────────────────────────
function SourceStrip({ sources, cached, age }: { sources: SourceReport[]; cached?: boolean; age?: number }) {
  if (!sources.length) return null
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 12.5 }}>
      <span className="muted" style={{ textTransform: 'uppercase', letterSpacing: '.1em', fontSize: 11, fontWeight: 700 }}>sources</span>
      {sources.map(s => (
        <span key={s.key} className="badge" title={s.error || `${s.count} events · ${s.ms}ms`} style={{ borderColor: s.ok && s.count ? 'rgba(25,227,194,.4)' : s.ok ? 'var(--line)' : 'rgba(255,107,129,.4)' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: s.ok && s.count ? '#19e3c2' : s.ok ? '#6e6a7c' : '#ff6b81' }} />
          {s.label} {s.ok ? s.count : 'blocked'}
        </span>
      ))}
      {cached && <span className="muted" style={{ fontSize: 11.5 }}>· cached {age}s ago</span>}
    </div>
  )
}

// ── event card (ticket stub) ───────────────────────────────────────
function EventCard({ e, idx, platColor, onOpen }: { e: EventItem; idx: number; platColor: Record<string, string>; onOpen: () => void }) {
  const s = stub(e.starts_at)
  const color = platColor[e.platforms[0]?.key] || (e.mine ? '#7c5cff' : '#7c5cff')
  return (
    <div className="card ev-card" style={{ ['--c' as any]: color, animationDelay: `${Math.min(idx, 9) * 50}ms` }} onClick={onOpen}>
      <div className="ev-stub">
        <span className="mo">{s.mo}</span><span className="day">{s.day}</span><span className="time">{s.time}</span>
      </div>
      <div className="ev-body">
        <div style={{ fontWeight: 800, fontSize: 16.5, letterSpacing: '-.01em', lineHeight: 1.18 }}>{e.title}</div>
        <div className="muted" style={{ fontSize: 13.5 }}>
          {e.online ? '🌐 Online' : (e.venue || e.address || e.city || 'Venue TBA')}{e.organizer ? ` · by ${e.organizer}` : ''}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 'auto', paddingTop: 4 }}>
          {e.platforms.map(p => (
            <span key={p.key} className="pill" style={{ ['--p' as any]: p.color }}><span className="swatch" />{p.label}</span>
          ))}
          {e.free ? <span className="badge badge-free">FREE</span> : <span className="badge badge-paid">{e.price?.amount} {e.price?.currency}</span>}
          {e.mine && <span className="badge badge-mine">★ yours</span>}
        </div>
      </div>
      {e.cover_image && <div className="ev-cover" style={{ backgroundImage: `url(${e.cover_image})` }} />}
    </div>
  )
}

// ── Mine view ──────────────────────────────────────────────────────
function MineView({ events, platColor, onOpen, onCompose }: {
  events: EventItem[]; keys: Record<string, string>; platColor: Record<string, string>; onOpen: (e: EventItem) => void; onCompose: () => void
}) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 16 }}>
        <h2 className="section-title">Events you&rsquo;ve broadcast</h2>
        <button className="btn btn-primary btn-sm" onClick={onCompose}>+ Broadcast event</button>
      </div>
      {events.length === 0 ? (
        <div className="card empty">
          <div className="big">📡</div>
          <div className="font-display" style={{ fontSize: 22, marginBottom: 6 }}>You haven&rsquo;t broadcast anything yet.</div>
          <div className="muted" style={{ fontSize: 15, marginBottom: 20 }}>Compose once — push it to Luma, Eventbrite and Meetup in one shot.</div>
          <button className="btn btn-primary" onClick={onCompose}>+ Broadcast your first event</button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
          {events.map((e, i) => <EventCard key={e.uid} e={e} idx={i} platColor={platColor} onOpen={() => onOpen(e)} />)}
        </div>
      )}
    </div>
  )
}

// ── City search ────────────────────────────────────────────────────
function CitySearch({ cities, active, onPick, onClose }: { cities: City[]; active: string; onPick: (k: string) => void; onClose: () => void }) {
  const [q, setQ] = useState('')
  const ql = q.trim().toLowerCase()
  const matches = cities.filter(c => !ql || c.label.toLowerCase().includes(ql) || (c.country || '').toLowerCase().includes(ql))
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 560 }} onClick={e => e.stopPropagation()}>
        <h2 className="font-display" style={{ fontSize: 24, marginBottom: 14 }}>Where are you?</h2>
        <input className="input" autoFocus placeholder="Search for your city…" value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && matches.length) onPick(matches[0].key) }} style={{ fontSize: 17, padding: '14px 16px', marginBottom: 12 }} />
        <div className="suggest-list">
          {matches.map(c => (
            <button key={c.key} className={`suggest ${c.key === active ? 'active' : ''}`} onClick={() => onPick(c.key)}>
              <span className="suggest-pin">📍</span>
              <span style={{ flex: 1, textAlign: 'left' }}><span style={{ fontWeight: 700 }}>{c.label}</span><span className="muted" style={{ marginLeft: 8, fontSize: 13 }}>{c.country}</span></span>
              {c.key === active && <span style={{ fontSize: 12, color: '#7ff0dc', fontWeight: 700 }}>● viewing</span>}
            </button>
          ))}
          {matches.length === 0 && <div className="muted" style={{ padding: '14px 4px', fontSize: 14 }}>No match. More cities are added as the network grows.</div>}
        </div>
      </div>
    </div>
  )
}

// ── Connections (BYOK) ─────────────────────────────────────────────
function ConnectionsModal({ platforms, onClose, onChange }: { platforms: Platform[]; onClose: () => void; onChange: () => void }) {
  const [busy, setBusy] = useState('')
  const [tok, setTok] = useState<Record<string, { token: string; organization_id: string }>>({})
  function fld(k: string) { return tok[k] || { token: '', organization_id: '' } }

  async function connect(p: Platform) {
    const f = fld(p.key)
    if (p.publish_mode === 'api' && !f.token.trim()) return toast.error('Paste your API token first')
    setBusy(p.key)
    try {
      await api('connect', { method: 'POST', body: { platform: p.key, fields: { token: f.token.trim(), organization_id: f.organization_id.trim() || undefined } } })
      toast.success(`${p.label} connected`); onChange()
    } catch (e: any) { toast.error(e.message) } finally { setBusy('') }
  }
  async function disconnect(p: Platform) {
    setBusy(p.key)
    try { await api('disconnect', { method: 'POST', body: { platform: p.key } }); toast.info(`${p.label} disconnected`); onChange() }
    catch (e: any) { toast.error(e.message) } finally { setBusy('') }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <h2 className="font-display" style={{ fontSize: 24 }}>Connect platforms</h2>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>
        <p className="muted" style={{ fontSize: 13.5, marginBottom: 18 }}>
          Bring your own keys. Tokens are stored <b>off-chain</b> on the server (chmod&nbsp;600) and are never committed or shared.
          Connect Eventbrite to push <b>real drafts</b>; Luma &amp; Meetup use a one-click prefilled hand-off.
        </p>
        {platforms.map(p => (
          <div key={p.key} className="card" style={{ marginBottom: 12, padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: p.publish_mode === 'api' ? 12 : 0 }}>
              <span className="target tswatch" style={{ background: p.color, width: 30, height: 30 }}>{p.label[0]}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 800 }}>{p.label}</div>
                <div className="muted" style={{ fontSize: 12.5 }}>
                  {p.publish_mode === 'api' ? 'Real API publishing — needs an OAuth token' : 'Prefilled hand-off — no key required'}
                  {p.can_discover ? ' · live discovery' : ''}
                </div>
              </div>
              {p.connected
                ? <button className="btn btn-sm btn-danger" disabled={busy === p.key} onClick={() => disconnect(p)}>Disconnect</button>
                : p.publish_mode !== 'api' && <span className="badge" style={{ opacity: .7 }}>ready</span>}
            </div>
            {p.publish_mode === 'api' && !p.connected && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
                <input className="input" placeholder={`${p.label} personal OAuth token`} value={fld(p.key).token}
                  onChange={e => setTok({ ...tok, [p.key]: { ...fld(p.key), token: e.target.value } })} />
                <input className="input" placeholder="organization id (optional — auto-detected)" value={fld(p.key).organization_id}
                  onChange={e => setTok({ ...tok, [p.key]: { ...fld(p.key), organization_id: e.target.value } })} />
                <button className="btn btn-primary btn-sm" disabled={busy === p.key} onClick={() => connect(p)}>{busy === p.key ? 'Connecting…' : 'Connect'}</button>
                <div className="muted" style={{ fontSize: 11.5 }}>Eventbrite → Account Settings → Developer → API Keys → your <b>private token</b>.</div>
              </div>
            )}
            {p.connected && <span className="badge badge-free" style={{ marginTop: 2 }}>● connected</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Compose + broadcast ────────────────────────────────────────────
function ComposeModal({ platforms, cities, defaultCity, handle, onClose, onCreated, onConnect }: {
  platforms: Platform[]; cities: City[]; defaultCity: string; handle: string
  onClose: () => void; onCreated: (ev: EventItem) => void; onConnect: () => void
}) {
  const [title, setTitle] = useState('')
  const [when, setWhen] = useState(toLocalInput(Math.floor(Date.now() / 1000) + 3 * 86400))
  const [endWhen, setEndWhen] = useState('')
  const [city, setCity] = useState(defaultCity)
  const [venue, setVenue] = useState('')
  const [address, setAddress] = useState('')
  const [online, setOnline] = useState(false)
  const [organizer, setOrganizer] = useState(handle)
  const [cover, setCover] = useState('')
  const [desc, setDesc] = useState('')
  const [paid, setPaid] = useState(false)
  const [amount, setAmount] = useState('10')
  const [currency, setCurrency] = useState('USD')
  const [targets, setTargets] = useState<string[]>(platforms.map(p => p.key))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<EventItem | null>(null)

  useEffect(() => { setOrganizer(handle) }, [handle])
  useEffect(() => { setTargets(platforms.filter(p => p.can_publish).map(p => p.key)) }, [platforms])
  function toggle(k: string) { setTargets(t => t.includes(k) ? t.filter(x => x !== k) : [...t, k]) }

  async function submit() {
    if (!title.trim()) return toast.error('Give it a title')
    if (!targets.length) return toast.error('Pick at least one platform to broadcast to')
    const starts_at = Math.floor(new Date(when).getTime() / 1000)
    const body: any = {
      title: title.trim(), starts_at, city, venue, address, online,
      organizer: organizer.trim(), cover_image: cover.trim(), description: desc.trim(), targets,
    }
    if (endWhen) body.ends_at = Math.floor(new Date(endWhen).getTime() / 1000)
    if (paid) body.price = { amount: parseFloat(amount) || 0, currency }
    setBusy(true)
    try {
      const ev: EventItem = await api('events', { method: 'POST', body })
      onCreated(ev); setResult(ev)
      toast.success('Broadcast composed!')
    } catch (e: any) { toast.error(e.message) } finally { setBusy(false) }
  }

  if (result) return <BroadcastResult ev={result} onClose={onClose} onConnect={onConnect} />

  const noKey = targets.some(t => { const p = platforms.find(x => x.key === t); return p?.publish_mode === 'api' && !p.connected })

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
          <h2 className="font-display" style={{ fontSize: 24 }}>Compose once →<span className="ev"> broadcast everywhere</span></h2>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>

        <Field label="Event title"><input className="input" autoFocus value={title} onChange={e => setTitle(e.target.value)} placeholder="Mod Orbit Launch Party" /></Field>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Field label="Starts"><input className="input" type="datetime-local" value={when} onChange={e => setWhen(e.target.value)} /></Field>
          <Field label="Ends (optional)"><input className="input" type="datetime-local" value={endWhen} onChange={e => setEndWhen(e.target.value)} /></Field>
        </div>
        <Field label="City">
          <select className="select" value={city} onChange={e => setCity(e.target.value)}>
            {cities.map(c => <option key={c.key} value={c.key}>{c.label}{c.country ? `, ${c.country}` : ''}</option>)}
          </select>
        </Field>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', margin: '2px 0 12px' }}>
          <input type="checkbox" checked={online} onChange={e => setOnline(e.target.checked)} style={{ width: 16, height: 16, accentColor: '#57b3ff' }} />
          <span style={{ fontWeight: 700 }}>Online event</span>
        </label>
        {!online && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <Field label="Venue"><input className="input" value={venue} onChange={e => setVenue(e.target.value)} placeholder="The Pad" /></Field>
            <Field label="Address"><input className="input" value={address} onChange={e => setAddress(e.target.value)} placeholder="123 Market St" /></Field>
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Field label="Organizer"><input className="input" value={organizer} onChange={e => setOrganizer(e.target.value)} placeholder="you" /></Field>
          <Field label="Cover image URL"><input className="input" value={cover} onChange={e => setCover(e.target.value)} placeholder="https://…" /></Field>
        </div>
        <Field label="Description"><textarea className="input" rows={3} value={desc} onChange={e => setDesc(e.target.value)} placeholder="What's the event about?" /></Field>

        <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', marginBottom: paid ? 12 : 4 }}>
          <input type="checkbox" checked={paid} onChange={e => setPaid(e.target.checked)} style={{ width: 16, height: 16, accentColor: '#ffd84d' }} />
          <span style={{ fontWeight: 700 }}>Ticketed</span><span className="muted" style={{ fontSize: 13 }}>{paid ? 'has a price' : 'free'}</span>
        </label>
        {paid && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <Field label="Price"><input className="input" value={amount} onChange={e => setAmount(e.target.value)} /></Field>
            <Field label="Currency"><select className="select" value={currency} onChange={e => setCurrency(e.target.value)}><option>USD</option><option>EUR</option><option>GBP</option><option>CAD</option></select></Field>
          </div>
        )}

        <div className="divider" />
        <div className="label" style={{ marginBottom: 10 }}>Broadcast to</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
          {platforms.filter(p => p.can_publish).map(p => (
            <div key={p.key} className={`target ${targets.includes(p.key) ? 'on' : ''}`} style={{ ['--p' as any]: p.color }} onClick={() => toggle(p.key)}>
              <span className="tswatch" style={{ background: p.color }}>{p.label[0]}</span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700 }}>{p.label}</div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {p.publish_mode === 'api' ? (p.connected ? 'real draft via your token' : '⚠ connect token to publish') : 'one-click prefilled hand-off'}
                </div>
              </div>
              <div className="tcheck">{targets.includes(p.key) ? '✓' : ''}</div>
            </div>
          ))}
        </div>
        {noKey && <div className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>Some targets need a token. You can <button className="btn btn-sm" style={{ padding: '2px 8px' }} onClick={onConnect}>connect now</button> — or broadcast anyway and they&rsquo;ll come back as a hand-off.</div>}

        <button className="btn btn-primary" style={{ width: '100%', marginTop: 16, padding: 13 }} disabled={busy} onClick={submit}>
          {busy ? 'Broadcasting…' : `Broadcast to ${targets.length} platform${targets.length !== 1 ? 's' : ''} →`}
        </button>
      </div>
    </div>
  )
}

function BroadcastResult({ ev, onClose, onConnect }: { ev: EventItem; onClose: () => void; onConnect: () => void }) {
  const synd = ev.syndications || {}
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <h2 className="font-display" style={{ fontSize: 24 }}>🎉 It&rsquo;s out there</h2>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>
        <p className="muted" style={{ fontSize: 14, marginBottom: 18 }}><b style={{ color: '#fff' }}>{ev.title}</b> was broadcast. Here&rsquo;s where it landed:</p>
        {Object.entries(synd).map(([plat, r]) => <SyndRow key={plat} plat={plat} r={r} onConnect={onConnect} />)}
        <button className="btn" style={{ width: '100%', marginTop: 10 }} onClick={onClose}>Done</button>
      </div>
    </div>
  )
}

function SyndRow({ plat, r, onConnect }: { plat: string; r: Syndication; onConnect?: () => void }) {
  const label: Record<string, string> = { draft: 'Draft created', published: 'Published', assisted: 'Ready to finish', needs_key: 'Needs connection', error: 'Failed', skipped: 'Skipped' }
  return (
    <div className="synd">
      <span className={`dot ${r.status}`} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, textTransform: 'capitalize' }}>{plat} <span className="muted" style={{ fontWeight: 500, fontSize: 12.5 }}>· {label[r.status] || r.status}</span></div>
        {r.message && <div className="muted" style={{ fontSize: 12.5 }}>{r.message}</div>}
        {r.draft && (r.status === 'assisted' || r.status === 'needs_key') && (
          <button className="btn btn-sm" style={{ marginTop: 7, padding: '4px 10px' }}
            onClick={() => { navigator.clipboard?.writeText(r.draft!.text); toast.success('Event details copied — paste into ' + plat) }}>⧉ Copy details</button>
        )}
      </div>
      {r.url && <a className="btn btn-sm btn-gold" href={r.url} target="_blank" rel="noreferrer">{r.status === 'assisted' ? 'Open & finish →' : 'View →'}</a>}
      {r.status === 'needs_key' && onConnect && <button className="btn btn-sm" onClick={onConnect}>Connect</button>}
    </div>
  )
}

// ── Detail ─────────────────────────────────────────────────────────
function DetailModal({ e, editKey, platforms, onClose, onChanged }: {
  e: EventItem; editKey?: string; platforms: Platform[]; onClose: () => void; onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const synd = e.syndications || {}
  const mine = !!e.mine
  const notYet = platforms.filter(p => p.can_publish && !synd[p.key])

  async function broadcastMore(plat: string) {
    if (!e.id || !editKey) return
    setBusy(true)
    try { await api(`event/${e.id}/broadcast`, { method: 'POST', body: { edit_key: editKey, targets: [plat] } }); toast.success(`Pushed to ${plat}`); onChanged(); onClose() }
    catch (err: any) { toast.error(err.message) } finally { setBusy(false) }
  }
  async function del() {
    if (!e.id || !editKey || !confirm('Delete this event from OpenEvent?')) return
    try { await api(`event/${e.id}/delete`, { method: 'POST', body: { edit_key: editKey } }); toast.success('Deleted'); onChanged(); onClose() }
    catch (err: any) { toast.error(err.message) }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 600 }} onClick={ev => ev.stopPropagation()}>
        {e.cover_image && <div style={{ height: 150, borderRadius: 16, backgroundImage: `url(${e.cover_image})`, backgroundSize: 'cover', backgroundPosition: 'center', marginBottom: 16 }} />}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
          <h2 className="font-display" style={{ fontSize: 26, lineHeight: 1.05 }}>{e.title}</h2>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>
        <div className="muted" style={{ fontSize: 14, marginTop: 6 }}>{e.online ? '🌐 Online' : (e.address || e.venue || e.city || '')}{e.organizer ? ` · by ${e.organizer}` : ''}</div>

        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', margin: '14px 0' }}>
          <span className="badge">🕒 {fmtWhen(e.starts_at)}</span>
          {e.free ? <span className="badge badge-free">FREE</span> : <span className="badge badge-paid">{e.price?.amount} {e.price?.currency}</span>}
          {e.online && <span className="badge badge-online">Online</span>}
          {mine && <span className="badge badge-mine">★ yours</span>}
        </div>
        {e.description && <div className="font-serif" style={{ fontStyle: 'italic', fontSize: 15.5, color: '#d7d3df', marginBottom: 16 }}>“{e.description}”</div>}

        <div className="label" style={{ marginBottom: 9 }}>{mine ? 'Broadcast status' : 'Listed on'}</div>
        {!mine && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {e.platforms.map(p => p.url
              ? <a key={p.key} className="btn btn-sm" href={p.url} target="_blank" rel="noreferrer" style={{ borderColor: p.color }}><span className="pill" style={{ ['--p' as any]: p.color, border: 'none', background: 'none', padding: 0 }}><span className="swatch" /></span>{p.label} →</a>
              : <span key={p.key} className="pill" style={{ ['--p' as any]: p.color }}><span className="swatch" />{p.label}</span>)}
          </div>
        )}
        {mine && (
          <>
            {Object.entries(synd).map(([plat, r]) => <SyndRow key={plat} plat={plat} r={r} />)}
            {Object.keys(synd).length === 0 && <div className="muted" style={{ fontSize: 14 }}>Not broadcast anywhere yet.</div>}
            {editKey && notYet.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="label" style={{ marginBottom: 8 }}>Also push to</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {notYet.map(p => <button key={p.key} className="btn btn-sm" disabled={busy} onClick={() => broadcastMore(p.key)} style={{ borderColor: p.color }}>+ {p.label}</button>)}
                </div>
              </div>
            )}
            {editKey && <><div className="divider" /><button className="btn btn-sm btn-danger" onClick={del}>Delete event</button></>}
            {mine && !editKey && <div className="muted" style={{ fontSize: 12, marginTop: 13 }}>Created on another device — manage controls only show where it was composed.</div>}
          </>
        )}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div style={{ marginBottom: 13 }}><label className="label">{label}</label>{children}</div>
}
