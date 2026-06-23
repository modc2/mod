"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { toast } from 'react-toastify'
import { api, Occurrence, GameDetail, GameLink, ChatMessage, Sport, Venue, City } from '@/lib/api'

// ── local identity + admin-key vault (this browser only) ───────────
const LS_HANDLE = 'openplay.handle'
const LS_WALLET = 'openplay.wallet'
const LS_KEYS = 'openplay.adminkeys'

function loadKeys(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(LS_KEYS) || '{}') } catch { return {} }
}
function saveKey(gameId: string, key: string) {
  const k = loadKeys(); k[gameId] = key; localStorage.setItem(LS_KEYS, JSON.stringify(k))
}

const SPORT_COLORS: Record<string, string> = {
  soccer: '#34e0a1', basketball: '#ff8a3d', hockey: '#5ec8ff',
  tennis: '#f6e056', volleyball: '#ff7ac6', football: '#b69cff',
}

function fmtWhen(ts: number) {
  const d = new Date(ts * 1000)
  return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
function fmtChatTime(ts: number) {
  return new Date(ts * 1000).toLocaleString(undefined, { hour: 'numeric', minute: '2-digit' })
}
function toLocalInput(ts: number) {
  const d = new Date(ts * 1000)
  const off = d.getTimezoneOffset() * 60000
  return new Date(d.getTime() - off).toISOString().slice(0, 16)
}
function linkLabel(url: string) {
  const u = url.toLowerCase()
  if (u.includes('t.me') || u.includes('telegram')) return 'Telegram'
  if (u.includes('whatsapp') || u.includes('wa.me')) return 'WhatsApp'
  if (u.includes('discord')) return 'Discord'
  if (u.includes('signal')) return 'Signal'
  return 'Group chat'
}

type Stats = { games: number; upcoming: number; players_going: number; by_sport: Record<string, number> }

export default function Page() {
  const [sports, setSports] = useState<Sport[]>([])
  const [venues, setVenues] = useState<Venue[]>([])
  const [cities, setCities] = useState<City[]>([])
  const [city, setCity] = useState<string>('')
  const [view, setView] = useState<'board' | 'about'>('board')
  const [cityOpen, setCityOpen] = useState(false)
  const [games, setGames] = useState<Occurrence[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [filter, setFilter] = useState<string>('all')
  const [handle, setHandle] = useState('')
  const [wallet, setWallet] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [detail, setDetail] = useState<{ gameId: string; occ: string } | null>(null)
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [mapReady, setMapReady] = useState(false)

  const mapRef = useRef<any>(null)
  const layerRef = useRef<any>(null)
  const mapElRef = useRef<HTMLDivElement>(null)

  const sportMeta = useMemo(() => Object.fromEntries(sports.map(s => [s.key, s])), [sports])
  const activeCity = useMemo(() => cities.find(c => c.key === city), [cities, city])

  useEffect(() => {
    setHandle(localStorage.getItem(LS_HANDLE) || '')
    setWallet(localStorage.getItem(LS_WALLET) || '')
    setKeys(loadKeys())
  }, [])

  const pickCity = useCallback((key: string) => { setCity(key); setView('board'); setCityOpen(false) }, [])

  const refresh = useCallback(async () => {
    if (!city) return
    try {
      const q = new URLSearchParams({ city })
      if (filter !== 'all') q.set('sport', filter)
      const [g, s] = await Promise.all([api(`games?${q.toString()}`), api('status')])
      setGames(g); setStats(s)
    } catch (e: any) { toast.error(e.message) }
    finally { setLoading(false) }
  }, [filter, city])

  useEffect(() => {
    api('sports').then(setSports).catch(() => {})
    api('venues').then(setVenues).catch(() => {})
    api('cities').then((cs: City[]) => {
      setCities(cs)
      const def = cs.find(c => c.default) || cs[0]
      if (def) setCity(def.key)
    }).catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { const t = setInterval(refresh, 20000); return () => clearInterval(t) }, [refresh])

  // ── Leaflet map ──────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const L = (await import('leaflet')).default
      if (cancelled || !mapElRef.current || mapRef.current) return
      const map = L.map(mapElRef.current, { zoomControl: true, attributionControl: false })
        .setView([43.653, -79.383], 12)
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 19 }).addTo(map)
      mapRef.current = map
      layerRef.current = L.layerGroup().addTo(map)
      setMapReady(true)
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const renderPins = useCallback(async (L?: any) => {
    if (!mapRef.current) return
    L = L || (await import('leaflet')).default
    const layer = layerRef.current
    layer.clearLayers()
    const byLoc: Record<string, Occurrence[]> = {}
    for (const g of games) {
      if (g.lat == null || g.lng == null) continue
      const key = `${g.lat.toFixed(4)},${g.lng.toFixed(4)}`
      ;(byLoc[key] ||= []).push(g)
    }
    Object.values(byLoc).forEach(list => {
      const g = list[0]
      const color = SPORT_COLORS[g.sport] || '#9ca3af'
      const emoji = sportMeta[g.sport]?.emoji || '•'
      const icon = L.divIcon({
        className: '', iconSize: [30, 30], iconAnchor: [15, 30], popupAnchor: [0, -28],
        html: `<div class="pin" style="--c:${color}"><span>${emoji}</span></div>`,
      })
      const mk = L.marker([g.lat, g.lng], { icon }).addTo(layer)
      const items = list.slice(0, 4).map(o =>
        `<div style="margin:5px 0"><b>${o.title}</b><br/><span style="opacity:.65;font-size:12px">${fmtWhen(o.occ_ts)} · ${o.going}${o.capacity ? '/' + o.capacity : ''} in · ${o.free ? 'free' : `${o.cost?.amount} ${o.cost?.currency}`}</span></div>`
      ).join('')
      mk.bindPopup(`<div style="min-width:190px"><div style="font-weight:800;margin-bottom:5px;font-size:14px">${g.venue || g.neighborhood}</div>${items}${list.length > 4 ? `<div style="opacity:.55;font-size:12px">+${list.length - 4} more</div>` : ''}</div>`)
      mk.on('click', () => setDetail({ gameId: g.game_id, occ: g.occ }))
    })
  }, [games, sportMeta])

  useEffect(() => { if (mapReady) renderPins() }, [renderPins, mapReady])

  // recenter the map on the active city
  useEffect(() => {
    if (!mapReady || !mapRef.current || !activeCity || activeCity.lat == null || activeCity.lng == null) return
    mapRef.current.setView([activeCity.lat, activeCity.lng], activeCity.zoom || 12)
  }, [activeCity, mapReady])

  function saveHandle(v: string) { setHandle(v); localStorage.setItem(LS_HANDLE, v) }

  const sportsLive = stats ? Object.keys(stats.by_sport).length : 0
  const glyphs = ['⚽', '🏀', '🏒', '🎾', '🏐', '🏈']

  return (
    <main>
      {/* Nav — filters, search & create all live up here */}
      <nav className="nav">
        <div className="wrap navbar">
          <button className="brand brand-btn" onClick={() => { setView('board'); setCityOpen(false) }}>
            Open<span className="play">Play</span>
          </button>
          <div className="tabs">
            <button className={`tab ${view === 'board' ? 'active' : ''}`} onClick={() => setView('board')}>🗺 Board</button>
            <button className={`tab ${view === 'about' ? 'active' : ''}`} onClick={() => setView('about')}>✦ About</button>
          </div>

          {view === 'board' && (
            <>
              <div className="filterbar">
                <button className={`chip ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>✦ All</button>
                {sports.map(s => (
                  <button key={s.key} className={`chip ${filter === s.key ? 'active' : ''}`} onClick={() => setFilter(s.key)}>
                    {s.emoji} {s.label}
                  </button>
                ))}
              </div>
              <div className="citywrap">
                <button className="chip city-btn" onClick={() => setCityOpen(o => !o)}>
                  📍 {activeCity?.label || 'Pick a city'} ▾
                </button>
                {cityOpen && (
                  <CityMenu cities={cities} active={city} onPick={pickCity} onClose={() => setCityOpen(false)} />
                )}
              </div>
            </>
          )}

          <div className="nav-spacer" />
          <input className="input nav-name" placeholder="your name" value={handle} onChange={e => saveHandle(e.target.value)} />
          <button className="btn btn-primary" onClick={() => setShowCreate(true)}>+ Create game</button>
        </div>
      </nav>

      {view === 'about' ? (
        <About stats={stats} cities={cities} onStart={() => { setView('board'); setShowCreate(true) }} onBrowse={() => setView('board')} />
      ) : (
        <>
          {/* Hero */}
          <section className="hero">
            {glyphs.map((g, i) => (
              <span key={i} className="glyph" style={{
                left: `${[6, 78, 30, 90, 52, 16][i]}%`, top: `${[20, 12, 70, 58, 88, 50][i]}%`,
                animationDelay: `${i * 2.3}s`, fontSize: `${[70, 54, 90, 60, 48, 76][i]}px`,
              }}>{g}</span>
            ))}
            <div className="wrap" style={{ position: 'relative' }}>
              <div className="hero-eyebrow">a city playing in the open</div>
              <h1>Games happening in<br /><span className="play">{activeCity?.label || 'your city'}</span>.</h1>
              <p className="hero-sub">
                Soccer, hockey, basketball — <b>pick your city and jump in.</b> Spin up a game,
                invite your crew, run it every week. <b>Free to play.</b>
              </p>
              <div className="ticker">
                <div className="stat">
                  <span className="num"><span className="dot-live" />{stats?.upcoming ?? '—'}</span>
                  <span className="lab">games on the board</span>
                </div>
                <div className="stat">
                  <span className="num">{stats?.players_going ?? '—'}</span>
                  <span className="lab">players out</span>
                </div>
                <div className="stat">
                  <span className="num">{sportsLive || '—'}</span>
                  <span className="lab">sports live</span>
                </div>
              </div>
            </div>
          </section>

          <div className="wrap" style={{ paddingBottom: 70 }}>
            <div style={{ display: 'flex', gap: 9, alignItems: 'center', flexWrap: 'wrap', marginBottom: 18 }}>
              <button className="chip active" onClick={() => setCityOpen(true)}>
                📍 {activeCity?.label || 'Pick a city'} ▾
              </button>
              <span className="muted" style={{ fontSize: 13 }}>{activeCity?.venues ?? 0} venue{activeCity?.venues !== 1 ? 's' : ''}</span>
              {filter !== 'all' && (
                <button className="chip" onClick={() => setFilter('all')}>
                  {sportMeta[filter]?.emoji} {sportMeta[filter]?.label} ✕
                </button>
              )}
            </div>

            <div className="op-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.05fr)', gap: 22, alignItems: 'start' }}>
              {/* Map */}
              <div className="card map-card map-frame" style={{ height: 560, position: 'sticky', top: 88 }}>
                <div className="map-label">↳ {activeCity?.label || 'the city'}, right now</div>
                <div className="map-overlay" />
                <div className="map-glow" />
                <div id="map" ref={mapElRef} />
              </div>

              {/* Feed */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14 }}>
                  <h2 className="section-title">What&rsquo;s on</h2>
                  <span className="muted" style={{ fontSize: 13 }}>{games.length} game{games.length !== 1 ? 's' : ''} upcoming</span>
                </div>
                {loading && <div className="muted">Reading the city…</div>}
                {!loading && games.length === 0 && (
                  <div className="card empty">
                    <div className="big">🌆</div>
                    <div className="font-display" style={{ fontSize: 22, marginBottom: 6 }}>It&rsquo;s quiet in {activeCity?.label || 'this city'}.</div>
                    <div className="muted" style={{ fontSize: 15, marginBottom: 20 }}>Be the one who starts something — or switch city.</div>
                    <div style={{ display: 'flex', gap: 9, justifyContent: 'center', flexWrap: 'wrap' }}>
                      <button className="btn btn-primary" onClick={() => setShowCreate(true)}>+ Start the first game</button>
                      <button className="btn" onClick={() => setCityOpen(true)}>📍 Switch city</button>
                    </div>
                  </div>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
                  {games.map((g, i) => (
                    <GameCard key={`${g.game_id}@${g.occ}`} g={g} sport={sportMeta[g.sport]} idx={i}
                              onOpen={() => setDetail({ gameId: g.game_id, occ: g.occ })} />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {showCreate && (
        <CreateModal sports={sports} venues={venues} handle={handle} wallet={wallet}
          onClose={() => setShowCreate(false)}
          onCreated={(gid, key) => { saveKey(gid, key); setKeys(loadKeys()); setShowCreate(false); refresh() }} />
      )}

      {detail && (
        <DetailModal gameId={detail.gameId} occ={detail.occ} handle={handle} wallet={wallet}
          adminKey={keys[detail.gameId]} sportMeta={sportMeta}
          onClose={() => setDetail(null)} onChanged={refresh} />
      )}
    </main>
  )
}

// ── City dropdown (search + pick) ─────────────────────────────────
function CityMenu({ cities, active, onPick, onClose }: {
  cities: City[]; active: string; onPick: (key: string) => void; onClose: () => void
}) {
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const ql = q.trim().toLowerCase()
  const matches = cities.filter(c =>
    !ql || c.label.toLowerCase().includes(ql) || (c.country || '').toLowerCase().includes(ql) || c.key.includes(ql)
  )
  useEffect(() => {
    function onDoc(e: MouseEvent) { if (ref.current && !ref.current.contains(e.target as Node)) onClose() }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [onClose])
  return (
    <div className="citymenu" ref={ref}>
      <input className="input" autoFocus placeholder="Search your city…" value={q}
        onChange={e => setQ(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && matches.length) onPick(matches[0].key); if (e.key === 'Escape') onClose() }} />
      <div className="citymenu-list">
        {matches.length === 0 && (
          <div className="muted" style={{ padding: '12px 4px', fontSize: 13 }}>
            No match. You can still create a game anywhere — it’ll land on the closest board.
          </div>
        )}
        {matches.map(c => (
          <button key={c.key} className={`suggest ${c.key === active ? 'active' : ''}`} onClick={() => onPick(c.key)}>
            <span className="suggest-pin">📍</span>
            <span style={{ flex: 1, textAlign: 'left' }}>
              <span style={{ fontWeight: 700 }}>{c.label}</span>
              {c.country && <span className="muted" style={{ marginLeft: 8, fontSize: 13 }}>{c.country}</span>}
            </span>
            <span className="muted" style={{ fontSize: 12 }}>{c.venues} venue{c.venues !== 1 ? 's' : ''}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

// ── About / mission ───────────────────────────────────────────────
function About({ stats, cities, onStart, onBrowse }: {
  stats: Stats | null; cities: City[]; onStart: () => void; onBrowse: () => void
}) {
  return (
    <div className="wrap about" style={{ paddingBottom: 90 }}>
      <div className="about-hero">
        <div className="hero-eyebrow">our mission</div>
        <h1 className="font-display about-title">
          Get the city<br /><span className="play">off the group chat</span><br />and onto the field.
        </h1>
        <p className="hero-sub" style={{ marginTop: 18 }}>
          Pickup sport is the easiest, cheapest, most human thing a city has — and it’s buried
          under a dozen WhatsApp groups, dead Facebook events, and “you up for ball later?” texts.
          <b> OpenPlay is one open board for every pickup game in your city.</b> See what’s on,
          tap in, and play today.
        </p>
        <div style={{ display: 'flex', gap: 10, marginTop: 26, flexWrap: 'wrap' }}>
          <button className="btn btn-primary" style={{ padding: '12px 22px' }} onClick={onStart}>+ Start a game</button>
          <button className="btn" style={{ padding: '12px 22px' }} onClick={onBrowse}>🗺 Browse the board</button>
        </div>
      </div>

      <div className="about-grid">
        <div className="card about-card">
          <div className="about-ic">🌍</div>
          <h3>Open by default</h3>
          <p>Every game is on one public map. No app to download to find a run, no gatekeeper, no “DM for the link.” Pick your city and the games are right there.</p>
        </div>
        <div className="card about-card">
          <div className="about-ic">🆓</div>
          <h3>Free to play</h3>
          <p>Pickup should cost nothing. Games are free by default. If an organizer rents a rink or a turf, they can collect a small fee in USDC or USDT on Base — split fairly, no middleman.</p>
        </div>
        <div className="card about-card">
          <div className="about-ic">🔁</div>
          <h3>Built for regulars</h3>
          <p>Run the Sunday morning game every week with one tap. Keep a standing roster, invite your crew, manage the waitlist, and never re-pin the group again.</p>
        </div>
        <div className="card about-card">
          <div className="about-ic">💬</div>
          <h3>Every game has a chat</h3>
          <p>The crew that RSVPs gets its own room to sort shirts, time, and who’s bringing the ball — and organizers can pin their Telegram or WhatsApp group right on the game.</p>
        </div>
      </div>

      <div className="card about-stats">
        <div className="about-stat"><span className="num">{stats?.upcoming ?? '—'}</span><span className="lab">games on the board</span></div>
        <div className="about-stat"><span className="num">{stats?.players_going ?? '—'}</span><span className="lab">players out this week</span></div>
        <div className="about-stat"><span className="num">{cities.length || '—'}</span><span className="lab">cities</span></div>
        <div className="about-stat"><span className="num">$0</span><span className="lab">to play, always</span></div>
      </div>

      <p className="muted about-foot">
        OpenPlay is a mod on the open protocol — your name and games live in your browser and on an
        open board, not locked in someone’s app. Go play. 🟢
      </p>
    </div>
  )
}

// ── Game card ────────────────────────────────────────────────────
function GameCard({ g, sport, idx, onOpen }: { g: Occurrence; sport?: Sport; idx: number; onOpen: () => void }) {
  const color = SPORT_COLORS[g.sport] || '#9ca3af'
  const full = g.capacity > 0 && g.spots_left === 0
  return (
    <div className="card game-card" style={{ ['--c' as any]: color, animationDelay: `${Math.min(idx, 8) * 55}ms` }} onClick={onOpen}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ display: 'flex', gap: 13, minWidth: 0 }}>
          <div className="sport-orb" style={{ ['--c' as any]: color }}>{sport?.emoji}</div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 800, fontSize: 16.5, letterSpacing: '-.01em' }}>{g.title}</div>
            <div className="muted" style={{ fontSize: 13.5, marginTop: 3 }}>
              {g.venue || g.neighborhood || 'TBD'}{g.neighborhood && g.venue ? ` · ${g.neighborhood}` : ''}
            </div>
            <div style={{ fontSize: 14, marginTop: 4, color: '#d7d3df' }}>{fmtWhen(g.occ_ts)}</div>
          </div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div className="font-grotesk" style={{ fontWeight: 700, fontSize: 19 }}>
            {g.going}<span className="muted" style={{ fontWeight: 500, fontSize: 14 }}>{g.capacity ? `/${g.capacity}` : ''}</span>
          </div>
          <div className="muted" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.08em' }}>{g.waitlist > 0 ? `+${g.waitlist} wait` : 'in'}</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 7, marginTop: 13, flexWrap: 'wrap' }}>
        {g.free ? <span className="badge badge-free">● FREE</span>
                : <span className="badge badge-paid">◇ {g.cost?.amount} {g.cost?.currency} · Base</span>}
        {g.recurring && <span className="badge badge-recur">↻ {g.recurrence}</span>}
        {full && <span className="badge badge-full">FULL · waitlist</span>}
        <span className="badge">by {g.admin}</span>
      </div>
    </div>
  )
}

// ── Create modal ─────────────────────────────────────────────────
function CreateModal({ sports, venues, handle, wallet, onClose, onCreated }: {
  sports: Sport[]; venues: Venue[]; handle: string; wallet: string
  onClose: () => void; onCreated: (gid: string, key: string) => void
}) {
  const [sport, setSport] = useState('soccer')
  const [title, setTitle] = useState('')
  const [venueIdx, setVenueIdx] = useState(-1)
  const [venue, setVenue] = useState('')
  const [neighborhood, setNeighborhood] = useState('')
  const [lat, setLat] = useState<string>('')
  const [lng, setLng] = useState<string>('')
  const [when, setWhen] = useState(toLocalInput(Math.floor(Date.now() / 1000) + 86400))
  const [duration, setDuration] = useState(90)
  const [capacity, setCapacity] = useState(10)
  const [freq, setFreq] = useState<'once' | 'daily' | 'weekly'>('once')
  const [days, setDays] = useState<number[]>([])
  const [admin, setAdmin] = useState(handle)
  const [paid, setPaid] = useState(false)
  const [amount, setAmount] = useState('5')
  const [currency, setCurrency] = useState('USDC')
  const [receiver, setReceiver] = useState(wallet)
  const [telegram, setTelegram] = useState('')
  const [whatsapp, setWhatsapp] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { setAdmin(handle) }, [handle])
  const filteredVenues = venues.filter(v => v.sports.includes(sport))

  function pickVenue(i: number) {
    setVenueIdx(i)
    if (i < 0) return
    const v = filteredVenues[i]
    setVenue(v.name); setNeighborhood(v.neighborhood); setLat(String(v.lat)); setLng(String(v.lng))
  }

  async function submit() {
    if (!title.trim()) return toast.error('Give it a name')
    if (!admin.trim()) return toast.error('Enter your name (the organizer)')
    if (paid && !receiver.trim()) return toast.error('Paid games need a Base wallet to receive funds')
    const starts_at = Math.floor(new Date(when).getTime() / 1000)
    const links: GameLink[] = []
    if (telegram.trim()) links.push({ label: 'Telegram', url: telegram.trim() })
    if (whatsapp.trim()) links.push({ label: 'WhatsApp', url: whatsapp.trim() })
    const body: any = {
      sport, title, starts_at, admin, venue, neighborhood, duration_min: duration, capacity, notes,
      lat: lat ? parseFloat(lat) : undefined, lng: lng ? parseFloat(lng) : undefined,
    }
    if (links.length) body.links = links
    if (freq !== 'once') body.recurrence = { freq, ...(freq === 'weekly' && days.length ? { days } : {}) }
    if (paid) body.cost = { amount: parseFloat(amount), currency, receiver }
    setBusy(true)
    try {
      const res = await api('games', { method: 'POST', body })
      toast.success('Game created — share the board!')
      onCreated(res.game_id, res.admin_key)
    } catch (e: any) { toast.error(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
          <h2 className="font-display" style={{ fontSize: 24 }}>New game</h2>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>

        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 16 }}>
          {sports.map(s => (
            <button key={s.key} className={`chip ${sport === s.key ? 'active' : ''}`}
                    onClick={() => { setSport(s.key); setVenueIdx(-1) }}>{s.emoji} {s.label}</button>
          ))}
        </div>

        <Field label="Game name"><input className="input" value={title} onChange={e => setTitle(e.target.value)} placeholder="Sunday morning run" /></Field>

        <Field label="Venue">
          <select className="select" value={venueIdx} onChange={e => pickVenue(parseInt(e.target.value))}>
            <option value={-1}>Custom / type your own</option>
            {filteredVenues.map((v, i) => <option key={v.name} value={i}>{v.name} — {v.neighborhood}</option>)}
          </select>
        </Field>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Field label="Place name"><input className="input" value={venue} onChange={e => setVenue(e.target.value)} placeholder="e.g. Pier 40" /></Field>
          <Field label="Neighborhood"><input className="input" value={neighborhood} onChange={e => setNeighborhood(e.target.value)} placeholder="e.g. West Village" /></Field>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Field label="Latitude"><input className="input" value={lat} onChange={e => setLat(e.target.value)} placeholder="40.73" /></Field>
          <Field label="Longitude"><input className="input" value={lng} onChange={e => setLng(e.target.value)} placeholder="-74.00" /></Field>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 10 }}>
          <Field label="When"><input className="input" type="datetime-local" value={when} onChange={e => setWhen(e.target.value)} /></Field>
          <Field label="Minutes"><input className="input" type="number" value={duration} onChange={e => setDuration(parseInt(e.target.value) || 0)} /></Field>
          <Field label="Max players"><input className="input" type="number" value={capacity} onChange={e => setCapacity(parseInt(e.target.value) || 0)} /></Field>
        </div>

        <Field label="Repeat">
          <div style={{ display: 'flex', gap: 7 }}>
            {(['once', 'daily', 'weekly'] as const).map(f => (
              <button key={f} className={`chip ${freq === f ? 'active' : ''}`} onClick={() => setFreq(f)}>
                {f === 'once' ? 'One-off' : f === 'daily' ? 'Daily' : 'Weekly'}
              </button>
            ))}
          </div>
        </Field>
        {freq === 'weekly' && (
          <div style={{ display: 'flex', gap: 6, marginBottom: 13, flexWrap: 'wrap' }}>
            {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => (
              <button key={d} className={`chip ${days.includes(i) ? 'active' : ''}`}
                onClick={() => setDays(days.includes(i) ? days.filter(x => x !== i) : [...days, i])}>{d}</button>
            ))}
          </div>
        )}

        <Field label="Organizer (you)"><input className="input" value={admin} onChange={e => setAdmin(e.target.value)} placeholder="your name" /></Field>

        <div className="divider" />
        <div className="label">Group chat links <span style={{ textTransform: 'none', letterSpacing: 0, color: 'var(--muted)' }}>— optional, pinned to the game</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 4 }}>
          <Field label="Telegram"><input className="input" value={telegram} onChange={e => setTelegram(e.target.value)} placeholder="https://t.me/…" /></Field>
          <Field label="WhatsApp"><input className="input" value={whatsapp} onChange={e => setWhatsapp(e.target.value)} placeholder="https://chat.whatsapp.com/…" /></Field>
        </div>

        <div className="divider" />
        <label style={{ display: 'flex', alignItems: 'center', gap: 11, cursor: 'pointer', marginBottom: paid ? 13 : 0 }}>
          <input type="checkbox" checked={paid} onChange={e => setPaid(e.target.checked)} style={{ width: 17, height: 17, accentColor: '#ff5e9c' }} />
          <span style={{ fontWeight: 700 }}>Charge a fee</span>
          <span className="muted" style={{ fontSize: 13 }}>{paid ? 'players pay to join' : 'free — the default'}</span>
        </label>
        {paid && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <Field label="Amount"><input className="input" value={amount} onChange={e => setAmount(e.target.value)} /></Field>
            <Field label="Token">
              <select className="select" value={currency} onChange={e => setCurrency(e.target.value)}>
                <option>USDC</option><option>USDT</option>
              </select>
            </Field>
            <div style={{ gridColumn: '1 / -1' }}>
              <Field label="Receive at (your Base wallet)"><input className="input" value={receiver} onChange={e => setReceiver(e.target.value)} placeholder="0x…" /></Field>
            </div>
          </div>
        )}

        <Field label="Notes (optional)"><input className="input" value={notes} onChange={e => setNotes(e.target.value)} placeholder="Bring a light & dark shirt" /></Field>

        <button className="btn btn-primary" style={{ width: '100%', marginTop: 8, padding: '13px' }} disabled={busy} onClick={submit}>
          {busy ? 'Creating…' : 'Create game →'}
        </button>
      </div>
    </div>
  )
}

// ── Detail modal ─────────────────────────────────────────────────
function DetailModal({ gameId, occ, handle, wallet, adminKey, sportMeta, onClose, onChanged }: {
  gameId: string; occ: string; handle: string; wallet: string; adminKey?: string
  sportMeta: Record<string, Sport>; onClose: () => void; onChanged: () => void
}) {
  const [g, setG] = useState<GameDetail | null>(null)
  const [curOcc, setCurOcc] = useState(occ)
  const [busy, setBusy] = useState(false)
  const [inviteName, setInviteName] = useState('')
  const [payInfo, setPayInfo] = useState<any>(null)
  const [payRef, setPayRef] = useState('')
  const [newLink, setNewLink] = useState('')

  const load = useCallback(async () => {
    try { setG(await api(`game/${gameId}?occ=${curOcc}`)) }
    catch (e: any) { toast.error(e.message) }
  }, [gameId, curOcc])
  useEffect(() => { load() }, [load])

  const isAdmin = !!adminKey
  const me = handle.trim()
  const amIn = !!g?.players.find(p => p.handle === me)
  const amInvited = !!g?.invited.includes(me)
  const isOrganizer = !!me && g?.admin === me
  const canChat = !!me && (amIn || isOrganizer || isAdmin)

  async function act(path: string, body: any, ok?: string) {
    if (!me) return toast.error('Set your name first (top right)')
    setBusy(true)
    try {
      const res = await api(`game/${gameId}/${path}`, { method: 'POST', body: { ...body, occ: curOcc } })
      if (res.payment_required) { setPayInfo(res.payment); toast.info('This game charges a fee — see payment box') }
      else {
        if (res.rsvp === 'waitlisted') toast.info('Game is full — you’re on the waitlist')
        else if (ok) toast.success(ok)
        setPayInfo(null); setPayRef(''); await load(); onChanged()
      }
    } catch (e: any) { toast.error(e.message) }
    finally { setBusy(false) }
  }

  async function saveLinks(links: GameLink[]) {
    try {
      await api(`game/${gameId}/edit`, { method: 'POST', body: { admin_key: adminKey, fields: { links } } })
      await load()
    } catch (e: any) { toast.error(e.message) }
  }
  function addLink() {
    const url = newLink.trim()
    if (!/^https?:\/\//i.test(url)) return toast.error('Paste a full https:// link')
    saveLinks([...(g?.links || []), { label: linkLabel(url), url }])
    setNewLink('')
  }

  if (!g) return null
  const sport = sportMeta[g.sport]
  const color = SPORT_COLORS[g.sport] || '#9ca3af'

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 600 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
          <div style={{ display: 'flex', gap: 14, minWidth: 0 }}>
            <div className="sport-orb" style={{ ['--c' as any]: color, width: 52, height: 52, fontSize: 26, borderRadius: 16 }}>{sport?.emoji}</div>
            <div>
              <h2 className="font-display" style={{ fontSize: 25, lineHeight: 1.05 }}>{g.title}</h2>
              <div className="muted" style={{ fontSize: 14, marginTop: 5 }}>{g.venue || g.neighborhood}{g.neighborhood && g.venue ? ` · ${g.neighborhood}` : ''}</div>
            </div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>

        {g.upcoming.length > 1 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '14px 0' }}>
            {g.upcoming.map(o => (
              <button key={o} className={`chip ${curOcc === o ? 'active' : ''}`} onClick={() => setCurOcc(o)}>
                {new Date(o + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}
              </button>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', margin: '14px 0' }}>
          <span className="badge">🕒 {fmtWhen(g.occ_ts)}</span>
          <span className="badge">{g.duration_min} min</span>
          {g.free ? <span className="badge badge-free">● FREE</span>
                  : <span className="badge badge-paid">◇ {g.cost?.amount} {g.cost?.currency} · Base</span>}
          {g.recurring && <span className="badge badge-recur">↻ {g.recurrence}</span>}
        </div>
        {g.notes && <div className="font-serif" style={{ fontStyle: 'italic', fontSize: 15.5, color: '#d7d3df', marginBottom: 14 }}>“{g.notes}”</div>}

        {g.links?.length > 0 && (
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 14 }}>
            {g.links.map((l, i) => (
              <a key={i} className="linkchip" href={l.url} target="_blank" rel="noreferrer">
                💬 {l.label} ↗
                {isAdmin && (
                  <span className="linkchip-x" onClick={e => { e.preventDefault(); saveLinks(g.links.filter((_, j) => j !== i)) }}>✕</span>
                )}
              </a>
            ))}
          </div>
        )}

        <div style={{ fontWeight: 800, marginBottom: 8 }}>
          Going <span className="font-grotesk muted" style={{ fontWeight: 600 }}>· {g.going}{g.capacity ? `/${g.capacity}` : ''}</span>
        </div>
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 12 }}>
          {g.players.length === 0 && <span className="muted" style={{ fontSize: 14 }}>No one yet — be first.</span>}
          {g.players.map(p => (
            <span key={p.handle} className="badge" style={{ borderColor: p.handle === me ? color : undefined, color: p.handle === me ? '#fff' : undefined }}>
              {p.handle}{p.paid && p.paid !== true ? ' 💵' : ''}
              {isAdmin && p.handle !== me && (
                <button style={{ background: 'none', border: 'none', color: '#ff9aa6', cursor: 'pointer', padding: '0 0 0 5px', fontSize: 12 }}
                  onClick={() => act('kick', { admin_key: adminKey, handle: p.handle }, `Removed ${p.handle}`)}>✕</button>
              )}
            </span>
          ))}
        </div>
        {g.invited.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <span className="muted" style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.08em' }}>Invited </span>
            {g.invited.map(h => <span key={h} className="badge" style={{ marginRight: 5, opacity: .75 }}>{h}</span>)}
          </div>
        )}

        {payInfo && (
          <div className="card" style={{ marginBottom: 13, borderColor: 'rgba(246,224,86,.4)' }}>
            <div style={{ fontWeight: 700, marginBottom: 7 }}>Pay to join — {payInfo.amount} {payInfo.currency} on {payInfo.chain}</div>
            <div className="muted" style={{ fontSize: 12.5, wordBreak: 'break-all' }}>Send to: {payInfo.receiver}</div>
            <div className="muted" style={{ fontSize: 12.5, wordBreak: 'break-all', marginBottom: 9 }}>Token: {payInfo.asset}</div>
            <input className="input" placeholder="paste your transaction hash" value={payRef} onChange={e => setPayRef(e.target.value)} style={{ marginBottom: 9 }} />
            <button className="btn btn-primary btn-sm" disabled={!payRef || busy}
              onClick={() => act(amInvited ? 'accept' : 'join', { handle: me, wallet, payment: payRef }, 'You’re in!')}>
              Confirm payment & join
            </button>
          </div>
        )}

        <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap', marginTop: 6 }}>
          {!amIn && !payInfo && (
            <button className="btn btn-primary" disabled={busy} style={{ padding: '11px 22px' }}
              onClick={() => act(amInvited ? 'accept' : 'join', { handle: me, wallet }, 'You’re in!')}>
              {g.free ? (amInvited ? 'Accept & join' : 'Join game') : `Join · ${g.cost?.amount} ${g.cost?.currency}`}
            </button>
          )}
          {amIn && <button className="btn btn-danger" disabled={busy} onClick={() => act('leave', { handle: me }, 'You left')}>Leave</button>}
          {amInvited && !amIn && <button className="btn" disabled={busy} onClick={() => act('decline', { handle: me }, 'Declined')}>Decline invite</button>}
        </div>

        {/* ── Game chat ── */}
        <div className="divider" />
        <ChatPanel gameId={gameId} me={me} canChat={canChat} chatCount={g.chat_count} />

        {isAdmin && (
          <>
            <div className="divider" />
            <div style={{ fontWeight: 800, marginBottom: 9, fontSize: 14 }}>⚙ Organizer controls</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="input" placeholder="invite by name" value={inviteName} onChange={e => setInviteName(e.target.value)} />
              <button className="btn btn-sm" disabled={busy || !inviteName}
                onClick={() => { act('invite', { admin_key: adminKey, handle: inviteName.trim() }, `Invited ${inviteName}`); setInviteName('') }}>Invite</button>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <input className="input" placeholder="add group link — https://t.me/… or WhatsApp" value={newLink}
                onChange={e => setNewLink(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addLink() }} />
              <button className="btn btn-sm" disabled={!newLink.trim()} onClick={addLink}>Pin link</button>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
              {g.recurring && (
                <button className="btn btn-sm btn-danger" disabled={busy}
                  onClick={() => act('cancel', { admin_key: adminKey }, 'This date cancelled')}>Cancel this date</button>
              )}
              <button className="btn btn-sm btn-danger" disabled={busy}
                onClick={async () => {
                  if (!confirm('Cancel the entire game/series?')) return
                  try { await api(`game/${gameId}/cancel`, { method: 'POST', body: { admin_key: adminKey } }); toast.success('Game cancelled'); onChanged(); onClose() }
                  catch (e: any) { toast.error(e.message) }
                }}>Cancel game</button>
            </div>
          </>
        )}

        {!isAdmin && g.admin === me && (
          <div className="muted" style={{ fontSize: 12, marginTop: 13 }}>You created this on another device — organizer controls only show where the game was created.</div>
        )}
      </div>
    </div>
  )
}

// ── Per-game chat ─────────────────────────────────────────────────
function ChatPanel({ gameId, me, canChat, chatCount }: {
  gameId: string; me: string; canChat: boolean; chatCount: number
}) {
  const [msgs, setMsgs] = useState<ChatMessage[]>([])
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const sinceRef = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)

  const poll = useCallback(async () => {
    try {
      const res = await api(`game/${gameId}/messages?since=${sinceRef.current}`)
      const incoming: ChatMessage[] = res.messages || []
      if (incoming.length) {
        sinceRef.current = incoming[incoming.length - 1].ts
        setMsgs(prev => {
          const seen = new Set(prev.map(m => m.id))
          return [...prev, ...incoming.filter(m => !seen.has(m.id))]
        })
      }
    } catch { /* quiet — chat is best-effort */ }
  }, [gameId])

  useEffect(() => {
    setMsgs([]); sinceRef.current = 0
    poll()
    const t = setInterval(poll, 4000)
    return () => clearInterval(t)
  }, [poll])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [msgs])

  async function send() {
    const t = text.trim()
    if (!t) return
    setSending(true)
    try {
      await api(`game/${gameId}/messages`, { method: 'POST', body: { handle: me, text: t } })
      setText('')
      await poll()
    } catch (e: any) { toast.error(e.message) }
    finally { setSending(false) }
  }

  return (
    <div>
      <div style={{ fontWeight: 800, marginBottom: 9, fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
        💬 Game chat
        <span className="muted" style={{ fontWeight: 500, fontSize: 12.5 }}>
          {chatCount > 0 ? `${chatCount} message${chatCount !== 1 ? 's' : ''}` : 'the crew’s room'}
        </span>
      </div>
      <div className="chat-scroll" ref={scrollRef}>
        {msgs.length === 0 && <div className="chat-empty">No messages yet — say hi to the crew. 👋</div>}
        {msgs.map(m => {
          const mine = m.handle === me
          return (
            <div key={m.id} className={`msg ${mine ? 'me' : ''}`}>
              {!mine && <div className="msg-who">{m.handle}</div>}
              <div className="bubble">{m.text}</div>
              <div className="msg-time">{fmtChatTime(m.ts)}</div>
            </div>
          )
        })}
      </div>
      {canChat ? (
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <input className="input" placeholder="message the crew…" value={text}
            onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') send() }} />
          <button className="btn btn-primary btn-sm" disabled={sending || !text.trim()} onClick={send}>Send</button>
        </div>
      ) : (
        <div className="muted" style={{ fontSize: 13, marginTop: 10 }}>
          {me ? 'Join the game to chat with the crew.' : 'Set your name (top right) and join to chat with the crew.'}
        </div>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div style={{ marginBottom: 13 }}><label className="label">{label}</label>{children}</div>
}
