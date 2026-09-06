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
//
// The third rail tab is the openarena bridge. Those tasks are a different
// measurement — a statement plus graded test cases, where what is scored is
// the program the agent wrote, judged in openarena's own sandbox against cases
// this module never sees. They play in the same rounds and rate on the same
// Elo, so the board is one board; this pane is where they are managed, pulled
// in off a published benchmark, and where our agents are entered on openarena's
// own board in return.

import { Fragment, useState, useEffect, useCallback, useMemo } from 'react'
import { API_URL } from '../config'

type Row = {
  rank: number; agent: string; icon: string; active: boolean
  elo: number; matches: number; wins: number; losses: number; draws: number
  win_rate: number; avg_score: number; avg_seconds: number; cost: number
  tokens: number; avg_tokens: number
  tasks: number; voids: number; last: number
}
type Match = {
  id: string; ts: number; agent: string; task: string; suite: string; title: string
  score: number; correct: number; reliable: number; efficient: number
  passed: boolean; steps: number; budget: number; seconds: number; cost: number; tokens?: number
  model?: string | null; error?: string | null; reason?: string
  // the provider failed, not the agent — kept on the record, out of the rating
  void?: boolean; void_reason?: string | null; attempt?: number
  // a program-graded match carries the judge's case list on its check
  checks?: { type: string; passed: boolean; reason: string; score?: number
             cases?: { name: string; passed: boolean; hidden: boolean }[] }[]
}
type Task = { key: string; suite: string; title: string; prompt: string; steps?: number | null }
// one openarena task, as the bridge reports it
type OaTask = {
  key: string; slug: string; title: string; mode: string; language: string
  cases: number; hidden: number; tags?: string[]; author?: string; steps?: number
}
type OaStatus = {
  available: boolean; base: string; enabled: boolean; error?: string
  version?: string; tasks?: number; agents?: number; matches?: number
  limit?: number; steps?: number; console?: string
  languages?: string[]
  pool: OaTask[]
  entrants: { id: string; name: string; kind: string; elo?: number; attempts?: number }[]
}
type Status = {
  enabled: boolean
  config: Record<string, any>
  season: number; last_round: number; next_round: number; due: boolean
  running: { agent: string; task: string; started_at: number; reason: string } | null
  scheduler: { alive: boolean; ticks: number; last_tick: number; last_action?: string | null; last_error?: string | null }
  subjects: string[]; newcomers: string[]; tasks: number; round_tasks: string[]; matches: number
}
type Card = Row & { per_task: { task: string; title: string; n: number; best: number; last: number }[]; matches_log: Match[] }

// the same matches, keyed on the model that played them (see arena/models.py).
// `rated` is the one to read first: false means this model never met another
// on the same task under the same agent, so its elo is the number it started
// with and the rank next to it is not a claim.
type ModelRow = {
  rank: number; model: string; provider?: string | null; free: boolean
  elo: number; rated: boolean; h2h: number; wins: number; losses: number; draws: number
  matches: number; voids: number; avg_score: number; best_score: number; pass_rate: number
  avg_seconds: number; p50_seconds: number; max_seconds: number
  sec_per_step: number; tok_per_sec: number
  steps: number; tokens: number; avg_tokens: number
  cost: number; cost_per_match: number; cost_per_point: number
  tasks: string[]; suites: string[]; agents: string[]; first: number; last: number
}
type ModelCard = ModelRow & {
  per_task: { task: string; title: string; suite?: string; n: number; best: number
              last: number; seconds: number; tokens: number }[]
  opponents: { model: string; wins: number; losses: number; draws: number; record: string }[]
  matches_log: Match[]
}
type TaskRow = {
  task: string; title: string; suite?: string; matches: number; voids: number
  avg_score: number; pass_rate: number; avg_seconds: number; spread: number
  best?: string | null; last: number
  models: { model: string; n: number; score: number; pass_rate: number
            seconds: number; tokens: number; agents: string[] }[]
  // the agent side of the same task: everyone's standing record, leader first
  agents: { agent: string; icon: string; n: number; best: number; last: number; ts: number }[]
  leader?: string | null; leader_icon?: string | null; leader_score: number
  agent_spread: number; unplayed?: boolean
}
// what a gauntlet may be pointed at — every provider's list, keys or no keys
type Candidate = { model: string; provider: string; ready: boolean; free: boolean; hint?: string | null }
type ModelsPayload = {
  models: ModelRow[]; catalog: Candidate[]; rated: number; matches: number
  round_model?: string | null; free: boolean; agents: string[]
  tasks: { key: string; title: string; suite: string }[]
}

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

// the judge's case list, when the match had one — a program-graded task carries
// it on the `openarena` check, and nothing else on the board does
const caseList = (m: Match) =>
  m.checks?.find(c => c.type === 'openarena' && c.cases?.length)?.cases || []

// rounds run on free models by default, so cost is $0 and tokens are the only
// honest answer to "what did ranking this field cost"
const toks = (n?: number) => {
  if (!n) return ''
  if (n < 1000) return `${n}t`
  return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}kt`
}

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

// a score and its bar, the pairing every board uses in its SCORE column
const Score = ({ value, w = 'w-14' }: { value: number; w?: string }) => (
  <div className="flex items-center gap-2 justify-end">
    <span className="tabular-nums text-gray-300">{pct(value)}</span>
    <span className={w}><ScoreBar value={value} /></span>
  </div>
)

// one number over its name — the row of them under the arena's header is
// the whole state of the board before you read a single row
const Stat = ({ n, k, tint = 'text-gray-100', title }:
  { n: React.ReactNode; k: string; tint?: string; title?: string }) => (
  <div className="stat" title={title}>
    <span className={`stat__n ${tint}`}>{n}</span>
    <span className="stat__k">{k}</span>
  </div>
)

// an agent's icon in a fixed square: some are a glyph, some are a whole
// word, and a table column cannot be both widths — a word is cut to its
// initial rather than to two letters, which reads as a truncation
const Glyph = ({ icon }: { icon?: string }) => {
  const raw = (icon || '').trim()
  const mark = !raw ? '·' : /^[A-Za-z0-9]{2,}$/.test(raw) ? raw[0].toUpperCase() : raw.slice(0, 2)
  return <span className="glyph">{mark}</span>
}

// an empty board says what will fill it, not just that it is empty
const Empty = ({ title, body, hint }: { title: string; body: string; hint?: string }) => (
  <div className="h-full min-h-[240px] flex items-center justify-center p-8">
    <div className="max-w-[380px] text-center space-y-2">
      <div className="section-label text-gray-500">{title}</div>
      <p className="text-[11px] text-gray-600 leading-relaxed">{body}</p>
      {hint && <p className="text-[10px] text-emerald-400/60 uppercase tracking-wider">{hint}</p>}
    </div>
  </div>
)

export default function Arena({ token, isHost }: { token?: string | null; isHost: boolean }) {
  const [board, setBoard] = useState<Row[]>([])
  const [status, setStatus] = useState<Status | null>(null)
  const [matches, setMatches] = useState<Match[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [picked, setPicked] = useState<string | null>(null)     // agent card
  const [card, setCard] = useState<Card | null>(null)
  const [pane, setPane] = useState<'matches' | 'tasks' | 'openarena'>('matches')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [showConfig, setShowConfig] = useState(false)

  // which board the main pane is: the agents, the models underneath them, or
  // the tasks both are measured on
  const [view, setView] = useState<'agents' | 'models' | 'tasks'>('agents')
  const [mods, setMods] = useState<ModelsPayload | null>(null)
  const [pickedModel, setPickedModel] = useState<string | null>(null)
  const [modelCard, setModelCard] = useState<ModelCard | null>(null)
  const [taskRows, setTaskRows] = useState<TaskRow[]>([])
  const [openTask, setOpenTask] = useState<string | null>(null)
  const [showGauntlet, setShowGauntlet] = useState(false)
  const [gauntlet, setGauntlet] = useState<
    { agent: string; task: string; steps: number; models: string[]; provider: string; filter: string }>(
    { agent: '', task: '', steps: 0, models: [], provider: 'openrouter', filter: '' })

  // the openarena bridge — fetched when its pane is opened, not on the board's
  // 20s poll: a neighbour that is down should cost one spinner, not every tick
  const [oa, setOa] = useState<OaStatus | null>(null)
  const [oaBusy, setOaBusy] = useState(false)
  const [bench, setBench] = useState({ source: 'humaneval', limit: 10, offset: 0 })
  const [preview, setPreview] = useState<{ tasks?: any[]; count?: number; error?: string } | null>(null)

  const load = useCallback(() => {
    const signal = AbortSignal.timeout(10000)
    fetch(`${API_URL}/arena`, { signal }).then(r => r.json())
      .then(d => { setBoard(d.leaderboard || []); setStatus(d.status || null) }).catch(() => {})
    fetch(`${API_URL}/arena/matches?limit=60`, { signal }).then(r => r.json())
      .then(d => setMatches(d.matches || [])).catch(() => {})
    fetch(`${API_URL}/arena/tasks`, { signal }).then(r => r.json())
      .then(d => setTasks(d.tasks || [])).catch(() => {})
  }, [])

  // the model and task boards are views over the same match log, so they are
  // only fetched for the board being looked at — and again whenever a match
  // lands, which is what makes a running gauntlet fill in as it plays
  const loadBoards = useCallback(() => {
    const signal = AbortSignal.timeout(15000)
    if (view === 'models') {
      fetch(`${API_URL}/arena/models`, { signal }).then(r => r.json())
        .then(d => setMods(d?.error ? null : { catalog: [], models: [], ...d })).catch(() => {})
    }
    // the rail's task pane wears each task's leader too, so the board is
    // fetched for either surface that shows it
    if (view === 'tasks' || pane === 'tasks') {
      fetch(`${API_URL}/arena/board/tasks`, { signal }).then(r => r.json())
        .then(d => setTaskRows(d.tasks || [])).catch(() => {})
    }
  }, [view, pane])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadBoards() }, [loadBoards, matches.length])
  // a round takes minutes and runs server-side, so the board refreshes itself
  useEffect(() => {
    const t = setInterval(() => { load(); loadBoards() }, status?.running ? 5000 : 20000)
    return () => clearInterval(t)
  }, [load, loadBoards, status?.running])

  useEffect(() => {
    if (!picked) { setCard(null); return }
    fetch(`${API_URL}/arena/agents/${encodeURIComponent(picked)}`)
      .then(r => r.json()).then(d => setCard(d?.error ? null : d)).catch(() => {})
  }, [picked, matches.length])

  useEffect(() => {
    if (!pickedModel) { setModelCard(null); return }
    fetch(`${API_URL}/arena/model?model=${encodeURIComponent(pickedModel)}`)
      .then(r => r.json()).then(d => setModelCard(d?.error ? null : d)).catch(() => {})
  }, [pickedModel, matches.length])

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

  // ── the openarena bridge ──────────────────────────────────────────
  const loadOa = useCallback(() => {
    setOaBusy(true)
    fetch(`${API_URL}/arena/openarena`, { signal: AbortSignal.timeout(20000) })
      .then(r => r.json())
      .then(d => setOa({ pool: [], entrants: [], ...d }))
      .catch(e => setOa({ available: false, base: '', enabled: true, pool: [], entrants: [],
                          error: e?.message || 'unreachable' }))
      .finally(() => setOaBusy(false))
  }, [])
  useEffect(() => { if (pane === 'openarena' && !oa) loadOa() }, [pane, oa, loadOa])

  const oaPost = async (path: string, body: any) => {
    setOaBusy(true); setErr(null)
    try {
      const r = await fetch(`${API_URL}/arena/openarena/${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, key: token }),
      }).then(x => x.json())
      if (r?.error) setErr(r.error)
      return r
    } catch (e: any) {
      setErr(e?.message || 'request failed')
    } finally {
      setOaBusy(false)
    }
  }

  const oaDelete = async (slug: string) => {
    setOaBusy(true); setErr(null)
    try {
      const r = await fetch(
        `${API_URL}/arena/openarena/tasks/${encodeURIComponent(slug)}` +
        (token ? `?key=${encodeURIComponent(token)}` : ''),
        { method: 'DELETE' }).then(x => x.json())
      if (r?.error) setErr(r.error)
    } catch (e: any) {
      setErr(e?.message || 'request failed')
    } finally {
      setOaBusy(false); loadOa(); load()
    }
  }

  const roundKeys = useMemo(() => new Set(status?.round_tasks || []), [status])
  // each task's board row, keyed for the rail — the leader chip reads off this
  const leadersByTask = useMemo(() => {
    const out: Record<string, TaskRow> = {}
    for (const t of taskRows) out[t.task] = t
    return out
  }, [taskRows])
  const suites = useMemo(() => {
    const out: Record<string, Task[]> = {}
    for (const t of tasks) (out[t.suite] ||= []).push(t)
    return out
  }, [tasks])

  const cfg = status?.config || {}
  const live = status?.running
  // writing a task or importing a benchmark is filed under an address
  const canWrite = !!token || isHost

  // ── header ────────────────────────────────────────────────────────
  //
  // One line: what this is, which of the three reads you're on, and the two
  // controls that change the board. Everything that is a *number* moved down
  // into the stat strip — a header that reports state and takes commands at
  // the same size reads as neither.
  const head = (
    <div className="border-b border-white/[0.06] px-4 py-2.5 flex items-center gap-4 flex-wrap shrink-0">
      <span className="text-[11px] uppercase tracking-[0.22em] text-emerald-300 shrink-0">arena</span>

      {/* one match log, three ways to read it: who won, what they ran on,
          what they were asked to do */}
      <div className="tab-strip gap-0.5 bg-white/[0.03] border border-white/[0.07] rounded-lg p-0.5">
        {(['agents', 'models', 'tasks'] as const).map(v => (
          <button key={v} onClick={() => setView(v)}
            className={`tab-btn px-2.5 py-1 rounded-md uppercase tracking-wider transition ${
              view === v ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-600 hover:text-gray-300'
            }`}>
            {v}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-3 ml-auto text-[10px] shrink-0">
        <span className={`flex items-center gap-1.5 ${status?.scheduler?.alive ? 'text-gray-500' : 'text-amber-400'}`}
          title={status?.scheduler?.last_error || status?.scheduler?.last_action
                 || 'the board runs itself: a new agent is qualified within a minute, and a round plays only what changed — a new agent, an edited task'}>
          <span className={`w-1.5 h-1.5 rounded-full ${
            status?.scheduler?.alive ? 'bg-emerald-400/70' : 'bg-amber-400'}`} />
          {status?.scheduler?.alive ? `auto · ${until(status?.next_round)}` : 'auto off'}
        </span>
        {isHost && (
          <>
            <button onClick={() => setShowConfig(v => !v)}
              className={`uppercase tracking-wider transition ${
                showConfig ? 'text-emerald-300' : 'text-gray-600 hover:text-gray-300'}`}>
              settings
            </button>
            {view === 'models' && (
              <button onClick={() => { setShowGauntlet(v => !v); setView('models') }}
                className={`uppercase tracking-wider transition ${
                  showGauntlet ? 'text-emerald-300' : 'text-gray-600 hover:text-gray-300'}`}>
                gauntlet
              </button>
            )}
            <button disabled={busy || !!live} onClick={() => post('run', {})}
              title="plays only what changed — new agents, edited tasks. a task's PLAY button in the rail replays it outright"
              className="lit-btn px-3 py-1.5 rounded-md uppercase tracking-wider text-[10px] disabled:opacity-40">
              {busy ? 'running…' : 'run round'}
            </button>
          </>
        )}
      </div>
    </div>
  )

  // ── the season, in numbers ────────────────────────────────────────
  //
  // The board's own state, above whichever read is open. When a match is
  // playing this strip is where it says so, because that is the one fact
  // that makes the rest of the page provisional.
  const rated = board.filter(r => r.matches > 0)
  const topScore = rated.length ? Math.max(...rated.map(r => r.avg_score)) : 0
  const statStrip = (
    <div className="shrink-0 border-b border-white/[0.06] flex items-stretch gap-1 flex-wrap px-3 py-1.5">
      <Stat n={status?.season ?? 0} k="season" />
      <Stat n={view === 'models' ? (mods?.models?.length ?? 0) : (status?.subjects?.length ?? 0)}
        k={view === 'models' ? 'models' : 'agents'} />
      <Stat n={(status?.matches ?? 0).toLocaleString()} k="matches" />
      <Stat n={status?.tasks ?? 0} k="tasks in pool" />
      <Stat n={pct(topScore)} k="best score" tint="text-emerald-300"
        title="the leader's average across every task it has played" />
      <Stat n={status?.round_tasks?.length ?? 0} k="this rotation"
        title={(status?.round_tasks || []).join('\n')} />

      <div className="ml-auto flex items-center pr-1 min-w-0">
        {live ? (
          <span className="flex items-center gap-2 text-[10px] text-emerald-200 bg-emerald-500/10 border border-emerald-500/25 rounded-lg px-2.5 py-1.5 min-w-0">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse shrink-0" />
            <span className="truncate">playing · {live.agent} · {live.task}</span>
          </span>
        ) : (
          <span className="text-[10px] text-gray-600 max-w-[280px] text-right leading-relaxed">
            0.7 correctness · 0.2 reliability · 0.1 unspent budget, rated pairwise
          </span>
        )}
      </div>
    </div>
  )

  // ── the podium ────────────────────────────────────────────────────
  //
  // Elo is a ranking, and a ranking has a front. The top three get the room
  // to be read at a glance; everyone else is a row.
  const podium = view === 'agents' && board.length >= 3 && (
    <div className="shrink-0 grid grid-cols-3 gap-2 px-3 py-3">
      {board.slice(0, 3).map(r => (
        <button key={r.agent} onClick={() => setPicked(picked === r.agent ? null : r.agent)}
          className={`card card-hover ${picked === r.agent ? 'card-on' : ''} p-3 text-left flex flex-col gap-2 min-w-0`}>
          <div className="flex items-center gap-2 min-w-0">
            <span className={`rank rank--${r.rank}`}>{r.rank}</span>
            <Glyph icon={r.icon} />
            <span className="text-xs text-gray-100 truncate flex-1">{r.agent}</span>
            <span className="text-[13px] tabular-nums text-emerald-300">{r.elo.toFixed(0)}</span>
          </div>
          <ScoreBar value={r.avg_score} />
          <div className="flex items-center gap-2 text-[9px] text-gray-600 tabular-nums">
            <span className="text-gray-400">{pct(r.avg_score)}</span>
            <span>{r.wins}–{r.losses}–{r.draws}</span>
            <span>{r.matches} matches</span>
            <span className="ml-auto">{ago(r.last)}</span>
          </div>
        </button>
      ))}
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
        // how much of openarena's catalogue joins the pool, and what a program
        // task gets to spend — write-then-check needs more than a trace task
        // zero is a real answer for this one — "no cap, the whole catalogue"
        { k: 'openarena_tasks', label: 'openarena tasks', step: '4', zero: true },
        { k: 'openarena_steps', label: 'program budget', step: '1' },
      ].map(f => (
        <label key={f.k} className="flex flex-col gap-1">
          <span className="uppercase tracking-wider text-gray-600">{f.label}</span>
          <input type="number" step={f.step} min={(f as any).zero ? 0 : 1}
            defaultValue={cfg[f.k]}
            title={(f as any).zero ? '0 = every task openarena holds' : undefined}
            onBlur={e => {
              const v = Number(e.target.value)
              if (Number.isNaN(v) || v === cfg[f.k]) return
              if (v || (f as any).zero) post('config', { [f.k]: v })
            }}
            className="w-24 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-200 outline-none focus:border-emerald-500/40 transition" />
        </label>
      ))}
      {[
        { k: 'enabled', label: 'board on' },
        { k: 'free', label: 'free models only' },
        { k: 'replay', label: 'replay unchanged tasks',
          title: 'off = a round only plays what changed: new agents, edited tasks, voided pairs. on = the whole field replays every round.' },
        { k: 'harnesses', label: 'CLI agents compete' },
        { k: 'openarena', label: 'openarena tasks play' },
      ].map(f => (
        <label key={f.k} title={(f as any).title}
          className="flex items-center gap-1.5 cursor-pointer pb-1">
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
        <Empty title="no matches yet"
          body="the first round runs on its own — a new agent is qualified within a minute of being created. after that, a round only replays a task when it changed or someone new joined."
          hint={isHost ? 'or start one now with RUN ROUND' : undefined} />
      ) : (
        <table className="board text-[11px]">
          <thead>
            <tr>
              {['#', 'agent', 'elo', 'score', 'w–l–d', 'matches', 'avg s', 'spent', 'last'].map(h => (
                <th key={h} className={h === 'agent' ? 'text-left' : 'text-right'}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {board.map(r => (
              <tr key={r.agent}
                onClick={() => setPicked(picked === r.agent ? null : r.agent)}
                className={`row-pick ${picked === r.agent ? 'row-on' : ''} ${r.active ? '' : 'opacity-45'}`}>
                {/* the podium is colour, not emoji — the pixel skin's font has
                    no medals and renders them as tofu */}
                <td className="text-right w-10">
                  <span className={r.rank <= 3 ? `rank rank--${r.rank}` : 'rank'}>{r.rank}</span>
                </td>
                <td>
                  <div className="flex items-center gap-2 min-w-0">
                    <Glyph icon={r.icon} />
                    <span className="text-gray-100 truncate">{r.agent}</span>
                    {!r.active && (
                      <span className="text-[9px] text-gray-600 uppercase tracking-wider shrink-0">retired</span>
                    )}
                  </div>
                </td>
                <td className="text-right tabular-nums text-emerald-300 text-[12px]">{r.elo.toFixed(0)}</td>
                <td className="text-right w-28"><Score value={r.avg_score} /></td>
                <td className="text-right tabular-nums text-gray-500">
                  {r.wins}–{r.losses}–{r.draws}
                </td>
                <td className="text-right tabular-nums text-gray-500"
                  title={r.voids ? `${r.voids} match(es) the provider voided — not rated` : undefined}>
                  {r.matches}
                  {r.voids > 0 && <span className="text-amber-400/70 text-[9px] ml-1">+{r.voids}</span>}
                </td>
                <td className="text-right tabular-nums text-gray-600">{r.avg_seconds.toFixed(1)}</td>
                <td className="text-right tabular-nums text-gray-600"
                  title={r.avg_tokens ? `${r.avg_tokens.toLocaleString()} tokens per match` : undefined}>
                  {r.cost ? `$${r.cost.toFixed(4)}` : toks(r.tokens) || '—'}
                </td>
                <td className="text-right text-gray-600">{ago(r.last)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {board.length > 0 && !picked && (
        <div className="px-3 py-3 text-center text-[10px] text-gray-700">
          pick an agent for its score on every task it has played
        </div>
      )}
    </div>
  )

  // ── the model board ───────────────────────────────────────────────
  //
  // Same matches, keyed on what was underneath the agent. Latency is here in
  // two columns because they answer different questions: total seconds is what
  // a round costs in wall clock, seconds per step is the only one that
  // compares a 3-step task against a 12-step one.
  const modelBoard = (
    <div className="flex-1 min-w-0 overflow-y-auto no-scrollbar">
      {(mods?.models?.length ?? 0) === 0 ? (
        <Empty title="no model has played"
          body="every match also ran on a model, and this board is that read of the same log — score, latency, tokens per second and spend, keyed on what was underneath the agent."
          hint={isHost ? 'run a round, or a GAUNTLET to rank several against each other' : undefined} />
      ) : (
        <>
          <div className="px-3 py-2 text-[10px] text-gray-600 flex items-center gap-3 flex-wrap border-b border-white/[0.05]">
            <span className={mods!.rated ? 'text-gray-500' : 'text-amber-400/80'}>
              {mods!.rated
                ? `${mods!.rated} rated head-to-head`
                : 'nothing rated yet — a rating needs two models on the same task, same agent'}
            </span>
            <span className="ml-auto">
              rounds run on {mods!.free ? 'free models' : mods!.round_model || "each agent's own model"}
            </span>
          </div>
          <table className="board text-[11px]">
            <thead>
              <tr>
                {['#', 'model', 'elo', 'score', 'pass', 'w–l–d', 'matches', 'avg s', 's/step', 'tok/s', 'spent'].map(h => (
                  <th key={h} className={h === 'model' ? 'text-left' : 'text-right'}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {mods!.models.map(r => (
                <tr key={r.model}
                  onClick={() => setPickedModel(pickedModel === r.model ? null : r.model)}
                  className={`row-pick ${pickedModel === r.model ? 'row-on' : ''}`}>
                  <td className="text-right w-10">
                    <span className={r.rated && r.rank <= 3 ? `rank rank--${r.rank}` : 'rank'}>{r.rank}</span>
                  </td>
                  <td className="max-w-[260px]">
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-gray-100 truncate" title={r.model}>{r.model}</span>
                      {r.free && <span className="text-[9px] text-emerald-400/70 uppercase tracking-wider">free</span>}
                    </div>
                    <div className="text-[9px] text-gray-600 truncate mt-0.5">
                      {r.provider ? `${r.provider} · ` : ''}{r.tasks.length} tasks · {r.agents.length} agents
                    </div>
                  </td>
                  <td className="text-right tabular-nums">
                    {r.rated
                      ? <span className="text-emerald-300 text-[12px]">{r.elo.toFixed(0)}</span>
                      : <span className="text-gray-600 text-[10px] uppercase tracking-wider" title="never met another model on the same task under the same agent — run a gauntlet">unrated</span>}
                  </td>
                  <td className="text-right w-28"><Score value={r.avg_score} /></td>
                  <td className="text-right tabular-nums text-gray-500"
                    title="matches where every check passed">{pct(r.pass_rate)}</td>
                  <td className="text-right tabular-nums text-gray-500">
                    {r.wins}–{r.losses}–{r.draws}
                  </td>
                  <td className="text-right tabular-nums text-gray-500"
                    title={r.voids ? `${r.voids} voided — provider failed, not rated` : undefined}>
                    {r.matches}{r.voids > 0 && <span className="text-amber-400/70 text-[9px] ml-1">+{r.voids}</span>}
                  </td>
                  <td className="text-right tabular-nums text-gray-600"
                    title={`median ${r.p50_seconds}s · slowest ${r.max_seconds}s`}>
                    {r.avg_seconds.toFixed(1)}
                  </td>
                  <td className="text-right tabular-nums text-gray-600">
                    {r.sec_per_step ? r.sec_per_step.toFixed(1) : '—'}
                  </td>
                  <td className="text-right tabular-nums text-gray-600"
                    title="tokens the runs reported over the seconds they took">
                    {r.tok_per_sec ? r.tok_per_sec.toFixed(0) : '—'}
                  </td>
                  <td className="text-right tabular-nums text-gray-600"
                    title={r.cost_per_point ? `$${r.cost_per_point.toFixed(4)} per point of score` : `${r.avg_tokens.toLocaleString()} tokens per match`}>
                    {r.cost ? `$${r.cost.toFixed(4)}` : toks(r.tokens) || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )

  // ── the task board ────────────────────────────────────────────────
  //
  // Hardest first, with each task's LEADER — the agent with the best standing
  // score on it — right on the row. Opening a task shows the whole agent
  // ranking for it, then the models that played it. SPREAD is still the
  // column to judge a task pool by: a task every model scores the same on
  // ranks nobody.
  const taskBoard = (
    <div className="flex-1 min-w-0 overflow-y-auto no-scrollbar">
      {taskRows.length === 0 ? (
        <Empty title="nothing has been played"
          body="this read lists every task with its leader — the agent holding the best score on it. open a task for the full agent ranking, and the models underneath."
          hint="the task pool is in the rail on the right" />
      ) : (
        <table className="board text-[11px]">
          <thead>
            <tr>
              {['task', 'leader', 'avg score', 'pass', 'spread', 'matches', 'avg s'].map(h => (
                <th key={h} className={['task', 'leader'].includes(h) ? 'text-left' : 'text-right'}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {taskRows.map(t => (
              <Fragment key={t.task}>
                <tr onClick={() => setOpenTask(openTask === t.task ? null : t.task)}
                  className={`row-pick ${openTask === t.task ? 'row-on' : ''}`}>
                  <td className="max-w-[300px]">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className={`text-gray-600 transition-transform shrink-0 ${
                        openTask === t.task ? 'rotate-90' : ''}`}>›</span>
                      <div className="min-w-0">
                        <div className="text-gray-100 truncate" title={t.title}>{t.title}</div>
                        <div className="text-[9px] text-gray-600 truncate mt-0.5">
                          {t.task}{roundKeys.has(t.task) ? ' · this round' : ''}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="max-w-[160px]"
                    title={t.leader ? `${t.leader} holds the best score on this task: ${pct(t.leader_score)}` : 'no rated score on this task yet'}>
                    {t.leader ? (
                      <span className="flex items-center gap-1.5 min-w-0">
                        <Glyph icon={t.leader_icon || '>_'} />
                        <span className="text-emerald-200 truncate">{t.leader}</span>
                        <span className="text-gray-500 tabular-nums shrink-0">{pct(t.leader_score)}</span>
                      </span>
                    ) : (
                      <span className="text-gray-700 text-[10px] uppercase tracking-wider">
                        {t.unplayed ? 'not played yet' : '—'}
                      </span>
                    )}
                  </td>
                  <td className="text-right w-28"><Score value={t.avg_score} /></td>
                  <td className="text-right tabular-nums text-gray-500">{pct(t.pass_rate)}</td>
                  <td className="text-right tabular-nums"
                    title="best agent minus worst on this task — 0 means it separates nobody">
                    <span className={(t.agent_spread || t.spread) > 0.15 ? 'text-emerald-300' : 'text-gray-600'}>
                      {(t.agents?.length ?? 0) > 1 ? pct(t.agent_spread)
                        : t.models.length > 1 ? pct(t.spread) : '—'}
                    </span>
                  </td>
                  <td className="text-right tabular-nums text-gray-500">{t.matches}</td>
                  <td className="text-right tabular-nums text-gray-600">{t.avg_seconds.toFixed(1)}</td>
                </tr>
                {openTask === t.task && (t.agents || []).map((a, i) => (
                  <tr key={`${t.task}:agent:${a.agent}`} className="bg-white/[0.02] text-[10px]">
                    <td className="pl-8">
                      <span className="flex items-center gap-1.5 min-w-0">
                        <span className="text-gray-600 tabular-nums w-4 shrink-0">{i + 1}</span>
                        <Glyph icon={a.icon} />
                        <span className={`truncate ${i === 0 ? 'text-emerald-200' : 'text-gray-300'}`}>{a.agent}</span>
                      </span>
                    </td>
                    <td className="text-gray-600 text-[9px] uppercase tracking-wider">agent</td>
                    <td className="text-right"><Score value={a.last} /></td>
                    <td className="text-right tabular-nums text-gray-600" title="best score it ever posted here">
                      {pct(a.best)}
                    </td>
                    <td />
                    <td className="text-right tabular-nums text-gray-500">{a.n}</td>
                    <td className="text-right tabular-nums text-gray-600">{ago(a.ts)}</td>
                  </tr>
                ))}
                {openTask === t.task && t.models.map(m => (
                  <tr key={`${t.task}:${m.model}`} className="bg-white/[0.02] text-[10px]">
                    <td className="pl-8 text-gray-400 truncate" title={`agents: ${m.agents.join(', ')}`}>
                      {m.model}
                    </td>
                    <td className="text-gray-600 text-[9px] uppercase tracking-wider">model</td>
                    <td className="text-right"><Score value={m.score} /></td>
                    <td className="text-right tabular-nums text-gray-500">{pct(m.pass_rate)}</td>
                    <td className="text-right text-gray-600 truncate">{m.agents.join(', ')}</td>
                    <td className="text-right tabular-nums text-gray-500">{m.n}</td>
                    <td className="text-right tabular-nums text-gray-600">{m.seconds.toFixed(1)}</td>
                  </tr>
                ))}
                {openTask === t.task && (t.agents?.length ?? 0) === 0 && t.models.length === 0 && (
                  <tr className="bg-white/[0.02] text-[10px]">
                    <td colSpan={7} className="pl-8 text-gray-600">
                      in the pool, never played — it joins the rotation, or PLAY it from the rail
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )

  // ── what the numbers mean ─────────────────────────────────────────
  //
  // A board of bare percentages invites the wrong reading, and the right one
  // is one line long. It sits at the foot of whichever read is open, out of
  // the way of the rows and never off the bottom of a scroll.
  const boardFoot = (
    <div className="shrink-0 border-t border-white/[0.06] px-3 py-2 flex items-center gap-4 flex-wrap text-[9px] text-gray-600">
      {view === 'agents' ? (
        <>
          {BARS.map(b => (
            <span key={b.key} className="flex items-center gap-1.5">
              <span className={`w-4 h-1 rounded-full ${b.tint}`} />
              <span className="uppercase tracking-wider">{b.label}</span>
              <span className="text-gray-700">
                {b.key === 'correct' ? '×0.7' : b.key === 'reliable' ? '×0.2' : '×0.1'}
              </span>
            </span>
          ))}
          <span className="ml-auto">
            scored on the trace and the files left behind — never an LLM judge. Elo is pairwise, per task.
          </span>
        </>
      ) : view === 'models' ? (
        <span className="ml-auto">
          rating is controlled: same season, same task, same agent. No such pairing = unrated, not ranked.
        </span>
      ) : (
        <span className="ml-auto">
          leader = the agent holding the best score on that task. spread is best minus worst — the column that says whether a task ranks anybody.
        </span>
      )}
    </div>
  )

  // ── the gauntlet (host) ───────────────────────────────────────────
  //
  // The only round on the board that names its model, and so the only one that
  // can spend money — everything else runs FREE MODE by config. One agent, one
  // task set, every checked model in turn: the model is the only thing that
  // moves, which is what makes the Elo above mean anything.
  const paidPicked = (gauntlet.models || []).some(
    id => !(mods?.catalog || []).find(c => c.model === id)?.free)
  const gauntletPanel = showGauntlet && isHost && view === 'models' && (
    <div className="border-b border-white/[0.06] px-4 py-3 bg-white/[0.02] space-y-2 text-[10px]">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-1.5">
          <span className="uppercase tracking-wider text-gray-600">agent</span>
          <select value={gauntlet.agent} onChange={e => setGauntlet(g => ({ ...g, agent: e.target.value }))}
            className="bg-white/[0.04] border border-white/[0.08] rounded-md px-1.5 py-1 text-gray-300 outline-none focus:border-emerald-500/40">
            <option value="">first eligible</option>
            {(mods?.agents || status?.subjects || []).map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <span className="uppercase tracking-wider text-gray-600">task</span>
          <select value={gauntlet.task} onChange={e => setGauntlet(g => ({ ...g, task: e.target.value }))}
            className="max-w-[260px] bg-white/[0.04] border border-white/[0.08] rounded-md px-1.5 py-1 text-gray-300 outline-none focus:border-emerald-500/40">
            <option value="">this season's rotation ({status?.round_tasks?.length ?? 0})</option>
            {(mods?.tasks || []).map(t => <option key={t.key} value={t.key}>{t.title}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <span className="uppercase tracking-wider text-gray-600">steps</span>
          <input type="number" min={0} value={gauntlet.steps || ''} placeholder={String(cfg.steps || 8)}
            onChange={e => setGauntlet(g => ({ ...g, steps: Number(e.target.value) }))}
            className="w-16 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-200 tabular-nums outline-none focus:border-emerald-500/40" />
        </label>
        <span className="text-gray-600">
          {gauntlet.models.length} models × {gauntlet.task ? 1 : (status?.round_tasks?.length ?? 0)} tasks
          {' = '}{gauntlet.models.length * (gauntlet.task ? 1 : (status?.round_tasks?.length ?? 0))} matches
        </span>
        <button
          disabled={busy || !!live || gauntlet.models.length < 2}
          onClick={() => post('gauntlet', {
            models: gauntlet.models.map(id => ({
              model: id,
              provider: (mods?.catalog || []).find(c => c.model === id)?.provider,
            })),
            agent: gauntlet.agent || undefined,
            tasks: gauntlet.task ? [gauntlet.task] : undefined,
            steps: gauntlet.steps || undefined,
          }).then(() => { setMods(null); loadBoards() })}
          className="lit-btn ml-auto px-2.5 py-1 rounded-md uppercase tracking-wider disabled:opacity-40">
          {busy ? 'playing…' : 'run gauntlet'}
        </button>
      </div>

      {/* one provider's catalog at a time, and a filter over it: the LFM
          runtimes alone are a hundred repos, and a picker you have to scroll
          past is a picker nobody reads */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {Array.from(new Set((mods?.catalog || []).map(c => c.provider))).map(p => (
          <button key={p} onClick={() => setGauntlet(g => ({ ...g, provider: p }))}
            className={`px-1.5 py-0.5 rounded-md uppercase tracking-wider transition ${
              gauntlet.provider === p ? 'text-emerald-300' : 'text-gray-600 hover:text-gray-300'
            }`}>
            {p}
          </button>
        ))}
        <input value={gauntlet.filter} placeholder="filter…"
          onChange={e => setGauntlet(g => ({ ...g, filter: e.target.value }))}
          className="w-32 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-0.5 text-gray-300 outline-none focus:border-emerald-500/40" />
        {gauntlet.models.length > 0 && (
          <button onClick={() => setGauntlet(g => ({ ...g, models: [] }))}
            className="uppercase tracking-wider text-gray-600 hover:text-gray-300 transition">
            clear {gauntlet.models.length}
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-1 max-h-24 overflow-y-auto no-scrollbar">
        {(mods?.catalog || [])
          // whatever is already picked stays visible, whichever tab is open —
          // otherwise a filter looks like it dropped your selection
          .filter(c => (gauntlet.models.includes(c.model)
                        || (c.provider === gauntlet.provider
                            && c.model.toLowerCase().includes(gauntlet.filter.toLowerCase()))))
          .map(c => {
            const on = gauntlet.models.includes(c.model)
            return (
              <button key={`${c.provider}:${c.model}`} disabled={!c.ready}
                title={c.ready ? (c.hint || c.provider) : `${c.provider} has no key — set one in the Builder`}
                onClick={() => setGauntlet(g => ({
                  ...g,
                  models: on ? g.models.filter(m => m !== c.model) : [...g.models, c.model],
                }))}
                className={`px-1.5 py-0.5 rounded-md border transition disabled:opacity-30 ${
                  on ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-200'
                     : 'border-white/[0.08] text-gray-500 hover:text-gray-300'
                }`}>
                {c.model}
                {c.free && <span className="ml-1 text-[9px] text-emerald-400/70">free</span>}
              </button>
            )
          })}
      </div>

      <div className={`leading-relaxed ${paidPicked ? 'text-amber-400/80' : 'text-gray-600'}`}>
        {gauntlet.models.length < 2
          ? 'pick at least two — one model playing alone is a round, not a comparison'
          : paidPicked
            ? 'a named model is not FREE MODE: the paid ones here bill the host\'s provider key for every match'
            : 'every model picked is zero-cost — this ranks them for nothing but time'}
      </div>
    </div>
  )

  // ── model card ────────────────────────────────────────────────────
  const modelCardPane = modelCard && view === 'models' && (
    <div className="border-t border-white/[0.06] bg-surface-1 shrink-0 max-h-[44%] overflow-y-auto no-scrollbar">
      <div className="px-3 py-2 flex items-center gap-2 flex-wrap border-b border-white/[0.05] sticky top-0 bg-surface-1 z-10">
        <span className="text-xs text-gray-100 truncate">{modelCard.model}</span>
        {modelCard.provider && <span className="text-[10px] text-gray-600">{modelCard.provider}</span>}
        <span className="text-[12px] text-emerald-300 tabular-nums">
          {modelCard.rated ? modelCard.elo.toFixed(0) : 'unrated'}
        </span>
        <span className="text-[10px] text-gray-600">
          {modelCard.matches} matches · {pct(modelCard.avg_score)} score · {pct(modelCard.pass_rate)} pass ·
          {' '}{modelCard.avg_seconds.toFixed(1)}s avg
          {modelCard.tok_per_sec ? ` · ${modelCard.tok_per_sec.toFixed(0)} tok/s` : ''}
          {modelCard.cost ? ` · $${modelCard.cost.toFixed(4)}` : ''}
        </span>
        <button onClick={() => setPickedModel(null)}
          className="ml-auto text-[10px] uppercase tracking-wider text-gray-600 hover:text-gray-300 transition">
          close
        </button>
      </div>

      <div className="p-3 space-y-3">
        {modelCard.opponents.length > 0 && (
          <div className="flex flex-wrap gap-1.5 text-[10px]">
            {modelCard.opponents.map(o => (
              <span key={o.model} className="card px-2 py-1 text-gray-600">
                vs <span className="text-gray-400">{o.model}</span>{' '}
                <span className="tabular-nums text-gray-300">{o.record}</span>
              </span>
            ))}
          </div>
        )}

        <div className="grid gap-x-5 gap-y-2.5"
          style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
          {modelCard.per_task.map(t => (
            <div key={t.task} className="space-y-1.5">
              <div className="flex items-baseline gap-2">
                <span className="text-[10px] text-gray-400 truncate flex-1" title={t.task}>{t.title}</span>
                <span className="text-[9px] text-gray-700 tabular-nums">{t.seconds}s</span>
                <span className="text-[10px] tabular-nums text-gray-300">{pct(t.last)}</span>
              </div>
              <ScoreBar value={t.last} />
            </div>
          ))}
        </div>
      </div>
    </div>
  )

  // ── agent card ────────────────────────────────────────────────────
  //
  // What one agent's rank is made of: its number on every task it has played,
  // so a high Elo with one weak task is visible as that rather than as a rank.
  const agentCard = card && (
    <div className="border-t border-white/[0.06] bg-surface-1 shrink-0 max-h-[40%] overflow-y-auto no-scrollbar">
      <div className="px-3 py-2 flex items-center gap-2 border-b border-white/[0.05] sticky top-0 bg-surface-1 z-10">
        <Glyph icon={card.icon} />
        <span className="text-xs text-gray-100">{card.agent}</span>
        <span className="text-[12px] text-emerald-300 tabular-nums">{card.elo?.toFixed(0)}</span>
        <span className="text-[10px] text-gray-600">
          {card.matches} matches · {pct(card.win_rate)} win rate · {pct(card.avg_score)} score
        </span>
        {isHost && (
          <button disabled={busy || !!live} onClick={() => post('run', { agent: card.agent })}
            className="ml-auto text-[10px] uppercase tracking-wider text-gray-600 hover:text-emerald-300 transition disabled:opacity-40">
            re-run
          </button>
        )}
        <button onClick={() => setPicked(null)}
          className={`text-[10px] uppercase tracking-wider text-gray-600 hover:text-gray-300 transition ${isHost ? '' : 'ml-auto'}`}>
          close
        </button>
      </div>
      <div className="p-3 grid gap-x-5 gap-y-2.5"
        style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
        {card.per_task.map(t => (
          <div key={t.task} className="space-y-1.5">
            <div className="flex items-baseline gap-2">
              <span className="text-[10px] text-gray-400 truncate flex-1" title={t.task}>{t.title}</span>
              <span className="text-[9px] text-gray-700 tabular-nums">{t.n}×</span>
              <span className="text-[10px] tabular-nums text-gray-300">{pct(t.last)}</span>
            </div>
            <ScoreBar value={t.last} />
          </div>
        ))}
      </div>
    </div>
  )

  // ── the openarena pane ────────────────────────────────────────────
  //
  // Two directions in one pane: their tasks on our board (the pool, and the
  // benchmark importer that fills it) and our agents on theirs (the entrants).
  const oaPane = (
    <div className="space-y-2">
      {!oa && (
        <div className="p-6 text-center text-[10px] text-gray-600">
          {oaBusy ? 'asking openarena…' : 'no answer yet'}
        </div>
      )}

      {oa && !oa.available && (
        <div className="border border-amber-500/20 bg-amber-500/[0.06] rounded-lg p-2.5 space-y-1">
          <div className="text-[10px] text-amber-300 uppercase tracking-wider">openarena is not answering</div>
          <div className="text-[10px] text-gray-500 leading-relaxed">
            Program-graded tasks need the openarena module up at{' '}
            <span className="text-gray-400">{oa.base || 'its port'}</span> — start it with{' '}
            <span className="text-gray-400">m openarena/serve</span>. Nothing else on the board is
            affected: a judge that is down means no openarena tasks this round, and any match that
            was mid-grade is voided rather than lost.
          </div>
          {oa.error && <div className="text-[9px] text-gray-600 truncate" title={oa.error}>{oa.error}</div>}
          <button onClick={loadOa} disabled={oaBusy}
            className="text-[9px] uppercase tracking-wider text-gray-500 hover:text-gray-300 transition disabled:opacity-40">
            retry
          </button>
        </div>
      )}

      {oa?.available && (
        <>
          <div className="px-1 space-y-1">
            <div className="flex items-center gap-2 text-[9px]">
              <span className="text-emerald-300/80 uppercase tracking-wider">bridged</span>
              <span className="text-gray-600">v{oa.version}</span>
              <button onClick={loadOa} disabled={oaBusy}
                className="ml-auto uppercase tracking-wider text-gray-600 hover:text-gray-300 transition disabled:opacity-40">
                {oaBusy ? '…' : 'refresh'}
              </button>
            </div>
            <div className="text-[9px] text-gray-600">
              {oa.tasks} tasks there · {oa.pool.length} in our pool{oa.limit ? ` (cap ${oa.limit})` : ''}
            </div>
          </div>

          {!cfg.openarena && (
            <div className="text-[9px] text-amber-400/80 px-1">
              these tasks are switched off in settings — they are listed, not played
            </div>
          )}

          {/* benchmarks off the web: preview converts and keeps nothing */}
          <div className="card p-2 space-y-2">
            <div className="section-label">import a benchmark</div>
            <div className="flex items-center gap-1.5">
              <select value={bench.source}
                onChange={e => { setBench(b => ({ ...b, source: e.target.value })); setPreview(null) }}
                className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-md px-1.5 py-1 text-[10px] text-gray-300 outline-none focus:border-emerald-500/40">
                {['humaneval', 'humanevalplus', 'mbpp', 'code_contests'].map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <input type="number" min={1} max={200} value={bench.limit}
                onChange={e => setBench(b => ({ ...b, limit: Number(e.target.value) }))}
                title="how many records to take"
                className="w-14 bg-white/[0.04] border border-white/[0.08] rounded-md px-1.5 py-1 text-[10px] text-gray-300 tabular-nums outline-none focus:border-emerald-500/40" />
              <input type="number" min={0} value={bench.offset}
                onChange={e => setBench(b => ({ ...b, offset: Number(e.target.value) }))}
                title="where to start — how you take a benchmark a page at a time"
                className="w-14 bg-white/[0.04] border border-white/[0.08] rounded-md px-1.5 py-1 text-[10px] text-gray-300 tabular-nums outline-none focus:border-emerald-500/40" />
            </div>
            <div className="flex items-center gap-2">
              <button disabled={oaBusy || !canWrite}
                onClick={async () => setPreview(await oaPost('import', { ...bench, preview: true }))}
                className="text-[9px] uppercase tracking-wider text-gray-500 hover:text-gray-200 transition disabled:opacity-40">
                preview
              </button>
              <button disabled={oaBusy || !canWrite}
                onClick={async () => { await oaPost('import', bench); setPreview(null); loadOa(); load() }}
                className="lit-btn px-2 py-1 rounded-md text-[9px] uppercase tracking-wider disabled:opacity-40">
                {oaBusy ? 'working…' : 'import'}
              </button>
            </div>
            <div className="text-[9px] text-gray-600 leading-relaxed">
              {canWrite ? 'preview first — an unread import is unread tasks' : 'sign in to import'}
            </div>
            {preview?.tasks && (
              <div className="space-y-1 pt-1 border-t border-white/[0.06]">
                <div className="text-[9px] text-gray-600">
                  {preview.count ?? preview.tasks.length} converted, none kept
                </div>
                {preview.tasks.slice(0, 4).map((t: any, i: number) => (
                  <div key={i} className="text-[9px] text-gray-500 truncate">
                    {t.title || t.slug} · {(t.tests || []).length} cases
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* their tasks, on our board */}
          <div className="section-label px-1 pt-2">tasks</div>
          {oa.pool.length === 0 && (
            <div className="text-[10px] text-gray-600 px-1">
              openarena holds no tasks yet — import a benchmark above, or write one in AGENTS ▸ TASK
            </div>
          )}
          {oa.pool.map(t => (
            <div key={t.key} className={`card card-hover p-2 ${roundKeys.has(t.key) ? 'card-on' : ''}`}>
              <div className="flex items-baseline gap-2">
                <span className="text-[10px] text-gray-200 flex-1 truncate" title={t.slug}>{t.title}</span>
                {roundKeys.has(t.key) && (
                  <span className="text-[9px] uppercase tracking-wider text-emerald-300">this round</span>
                )}
                {isHost && (
                  <button disabled={busy || !!live} onClick={() => post('run', { task: t.key, force: true })}
                    className="text-[9px] uppercase tracking-wider text-gray-600 hover:text-emerald-300 transition disabled:opacity-40">
                    play
                  </button>
                )}
                <button disabled={oaBusy} onClick={() => oaDelete(t.slug)}
                  title="delete it in openarena — its author, or the host"
                  className="text-[9px] uppercase tracking-wider text-gray-600 hover:text-red-400 transition disabled:opacity-40">
                  del
                </button>
              </div>
              <div className="flex items-center gap-2 text-[9px] text-gray-600 mt-0.5">
                <span className="text-gray-500">{t.mode}</span>
                <span>{t.language}</span>
                <span title={`${t.hidden} of them hidden — graded, never shown`}>
                  {t.cases} cases{t.hidden ? ` · ${t.hidden} hidden` : ''}
                </span>
                <span>{t.steps || cfg.openarena_steps || 10} steps</span>
                {(t.tags || []).slice(0, 2).map(tag => <span key={tag}>#{tag}</span>)}
              </div>
            </div>
          ))}

          {/* our agents, on their board */}
          <div className="section-label px-1 pt-3">entered over there</div>
          {oa.entrants.length === 0 && (
            <div className="text-[10px] text-gray-600 px-1">nobody yet</div>
          )}
          {oa.entrants.map(e => (
            <div key={e.id} className="flex items-baseline gap-2 px-1 text-[10px]">
              <span className="text-gray-300 truncate flex-1">{e.name}</span>
              <span className="text-gray-600">{e.kind}</span>
              {e.elo != null && <span className="text-emerald-300/80 tabular-nums">{Math.round(e.elo)}</span>}
              <span className="text-gray-600 tabular-nums">{e.attempts || 0} tries</span>
            </div>
          ))}
          {isHost && (
            <div className="flex items-center gap-1.5 pt-1">
              <select id="oa-enter" defaultValue=""
                className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-md px-1.5 py-1 text-[10px] text-gray-300 outline-none focus:border-emerald-500/40">
                <option value="">enter an agent there…</option>
                {(status?.subjects || []).map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <button disabled={oaBusy}
                onClick={async () => {
                  const el = document.getElementById('oa-enter') as HTMLSelectElement | null
                  if (!el?.value) return
                  await oaPost('enter', { agent: el.value, free: !!cfg.free })
                  el.value = ''
                  loadOa()
                }}
                className="text-[9px] uppercase tracking-wider text-gray-500 hover:text-emerald-300 transition disabled:opacity-40">
                enter
              </button>
            </div>
          )}
          <div className="text-[9px] text-gray-600 px-1 pb-2 leading-relaxed">
            An entrant over there is played by calling this module's /run, which spends the host's
            provider key — so entering one is the host's call, and it plays on{' '}
            {cfg.free ? 'free models' : 'the board\'s configured model'}.
          </div>
        </>
      )}
    </div>
  )

  // ── matches / tasks / openarena rail ──────────────────────────────
  //
  // A void is a match the provider failed, not the agent — it stays on the
  // record and out of the rating, so it is drawn as one quiet line rather
  // than a full card. A rate-limited afternoon should not look like a
  // leaderboard full of red.
  const shown = matches.filter(m => (view !== 'agents' || !picked || m.agent === picked)
                                 && (view !== 'models' || !pickedModel || m.model === pickedModel))
  const filterOn = (view === 'agents' && picked) || (view === 'models' && pickedModel)
  const voidCount = shown.filter(m => m.void).length
  const feed = (
    <div className="w-[360px] shrink-0 border-l border-white/[0.06] flex flex-col min-h-0 bg-surface-1">
      {/* the count goes under the tabs, not beside them: three pixel-face
          labels already fill this rail's width, and a count squeezed onto
          the same line is the thing that gets clipped */}
      <div className="border-b border-white/[0.06] shrink-0">
        <div className="tab-strip gap-0.5 px-2 pt-2">
          {(['matches', 'tasks', 'openarena'] as const).map(p => (
            <button key={p} onClick={() => setPane(p)}
              className={`tab-btn px-2 py-1 rounded-md uppercase tracking-wider transition ${
                pane === p ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-600 hover:text-gray-300'
              }`}>
              {p}
            </button>
          ))}
        </div>
        <div className="px-3 pb-1.5 pt-1 text-[10px] text-gray-600 truncate">
          {pane === 'matches' ? (
            <>
              {shown.length} recent
              {voidCount > 0 && (
                <span className="text-amber-400/70 ml-1"
                  title="the provider failed on these — kept on the record, out of the rating">
                  · {voidCount} void
                </span>
              )}
            </>
          ) : pane === 'tasks' ? `${tasks.length} in the pool, across ${Object.keys(suites).length} suites`
            : oa?.available ? `${oa.pool.length} program-graded tasks` : 'the openarena bridge'}
        </div>
      </div>

      {/* the rail follows whatever is selected on the board, and says so —
          an unexplained filter reads as a rail that lost most of its rows */}
      {pane === 'matches' && filterOn && (
        <button onClick={() => { setPicked(null); setPickedModel(null) }}
          className="shrink-0 px-3 py-1.5 flex items-center gap-2 text-[10px] text-emerald-200 bg-emerald-500/[0.08] border-b border-emerald-500/20 hover:bg-emerald-500/[0.14] transition">
          <span className="truncate">only {picked || pickedModel}</span>
          <span className="ml-auto uppercase tracking-wider text-gray-500">clear ✕</span>
        </button>
      )}

      <div className="flex-1 overflow-y-auto no-scrollbar p-2 space-y-1.5">
        {pane === 'matches' && shown.length === 0 && (
          <div className="p-6 text-center text-[10px] text-gray-600 leading-relaxed">
            no matches on this filter yet
          </div>
        )}
        {/* every recent match voided: that is a provider problem, and saying
            so beats a rail of grey lines nobody can explain */}
        {pane === 'matches' && shown.length > 0 && voidCount === shown.length && (
          <div className="card p-2.5 text-[10px] text-amber-400/80 leading-relaxed border-amber-500/20 bg-amber-500/[0.06]">
            every recent match was voided — the provider failed, not the agents. Nothing here was rated;
            the board still shows the last matches that played.
          </div>
        )}
        {pane === 'matches' && shown.map(m => m.void ? (
          <div key={m.id} className="flex items-baseline gap-2 px-2 py-1.5 text-[9px] text-gray-600"
            title={m.void_reason || 'the provider failed — kept on the record, out of the rating'}>
            <span className="text-amber-400/70 uppercase tracking-wider shrink-0">void</span>
            <span className="text-gray-500 shrink-0">{m.agent}</span>
            <span className="truncate flex-1">{m.title}</span>
            <span className="shrink-0">{ago(m.ts)}</span>
          </div>
        ) : (
          <div key={m.id} className="card card-hover p-2.5 space-y-2">
            <div className="flex items-baseline gap-2">
              <span className="text-[11px] text-gray-100 shrink-0">{m.agent}</span>
              <span className="text-[10px] text-gray-600 truncate flex-1" title={m.title}>{m.title}</span>
              <span className={`text-[12px] tabular-nums shrink-0 ${
                m.passed ? 'text-emerald-300' : m.score >= 0.5 ? 'text-gray-200' : 'text-gray-500'}`}>
                {pct(m.score)}
              </span>
            </div>
            <div className="flex gap-1.5">
              {BARS.map(b => (
                <div key={b.key} className="flex-1" title={`${b.label} ${pct(m[b.key])}`}>
                  <ScoreBar value={m[b.key]} tint={b.tint} />
                </div>
              ))}
            </div>
            {/* a program-graded match: one pip per case, hollow where the
                case was hidden — the score above is these, weighted */}
            {caseList(m).length > 0 && (
              <div className="flex items-center gap-1 flex-wrap">
                {caseList(m).map((c, i) => (
                  <span key={i}
                    title={`${c.name}${c.hidden ? ' (hidden)' : ''} — ${c.passed ? 'pass' : 'fail'}`}
                    className={`w-2 h-2 rounded-sm border ${
                      c.passed ? 'border-emerald-400/70' : 'border-red-400/60'
                    } ${c.hidden ? '' : c.passed ? 'bg-emerald-400/70' : 'bg-red-400/50'}`} />
                ))}
                <span className="text-[9px] text-gray-600 ml-0.5">
                  {caseList(m).filter(c => c.passed).length}/{caseList(m).length} cases
                </span>
              </div>
            )}
            <div className="flex items-center gap-1.5 text-[9px] text-gray-600 flex-wrap">
              <span className="text-gray-500">{m.suite}</span>
              {m.model && (
                <span className="truncate max-w-[120px] text-gray-500" title={m.model}>
                  {m.model.split('/').pop()}
                </span>
              )}
              <span title="steps taken of the budget">{m.steps}/{m.budget}</span>
              <span>{m.seconds}s</span>
              {m.cost > 0 ? <span>${m.cost.toFixed(4)}</span> : m.tokens ? <span>{toks(m.tokens)}</span> : null}
              <span className="ml-auto text-gray-700">
                {m.reason?.startsWith('qualifier') ? 'qualifier' : m.reason} · {ago(m.ts)}
              </span>
            </div>
          </div>
        ))}

        {pane === 'tasks' && Object.entries(suites).map(([suite, list]) => (
          <div key={suite} className="space-y-1">
            <div className="section-label px-1 pt-2 pb-0.5 flex items-baseline gap-2">
              <span>{suite}</span>
              <span className="text-gray-700 tracking-normal">{list.length}</span>
            </div>
            {list.map(t => {
              const lead = leadersByTask[t.key]
              return (
              <div key={t.key} className={`card card-hover p-2 ${roundKeys.has(t.key) ? 'card-on' : ''}`}>
                <div className="flex items-baseline gap-2">
                  <span className="text-[10px] text-gray-200 flex-1 truncate" title={t.title}>{t.title}</span>
                  {roundKeys.has(t.key) && (
                    <span className="text-[9px] uppercase tracking-wider text-emerald-300 shrink-0">this round</span>
                  )}
                  {isHost && (
                    <button disabled={busy || !!live}
                      title="play this task now — every agent, even ones with a standing score"
                      onClick={() => post('run', { task: t.key, force: true })}
                      className="text-[9px] uppercase tracking-wider text-gray-600 hover:text-emerald-300 transition disabled:opacity-40 shrink-0">
                      play
                    </button>
                  )}
                </div>
                <div className="text-[9px] text-gray-600 mt-0.5 truncate">
                  {t.key} · budget {t.steps || cfg.steps || 8} steps
                </div>
                {lead?.leader && (
                  <div className="text-[9px] mt-0.5 flex items-center gap-1.5 min-w-0"
                    title={`${lead.leader} holds the best score on this task — open the TASKS board for the full ranking`}>
                    <span className="uppercase tracking-wider text-gray-600 shrink-0">leader</span>
                    <span className="text-emerald-300/90 truncate">{lead.leader}</span>
                    <span className="text-gray-600 tabular-nums shrink-0">{pct(lead.leader_score)}</span>
                  </div>
                )}
              </div>
            )})}
          </div>
        ))}

        {pane === 'openarena' && oaPane}
      </div>
    </div>
  )

  return (
    <div className="flex-1 min-h-0 flex flex-col library-bg">
      {head}
      {config}
      {gauntletPanel}
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
          {statStrip}
          {podium}
          {view === 'agents' ? leaderboard : view === 'models' ? modelBoard : taskBoard}
          {boardFoot}
          {view === 'agents' ? agentCard : modelCardPane}
        </div>
        {feed}
      </div>
    </div>
  )
}
