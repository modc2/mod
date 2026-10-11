'use client'

// Games — the arena mod's games, inside the agent console.
//
// The list is the arena's own: GET /arena/api/arcade (every stored game with
// its runs, seat count and champion). Picking a game frames that game's page
// on the arena mod (/arena/game/<id>) — leaderboard, matches, play it — so
// nothing about a game is copied here and nothing can drift. Our agents are
// seated over there by the arena's agent-protocol sync (agentproto.rs reads
// GET /agents on this module), so the champions listed are the same players.
//
// Same origin behind the gateway; in dev (no gateway on :3117) the arena is
// reached on its own port.

import { useCallback, useEffect, useState } from 'react'

const ARENA = process.env.NODE_ENV === 'development' ? 'http://localhost:50470' : ''
const ARENA_API = process.env.NODE_ENV === 'development' ? ARENA : '/arena/api'

type Top = { id: string; name: string; kind: string; avg_score: number; runs: number }
type Game = { id: string; name: string; players: number; runs: number; top?: Top | null }

export default function Games() {
  const [games, setGames] = useState<Game[] | null>(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${ARENA_API}/arcade`, { cache: 'no-store' })
      if (!r.ok) throw new Error(`arena answered ${r.status}`)
      const d = await r.json()
      const gs: Game[] = (d.games || []).slice().sort((a: Game, b: Game) => b.runs - a.runs)
      setGames(gs); setErr('')
      setSel(s => s ?? gs[0]?.id ?? null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'arena unreachable')
      setGames(g => g ?? [])
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [load])

  const shown = (games || []).filter(g => !q || g.name.toLowerCase().includes(q.toLowerCase()))

  return (
    <div className="flex-1 min-h-0 flex">
      <aside className="w-[280px] shrink-0 border-r border-white/[0.06] flex flex-col min-h-0">
        <div className="px-3 py-2 border-b border-white/[0.06] flex items-center gap-2">
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter games…"
            className="flex-1 min-w-0 bg-white/[0.03] border border-white/[0.06] rounded-md px-2 py-1 text-xs text-gray-300 outline-none" />
          <a href={`${ARENA}/arena`} target="_blank" rel="noopener"
            className="text-[10px] uppercase tracking-wider text-gray-600 hover:text-gray-300"
            title="The arena mod — every game, every agent, one board">arena ↗</a>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1">
          {games === null && <div className="text-xs text-gray-600 px-2 py-3">loading games…</div>}
          {err && (
            <div className="text-xs text-red-400 px-2 py-2">
              {err} · <button onClick={load} className="underline">retry</button>
            </div>
          )}
          {games !== null && !err && shown.length === 0 && (
            <div className="text-xs text-gray-600 px-2 py-3">no games{q ? ' match' : ' yet'}</div>
          )}
          {shown.map(g => (
            <button key={g.id} onClick={() => setSel(g.id)}
              className={`w-full text-left rounded-md px-2.5 py-2 border transition ${
                sel === g.id ? 'bg-emerald-500/10 border-emerald-500/25'
                             : 'bg-white/[0.02] border-white/[0.04] hover:border-white/[0.1]'
              }`}>
              <div className="flex items-baseline gap-2">
                <span className={`font-medium text-sm ${sel === g.id ? 'text-emerald-200' : 'text-gray-300'}`}>{g.name}</span>
                <span className="ml-auto font-mono text-[10px] text-gray-600">{g.runs} runs · {g.players} seats</span>
              </div>
              <div className="text-[11px] text-gray-600 truncate mt-0.5">
                {g.top ? <>champ <span className="text-gray-400">{g.top.name}</span> · {g.top.kind} · avg {g.top.avg_score.toFixed(2)}</>
                       : 'not played yet'}
              </div>
            </button>
          ))}
        </div>
      </aside>
      <div className="flex-1 min-w-0 min-h-0 flex">
        {sel ? (
          <iframe key={sel} src={`${ARENA}/arena/game/${sel}`}
            title="game — on the arena mod"
            className="flex-1 w-full h-full border-0 bg-surface-0" />
        ) : (
          <div className="m-auto text-xs text-gray-600">pick a game</div>
        )}
      </div>
    </div>
  )
}
