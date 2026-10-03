'use client'

// SchedulePanel — agents on a timer, and the machine they run on.
//
//   builder   the grower (GET /grow): a new tool + agent every N minutes.
//             Anyone sees it tick; only the owner changes it (POST /grow/config).
//   runs      cron jobs (GET /cron): run an agent with a prompt every N
//             minutes. The owner, and addresses the owner granted 'cron',
//             can schedule; a grantee sees only its own jobs.
//   compute   GET /compute: this host (where the loop and tools run) and
//             where the agent's model runs.
//
// `agent` scopes the panel to one agent (the editor); without it the panel
// shows every job you may see, plus the builder timer.

import { useCallback, useEffect, useState } from 'react'
import { API_URL } from '../config'
import { ask } from '../lib/ask'

type Run = { t: number; status: string; summary?: string; elapsed?: number; cost?: number | null; manual?: boolean;
  compute?: { hostname?: string; inference?: Inference } }
type Job = { id: string; agent: string; prompt: string; every: number; enabled: boolean; owner: string;
  next_at: number; last?: Run | null; runs_total?: number; fails?: number; paused_reason?: string | null;
  free?: boolean; running?: boolean; label?: string | null; daily_cap?: number }
type Inference = { kind: string; where: string; provider?: string | null; model?: string | null }
type Host = { hostname?: string; os?: string; arch?: string; cpu?: string; cores?: number; load?: number[];
  busy?: number; mem?: { total_gb?: number; free_gb?: number }; disk?: { total_gb?: number; free_gb?: number };
  gpus?: { name: string; mem_gb: number; used_gb: number; util: number }[]; container?: boolean }
type CronStatus = { jobs: Job[]; total: number; enabled: number; running: number;
  you: { address: string | null; owner: boolean; can_schedule: boolean };
  scheduler: { running: boolean }; compute: { host?: Host; inference?: Inference } }
type Grow = { config: { enabled: boolean; interval: number; engine: string }; engines: string[];
  grown: { tools: number; agents: number }; last_tick?: { t: number; tool?: string; agent?: string; skipped?: string; error?: string } | null;
  scheduler?: { running: boolean; next_at?: number | null }; owner?: string }

const EVERY = [1, 5, 15, 30, 60, 360, 1440]
const ago = (t?: number | null) => {
  if (!t) return '—'
  const s = Math.round(Date.now() / 1000 - t)
  const a = Math.abs(s), u = a < 60 ? `${a}s` : a < 3600 ? `${Math.round(a / 60)}m` : a < 86400 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`
  return s >= 0 ? `${u} ago` : `in ${u}`
}
const mins = (m: number) => m % 1440 === 0 ? `${m / 1440}d` : m % 60 === 0 ? `${m / 60}h` : `${m}m`
const tone = (s?: string) => s === 'done' ? 'text-emerald-300' : s === 'error' ? 'text-rose-300'
  : s === 'paused' ? 'text-amber-300' : 'text-gray-400'

async function post(path: string, body: any) {
  const r = await fetch(`${API_URL}${path}`, { method: 'POST',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok || d?.error) throw new Error(d?.error || d?.detail || `HTTP ${r.status}`)
  return d
}

export function ComputeCard({ host, inference }: { host?: Host; inference?: Inference }) {
  if (!host && !inference) return null
  const mem = host?.mem
  const memPct = mem?.total_gb && mem.free_gb != null ? Math.round(100 * (1 - mem.free_gb / mem.total_gb)) : null
  const busy = host?.busy
  return (
    <div className="rounded-md border border-sky-500/20 bg-sky-500/[0.04] px-2.5 py-2 text-[10px] leading-relaxed">
      <div className="flex items-center gap-2 text-[9px] uppercase tracking-wider text-sky-300/80 mb-1">
        compute <span className="text-gray-600 normal-case tracking-normal">where this runs</span>
      </div>
      {host && (
        <div className="text-gray-300">
          <span className="text-gray-500">loop + tools</span>{' '}
          <span className="font-mono text-sky-200">{host.hostname}</span>
          {host.container ? <span className="text-gray-500"> (container)</span> : null}
          <div className="text-gray-400">
            {host.cores} × {host.cpu || host.arch}
            {busy != null && <> · load <span className={busy > 1 ? 'text-amber-300' : 'text-emerald-300'}>
              {Math.round(busy * 100)}%</span></>}
            {memPct != null && <> · mem {memPct}% of {mem?.total_gb}G</>}
            {host.disk?.free_gb != null && <> · disk {host.disk.free_gb}G free</>}
          </div>
          <div className="text-gray-400">
            {host.gpus?.length
              ? host.gpus.map((g, i) => <span key={i}>{i ? ' · ' : ''}GPU {g.name} {g.used_gb}/{g.mem_gb}G {g.util}%</span>)
              : 'no GPU'}{' · '}{host.os}
          </div>
        </div>
      )}
      {inference && (
        <div className="text-gray-300 mt-1">
          <span className="text-gray-500">model</span>{' '}
          <span className="font-mono text-sky-200">{inference.model || 'default'}</span>
          <span className="text-gray-500"> on </span>{inference.where}
          <span className={`ml-1 px-1 rounded border text-[8px] uppercase ${inference.kind === 'remote'
            ? 'border-violet-400/30 text-violet-300' : 'border-emerald-400/30 text-emerald-300'}`}>{inference.kind}</span>
        </div>
      )}
    </div>
  )
}

export default function SchedulePanel({ token, agent, isHost, showBuilder = !agent }: {
  token?: string
  /** scope to one agent; omit for every job you may see */
  agent?: string | null
  isHost?: boolean
  /** show the builder timer (the grower) — on by default when unscoped */
  showBuilder?: boolean
}) {
  const [st, setSt] = useState<CronStatus | null>(null)
  const [grow, setGrow] = useState<Grow | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [prompt, setPrompt] = useState('')
  const [every, setEvery] = useState(60)
  const [free, setFree] = useState(false)
  const [pick, setPick] = useState(agent || '')
  const [agents, setAgents] = useState<string[]>([])
  const [open, setOpen] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const q = new URLSearchParams()
      if (agent) q.set('agent', agent)
      if (token) q.set('key', token)
      const r = await fetch(`${API_URL}/cron?${q}`)
      if (r.ok) setSt(await r.json())
      if (showBuilder) {
        const g = await fetch(`${API_URL}/grow`)
        if (g.ok) setGrow(await g.json())
      }
    } catch { /* offline — keep the last view */ }
  }, [agent, token, showBuilder])

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t) }, [load])
  useEffect(() => {
    if (agent) return
    fetch(`${API_URL}/agents`).then(r => r.json()).then(d => {
      const names = (d?.agents || []).map((a: any) => typeof a === 'string' ? a : a?.name).filter(Boolean)
      setAgents(names)
      setPick(p => p || names[0] || '')
    }).catch(() => {})
  }, [agent])

  const act = async (label: string, fn: () => Promise<any>) => {
    setBusy(label); setErr(null)
    try { await fn(); await load() } catch (e: any) { setErr(e?.message || String(e)) }
    setBusy(null)
  }

  const canSchedule = !!st?.you?.can_schedule
  const owner = !!st?.you?.owner || (!!isHost && !!token)
  const target = agent || pick

  const add = () => act('add', async () => {
    await post('/cron/add', { key: token, agent: target, prompt, every, free })
    setPrompt('')
  })

  const growMin = grow ? Math.max(1, Math.round(grow.config.interval / 60)) : 1

  return (
    <div className="space-y-2">
      {showBuilder && grow && (
        <div className="rounded-md border border-emerald-500/20 bg-emerald-500/[0.04] px-2.5 py-2 text-[10px]">
          <div className="flex items-center gap-2">
            <span className="text-[9px] uppercase tracking-wider text-emerald-300/80">builder timer</span>
            <span className={`w-1.5 h-1.5 rounded-full ${grow.config.enabled ? 'bg-emerald-400 animate-pulse' : 'bg-gray-600'}`} />
            <span className="text-gray-400">
              {grow.config.enabled ? `a new tool + agent every ${mins(growMin)}` : 'paused'}
              {' · '}{grow.grown.agents} agents, {grow.grown.tools} tools grown
            </span>
            <span className="ml-auto text-gray-500">
              {grow.config.enabled && grow.scheduler?.next_at ? `next ${ago(grow.scheduler.next_at)}` : ''}
            </span>
          </div>
          {grow.last_tick && (
            <div className="text-gray-500 mt-0.5 truncate">
              last {ago(grow.last_tick.t)}: {grow.last_tick.agent ? `+${grow.last_tick.agent}` : ''}
              {grow.last_tick.tool ? ` +${grow.last_tick.tool}` : ''}
              {grow.last_tick.skipped ? ` skipped: ${grow.last_tick.skipped}` : ''}
              {grow.last_tick.error ? ` error: ${grow.last_tick.error}` : ''}
            </div>
          )}
          {owner ? (
            <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
              <button disabled={!!busy}
                onClick={() => act('grow', () => post('/grow/config', { key: token, enabled: !grow.config.enabled }))}
                className="px-2 py-0.5 rounded border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/10">
                {grow.config.enabled ? 'pause' : 'start'}
              </button>
              <span className="text-gray-500">every</span>
              {EVERY.filter(m => m <= 1440).map(m => (
                <button key={m} disabled={!!busy}
                  onClick={() => act('grow', () => post('/grow/config', { key: token, interval: m * 60 }))}
                  className={`px-1.5 py-0.5 rounded border ${m === growMin
                    ? 'border-emerald-400/50 text-emerald-200 bg-emerald-500/10' : 'border-white/10 text-gray-400 hover:text-gray-200'}`}>
                  {mins(m)}
                </button>
              ))}
              <select value={grow.config.engine} disabled={!!busy}
                onChange={e => act('grow', () => post('/grow/config', { key: token, engine: e.target.value }))}
                className="bg-transparent border border-white/10 rounded px-1 py-0.5 text-gray-300">
                {grow.engines.map(e => <option key={e} value={e} className="bg-gray-900">{e}</option>)}
              </select>
              <button disabled={!!busy} onClick={() => act('tick', () => post('/grow/tick', { key: token }))}
                className="px-2 py-0.5 rounded border border-white/10 text-gray-300 hover:text-white">build one now</button>
            </div>
          ) : (
            <div className="text-gray-600 mt-1">only the owner can change the builder timer</div>
          )}
        </div>
      )}

      <div className="rounded-md border border-violet-500/20 bg-violet-500/[0.03] px-2.5 py-2 text-[10px]">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[9px] uppercase tracking-wider text-violet-300/80">scheduled runs</span>
          <span className="text-gray-500">
            {st ? `${st.jobs.length} yours · ${st.enabled}/${st.total} on${st.running ? ` · ${st.running} running` : ''}` : '…'}
          </span>
          {st && !st.scheduler.running && <span className="text-amber-300/80">scheduler stopped</span>}
        </div>

        {st?.jobs.map(j => (
          <div key={j.id} className="border-t border-white/5 py-1">
            <div className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${j.running ? 'bg-sky-400 animate-pulse'
                : j.enabled ? 'bg-emerald-400' : 'bg-gray-600'}`} />
              {!agent && <span className="font-mono text-violet-200">{j.agent}</span>}
              <span className="text-gray-400">every {mins(j.every)}</span>
              <button onClick={() => setOpen(open === j.id ? null : j.id)}
                className="min-w-0 flex-1 text-left truncate text-gray-300 hover:text-white" title={j.prompt}>
                {j.label || j.prompt}
              </button>
              <span className="text-gray-500 shrink-0">
                {j.running ? 'running…' : j.enabled ? `next ${ago(j.next_at)}` : 'paused'}
              </span>
              <button disabled={!!busy || j.running} title="run now"
                onClick={() => act(j.id, () => post('/cron/run', { key: token, id: j.id }))}
                className="px-1 text-gray-400 hover:text-sky-300">run</button>
              <button disabled={!!busy}
                onClick={() => act(j.id, () => post('/cron/update', { key: token, id: j.id, enabled: !j.enabled }))}
                className="px-1 text-gray-400 hover:text-amber-300">{j.enabled ? 'pause' : 'resume'}</button>
              <button disabled={!!busy} title="delete"
                onClick={async () => {
                  if (!(await ask({ title: `Delete this schedule for ${j.agent}?`, ok: 'Delete', danger: true }))) return
                  act(j.id, () => post('/cron/rm', { key: token, id: j.id }))
                }}
                className="px-1 text-gray-500 hover:text-rose-300">×</button>
            </div>
            {j.paused_reason && <div className="text-amber-300/80 pl-3 truncate">{j.paused_reason}</div>}
            {j.last && (
              <div className="pl-3 text-gray-500 truncate">
                <span className={tone(j.last.status)}>{j.last.status}</span> {ago(j.last.t)}
                {j.last.elapsed != null && ` · ${j.last.elapsed}s`}
                {j.last.cost ? ` · $${j.last.cost.toFixed(4)}` : ''}
                {j.last.compute?.hostname && ` · on ${j.last.compute.hostname}`}
                {j.last.compute?.inference?.model && ` / ${j.last.compute.inference.model}`}
                {j.last.summary && <> · <span className="text-gray-400">{j.last.summary}</span></>}
              </div>
            )}
            {open === j.id && (
              <div className="pl-3 mt-1 space-y-0.5 text-gray-400">
                <div className="whitespace-pre-wrap text-gray-300">{j.prompt}</div>
                <div>{j.runs_total || 0} runs · cap {j.daily_cap || '∞'}/day{j.free ? ' · free models' : ''}
                  {owner && j.owner !== st?.you?.address ? ` · by ${j.owner.slice(0, 8)}…` : ''}</div>
                <div className="flex gap-1 flex-wrap">
                  <span>every</span>
                  {EVERY.map(m => (
                    <button key={m} disabled={!!busy}
                      onClick={() => act(j.id, () => post('/cron/update', { key: token, id: j.id, every: m }))}
                      className={`px-1.5 rounded border ${m === j.every ? 'border-violet-400/50 text-violet-200'
                        : 'border-white/10 hover:text-gray-200'}`}>{mins(m)}</button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}

        {canSchedule ? (
          <div className="border-t border-white/5 pt-1.5 mt-1 space-y-1">
            {!agent && (
              <select value={pick} onChange={e => setPick(e.target.value)}
                className="w-full bg-transparent border border-white/10 rounded px-1.5 py-1 text-gray-200">
                {agents.map(a => <option key={a} value={a} className="bg-gray-900">{a}</option>)}
              </select>
            )}
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={2}
              placeholder={`what should ${target || 'the agent'} do each run?`}
              className="w-full bg-black/20 border border-white/10 rounded px-1.5 py-1 text-[11px] text-gray-200 resize-y" />
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-gray-500">every</span>
              {EVERY.map(m => (
                <button key={m} onClick={() => setEvery(m)}
                  className={`px-1.5 py-0.5 rounded border ${m === every ? 'border-violet-400/50 text-violet-200 bg-violet-500/10'
                    : 'border-white/10 text-gray-400 hover:text-gray-200'}`}>{mins(m)}</button>
              ))}
              <input type="number" min={1} value={every} onChange={e => setEvery(Math.max(1, +e.target.value || 1))}
                className="w-14 bg-transparent border border-white/10 rounded px-1 py-0.5 text-gray-200" title="minutes" />
              <label className="flex items-center gap-1 text-gray-400 ml-1">
                <input type="checkbox" checked={free} onChange={e => setFree(e.target.checked)} /> free models
              </label>
              <button disabled={!!busy || !prompt.trim() || !target} onClick={add}
                className="ml-auto px-2.5 py-0.5 rounded border border-violet-400/40 text-violet-200 hover:bg-violet-500/10 disabled:opacity-40">
                {busy === 'add' ? 'scheduling…' : 'schedule'}
              </button>
            </div>
          </div>
        ) : (
          <div className="text-gray-600 border-t border-white/5 pt-1 mt-1">
            {st?.you?.address ? "scheduling needs the owner's 'cron' grant" : 'sign in to schedule this agent'}
          </div>
        )}
        {err && <div className="text-rose-300 mt-1">{err}</div>}
      </div>

      <ComputeCard host={st?.compute?.host} inference={st?.compute?.inference} />
    </div>
  )
}
