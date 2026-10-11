'use client'

// ArenaPortal — the arena mod, whole, inside the agent console, as you.
//
// The arena's own console (/arena?portal) is framed edge to edge: its lobby,
// agents, games, studio and docs, nothing copied, nothing that can drift.
// This file draws only what a frame cannot: the chrome around it (sections,
// the + buttons, live numbers, a match ticker) and the identity.
//
// Identity is the point. The arena verifies any mod-protocol token offline,
// so the token this console signed in with is handed to the frame over
// postMessage and the arena calls as that address — same key, no second
// sign-in. When the arena wants a sign-in (or a sign-out) it asks back, and
// it is this console's AuthGate that opens. page.tsx also mirrors the token
// into `arena_token`, so /arena in a tab of its own is signed in too.
//
// Protocol (both sides check the other is who it says):
//   arena → us   arena-ready · arena-view {view} · arena-me {address, box_owner}
//                mod-signin · mod-signout
//   us → arena   mod-auth {token, ephemeral} · arena-go {to} · arena-theme {theme}
//
// Same origin behind the gateway; in dev (no gateway on :3117) the arena is
// reached on its own port, which the arena's bridge accepts from localhost.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const DEV = process.env.NODE_ENV === 'development'
const ARENA = DEV ? 'http://localhost:50470' : ''
const ARENA_API = DEV ? ARENA : '/arena/api'
const SRC = `${ARENA}/arena?portal`
const VIEW_KEY = 'agent_arena_view'

// the console's skins, said in the arena's palette names
const THEME_MAP: Record<string, string> = {
  midnight: 'dark', abyss: 'ocean', ember: 'amber', neon: 'rose', paper: 'paper',
  daylight: 'light', arcade: 'dark', matrix: 'crt', mario: 'dark', win95: 'light',
}

type Section = 'lobby' | 'agents' | 'games' | 'docs'
const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: 'lobby', label: 'arena', hint: 'the lobby — a match on the stage, games, the ranking, the feed' },
  { id: 'agents', label: 'agents', hint: 'every player ranked — models, agents, classes, wasm' },
  { id: 'games', label: 'games', hint: 'every game stored — yours, everyone’s' },
  { id: 'docs', label: 'docs', hint: 'how the arena works, the ABI, the protocol' },
]

type Seat = { player_name: string; score: number; delta?: number }
type Match = { id: string; game: string; game_name?: string; created: number; seats: Seat[]; summary?: string }
type Stats = { games: number; matches: number; ranked: number; players: number }

type Props = {
  auth: { address: string; token: string; isOwner: boolean; local?: boolean; ephemeral?: boolean } | null
  theme: string
  onSignIn: () => void
  onSignOut: () => void
}

const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`
const ago = (t: number) => {
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}
// a stable two-tone dot per address, the same idea as the arena's own
function addrHue(a: string) {
  let h = 0
  for (const c of a.toLowerCase()) h = (h * 33 + c.charCodeAt(0)) % 360
  return h
}

// count up to a number instead of snapping to it
function useTween(target: number, ms = 900) {
  const [v, setV] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    const a = from.current, b = target
    if (a === b) return
    const t0 = performance.now()
    let raf = 0
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms)
      const e = 1 - Math.pow(1 - k, 3)
      setV(Math.round(a + (b - a) * e))
      if (k < 1) raf = requestAnimationFrame(step)
      else from.current = b
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}

function Stat({ n, label }: { n: number; label: string }) {
  const v = useTween(n)
  return (
    <span className="ap-stat">
      <b>{v.toLocaleString()}</b>
      <i>{label}</i>
    </span>
  )
}

function matchLine(m: Match) {
  const s = [...(m.seats || [])].sort((a, b) => b.score - a.score)
  const game = m.game_name || 'match'
  if (s.length < 2) return { game, text: s[0]?.player_name || m.summary || '', delta: s[0]?.delta }
  if (s[0].score === s[1].score) return { game, text: `${s[0].player_name} drew ${s[1].player_name}`, delta: undefined }
  return { game, text: `${s[0].player_name} beat ${s[1].player_name}`, delta: s[0].delta }
}

export default function ArenaPortal({ auth, theme, onSignIn, onSignOut }: Props) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [ready, setReady] = useState(false)
  const [slow, setSlow] = useState(false)
  const [nonce, setNonce] = useState(0)          // bump to reload the frame
  const [view, setView] = useState<Section>('lobby')
  const [arenaMe, setArenaMe] = useState<{ address: string | null; box_owner: boolean } | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [feed, setFeed] = useState<Match[]>([])
  const [down, setDown] = useState(false)

  const origin = useMemo(() => (DEV ? ARENA : typeof window !== 'undefined' ? window.location.origin : ''), [])
  const post = useCallback((msg: object) => {
    frame.current?.contentWindow?.postMessage(msg, origin || '*')
  }, [origin])

  // the live numbers and the ticker, read from the arena's API directly
  const load = useCallback(async () => {
    try {
      const [arc, pl, mt] = await Promise.all([
        fetch(`${ARENA_API}/arcade`, { cache: 'no-store' }).then(r => r.json()),
        fetch(`${ARENA_API}/players`, { cache: 'no-store' }).then(r => r.json()),
        fetch(`${ARENA_API}/matches?limit=16`, { cache: 'no-store' }).then(r => r.json()),
      ])
      const games = arc.games || []
      const players = pl.players || []
      setStats({
        games: arc.count ?? games.length,
        matches: games.reduce((n: number, g: { runs?: number }) => n + (g.runs || 0), 0),
        ranked: players.filter((p: { matches?: number; kind?: string }) => (p.matches || 0) > 0 && p.kind !== 'gone').length,
        players: pl.count ?? players.length,
      })
      setFeed(mt.matches || [])
      setDown(false)
    } catch { setDown(true) }
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(() => { if (!document.hidden) load() }, 15000)
    return () => clearInterval(t)
  }, [load])

  // identity + theme, sent whenever either changes and again on every ready
  const sendAuth = useCallback(() => {
    post({ type: 'mod-auth', token: auth?.token || null, ephemeral: !!auth?.ephemeral })
  }, [post, auth?.token, auth?.ephemeral])
  const sendTheme = useCallback(() => {
    post({ type: 'arena-theme', theme: THEME_MAP[theme] || 'dark' })
  }, [post, theme])
  useEffect(() => { if (ready) sendAuth() }, [ready, sendAuth])
  useEffect(() => { if (ready) sendTheme() }, [ready, sendTheme])

  const go = useCallback((to: string) => {
    post({ type: 'arena-go', to })
    if ((SECTIONS as { id: string }[]).some(s => s.id === to)) setView(to as Section)
  }, [post])

  // the arena talking back
  const handlers = useRef({ onSignIn, onSignOut })
  handlers.current = { onSignIn, onSignOut }
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return
      if (origin && e.origin !== origin) return
      const m = e.data || {}
      switch (m.type) {
        case 'arena-ready': {
          setReady(true); setSlow(false)
          let last: string | null = null
          try { last = localStorage.getItem(VIEW_KEY) } catch {}
          if (last && last !== 'lobby' && SECTIONS.some(s => s.id === last)) go(last)
          else if (m.view) setView(m.view)
          break
        }
        case 'arena-view':
          if (SECTIONS.some(s => s.id === m.view)) {
            setView(m.view)
            try { localStorage.setItem(VIEW_KEY, m.view) } catch {}
          }
          break
        case 'arena-me': setArenaMe({ address: m.address || null, box_owner: !!m.box_owner }); break
        case 'mod-signin': handlers.current.onSignIn(); break
        case 'mod-signout': handlers.current.onSignOut(); break
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [origin, go])

  // a frame that never says hello: show it anyway after a beat, and say so
  useEffect(() => {
    setReady(false); setSlow(false); setArenaMe(null)
    const t = setTimeout(() => setSlow(true), 9000)
    return () => clearTimeout(t)
  }, [nonce])

  const linked = !!auth && !!arenaMe?.address && arenaMe.address.toLowerCase() === auth.address.toLowerCase()
  const hue = auth ? addrHue(auth.address) : 160
  const ticker = feed.slice(0, 16)

  return (
    <div className="arena-portal flex-1 min-w-0 min-h-0 flex flex-col">
      {/* ── chrome ───────────────────────────────────────────────────── */}
      <div className="ap-bar">
        <button type="button" className="ap-brand" onClick={() => go('lobby')} title="the arena — lobby">
          <span className={`ap-ring ${ready ? 'ap-ring--on' : ''}`} aria-hidden><span /></span>
          <span className="ap-word">ARENA</span>
          <span className={`ap-live ap-w720 ${down ? 'ap-live--down' : ''}`}>{down ? 'offline' : 'live'}</span>
        </button>

        <nav className="ap-tabs" aria-label="arena sections">
          {SECTIONS.map(s => (
            <button key={s.id} type="button" title={s.hint} onClick={() => go(s.id)}
              className={view === s.id ? 'on' : ''}>{s.label}</button>
          ))}
        </nav>

        {stats && (
          <div className="ap-stats">
            <Stat n={stats.games} label="games" />
            <Stat n={stats.ranked} label="ranked" />
            <Stat n={stats.matches} label="matches" />
          </div>
        )}

        <div className="ml-auto flex items-center gap-1.5 shrink-0">
          <button type="button" className="ap-act ap-act--hot" onClick={() => go('vibe')}
            title="a new game — say what it is, the agent writes it">+ game</button>
          <button type="button" className="ap-act ap-w720" onClick={() => go('vibe-agent')}
            title="a new player — vibecode it, or clone it from a git url">+ agent</button>
          <button type="button" className="ap-act ap-w820" onClick={() => go('upload')}
            title="upload a .wasm / .py / .rs, harvest a repo, or clone a git url">upload</button>

          {auth ? (
            <span className={`ap-me ${linked ? 'ap-me--linked' : ''}`}
              title={`${auth.address}\n${linked ? 'the arena sees the same key — you play as yourself' : 'handing your key to the arena…'}${arenaMe?.box_owner ? '\nyou run this box' : ''}`}>
              <span className="ap-dot" style={{ background: `conic-gradient(hsl(${hue} 70% 60%), hsl(${(hue + 120) % 360} 70% 52%), hsl(${hue} 70% 60%))` }} />
              <span className="font-mono">{short(auth.address)}</span>
              <svg className="ap-link" viewBox="0 0 24 24" aria-hidden>
                <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
                <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
              </svg>
              {arenaMe?.box_owner && <span className="ap-badge">host</span>}
            </span>
          ) : (
            <button type="button" className="ap-act ap-act--hot" onClick={onSignIn}
              title="one sign-in — this console and the arena both">sign in to play</button>
          )}

          <a href={`${ARENA}/arena`} target="_blank" rel="noopener" className="ap-out"
            title="open the arena in a tab of its own — you stay signed in there too">
            <svg viewBox="0 0 24 24" aria-hidden><path d="M14 4h6v6" /><path d="M20 4l-9 9" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>
          </a>
        </div>
      </div>

      {/* ── the arena itself ─────────────────────────────────────────── */}
      <div className="ap-stage">
        <iframe ref={frame} key={nonce} src={SRC} title="the arena"
          className={`ap-frame ${ready || slow ? 'ap-frame--in' : ''}`}
          allow="clipboard-write" />
        {!ready && (
          <div className={`ap-warp ${slow ? 'ap-warp--slow' : ''}`}>
            <div className="ap-vortex" aria-hidden>
              <span /><span /><span /><span />
            </div>
            <div className="ap-warp-text">
              {slow ? (
                <>the arena is taking its time ·{' '}
                  <button type="button" onClick={() => setNonce(n => n + 1)} className="underline">reopen</button></>
              ) : auth ? <>entering as <span className="font-mono">{short(auth.address)}</span></> : 'opening the arena'}
            </div>
          </div>
        )}
      </div>

      {/* ── ticker: the last matches, newest first ───────────────────── */}
      {ticker.length > 0 && (
        <div className="ap-ticker" aria-label="latest matches">
          <span className="ap-ticker-tag">feed</span>
          <div className="ap-ticker-track">
            <div className="ap-ticker-run" style={{ animationDuration: `${Math.max(30, ticker.length * 6)}s` }}>
              {[0, 1].map(copy => (
                <div key={copy} className="ap-ticker-set" aria-hidden={copy === 1}>
                  {ticker.map(m => {
                    const l = matchLine(m)
                    return (
                      <button key={`${copy}-${m.id}`} type="button" className="ap-tick"
                        onClick={() => go(`game/${m.game}`)} title={`open ${l.game}`}>
                        <span className="ap-tick-game">{l.game}</span>
                        <span>{l.text}</span>
                        {typeof l.delta === 'number' && l.delta !== 0 && (
                          <span className={l.delta > 0 ? 'ap-up' : 'ap-dn'}>
                            {l.delta > 0 ? '+' : ''}{l.delta.toFixed(1)}
                          </span>
                        )}
                        <span className="ap-tick-ago">{ago(m.created)}</span>
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
