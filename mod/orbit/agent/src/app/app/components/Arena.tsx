'use client'

// Arena — the board.
//
// Every agent runs the same tasks, in the same scratch dir, under the same
// step budget, and is scored off the trace and the files it left behind. This
// view is that board: who is ahead, on what, and what it cost to find out.
//
// Nothing here has to be clicked for it to stay current. A background process
// on the server qualifies an agent within a minute of it coming online and
// runs a full round once a day; RUN ROUND is just the manual override.

import { useState, useEffect, useCallback, useMemo } from 'react'
import { API_URL } from '../config'

type Row = {
  rank: number; agent: string; icon: string; active: boolean
  elo: number; matches: number; wins: number; losses: number; draws: number
  win_rate: number; avg_score: number; avg_seconds: number; cost: number
  tasks: number; last: number
}
type Match = {
  id: string; ts: number; agent: string; task: string; suite: string; title: string
  score: number; correct: number; reliable: number; efficient: number
  passed: boolean; steps: number; budget: number; seconds: number; cost: number
  model?: string | null; error?: string | null; reason?: string
  checks?: { type: string; passed: boolean; reason: string }[]
}
type Task = { key: string; suite: string; title: string; prompt: string; steps?: number | null }
type Status = {
  enabled: boolean
  config: Record<string, any>
  season: number; last_round: number; next_round: number; due: boolean
  running: { agent: string; task: string; started_at: number; reason: string } | null
  scheduler: { alive: boolean; ticks: number; last_tick: number; last_action?: string | null; last_error?: string | null }
  subjects: string[]; newcomers: string[]; tasks: number; round_tasks: string[]; matches: number
}
type Card = Row & { per_task: { task: string; title: string; n: number; best: number; last: number }[]; matches_log: Match[] }

const ago = (ts?: number) => {
  if (!ts) return 'never'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const until = (ts?: number) => {
  if (!ts) return 'now'
  const s = Math.floor(ts - Date.now() / 1000)
  if (s <= 0) return 'due'
  if (s < 3600) return `in ${Math.ceil(s / 60)}m`
  return `in ${Math.round(s / 3600)}h`
}

const pct = (n: number) => `${Math.round((n || 0) * 100)}%`

// the three parts of a score, in the order they're weighted
const BARS: { key: 'correct' | 'reliable' | 'efficient'; label: string; tint: string }[] = [
  { key: 'correct', label: 'correct', tint: 'bg-emerald-400' },
  { key: 'reliable', label: 'clean', tint: 'bg-sky-400' },
  { key: 'efficient', label: 'lean', tint: 'bg-violet-400' },
]

const ScoreBar = ({ value, tint = 'bg-emerald-400' }: { value: number; tint?: string }) => (
  <div className="h-1 w-full bg-white/[0.06] rounded-full overflow-hidden">
    <div className={`h-full ${tint} rounded-full transition-all`}
      style={{ width: `${Math.max(2, Math.min(100, (value || 0) * 100))}%` }} />
  </div>
)

export default function Arena({ token, isHost }: { token?: string | null; isHost: boolean }) {
  const [board, setBoard] = useState<Row[]>([])
  const [status, setStatus] = useState<Status | null>(null)
  const [matches, setMatches] = useState<Match[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [picked, setPicked] = useState<string | null>(null)     // agent card
  const [card, setCard] = useState<Card | null>(null)
  const [pane, setPane] = useState<'matches' | 'tasks'>('matches')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [showConfig, setShowConfig] = useState(false)

  const load = useCallback(() => {
    const signal = AbortSignal.timeout(10000)
    fetch(`${API_URL}/arena`, { signal }).then(r => r.json())
      .then(d => { setBoard(d.leaderboard || []); setStatus(d.status || null) }).catch(() => {})
    fetch(`${API_URL}/arena/matches?limit=60`, { signal }).then(r => r.json())
      .then(d => setMatches(d.matches || [])).catch(() => {})
    fetch(`${API_URL}/arena/tasks`, { signal }).then(r => r.json())
      .then(d => setTasks(d.tasks || [])).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])
  // a round takes minutes and runs server-side, so the board refreshes itself
  useEffect(() => {
    const t = setInterval(load, status?.running ? 5000 : 20000)
    return () => clearInterval(t)
  }, [load, status?.running])

  useEffect(() => {
    if (!picked) { setCard(null); return }
    fetch(`${API_URL}/arena/agents/${encodeURIComponent(picked)}`)
      .then(r => r.json()).then(d => setCard(d?.error ? null : d)).catch(() => {})
  }, [picked, matches.length])

  const post = async (path: string, body: any) => {
    setBusy(true); setErr(null)
    try {
      const r = await fetch(`${API_URL}/arena/${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, key: token }),
      }).then(x => x.json())
      if (r?.error) setErr(r.error)
      return r
    } catch (e: any) {
      setErr(e?.message || 'request failed')
    } finally {
      setBusy(false)
      load()
    }
  }

  const roundKeys = useMemo(() => new Set(status?.round_tasks || []), [status])
  const suites = useMemo(() => {
    const out: Record<string, Task[]> = {}
    for (const t of tasks) (out[t.suite] ||= []).push(t)
    return out
  }, [tasks])

  const cfg = status?.config || {}
  const live = status?.running

  // ── header ────────────────────────────────────────────────────────
  const head = (
    <div className="border-b border-white/[0.06] px-4 py-2.5 flex items-center gap-3 flex-wrap">
      <div className="flex items-center gap-2">
        <span className="text-[11px] uppercase tracking-[0.2em] text-emerald-300">arena</span>
        <span className="text-[10px] text-gray-600">season {status?.season ?? 0}</span>
      </div>

      {live ? (
        <span className="flex items-center gap-1.5 text-[10px] text-emerald-300 bg-emerald-500/10 border border-emerald-500/20 rounded-md px-2 py-0.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          {live.agent} · {live.task}
        </span>
      ) : (
        <span className="text-[10px] text-gray-600">
          {status?.matches ?? 0} matches · {status?.tasks ?? 0} tasks · {status?.subjects?.length ?? 0} agents
        </span>
      )}

      <div className="flex items-center gap-3 ml-auto text-[10px]">
        <span className={status?.scheduler?.alive ? 'text-gray-500' : 'text-amber-400'}
          title={status?.scheduler?.last_error || status?.scheduler?.last_action || ''}>
          {status?.scheduler?.alive
            ? `auto · next round ${until(status?.next_round)}`
            : 'auto off'}
        </span>
        {isHost && (
          <>
            <button onClick={() => setShowConfig(v => !v)}
              className="uppercase tracking-wider text-gray-600 hover:text-gray-300 transition">
              settings
            </button>
            <button disabled={busy || !!live} onClick={() => post('run', {})}
              className="lit-btn px-2.5 py-1 rounded-md uppercase tracking-wider text-[10px] disabled:opacity-40">
              {busy ? 'running…' : 'run round'}
            </button>
          </>
        )}
      </div>
    </div>
  )

  // ── settings (host) ───────────────────────────────────────────────
  const config = showConfig && isHost && (
    <div className="border-b border-white/[0.06] px-4 py-3 bg-white/[0.02] flex flex-wrap items-end gap-4 text-[10px]">
      {[
        { k: 'period_hours', label: 'round every (h)', step: '1' },
        { k: 'poll_seconds', label: 'new-agent poll (s)', step: '10' },
        { k: 'tasks_per_round', label: 'tasks / round', step: '1' },
        { k: 'max_matches', label: 'match cap', step: '5' },
        { k: 'steps', label: 'step budget', step: '1' },
      ].map(f => (
        <label key={f.k} className="flex flex-col gap-1">
          <span className="uppercase tracking-wider text-gray-600">{f.label}</span>
          <input type="number" step={f.step} defaultValue={cfg[f.k]}
            onBlur={e => {
              const v = Number(e.target.value)
              if (v && v !== cfg[f.k]) post('config', { [f.k]: v })
            }}
            className="w-24 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-200 outline-none focus:border-emerald-500/40 transition" />
        </label>
      ))}
      {[
        { k: 'enabled', label: 'board on' },
        { k: 'free', label: 'free models only' },
        { k: 'harnesses', label: 'CLI agents compete' },
      ].map(f => (
        <label key={f.k} className="flex items-center gap-1.5 cursor-pointer pb-1">
          <input type="checkbox" checked={!!cfg[f.k]}
            onChange={e => post('config', { [f.k]: e.target.checked })}
            className="accent-emerald-500" />
          <span className="uppercase tracking-wider text-gray-500">{f.label}</span>
        </label>
      ))}
      <label className="flex items-center gap-1.5 cursor-pointer pb-1">
        <input type="checkbox" checked={!!status?.scheduler?.alive}
          onChange={e => post('config', { scheduler: e.target.checked })}
          className="accent-emerald-500" />
        <span className="uppercase tracking-wider text-gray-500">background process</span>
      </label>
      <span className="text-gray-600 pb-1">
        free models keep the daily round from spending the host's credits
      </span>
    </div>
  )

  // ── leaderboard ───────────────────────────────────────────────────
  const leaderboard = (
    <div className="flex-1 min-w-0 overflow-y-auto no-scrollbar">
      {board.length === 0 ? (
        <div className="p-8 text-center text-xs text-gray-600">
          no matches yet — the first round runs on its own, or start one with RUN ROUND
        </div>
      ) : (
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-surface-0 z-10">
            <tr className="text-gray-600 uppercase tracking-wider text-[9px]">
              {['#', 'agent', 'elo', 'score', 'w–l–d', 'matches', 'avg s', 'spent', 'last'].map(h => (
                <th key={h} className={`font-medium py-2 px-2 ${h === 'agent' ? 'text-left' : 'text-right'}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {board.map(r => (
              <tr key={r.agent}
                onClick={() => setPicked(picked === r.agent ? null : r.agent)}
                className={`border-t border-white/[0.04] cursor-pointer transition ${
                  picked === r.agent ? 'bg-emerald-500/10' : 'hover:bg-white/[0.03]'
                } ${r.active ? '' : 'opacity-45'}`}>
                <td className="py-1.5 px-2 text-right tabular-nums text-gray-500">
                  {r.rank <= 3 ? ['🥇', '🥈', '🥉'][r.rank - 1] : r.rank}
                </td>
                <td className="py-1.5 px-2">
                  <span className="text-gray-500 mr-1.5">{r.icon}</span>
                  <span className="text-gray-200">{r.agent}</span>
                  {!r.active && <span className="ml-1.5 text-[9px] text-gray-600 uppercase">retired</span>}
                </td>
                <td className="py-1.5 px-2 text-right tabular-nums text-emerald-300">{r.elo.toFixed(0)}</td>
                <td className="py-1.5 px-2 text-right w-24">
                  <div className="flex items-center gap-1.5 justify-end">
                    <span className="tabular-nums text-gray-400">{pct(r.avg_score)}</span>
                    <span className="w-12"><ScoreBar value={r.avg_score} /></span>
                  </div>
                </td>
                <td className="py-1.5 px-2 text-right tabular-nums text-gray-500">
                  {r.wins}–{r.losses}–{r.draws}
                </td>
                <td className="py-1.5 px-2 text-right tabular-nums text-gray-500">{r.matches}</td>
                <td className="py-1.5 px-2 text-right tabular-nums text-gray-600">{r.avg_seconds.toFixed(1)}</td>
                <td className="py-1.5 px-2 text-right tabular-nums text-gray-600">
                  {r.cost ? `$${r.cost.toFixed(4)}` : '—'}
                </td>
                <td className="py-1.5 px-2 text-right text-gray-600">{ago(r.last)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )

  // ── agent card ────────────────────────────────────────────────────
  const agentCard = card && (
    <div className="border-t border-white/[0.06] p-3 space-y-2 max-h-[38%] overflow-y-auto no-scrollbar">
      <div className="flex items-center gap-2">
        <span className="text-gray-500">{card.icon}</span>
        <span className="text-xs text-gray-200">{card.agent}</span>
        <span className="text-[10px] text-emerald-300 tabular-nums">{card.elo?.toFixed(0)} elo</span>
        <span className="text-[10px] text-gray-600">{card.matches} matches · {pct(card.win_rate)} win rate</span>
        {isHost && (
          <button disabled={busy || !!live} onClick={() => post('run', { agent: card.agent })}
            className="ml-auto text-[10px] uppercase tracking-wider text-gray-600 hover:text-emerald-300 transition disabled:opacity-40">
            re-run
          </button>
        )}
        <button onClick={() => setPicked(null)}
          className="text-[10px] uppercase tracking-wider text-gray-600 hover:text-gray-300 transition">
          close
        </button>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
        {card.per_task.map(t => (
          <div key={t.task} className="space-y-1">
            <div className="flex items-baseline gap-2">
              <span className="text-[10px] text-gray-400 truncate flex-1">{t.title}</span>
              <span className="text-[10px] tabular-nums text-gray-500">{pct(t.last)}</span>
            </div>
            <ScoreBar value={t.last} />
          </div>
        ))}
      </div>
    </div>
  )

  // ── matches / tasks rail ──────────────────────────────────────────
  const feed = (
    <div className="w-[380px] shrink-0 border-l border-white/[0.06] flex flex-col min-h-0">
      <div className="flex items-center gap-0.5 px-3 py-2 border-b border-white/[0.06]">
        {(['matches', 'tasks'] as const).map(p => (
          <button key={p} onClick={() => setPane(p)}
            className={`px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider transition ${
              pane === p ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-600 hover:text-gray-300'
            }`}>
            {p}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-gray-600">
          {pane === 'matches' ? `${matches.length} recent` : `${tasks.length} in pool`}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto no-scrollbar p-2 space-y-1.5">
        {pane === 'matches' && matches
          .filter(m => !picked || m.agent === picked)
          .map(m => (
            <div key={m.id} className="border border-white/[0.06] rounded-lg p-2 space-y-1.5 hover:border-white/[0.12] transition">
              <div className="flex items-baseline gap-2">
                <span className="text-[11px] text-gray-200">{m.agent}</span>
                <span className="text-[10px] text-gray-600 truncate flex-1">{m.title}</span>
                <span className={`text-[11px] tabular-nums ${m.error ? 'text-red-400' : m.passed ? 'text-emerald-300' : 'text-gray-400'}`}>
                  {m.error ? 'forfeit' : pct(m.score)}
                </span>
              </div>
              <div className="flex gap-1.5">
                {BARS.map(b => (
                  <div key={b.key} className="flex-1" title={`${b.label} ${pct(m[b.key])}`}>
                    <ScoreBar value={m[b.key]} tint={b.tint} />
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-2 text-[9px] text-gray-600">
                <span>{m.suite}</span>
                <span>{m.steps}/{m.budget} steps</span>
                <span>{m.seconds}s</span>
                {m.cost > 0 && <span>${m.cost.toFixed(4)}</span>}
                <span className="ml-auto">{m.reason?.startsWith('qualifier') ? 'qualifier' : m.reason}</span>
                <span>{ago(m.ts)}</span>
              </div>
              {m.error && <div className="text-[9px] text-red-400/80 truncate">{m.error}</div>}
            </div>
          ))}

        {pane === 'tasks' && Object.entries(suites).map(([suite, list]) => (
          <div key={suite} className="space-y-1">
            <div className="text-[9px] uppercase tracking-wider text-gray-600 px-1 pt-1">{suite}</div>
            {list.map(t => (
              <div key={t.key}
                className={`border rounded-lg p-2 transition ${
                  roundKeys.has(t.key) ? 'border-emerald-500/25 bg-emerald-500/[0.06]' : 'border-white/[0.06]'
                }`}>
                <div className="flex items-baseline gap-2">
                  <span className="text-[10px] text-gray-300 flex-1 truncate">{t.title}</span>
                  {roundKeys.has(t.key) && (
                    <span className="text-[9px] uppercase tracking-wider text-emerald-300">this round</span>
                  )}
                  {isHost && (
                    <button disabled={busy || !!live}
                      onClick={() => post('run', { task: t.key })}
                      className="text-[9px] uppercase tracking-wider text-gray-600 hover:text-emerald-300 transition disabled:opacity-40">
                      play
                    </button>
                  )}
                </div>
                <div className="text-[9px] text-gray-600 mt-0.5">
                  {t.key} · budget {t.steps || cfg.steps || 8} steps
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {head}
      {config}
      {err && (
        <div className="px-4 py-1.5 text-[10px] text-red-400 bg-red-500/10 border-b border-red-500/20">{err}</div>
      )}
      {status && !status.enabled && (
        <div className="px-4 py-1.5 text-[10px] text-amber-400 bg-amber-500/10 border-b border-amber-500/20">
          the board is switched off — no rounds will run until it is turned back on
        </div>
      )}
      <div className="flex-1 min-h-0 flex">
        <div className="flex-1 min-w-0 flex flex-col">
          {leaderboard}
          {agentCard}
        </div>
        {feed}
      </div>
    </div>
  )
}
