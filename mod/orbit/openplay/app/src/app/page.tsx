"use client";

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { toast } from 'react-toastify'
import { api, Occurrence, GameDetail, GameLink, ChatMessage, Sport, Venue, City, Conflict, AgentRequest, ApiError } from '@/lib/api'
import {
  Account, loadAccount, clearAccount, signIn, signInWithKey,
  changePassword, rotateFresh, loadKeys, saveKey, shortAddr,
  loadGuest, clearGuest, useGuestName, randomGuestName,
} from '@/lib/account'
import SkinPicker from './SkinPicker'
import { useTheme, tileUrlFor } from './theme'

// Sport identity is a token, not a hex — every world repaints the six in
// its own palette, and the map pins read the same vars through `--c`.
const SPORT_COLORS: Record<string, string> = {
  soccer: 'var(--s-soccer)', basketball: 'var(--s-basketball)', hockey: 'var(--s-hockey)',
  tennis: 'var(--s-tennis)', volleyball: 'var(--s-volleyball)', football: 'var(--s-football)',
}
const SPORT_FALLBACK = 'var(--coin)'

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

type Stats = { games: number; upcoming: number; players_going: number; by_sport: Record<string, number>; agent_requests_pending?: number }

export default function Page() {
  const [sports, setSports] = useState<Sport[]>([])
  const [venues, setVenues] = useState<Venue[]>([])
  const [cities, setCities] = useState<City[]>([])
  const [city, setCity] = useState<string>('')
  const [view, setView] = useState<'board' | 'mine' | 'about'>('board')
  const [cityOpen, setCityOpen] = useState(false)
  const [myNonce, setMyNonce] = useState(0)
  const [games, setGames] = useState<Occurrence[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [filter, setFilter] = useState<string>('all')
  const [account, setAccount] = useState<Account | null>(null)
  const [guest, setGuest] = useState('')
  const [showAuth, setShowAuth] = useState(false)
  const [showAccount, setShowAccount] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [detail, setDetail] = useState<{ gameId: string; occ: string } | null>(null)
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [mapReady, setMapReady] = useState(false)
  // Owner-side: agent proposals waiting for a yes. The secret stays on this device.
  const [showAgents, setShowAgents] = useState(false)
  const [adminSecret, setAdminSecret] = useState('')

  // Identity comes in two grades. A GUEST is just a name kept on this
  // device — enough to create a game, join one, and chat, because the
  // organizer key the server hands back is what really proves you run a
  // game. Signing in claims that name with a key so nobody else can wear
  // it. Both grades hand the rest of the app the same `handle`.
  const handle = account?.name || guest
  const wallet = account?.address || ''
  const signedIn = !!account
  const hasName = !!handle
  // Anyone can start a game — the modal collects a name if you have none.
  const openCreate = useCallback(() => setShowCreate(true), [])

  const mapRef = useRef<any>(null)
  const layerRef = useRef<any>(null)
  const tileRef = useRef<any>(null)
  const mapElRef = useRef<HTMLDivElement>(null)
  const { theme } = useTheme()

  const sportMeta = useMemo(() => Object.fromEntries(sports.map(s => [s.key, s])), [sports])
  const activeCity = useMemo(() => cities.find(c => c.key === city), [cities, city])

  useEffect(() => {
    setAccount(loadAccount())
    setGuest(loadGuest())
    setKeys(loadKeys())
    try { setAdminSecret(localStorage.getItem('openplay_admin_secret') || '') } catch {}
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
      mapRef.current = map
      layerRef.current = L.layerGroup().addTo(map)
      setMapReady(true)
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // The basemap belongs to the world: a night level isn't lit by a white
  // city. Swapping worlds swaps the tiles under the same pins.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    let cancelled = false
    ;(async () => {
      const L = (await import('leaflet')).default
      if (cancelled || !mapRef.current) return
      if (tileRef.current) mapRef.current.removeLayer(tileRef.current)
      tileRef.current = L.tileLayer(tileUrlFor(theme), { maxZoom: 19 })
      tileRef.current.addTo(mapRef.current)
      tileRef.current.bringToBack()
    })()
    return () => { cancelled = true }
  }, [theme, mapReady])

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
      const color = SPORT_COLORS[g.sport] || SPORT_FALLBACK
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

  const sportsLive = stats ? Object.keys(stats.by_sport).length : 0
  const glyphs = ['⚽', '🏀', '🏒', '🎾', '🏐', '🏈']

  return (
    <main>
      {/* HUD — the status bar off the top of the screen */}
      <Hud handle={handle} signedIn={signedIn} coins={stats?.upcoming ?? 0}
           players={stats?.players_going ?? 0} world={activeCity?.label || '—'} />

      {/* Nav — filters, world select & create all live up here */}
      <nav className="nav">
        <div className="wrap navbar">
          <button className="brand brand-btn" onClick={() => { setView('board'); setCityOpen(false) }}>
            OPEN<span className="play">PLAY</span>
          </button>
          <div className="tabs">
            <button className={`tab ${view === 'board' ? 'active' : ''}`} onClick={() => setView('board')}>BOARD</button>
            <button className={`tab ${view === 'mine' ? 'active' : ''}`} onClick={() => setView('mine')}>MY GAMES</button>
            <button className={`tab ${view === 'about' ? 'active' : ''}`} onClick={() => setView('about')}>ABOUT</button>
          </div>

          <div className="nav-spacer" />
          {(!!stats?.agent_requests_pending || !!adminSecret) && (
            <button className="chip" onClick={() => setShowAgents(true)}
                    title="Games an agent wants to put on the board">
              🤖 {stats?.agent_requests_pending ? `${stats.agent_requests_pending} waiting` : 'Agents'}
            </button>
          )}
          <SkinPicker />
          {signedIn ? (
            <button className="who" onClick={() => setShowAccount(true)} title="Your account">
              <span className="who-dot" />
              <span className="who-name">{handle}</span>
              <span className="who-addr">{shortAddr(wallet)}</span>
            </button>
          ) : hasName ? (
            // A guest is a real player here — the button offers the upgrade
            // rather than pretending they're signed out.
            <button className="who who-guest" onClick={() => setShowAuth(true)} title="Claim this name with a password">
              <span className="who-dot" />
              <span className="who-name">{handle}</span>
              <span className="who-tag">GUEST</span>
            </button>
          ) : (
            <button className="btn" onClick={() => setShowAuth(true)}>SIGN IN</button>
          )}
          <button className="btn btn-primary btn-q" onClick={openCreate}>NEW GAME</button>

          {/* second line: the sport filter and the city, which never fit
              beside the identity block on anything narrower than a desk */}
          {view === 'board' && (
            <div className="navrow">
              <div className="filterbar">
                <button className={`chip ${filter === 'all' ? 'active' : ''}`} onClick={() => setFilter('all')}>★ ALL</button>
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
            </div>
          )}
        </div>
      </nav>

      {view === 'about' ? (
        <About stats={stats} cities={cities} onStart={() => { setView('board'); openCreate() }} onBrowse={() => setView('board')} />
      ) : view === 'mine' ? (
        <MyGames handle={handle} signedIn={hasName} adminKeys={Object.keys(keys)} sportMeta={sportMeta} nonce={myNonce}
          onOpen={(gameId, occ) => setDetail({ gameId, occ })}
          onCreate={openCreate} onSignIn={() => setShowAuth(true)} onBrowse={() => setView('board')} />
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
              <div className="hero-eyebrow">★ a city playing in the open</div>
              <h1>GAMES ON IN<br /><span className="play">{(activeCity?.label || 'YOUR CITY').toUpperCase()}</span></h1>
              <p className="hero-sub">
                Soccer, hockey, basketball — <b>pick your city and jump in.</b> Anyone can start a
                game: type a name, hit the block, you&rsquo;re the organizer. <b>No sign-up. Free to play.</b>
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
          {/* the brick floor the hero stands on */}
          <div className="ground" />

          <div className="wrap" style={{ paddingTop: 26, paddingBottom: 70 }}>
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
                    <div className="font-display" style={{ fontSize: 15, marginBottom: 10 }}>IT&rsquo;S QUIET IN {(activeCity?.label || 'THIS CITY').toUpperCase()}</div>
                    <div className="muted" style={{ fontSize: 15, marginBottom: 20 }}>Be the one who starts something — or switch city.</div>
                    <div style={{ display: 'flex', gap: 9, justifyContent: 'center', flexWrap: 'wrap' }}>
                      <button className="btn btn-primary" onClick={openCreate}>+ Start the first game</button>
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
        <CreateModal sports={sports} venues={venues} handle={handle} signedIn={signedIn} wallet={wallet}
          onClose={() => setShowCreate(false)}
          onGuest={(name) => setGuest(name)}
          onCreated={(gid, key) => { setKeys(saveKey(gid, key)); setShowCreate(false); refresh(); setMyNonce(n => n + 1) }} />
      )}

      {detail && (
        <DetailModal gameId={detail.gameId} occ={detail.occ} handle={handle} wallet={wallet}
          adminKey={keys[detail.gameId]} sportMeta={sportMeta} onRequireAuth={() => setShowAuth(true)}
          onClose={() => setDetail(null)} onChanged={() => { refresh(); setMyNonce(n => n + 1) }} />
      )}

      {showAuth && (
        <AuthModal
          startGuest={!hasName}
          onClose={() => setShowAuth(false)}
          onGuest={(name) => { setGuest(name); setShowAuth(false); toast.success(`You're on as ${name}. Go play.`) }}
          onDone={(a, created) => {
            setAccount(a); setGuest(''); clearGuest(); setShowAuth(false)
            toast.success(created ? `Welcome, ${a.name} — your name is yours.` : `Signed in as ${a.name}.`)
          }} />
      )}

      {showAccount && account && (
        <AccountPanel account={account}
          onClose={() => setShowAccount(false)}
          onUpdate={(a) => setAccount(a)}
          onSignOut={() => { clearAccount(); setAccount(null); setShowAccount(false); toast.info('Signed out on this device. Your name & key are safe — sign back in any time.') }} />
      )}
    </main>
  )
}

// ── HUD — the status bar off the top of the screen ────────────────
// Coins are games on the board, WORLD is the city you're looking at, and
// TIME is the real clock, because a pickup board is about what's on now.
function Hud({ handle, signedIn, coins, players, world }: {
  handle: string; signedIn: boolean; coins: number; players: number; world: string
}) {
  const [clock, setClock] = useState('')
  useEffect(() => {
    const tick = () => setClock(new Date().toLocaleTimeString(undefined, { hour12: false }))
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [])
  const pad = (n: number) => String(Math.min(n, 999999)).padStart(6, '0')
  return (
    <div className="hud">
      <div className="hud-row">
        <div className="hud-item">
          <span className="hud-lab">{signedIn ? 'PLAYER' : handle ? 'GUEST' : 'PRESS START'}</span>
          <span className="hud-val">{(handle || 'ANYONE').toUpperCase().slice(0, 14)}</span>
        </div>
        <div className="hud-item">
          <span className="hud-lab">GAMES</span>
          <span className="hud-val hud-coin">🪙×{pad(coins)}</span>
        </div>
        <div className="hud-item">
          <span className="hud-lab">WORLD</span>
          <span className="hud-val">{world.toUpperCase().slice(0, 16)}</span>
        </div>
        <div className="hud-item">
          <span className="hud-lab">PLAYERS</span>
          <span className="hud-val">{pad(players)}</span>
        </div>
        <div className="hud-spacer" />
        <div className="hud-item">
          <span className="hud-lab">TIME</span>
          <span className="hud-val hud-blink">{clock || '--:--:--'}</span>
        </div>
      </div>
    </div>
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
        <div className="hero-eyebrow">★ our mission</div>
        <h1 className="font-display about-title">
          GET THE CITY<br /><span className="play">OFF THE GROUP CHAT</span><br />AND ONTO THE FIELD
        </h1>
        <p className="hero-sub" style={{ marginTop: 18 }}>
          Pickup sport is the easiest, cheapest, most human thing a city has — and it’s buried
          under a dozen WhatsApp groups, dead Facebook events, and “you up for ball later?” texts.
          <b> OpenPlay is one open board for every pickup game in your city.</b> See what’s on,
          tap in, and play today.
        </p>
        <div style={{ display: 'flex', gap: 10, marginTop: 26, flexWrap: 'wrap' }}>
          <button className="btn btn-primary btn-q" style={{ padding: '14px 20px' }} onClick={onStart}>START A GAME</button>
          <button className="btn" style={{ padding: '14px 20px' }} onClick={onBrowse}>BROWSE THE BOARD</button>
        </div>
      </div>

      <div className="about-grid">
        <div className="card about-card">
          <div className="about-ic">🌍</div>
          <h3>ANYONE CAN START ONE</h3>
          <p>No sign-up, no approval, no gatekeeper. Type a name, hit the block, and your game is on the city&rsquo;s map — you keep the organizer key that runs it.</p>
        </div>
        <div className="card about-card">
          <div className="about-ic">🆓</div>
          <h3>FREE TO PLAY</h3>
          <p>Pickup should cost nothing. Games are free by default. If an organizer rents a rink or a turf, they can collect a small fee in USDC or USDT on Base — split fairly, no middleman.</p>
        </div>
        <div className="card about-card">
          <div className="about-ic">🔁</div>
          <h3>BUILT FOR REGULARS</h3>
          <p>Run the Sunday morning game every week with one tap. Keep a standing roster, invite your crew, manage the waitlist, and never re-pin the group again.</p>
        </div>
        <div className="card about-card">
          <div className="about-ic">💬</div>
          <h3>EVERY GAME HAS A CHAT</h3>
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
        open board, not locked in someone’s app. Now go play. ★
      </p>
    </div>
  )
}

// ── My games (hosting + playing) ──────────────────────────────────
function MyGames({ handle, signedIn, adminKeys, sportMeta, nonce, onOpen, onCreate, onSignIn, onBrowse }: {
  handle: string; signedIn: boolean; adminKeys: string[]; sportMeta: Record<string, Sport>; nonce: number
  onOpen: (gameId: string, occ: string) => void; onCreate: () => void; onSignIn: () => void; onBrowse: () => void
}) {
  const [hosting, setHosting] = useState<Occurrence[]>([])
  const [playing, setPlaying] = useState<Occurrence[]>([])
  const [loading, setLoading] = useState(true)
  const me = handle.trim()
  const keyStr = adminKeys.join(',')

  useEffect(() => {
    let cancelled = false
    if (!me && adminKeys.length === 0) { setHosting([]); setPlaying([]); setLoading(false); return }
    setLoading(true)
    api('my_games', { method: 'POST', body: { handle: me, game_ids: adminKeys } })
      .then((d: { hosting: Occurrence[]; playing: Occurrence[] }) => {
        if (cancelled) return
        setHosting(d.hosting || []); setPlaying(d.playing || [])
      })
      .catch((e: any) => { if (!cancelled) toast.error(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, keyStr, nonce])

  if (!signedIn && adminKeys.length === 0) {
    return (
      <div className="wrap" style={{ paddingTop: 60, paddingBottom: 90 }}>
        <div className="card empty" style={{ maxWidth: 560, margin: '0 auto' }}>
          <div className="big">🎟</div>
          <div className="font-display" style={{ fontSize: 17, marginBottom: 10 }}>YOUR GAMES LIVE HERE</div>
          <div className="muted" style={{ fontSize: 18, marginBottom: 20 }}>
            Pick a name and you&rsquo;re playing — that&rsquo;s it. Add a password later and the games you
            host and the ones you&rsquo;ve joined follow you to any device.
          </div>
          <div style={{ display: 'flex', gap: 9, justifyContent: 'center', flexWrap: 'wrap' }}>
            <button className="btn btn-primary" onClick={onSignIn}>PICK A NAME</button>
            <button className="btn" onClick={onBrowse}>🗺 Browse the board</button>
          </div>
        </div>
      </div>
    )
  }

  const Section = ({ title, sub, list, emptyText }: { title: string; sub: string; list: Occurrence[]; emptyText: string }) => (
    <div style={{ marginBottom: 34 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 14 }}>
        <h2 className="section-title">{title}</h2>
        <span className="muted" style={{ fontSize: 13 }}>{sub}</span>
      </div>
      {list.length === 0
        ? <div className="muted" style={{ fontSize: 14.5 }}>{emptyText}</div>
        : <div style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
            {list.map((g, i) => (
              <GameCard key={`${g.game_id}@${g.occ}`} g={g} sport={sportMeta[g.sport]} idx={i}
                onOpen={() => onOpen(g.game_id, g.occ)} />
            ))}
          </div>}
    </div>
  )

  return (
    <div className="wrap mygames" style={{ paddingTop: 48, paddingBottom: 90 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 12, marginBottom: 28 }}>
        <div>
          <div className="hero-eyebrow">your corner of the city</div>
          <h1 className="font-display" style={{ fontSize: 'clamp(18px,4vw,34px)' }}>
            {me ? <>Hey <span className="play">{me}</span>.</> : 'My games.'}
          </h1>
        </div>
        <button className="btn btn-primary" style={{ padding: '12px 22px' }} onClick={onCreate}>+ Create a game</button>
      </div>

      {loading ? <div className="muted">Finding your games…</div> : (
        <>
          <Section title="Hosting" sub={`${hosting.length} you organize`} list={hosting}
            emptyText="You’re not hosting anything yet — spin one up and it’ll appear here." />
          <Section title="Playing" sub={`${playing.length} you’ve joined`} list={playing}
            emptyText={me ? 'You haven’t joined a game yet — find one on the board.' : 'Set your name to track games you join.'} />
        </>
      )}
    </div>
  )
}

// ── Game card ────────────────────────────────────────────────────
function GameCard({ g, sport, idx, onOpen }: { g: Occurrence; sport?: Sport; idx: number; onOpen: () => void }) {
  const color = SPORT_COLORS[g.sport] || SPORT_FALLBACK
  const full = g.capacity > 0 && g.spots_left === 0
  return (
    <div className="card game-card" style={{ ['--c' as any]: color, animationDelay: `${Math.min(idx, 8) * 55}ms` }} onClick={onOpen}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ display: 'flex', gap: 13, minWidth: 0 }}>
          <div className="sport-orb" style={{ ['--c' as any]: color }}>{sport?.emoji}</div>
          <div style={{ minWidth: 0 }}>
            <div className="font-grotesk" style={{ fontWeight: 700, fontSize: 14 }}>{g.title}</div>
            <div className="muted" style={{ fontSize: 17, marginTop: 5 }}>
              {g.venue || g.neighborhood || 'TBD'}{g.neighborhood && g.venue ? ` · ${g.neighborhood}` : ''}
            </div>
            <div style={{ fontSize: 18, marginTop: 2 }}>{fmtWhen(g.occ_ts)}</div>
          </div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div className="font-display" style={{ fontSize: 14 }}>
            {g.going}<span className="muted" style={{ fontSize: 11 }}>{g.capacity ? `/${g.capacity}` : ''}</span>
          </div>
          <div className="muted font-grotesk" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '.08em', marginTop: 6 }}>{g.waitlist > 0 ? `+${g.waitlist} wait` : 'in'}</div>
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
// Open to everyone. Signed in, your claimed name is the organizer; signed
// out, you type one (or roll one) and it's kept on this device. Either way
// the server hands back the admin_key that actually runs the game.
function CreateModal({ sports, venues, handle, signedIn, wallet, onClose, onGuest, onCreated }: {
  sports: Sport[]; venues: Venue[]; handle: string; signedIn: boolean; wallet: string
  onClose: () => void; onGuest: (name: string) => void; onCreated: (gid: string, key: string) => void
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
  // A clash with a game already on the board: shown here, not shouted in a toast.
  const [clash, setClash] = useState<{ error: string; conflicts: Conflict[]; warnings: Conflict[] } | null>(null)

  // Guests land on a rolled name rather than an empty field — one less thing
  // between "I want a game on Sunday" and a game on Sunday.
  useEffect(() => { setAdmin(handle || randomGuestName()) }, [handle])
  const filteredVenues = venues.filter(v => v.sports.includes(sport))

  function pickVenue(i: number) {
    setVenueIdx(i)
    if (i < 0) return
    const v = filteredVenues[i]
    setVenue(v.name); setNeighborhood(v.neighborhood); setLat(String(v.lat)); setLng(String(v.lng))
  }

  async function submit(force = false) {
    if (!title.trim()) return toast.error('Give it a name')
    if (!admin.trim()) return toast.error('Enter your name (the organizer)')
    if (paid && !receiver.trim()) return toast.error('Paid games need a Base wallet to receive funds')

    // Guests keep their organizer name on this device — but never one that
    // a signed-in player already owns.
    let organizer = admin.trim()
    if (!signedIn) {
      setBusy(true)
      try { organizer = await useGuestName(organizer); setAdmin(organizer); onGuest(organizer) }
      catch (e: any) { setBusy(false); return toast.error(e.message) }
      setBusy(false)
    }

    const starts_at = Math.floor(new Date(when).getTime() / 1000)
    const links: GameLink[] = []
    if (telegram.trim()) links.push({ label: 'Telegram', url: telegram.trim() })
    if (whatsapp.trim()) links.push({ label: 'WhatsApp', url: whatsapp.trim() })
    const body: any = {
      sport, title, starts_at, admin: organizer, venue, neighborhood, duration_min: duration, capacity, notes,
      lat: lat ? parseFloat(lat) : undefined, lng: lng ? parseFloat(lng) : undefined,
    }
    if (links.length) body.links = links
    if (freq !== 'once') body.recurrence = { freq, ...(freq === 'weekly' && days.length ? { days } : {}) }
    if (paid) body.cost = { amount: parseFloat(amount), currency, receiver }
    if (force) body.force = true
    setBusy(true)
    try {
      const res = await api('games', { method: 'POST', body })
      setClash(null)
      toast.success('Game created — share the board!')
      if (res.warnings?.length) toast.info(res.warnings[0].detail)
      onCreated(res.game_id, res.admin_key)
    } catch (e: any) {
      const err = e as ApiError
      if (err.status === 409 && err.body?.conflicts) {
        setClash({ error: err.message, conflicts: err.body.conflicts, warnings: err.body.warnings || [] })
      } else { toast.error(err.message) }
    }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
          <h2 className="font-display">NEW GAME</h2>
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

        <Field label="Organizer (you)">
          {signedIn ? (
            <div className="input locked" title="You're signed in — this is your name">
              <span className="who-dot" style={{ width: 8, height: 8 }} /> {admin}
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 8 }}>
                <input className="input" value={admin} placeholder="what should the board call you?"
                  onChange={e => setAdmin(e.target.value)} />
                <button className="btn btn-sm" title="Roll a name" onClick={() => setAdmin(randomGuestName())}>🎲</button>
              </div>
              <div className="muted" style={{ fontSize: 16, marginTop: 6 }}>
                No account needed — the name is kept on this device and you get the organizer key.
              </div>
            </>
          )}
        </Field>

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

        {clash && <ClashPanel error={clash.error} conflicts={clash.conflicts} warnings={clash.warnings}
                              busy={busy} onAnyway={() => submit(true)} />}

        <button className="btn btn-primary btn-q" style={{ width: '100%', marginTop: 8, padding: '15px' }} disabled={busy} onClick={() => submit()}>
          {busy ? 'CREATING…' : 'PUT IT ON THE BOARD'}
        </button>
      </div>
    </div>
  )
}

// ── Clashes ──────────────────────────────────────────────────────
// A blocked create isn't a failure, it's information: here's who has the
// field. Warnings are softer — same sport nearby, or you double-booking
// yourself — so they're shown but never stand in the way.
function ClashPanel({ error, conflicts, warnings, busy, onAnyway }: {
  error: string; conflicts: Conflict[]; warnings: Conflict[]; busy?: boolean; onAnyway?: () => void
}) {
  return (
    <div className="card" style={{ marginTop: 14, borderColor: conflicts.length ? 'var(--brick)' : 'var(--coin)' }}>
      <div className="font-display" style={{ fontSize: 13, marginBottom: 8, color: conflicts.length ? 'var(--brick)' : 'var(--coin)' }}>
        {conflicts.length ? '⛔ ALREADY BOOKED' : '⚠ HEADS UP'}
      </div>
      <div style={{ fontSize: 14, marginBottom: conflicts.length + warnings.length ? 10 : 0 }}>{error}</div>
      {[...conflicts, ...warnings].map((c, i) => (
        <div key={`${c.game_id}-${c.kind}-${i}`} style={{ display: 'flex', gap: 8, fontSize: 13, padding: '5px 0', opacity: c.severity === 'warning' ? 0.8 : 1 }}>
          <span>{c.severity === 'blocking' ? '⛔' : '⚠'}</span>
          <span>{c.detail}</span>
        </div>
      ))}
      {onAnyway && conflicts.length > 0 && (
        <button className="btn btn-sm" style={{ marginTop: 10 }} disabled={busy} onClick={onAnyway}>
          Book it anyway
        </button>
      )}
    </div>
  )
}

// ── Agent requests (owner) ───────────────────────────────────────
// An agent may read the board freely, but a game it wants to create lands
// here first — the board only changes when a human says yes.
function AgentPanel({ secret, onSecret, onClose, onChanged }: {
  secret: string; onSecret: (s: string) => void; onClose: () => void; onChanged: () => void
}) {
  const [input, setInput] = useState(secret)
  const [reqs, setReqs] = useState<AgentRequest[] | null>(null)
  const [busy, setBusy] = useState('')

  const load = useCallback(async (s: string) => {
    if (!s) return setReqs(null)
    try {
      const r = await api('agent/requests', { method: 'POST', body: { admin_secret: s, status: 'pending' } })
      setReqs(r.requests); onSecret(s)
    } catch (e: any) { setReqs(null); toast.error(e.message) }
  }, [onSecret])

  useEffect(() => { if (secret) load(secret) }, [secret, load])

  async function decide(rid: string, action: 'authorize' | 'deny', force = false) {
    setBusy(rid)
    try {
      const r = await api(`agent/request/${rid}/${action}`, {
        method: 'POST', body: { admin_secret: input, force, reason: '' },
      })
      if (action === 'authorize') {
        saveKey(r.game.game_id, r.admin_key)   // the organizer key is yours, not the agent's
        toast.success(`Authorized — “${r.game.title}” is on the board`)
        onChanged()
      } else toast.info('Request denied')
      load(input)
    } catch (e: any) { toast.error(e.message) }
    finally { setBusy('') }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <h2 className="font-display">AGENT REQUESTS</h2>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>
        <div className="muted" style={{ fontSize: 14, marginBottom: 16 }}>
          Agents can search the board on their own. Creating a game needs you.
        </div>

        {reqs === null ? (
          <>
            <Field label="Module admin secret">
              <input className="input" type="password" value={input} onChange={e => setInput(e.target.value)}
                     placeholder="~/.openplay/admin.json" onKeyDown={e => e.key === 'Enter' && load(input)} />
            </Field>
            <button className="btn btn-primary btn-q" style={{ width: '100%' }} onClick={() => load(input)}>UNLOCK</button>
          </>
        ) : reqs.length === 0 ? (
          <div className="card empty"><div className="big">🤖</div>
            <div className="muted">No agent is waiting on you.</div></div>
        ) : reqs.map(r => {
          const p = r.proposal
          const blocking = r.conflicts_now?.blocking || []
          return (
            <div key={r.request_id} className="card" style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline' }}>
                <div className="font-display" style={{ fontSize: 14 }}>{p.title}</div>
                <span className="chip">🤖 {r.agent}</span>
              </div>
              <div className="muted" style={{ fontSize: 13, margin: '7px 0' }}>
                {p.sport} · {fmtWhen(p.starts_at)} · {p.venue || p.city || '—'} · {p.capacity || 10} spots · organizer {p.admin}
              </div>
              {r.reason && <div style={{ fontSize: 13, marginBottom: 8 }}>“{r.reason}”</div>}
              {(blocking.length > 0 || (r.conflicts_now?.warnings.length ?? 0) > 0) && (
                <ClashPanel error={blocking.length ? 'Clashes with the board right now' : 'Worth knowing'}
                            conflicts={blocking} warnings={r.conflicts_now?.warnings || []} />
              )}
              <div style={{ display: 'flex', gap: 9, marginTop: 12 }}>
                <button className="btn btn-primary" disabled={!!busy}
                        onClick={() => decide(r.request_id, 'authorize', blocking.length > 0)}>
                  {busy === r.request_id ? '…' : blocking.length ? 'AUTHORIZE ANYWAY' : 'AUTHORIZE'}
                </button>
                <button className="btn" disabled={!!busy} onClick={() => decide(r.request_id, 'deny')}>DENY</button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Detail modal ─────────────────────────────────────────────────
function DetailModal({ gameId, occ, handle, wallet, adminKey, sportMeta, onRequireAuth, onClose, onChanged }: {
  gameId: string; occ: string; handle: string; wallet: string; adminKey?: string
  sportMeta: Record<string, Sport>; onRequireAuth: () => void; onClose: () => void; onChanged: () => void
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
    if (!me) { onRequireAuth(); return }
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
  const color = SPORT_COLORS[g.sport] || SPORT_FALLBACK

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 600 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
          <div style={{ display: 'flex', gap: 14, minWidth: 0 }}>
            <div className="sport-orb" style={{ ['--c' as any]: color, width: 52, height: 52, fontSize: 26, borderRadius: 16 }}>{sport?.emoji}</div>
            <div>
              <h2 className="font-display" style={{ fontSize: 14 }}>{g.title}</h2>
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
              {!me ? 'PICK A NAME TO JOIN' : g.free ? (amInvited ? 'Accept & join' : 'Join game') : `Join · ${g.cost?.amount} ${g.cost?.currency}`}
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
          {me ? 'Join the game to chat with the crew.' : 'Pick a name (top right) and join to chat with the crew.'}
        </div>
      )}
    </div>
  )
}

// ── Sign in / create account ──────────────────────────────────────
// Name + password → a key derived in your browser (scrypt). Same name+password
// regenerates the same key anywhere, so it's a real sign-in. The name is then
// claimed by proving control of the key — one name, one key, no impersonation.
function AuthModal({ startGuest, onClose, onGuest, onDone }: {
  startGuest?: boolean; onClose: () => void
  onGuest: (name: string) => void; onDone: (account: Account, created: boolean) => void
}) {
  // Two doors, and the guest one is open by default: a name on this device
  // is enough to create, join and chat. The password door claims the name
  // with a key so it follows you to other devices and nobody can take it.
  const [mode, setMode] = useState<'guest' | 'account'>(startGuest ? 'guest' : 'account')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [busy, setBusy] = useState(false)
  const [avail, setAvail] = useState<null | { exists: boolean }>(null)
  const [keyMode, setKeyMode] = useState(false)
  const [privKey, setPrivKey] = useState('')

  async function playAsGuest() {
    setBusy(true)
    try { onGuest(await useGuestName(name)) }
    catch (e: any) { toast.error(e.message) }
    finally { setBusy(false) }
  }

  // live availability hint as you type the name
  useEffect(() => {
    const n = name.trim()
    if (n.length < 2) { setAvail(null); return }
    let cancelled = false
    const t = setTimeout(() => {
      api(`account/${encodeURIComponent(n)}`)
        .then((d: any) => { if (!cancelled) setAvail({ exists: !!d.exists }) })
        .catch(() => { if (!cancelled) setAvail(null) })
    }, 350)
    return () => { cancelled = true; clearTimeout(t) }
  }, [name])

  async function submit() {
    setBusy(true)
    try {
      const { account, created } = await signIn(name, password)
      onDone(account, created)
    } catch (e: any) { toast.error(e.message) }
    finally { setBusy(false) }
  }
  async function submitKey() {
    setBusy(true)
    try { onDone(await signInWithKey(name, privKey), false) }
    catch (e: any) { toast.error(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 460 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <h2 className="font-display">WHO&rsquo;S PLAYING?</h2>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>

        <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
          <button className={`chip ${mode === 'guest' ? 'active' : ''}`} onClick={() => setMode('guest')}>🎮 PLAY AS GUEST</button>
          <button className={`chip ${mode === 'account' ? 'active' : ''}`} onClick={() => setMode('account')}>🔑 SIGN IN</button>
        </div>

        {mode === 'guest' ? (
          <>
            <p className="muted" style={{ fontSize: 17, lineHeight: 1.3, marginBottom: 16 }}>
              Just a name. That&rsquo;s the whole thing — create games, join them, chat with the crew.
              It lives on this device; claim it with a password whenever you want it to follow you.
            </p>
            <Field label="Your name">
              <div style={{ display: 'flex', gap: 8 }}>
                <input className="input" value={name} autoFocus placeholder="e.g. alex, court_king, the_keeper"
                  onChange={e => setName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && name.trim()) playAsGuest() }} />
                <button className="btn btn-sm" title="Roll a name" onClick={() => setName(randomGuestName())}>🎲</button>
              </div>
            </Field>
            <button className="btn btn-primary" style={{ width: '100%', marginTop: 4 }} disabled={busy || !name.trim()} onClick={playAsGuest}>
              {busy ? 'WORKING…' : 'START'}
            </button>
            <div className="muted" style={{ fontSize: 16, marginTop: 14, textAlign: 'center' }}>
              Names already claimed by a signed-in player are off limits.
            </div>
          </>
        ) : !keyMode ? (
          <>
            <p className="muted" style={{ fontSize: 17, lineHeight: 1.3, marginBottom: 16 }}>
              A name and a password — your browser turns them into a private key. The same pair signs
              you in on any device. We only ever store your name ↔ public key, never the password.
            </p>
            <Field label="Your name">
              <input className="input" value={name} autoFocus placeholder="e.g. alex, court_king, the_keeper"
                onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') (document.getElementById('op-pw') as HTMLInputElement)?.focus() }} />
            </Field>
            {avail && (
              <div className="muted" style={{ fontSize: 16, marginTop: -7, marginBottom: 12 }}>
                {avail.exists
                  ? <>● <b style={{ color: 'var(--coin)' }}>Taken</b> — if it’s yours, enter your password to sign in.</>
                  : <>✓ <b style={{ color: 'var(--pipe)' }}>Available</b> — you’ll claim this name.</>}
              </div>
            )}
            <Field label="Password">
              <div style={{ position: 'relative' }}>
                <input id="op-pw" className="input" type={showPw ? 'text' : 'password'} value={password}
                  placeholder="generates your key" onChange={e => setPassword(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && name.trim() && password) submit() }} />
                <button type="button" className="pw-eye" onClick={() => setShowPw(s => !s)} tabIndex={-1}>{showPw ? '🙈' : '👁'}</button>
              </div>
            </Field>
            <button className="btn btn-primary" style={{ width: '100%', marginTop: 4 }} disabled={busy || !name.trim() || !password} onClick={submit}>
              {busy ? 'WORKING…' : avail?.exists ? 'SIGN IN' : 'CLAIM THIS NAME'}
            </button>
            <div className="muted" style={{ fontSize: 12, marginTop: 14, textAlign: 'center' }}>
              <button className="linklike" onClick={() => setKeyMode(true)}>Have a private key? Sign in with it →</button>
            </div>
          </>
        ) : (
          <>
            <Field label="Your name"><input className="input" value={name} placeholder="the name this key owns" onChange={e => setName(e.target.value)} /></Field>
            <Field label="Private key"><input className="input" value={privKey} placeholder="0x…" onChange={e => setPrivKey(e.target.value)} /></Field>
            <button className="btn btn-primary" style={{ width: '100%', marginTop: 4 }} disabled={busy || !name.trim() || !privKey.trim()} onClick={submitKey}>
              {busy ? 'WORKING…' : 'SIGN IN WITH KEY'}
            </button>
            <div className="muted" style={{ fontSize: 12, marginTop: 14, textAlign: 'center' }}>
              <button className="linklike" onClick={() => setKeyMode(false)}>← Back to name &amp; password</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Account panel — rotate the key, reveal it, sign out ───────────
function AccountPanel({ account, onClose, onUpdate, onSignOut }: {
  account: Account; onClose: () => void; onUpdate: (a: Account) => void; onSignOut: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [pwOpen, setPwOpen] = useState(false)
  const [newPw, setNewPw] = useState('')
  const [reveal, setReveal] = useState(false)

  async function copy(text: string, what: string) {
    try { await navigator.clipboard.writeText(text); toast.success(`${what} copied`) } catch { toast.error('Copy failed — select it by hand') }
  }
  async function doChangePw() {
    if (!newPw) return toast.error('Enter a new password')
    setBusy(true)
    try {
      const a = await changePassword(account, newPw)
      onUpdate(a); setPwOpen(false); setNewPw('')
      toast.success('Key rotated — sign in with your new password from now on.')
    } catch (e: any) { toast.error(e.message) }
    finally { setBusy(false) }
  }
  async function doRotateFresh() {
    if (!confirm('Generate a brand-new key for this name? Your current key stops working. You should back up the new key afterwards.')) return
    setBusy(true)
    try {
      const a = await rotateFresh(account)
      onUpdate(a); setReveal(true)
      toast.success('Rotated to a fresh key — back it up below.')
    } catch (e: any) { toast.error(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 460 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 className="font-display">YOUR ACCOUNT</h2>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>

        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
            <span className="who-dot" style={{ width: 12, height: 12 }} />
            <div style={{ minWidth: 0 }}>
              <div className="font-display" style={{ fontSize: 13 }}>{account.name}</div>
              <button className="addr-copy" onClick={() => copy(account.address, 'Address')} title="Copy address">
                {shortAddr(account.address)} ⧉
              </button>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 7, marginTop: 12, flexWrap: 'wrap' }}>
            {account.derived
              ? <span className="badge badge-free">🔑 password-backed</span>
              : <span className="badge badge-paid">⚠ one-off key — back it up</span>}
            {(account.rotations ?? 0) > 0 && <span className="badge">↻ rotated ×{account.rotations}</span>}
          </div>
        </div>

        {/* Change password (re-derives a new key, stays recoverable) */}
        {!pwOpen ? (
          <button className="btn" style={{ width: '100%', marginBottom: 9 }} onClick={() => setPwOpen(true)}>🔑 Change password</button>
        ) : (
          <div className="card" style={{ marginBottom: 9 }}>
            <div className="label" style={{ marginBottom: 8 }}>New password</div>
            <input className="input" type="password" value={newPw} autoFocus placeholder="new password → new key"
              onChange={e => setNewPw(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') doChangePw() }} style={{ marginBottom: 9 }} />
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary btn-sm" disabled={busy || !newPw} onClick={doChangePw}>Rotate key</button>
              <button className="btn btn-sm" onClick={() => { setPwOpen(false); setNewPw('') }}>Cancel</button>
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 9 }}>Keeps your name; your old password stops working.</div>
          </div>
        )}

        <button className="btn" style={{ width: '100%', marginBottom: 9 }} disabled={busy} onClick={doRotateFresh}>♻ Rotate to a fresh key</button>

        <button className="btn" style={{ width: '100%', marginBottom: 9 }} onClick={() => setReveal(r => !r)}>
          {reveal ? '🙈 Hide private key' : '👁 Reveal private key'}
        </button>
        {reveal && (
          <div className="card" style={{ marginBottom: 9, borderColor: 'rgba(255,90,110,.35)' }}>
            <div className="muted" style={{ fontSize: 12, marginBottom: 7 }}>
              Anyone with this key controls your name. Never share it. {account.derived && 'You can also just re-derive it from your password.'}
            </div>
            <div className="keybox" onClick={() => copy(account.privateKey, 'Private key')}>{account.privateKey} ⧉</div>
          </div>
        )}

        <div className="divider" />
        <button className="btn btn-danger" style={{ width: '100%' }} onClick={onSignOut}>Sign out on this device</button>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div style={{ marginBottom: 13 }}><label className="label">{label}</label>{children}</div>
}
