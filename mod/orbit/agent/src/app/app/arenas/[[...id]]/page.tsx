'use client'

// /agent/arenas/<name> — one arena, standing on its own page.
// /agent/arenas        — every arena the arena mod holds, one card each.
// (/agent/g/… was the old address; g/[[...id]] redirects here.)
//
// The ARENA tab frames the arena's console whole; this route is an arena made
// linkable from here — what it is, who made it, the Elo ladder and the
// latest matches — read straight off the arena mod's public API, nothing
// copied. Playing it is the arena's job: "play in console" lands in the
// ARENA tab on this arena (signed in as you), "open in arena" is the arena
// mod's own page for it in a tab of its own. The arena mod still says
// "game" on the wire (role, ?game=, /arena/game/<id>) — those stay as-is.
//
// Optional catch-all for the same reason /m is: bare /arenas is the catalog.
// An arena is addressed by name (names are unique among live arenas); a
// sha256 id or its 12-char prefix also resolves, so a superseded version
// keeps a URL.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? '/agent'
const DEV = process.env.NODE_ENV === 'development'
const ARENA = DEV ? 'http://localhost:50470' : ''
const ARENA_API = DEV ? ARENA : '/arena/api'
// ArenaPortal reads this once the frame says hello and sends it as arena-go
const GO_KEY = 'agent_arena_go'

interface ArenaMod {
  id: string
  short: string
  name: string
  description?: string
  role: string
  owner?: string
  owner_is_host?: boolean
  author?: string
  origin?: string
  made_with?: string
  lang?: string
  tags?: string[]
  min_players?: number
  runs: number
  size?: number
  created: number
  cid?: string
  superseded?: string | null
}

interface Top { name: string; kind: string; avg_score: number; runs: number }
interface ArcadeRow { id: string; name: string; runs: number; players: number; top?: Top }

interface LadderRow {
  id: string
  name: string
  kind: string
  elo: number
  matches: number
  wins: number
  draws: number
  losses: number
  win_rate: number
  avg_score: number
}

interface Seat { player_id: string; player_name: string; score: number; delta?: number; error?: string }
interface Match { id: string; created: number; seats: Seat[]; summary?: string; turns?: number; ms?: number; rated?: boolean }

const arenaHref = (name: string) => `${BASE}/arenas/${encodeURIComponent(name)}`

const ago = (t: number) => {
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const getJson = async (path: string) => {
  const r = await fetch(`${ARENA_API}${path}`, { cache: 'no-store', signal: AbortSignal.timeout(10000) })
  if (!r.ok) throw new Error(`${path} → ${r.status}`)
  return r.json()
}

export default function ArenaPage() {
  const params = useParams<{ id?: string[] }>()
  const key = (() => {
    const raw = (params?.id || []).join('/')
    try { return decodeURIComponent(raw) } catch { return raw }
  })()

  const [mods, setMods] = useState<ArenaMod[]>([])
  const [arcade, setArcade] = useState<Record<string, ArcadeRow>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ladder, setLadder] = useState<LadderRow[] | null>(null)
  const [matches, setMatches] = useState<Match[] | null>(null)
  const [q, setQ] = useState('')

  useEffect(() => {
    getJson('/modules')
      .then(d => setMods((d.modules || []).filter((m: ArenaMod) => m.role === 'game')))
      .catch(e => setError(`the arena didn't answer — ${(e as Error).message}`))
      .finally(() => setLoading(false))
    getJson('/arcade')
      .then(d => setArcade(Object.fromEntries((d.games || []).map((g: ArcadeRow) => [g.id, g]))))
      .catch(() => {})
  }, [])

  const live = useMemo(
    () => mods.filter(m => !m.superseded).sort((a, b) => b.runs - a.runs || a.name.localeCompare(b.name)),
    [mods],
  )

  const arena = useMemo(() => {
    if (!key) return null
    const k = key.toLowerCase()
    return live.find(m => m.name.toLowerCase() === k)
      || mods.find(m => m.id === k || m.short === k)
      || mods.find(m => k.length >= 6 && m.id.startsWith(k))
      || null
  }, [key, live, mods])

  const loadArena = useCallback((id: string) => {
    getJson(`/leaderboard?game=${id}&limit=50`).then(d => setLadder(d.players || [])).catch(() => setLadder([]))
    getJson(`/matches?game=${id}&limit=20`).then(d => setMatches(d.matches || [])).catch(() => setMatches([]))
  }, [])
  useEffect(() => { if (arena) loadArena(arena.id) }, [arena, loadArena])

  const playInConsole = () => {
    if (!arena) return
    try { localStorage.setItem(GO_KEY, `game/${arena.id}`) } catch {}
    window.location.href = `${BASE}/`
  }

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return live
    return live.filter(m => m.name.toLowerCase().includes(s) || (m.description || '').toLowerCase().includes(s)
      || (m.tags || []).some(t => t.toLowerCase().includes(s)))
  }, [live, q])

  const siblings = useMemo(() => live.filter(m => m.id !== arena?.id), [live, arena])

  const card = 'rounded-xl border border-white/[0.06] bg-surface-1'
  const legend = 'text-[9px] uppercase tracking-wider text-gray-600 mb-1.5'
  const pill = 'px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 transition'

  return (
    <main className="min-h-screen bg-surface-0 text-gray-200">
      <header className="site-header border-b border-white/[0.06] px-4 min-h-12 py-1.5 flex items-center gap-3 bg-surface-0">
        <a href={`${BASE}/`} className="flex items-center gap-2.5 shrink-0" title="Back to the console">
          <div className="brand-mark w-7 h-7 flex items-center justify-center shrink-0">
            <span className="select-none">{'>'}_</span>
          </div>
          <span className="title-gradient uppercase select-none hidden sm:block">agent</span>
        </a>
        <span className="text-[10px] uppercase tracking-wider text-gray-600 truncate">
          / <a href={`${BASE}/arenas`} className="hover:text-gray-400 transition">arenas</a>{key && <> / {arena?.name || key}</>}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {key && <a href={`${BASE}/arenas`} className={pill}>all arenas</a>}
          <a href={`${BASE}/`} className={pill}>console</a>
        </div>
      </header>

      <div className="max-w-3xl mx-auto p-4 space-y-3">
        {loading && <div className="p-4 text-[11px] text-gray-600 animate-pulse">asking the arena…</div>}
        {error && (
          <div className="px-3 py-2 rounded-lg text-[11px] text-red-300 border border-red-500/25 bg-red-500/[0.07]">
            {error}
          </div>
        )}

        {/* ── the catalog ─────────────────────────────────────────────── */}
        {!key && !loading && !error && (
          <>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-medium text-gray-100">Arenas</h1>
              <span className="text-[11px] text-gray-600">{live.length} in the arena</span>
              <input value={q} onChange={e => setQ(e.target.value)} placeholder="search"
                className="ml-auto w-40 px-2.5 py-1 rounded-md text-[11px] bg-white/[0.03] border border-white/[0.08] text-gray-200 placeholder:text-gray-600 outline-none focus:border-white/[0.18]" />
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
              {shown.map(m => {
                const a = arcade[m.id]
                return (
                  <a key={m.id} href={arenaHref(m.name)}
                    className={`${card} p-3 block hover:border-white/[0.14] transition`}>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm text-emerald-300">{m.name}</span>
                      <span className="ml-auto font-mono text-[10px] text-gray-500">{m.runs} runs</span>
                    </div>
                    <p className="mt-1 text-[11px] text-gray-400 leading-relaxed line-clamp-2">{m.description || 'no description'}</p>
                    <div className="mt-1.5 text-[10px] text-gray-600 truncate">
                      {a?.players ? `${a.players} players` : 'no players yet'}
                      {a?.top && <> · top <span className="text-gray-400">{a.top.name}</span></>}
                    </div>
                  </a>
                )
              })}
            </div>
            {shown.length === 0 && <div className={`${card} p-4 text-xs text-gray-500`}>No arena matches “{q}”.</div>}
          </>
        )}

        {/* ── one arena ────────────────────────────────────────────────── */}
        {key && !loading && !arena && !error && (
          <div className={`${card} p-4 text-xs text-gray-500`}>
            No arena named <span className="font-mono text-gray-300">{key}</span> in the arena.{' '}
            <a href={`${BASE}/arenas`} className="text-emerald-300 hover:text-emerald-200 underline underline-offset-2">all arenas</a>
          </div>
        )}

        {arena && (
          <>
            <div className={`${card} p-4 flex items-start gap-3 flex-wrap`}>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <h1 className="text-base font-medium text-gray-100 font-mono">{arena.name}</h1>
                  {arena.lang && (
                    <span className="text-[8px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-white/[0.08] text-gray-500">{arena.lang}</span>
                  )}
                  {arena.superseded && (
                    <span className="text-[8px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-amber-400/25 text-amber-300">old version</span>
                  )}
                </div>
                <p className="mt-1 text-xs text-gray-400 leading-relaxed">{arena.description || 'no description'}</p>
                <div className="mt-1.5 font-mono text-[9px] text-gray-600 truncate" title={arena.id}>
                  {arena.short}
                  {arena.owner && <> · by {arena.owner.slice(0, 10)}…{arena.owner_is_host ? ' (host)' : ''}</>}
                  {arena.made_with && <> · {arena.made_with}</>}
                  {' · '}{ago(arena.created)}
                </div>
                {!!arena.tags?.length && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {arena.tags.map(t => (
                      <span key={t} className="px-1.5 py-0.5 rounded border border-white/[0.07] bg-white/[0.02] font-mono text-[9px] text-gray-400">{t}</span>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <button onClick={playInConsole}
                  className="px-3 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-emerald-500/25 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 transition">
                  play in console
                </button>
                <a href={`${ARENA}/arena/game/${arena.id}`} target="_blank" rel="noopener"
                  className="px-3 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 transition">
                  open in arena ↗
                </a>
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
              {([
                ['runs', arena.runs],
                ['players', ladder ? ladder.length : '…'],
                ['seats', arena.min_players ?? '—'],
                ['size', arena.size ? `${(arena.size / 1024).toFixed(1)} KB` : '—'],
              ] as const).map(([k, v]) => (
                <div key={k} className="px-2.5 py-1.5 rounded-md border border-white/[0.06] bg-surface-1">
                  <div className="text-[9px] uppercase tracking-wider text-gray-600">{k}</div>
                  <div className="font-mono text-xs text-emerald-300">{String(v)}</div>
                </div>
              ))}
            </div>

            {/* the ladder */}
            <div className={`${card} p-4`}>
              <div className={legend}>Ladder — Elo on this arena</div>
              {ladder === null ? (
                <p className="text-[11px] text-gray-600 animate-pulse">reading…</p>
              ) : ladder.length === 0 ? (
                <p className="text-[11px] text-gray-600">nobody has played it yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="text-[9px] uppercase tracking-wider text-gray-600 text-left">
                        <th className="py-1 pr-2 font-normal">#</th>
                        <th className="py-1 pr-2 font-normal">player</th>
                        <th className="py-1 pr-2 font-normal text-right">elo</th>
                        <th className="py-1 pr-2 font-normal text-right">w·d·l</th>
                        <th className="py-1 pr-2 font-normal text-right">win</th>
                        <th className="py-1 font-normal text-right">avg</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ladder.map((p, i) => (
                        <tr key={p.id} className="border-t border-white/[0.04]">
                          <td className="py-1 pr-2 font-mono text-gray-600">{i + 1}</td>
                          <td className="py-1 pr-2">
                            <a href={`${ARENA}/arena/agent/${p.id}`} target="_blank" rel="noopener"
                              className="text-gray-200 hover:text-emerald-300 transition">{p.name}</a>
                            <span className="ml-1.5 text-[9px] text-gray-600">{p.kind}</span>
                          </td>
                          <td className="py-1 pr-2 font-mono text-right text-emerald-300">{Math.round(p.elo)}</td>
                          <td className="py-1 pr-2 font-mono text-right text-gray-400">{p.wins}·{p.draws}·{p.losses}</td>
                          <td className="py-1 pr-2 font-mono text-right text-gray-400">{Math.round(p.win_rate * 100)}%</td>
                          <td className="py-1 font-mono text-right text-gray-400">{p.avg_score.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* the latest matches */}
            <div className={`${card} p-4`}>
              <div className={legend}>Latest matches</div>
              {matches === null ? (
                <p className="text-[11px] text-gray-600 animate-pulse">reading…</p>
              ) : matches.length === 0 ? (
                <p className="text-[11px] text-gray-600">no matches yet.</p>
              ) : (
                <div className="space-y-1">
                  {matches.map(m => {
                    const seats = [...(m.seats || [])].sort((a, b) => b.score - a.score)
                    return (
                      <div key={m.id} className="flex items-center gap-2 text-[11px] py-0.5">
                        <span className="font-mono text-[9px] text-gray-600 w-14 shrink-0">{ago(m.created)}</span>
                        <span className="min-w-0 flex-1 truncate">
                          {seats.map((s, j) => (
                            <span key={s.player_id + j}>
                              {j > 0 && <span className="text-gray-600"> · </span>}
                              <span className={j === 0 && seats.length > 1 && s.score > seats[1].score ? 'text-gray-100' : 'text-gray-400'}>{s.player_name}</span>
                              <span className="font-mono text-gray-600"> {s.score}</span>
                              {typeof s.delta === 'number' && s.delta !== 0 && (
                                <span className={`font-mono text-[9px] ${s.delta > 0 ? 'text-emerald-400' : 'text-red-400'}`}> {s.delta > 0 ? '+' : ''}{s.delta.toFixed(1)}</span>
                              )}
                            </span>
                          ))}
                        </span>
                        {m.summary && <span className="text-[10px] text-gray-500 truncate max-w-[35%]">{m.summary}</span>}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {siblings.length > 0 && (
              <div className={`${card} p-4`}>
                <div className={legend}>More arenas</div>
                <div className="flex flex-wrap gap-1.5">
                  {siblings.map(m => (
                    <a key={m.id} href={arenaHref(m.name)}
                      className="px-2.5 py-1 rounded-md border border-white/[0.07] bg-white/[0.02] font-mono text-[11px] text-gray-400 hover:text-gray-200 hover:border-white/[0.14] transition">
                      {m.name}
                    </a>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  )
}
