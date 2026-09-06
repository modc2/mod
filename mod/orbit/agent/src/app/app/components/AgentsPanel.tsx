'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_URL } from '../config'

// ── AGENTS — the registry, browsed ──────────────────────────────────
// The canvas next door wires ONE agent as a graph. This is the other half of
// the same job: every agent this module ships or has been given, in a list,
// with what it is made of readable in one screen.
//
// An agent here is exactly what it is on disk — a folder of code under
// `src/agents/<name>/` holding a `mod.py` with an `Agent` class (name,
// description, icon, goal, tools, model, memory, harness, owner). The panel
// keeps no store of its own; it speaks this module's own protocol:
//
//   GET    /agents               the registry (names + schemas + host)
//   POST   /agents               create the folder + mod.py
//   PUT    /agents/{name}        rebind goal / model / tools / memory / icon
//   DELETE /agents/{name}        remove it
//   GET    /providers            every model, by provider  ← the MODELS tab
//   GET    /toolboxes            the snap-on bundles
//   GET    /memory/modules       the memory components an agent is built with
//   GET    /memory + /state      library notes, and the live subsystem
//   GET    /modules/agent/file   the agent's own source, for OPEN CODE
//
// So anything made here is a real agent to every other consumer of that
// registry — the canvas, the market rail, the arena — and anything made
// there shows up here.

export interface AgentSchema {
  name: string
  description: string
  goal: string
  icon: string | null
  tools: string[] | null
  model: string | null
  memory: string | null
  harness: string | null
  arena?: boolean
  builtin: boolean
  owner: string | null
  /** 'host' = nobody claimed it, so the module owner holds it. */
  owner_source?: string
}

interface Provider {
  key: string
  models: string[]
  default_model: string | null
  configured: boolean
  encrypted: boolean
  unlocked: boolean
  keyless: boolean
  free: boolean
  runtime?: string | null
}

interface Toolbox { name: string; description: string; tools: string[]; builtin: boolean }
interface MemModule { name: string; label?: string; description?: string; default?: boolean }
interface MemNote { id: string; name?: string; content?: string; tags?: string[] }
interface MemState {
  kind?: string
  session?: string
  episodes?: number
  exchanges?: number
  facts?: number
  persist?: boolean
  port?: number
  layers?: string[]
}

type Props = {
  /** signed token — the server files a new agent under it and checks edits */
  token?: string | null
  address?: string | null
  /** the host owns every agent nobody else does, shipped ones included */
  isHost?: boolean
  onSignIn?: () => void
  /** open this agent on the canvas next door */
  onEditOnCanvas?: (name: string) => void
  /** hand a prompt to this agent in the console */
  onRun?: (name: string, prompt: string, memoryIds: string[]) => void
  /** the registry changed — the console refetches its own agent list */
  onChanged?: () => void
}

const ICONS = ['>_', '△', '◉', '⬡', '◈', '✦', '⚙', '◆', '▣', '✧']

/** Where an agent's code lives, relative to this module. */
export function agentFile(name: string) { return `src/agents/${name}/mod.py` }

// Per-agent NOTE bindings. The schema binds a memory *module*, but the notes
// carried into a run are a per-run argument, so which ones this agent should
// carry is remembered here and sent with each run. Kept tiny — every module on
// this host shares one origin's localStorage quota.
const NOTES_KEY = 'agent_browse_notes'
const readNoteBindings = (): Record<string, string[]> => {
  try { return JSON.parse(localStorage.getItem(NOTES_KEY) || '{}') } catch { return {} }
}
const writeNoteBindings = (map: Record<string, string[]>) => {
  try { localStorage.setItem(NOTES_KEY, JSON.stringify(map)) } catch { /* quota — a convenience, not state worth failing over */ }
}

export default function AgentsPanel({
  token, address, isHost, onSignIn, onEditOnCanvas, onRun, onChanged,
}: Props) {
  const [agents, setAgents] = useState<Record<string, AgentSchema>>({})
  const [order, setOrder] = useState<string[]>([])
  const [host, setHost] = useState<string | null>(null)
  const [providers, setProviders] = useState<Provider[]>([])
  const [boxes, setBoxes] = useState<Toolbox[]>([])
  const [memModules, setMemModules] = useState<MemModule[]>([])
  const [notes, setNotes] = useState<MemNote[]>([])
  const [memState, setMemState] = useState<MemState | null>(null)

  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState<'agent' | 'models' | 'memory'>('agent')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)

  // the draft is local until SAVE writes it back through the protocol
  const [draft, setDraft] = useState<Partial<AgentSchema>>({})
  const [noteIds, setNoteIds] = useState<string[]>([])
  const [prompt, setPrompt] = useState('')

  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')

  // the source of the selected agent, once OPEN CODE asks for it
  const [source, setSource] = useState<{ path: string; text: string } | null>(null)
  const [srcBusy, setSrcBusy] = useState(false)

  const getJson = useCallback(async (path: string) => {
    const r = await fetch(`${API_URL}${path}`, { signal: AbortSignal.timeout(10000) })
    if (!r.ok) throw new Error(`${path} → ${r.status}`)
    return r.json()
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const reg = await getJson('/agents')
      const schemas: Record<string, AgentSchema> = reg.schemas || {}
      setAgents(schemas)
      setOrder(reg.agents || Object.keys(schemas))
      setHost(reg.host || null)
      setSelected(cur => cur ?? (reg.agents?.[0] ?? null))
    } catch (e) {
      setError(`registry unreachable — ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
    // The rest are what an agent is built from, not the registry: any of them
    // failing narrows the panel, but must never blank the agent list.
    getJson('/providers').then(d => setProviders(d.providers || [])).catch(() => {})
    getJson('/toolboxes').then(d => setBoxes(d.toolboxes || [])).catch(() => {})
    getJson('/memory/modules').then(d => setMemModules(d.memories || [])).catch(() => {})
    getJson('/memory').then(d => setNotes(d.memory || [])).catch(() => {})
    getJson('/memory/state').then(setMemState).catch(() => {})
  }, [getJson])

  useEffect(() => { load() }, [load])

  // picking an agent resets the draft to what's on disk
  useEffect(() => {
    if (!selected || !agents[selected]) return
    setDraft({ ...agents[selected] })
    setNoteIds(readNoteBindings()[selected] || [])
    setSaved(false)
    setPrompt('')
    setSource(null)
  }, [selected, agents])

  const current = selected ? agents[selected] : null

  /** Unowned agents (owner_source 'host') belong to the module owner; the
      rest to whoever made them. The host may edit either. */
  const canEdit = useMemo(() => {
    if (!current) return false
    // Signed out the console guesses you might be the host; the server never
    // does, and refuses an unsigned write — so browse read-only until there
    // is a token to send.
    if (!token) return false
    if (isHost) return true
    if (!address) return false
    if (current.builtin || current.owner_source === 'host') return false
    return (current.owner || '').toLowerCase() === address.toLowerCase()
  }, [current, address, token, isHost])

  const allModels = useMemo(
    () => providers.reduce((n, p) => n + p.models.length, 0),
    [providers],
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return order
    return order.filter(n =>
      n.toLowerCase().includes(q) ||
      (agents[n]?.description || '').toLowerCase().includes(q) ||
      (agents[n]?.model || '').toLowerCase().includes(q))
  }, [order, agents, search])

  const send = async (path: string, body: any, method: 'POST' | 'PUT' | 'DELETE' = 'POST') => {
    const r = await fetch(`${API_URL}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: method === 'DELETE' ? undefined : JSON.stringify({ ...body, key: token }),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok || d?.error) throw new Error(d?.error || `${method} ${path} → ${r.status}`)
    return d
  }

  const save = async () => {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      const tools = draft.tools && draft.tools.length ? draft.tools : null
      await send(`/agents/${encodeURIComponent(selected)}`, {
        description: draft.description,
        goal: draft.goal,
        icon: draft.icon,
        ...(tools ? { tools } : { clear_tools: true }),
        ...(draft.model ? { model: draft.model } : { clear_model: true }),
        ...(draft.memory ? { memory: draft.memory } : { clear_memory: true }),
      }, 'PUT')
      const bindings = readNoteBindings()
      bindings[selected] = noteIds
      writeNoteBindings(bindings)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      await load()
      onChanged?.()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const create = async () => {
    const name = newName.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-')
    if (!name) return
    if (!token) { onSignIn?.(); return }
    setBusy(true)
    setError(null)
    try {
      await send('/agents', {
        name,
        description: '',
        goal: 'You are a helpful coding agent.',
        icon: ICONS[order.length % ICONS.length],
      })
      setCreating(false)
      setNewName('')
      await load()
      setSelected(name)
      setTab('agent')
      onChanged?.()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      await send(`/agents/${encodeURIComponent(selected)}?key=${encodeURIComponent(token || '')}`, null, 'DELETE')
      setSelected(null)
      await load()
      onChanged?.()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // OPEN CODE — the folder's mod.py, read back over the public module surface
  // the same way any auditor would read it.
  const openCode = async () => {
    if (!selected) return
    if (source) { setSource(null); return }
    setSrcBusy(true)
    try {
      const path = agentFile(selected)
      const d = await getJson(`/modules/agent/file?path=${encodeURIComponent(path)}`)
      if (d?.error) throw new Error(d.error)
      setSource({ path, text: d.text || '' })
    } catch (e) {
      setError(`can't read the source — ${(e as Error).message}`)
    } finally {
      setSrcBusy(false)
    }
  }

  // ── shared styling ──
  const field = 'w-full bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition'
  const legend = 'text-[9px] uppercase tracking-wider text-gray-600 mb-1.5'
  const card = 'rounded-xl border border-white/[0.06] bg-surface-1'

  const Field = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
    <div>
      <div className={legend} title={hint}>{label}</div>
      {children}
    </div>
  )

  return (
    <div className="h-full flex flex-col min-h-0">
      {/* ── toolbar ── */}
      <div className="shrink-0 border-b border-white/[0.06] bg-surface-1 px-3 py-2 flex items-center gap-3 flex-wrap">
        <span className="text-[11px] uppercase tracking-wider text-emerald-300 font-medium">✦ Agents</span>
        <span className="text-[10px] text-gray-500">
          <b className="text-gray-300">{order.length}</b> agents
          <span className="text-gray-700 px-1.5">·</span>
          <b className="text-gray-300">{allModels}</b> models
        </span>
        <span className="text-[10px] text-gray-600 truncate min-w-0 hidden sm:block">
          each one a folder of code under src/agents/
        </span>
        <div className="ml-auto flex items-center gap-2">
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="filter agents…"
            className="w-44 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-[11px] text-gray-300 outline-none placeholder:text-gray-700 focus:border-emerald-500/40 transition"
          />
          <button
            onClick={() => (token ? setCreating(true) : onSignIn?.())}
            title={token ? 'Create a new agent folder' : 'Sign in to create an agent'}
            className="px-3 py-1 rounded-md text-[11px] uppercase tracking-wider border border-emerald-500/25 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 transition"
          >
            + New agent
          </button>
        </div>
      </div>

      {error && (
        <div className="shrink-0 mx-3 mt-2 px-3 py-2 rounded-lg text-[11px] text-red-300 border border-red-500/25 bg-red-500/[0.07]">
          {error}
        </div>
      )}

      <div className="flex-1 flex min-h-0">
        {/* ── the registry: one row per folder ── */}
        <div className="w-60 shrink-0 border-r border-white/[0.06] bg-surface-1 overflow-y-auto p-2 space-y-1">
          {loading && <div className="p-3 text-[11px] text-gray-600 animate-pulse">loading agents…</div>}
          {!loading && filtered.length === 0 && (
            <div className="p-3 text-[11px] text-gray-600">no agents match.</div>
          )}
          {filtered.map(name => {
            const a = agents[name]
            const on = name === selected
            return (
              <button
                key={name}
                onClick={() => { setSelected(name); setTab('agent') }}
                className={`w-full text-left px-2.5 py-2 rounded-lg border transition ${
                  on
                    ? 'bg-emerald-500/10 border-emerald-500/25'
                    : 'border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/[0.12]'
                }`}
              >
                <span className="flex items-center gap-2">
                  <span className={`font-mono text-xs shrink-0 ${on ? 'text-emerald-300' : 'text-gray-500'}`}>
                    {a?.icon || '>_'}
                  </span>
                  <span className="text-xs text-gray-200 truncate font-medium">{name}</span>
                  {a?.builtin && (
                    <span className="ml-auto text-[8px] uppercase tracking-wider text-gray-700 shrink-0">shipped</span>
                  )}
                </span>
                <span className="block mt-0.5 text-[10px] text-gray-600 truncate">
                  {a?.model || (a?.harness ? `${a.harness} harness` : 'no model bound')}
                </span>
              </button>
            )
          })}
        </div>

        {/* ── the selected agent ── */}
        <div className="flex-1 min-w-0 overflow-y-auto p-3 space-y-2.5">
          {creating && (
            <div className={`${card} p-4 space-y-3`}>
              <div className={legend}>New agent</div>
              <p className="text-[11px] text-gray-500 leading-relaxed">
                Writes <code className="text-gray-400">src/agents/&lt;name&gt;/mod.py</code> — a folder
                with an <code className="text-gray-400">Agent</code> class in it. Every consumer of the
                registry picks it up from there: this list, the canvas, the market, the arena.
              </p>
              <input
                autoFocus
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') create() }}
                placeholder="agent name (lowercase, no spaces)"
                className={field}
              />
              <div className="flex items-center gap-2">
                <button onClick={create} disabled={busy || !newName.trim()}
                  className="px-4 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-emerald-500/25 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-40 transition">
                  {busy ? 'creating…' : 'create'}
                </button>
                <button onClick={() => setCreating(false)}
                  className="px-4 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 transition">
                  cancel
                </button>
              </div>
            </div>
          )}

          {!current && !creating && !loading && (
            <div className="p-4 text-xs text-gray-600">Pick an agent on the left, or make a new one.</div>
          )}

          {current && (
            <>
              {/* identity + where the code is */}
              <div className={`${card} px-3 py-2.5 flex items-center gap-2.5 flex-wrap`}>
                <span className="w-8 h-8 shrink-0 rounded-lg border border-white/[0.08] bg-white/[0.03] flex items-center justify-center font-mono text-sm text-emerald-300">
                  {current.icon || '>_'}
                </span>
                <span className="min-w-0">
                  <span className="block text-[13px] font-medium text-gray-100 truncate">{selected}</span>
                  <span className="block font-mono text-[9px] text-gray-600 truncate">{agentFile(selected!)}</span>
                </span>
                <div className="ml-auto flex items-center gap-1.5">
                  {current.harness && (
                    <span className="text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-violet-400/25 bg-violet-400/10 text-violet-300"
                      title="This agent hands the whole run to an external CLI instead of this module's loop">
                      {current.harness}
                    </span>
                  )}
                  <button onClick={openCode} disabled={srcBusy}
                    className="px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 hover:border-white/[0.16] transition disabled:opacity-40"
                    title="Read this agent's mod.py">
                    {srcBusy ? 'reading…' : source ? 'hide code' : 'open code'}
                  </button>
                  {onEditOnCanvas && (
                    <button onClick={() => onEditOnCanvas(selected!)}
                      className="px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 hover:border-white/[0.16] transition"
                      title="Open this agent on the canvas">
                      canvas
                    </button>
                  )}
                  {canEdit && (
                    <button onClick={remove} disabled={busy}
                      className="px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider border border-red-500/25 text-red-300 hover:bg-red-500/10 transition disabled:opacity-40">
                      delete
                    </button>
                  )}
                </div>
              </div>

              {source && (
                <pre className="text-[11px] leading-relaxed text-gray-300 bg-white/[0.03] border border-white/[0.06] rounded-xl p-3 overflow-x-auto whitespace-pre">
                  {source.text}
                </pre>
              )}

              {/* tabs — the agent itself, the model catalog, its memory */}
              <div className="flex items-center gap-1">
                {(['agent', 'models', 'memory'] as const).map(t => (
                  <button key={t} onClick={() => setTab(t)}
                    className={`px-3 py-1 rounded-md text-[10px] uppercase tracking-wider border transition ${
                      tab === t
                        ? 'bg-emerald-500/15 border-emerald-500/25 text-emerald-200'
                        : 'border-white/[0.07] text-gray-500 hover:text-gray-300 hover:bg-white/[0.04]'
                    }`}>
                    {t}
                  </button>
                ))}
                <span className="ml-auto text-[10px] text-gray-600 truncate">
                  {canEdit ? 'yours to edit' : !token
                    ? 'signed out — read-only'
                    : current.builtin
                      ? 'shipped agent — host-owned'
                      : current.owner_source === 'host'
                        ? 'unclaimed — the host holds it'
                        : 'belongs to another address'}
                </span>
              </div>

              {/* ── AGENT: what the folder's mod.py holds ── */}
              {tab === 'agent' && (
                <div className={`${card} p-4 space-y-4`}>
                  {!canEdit && (
                    <p className="text-[11px] text-gray-600 leading-relaxed">
                      Read-only here.{' '}
                      {token
                        ? 'Copy it onto the canvas and save it under your own name to make it yours.'
                        : (
                          <button onClick={onSignIn} className="text-emerald-300 hover:text-emerald-200 underline underline-offset-2">
                            sign in
                          </button>
                        )}
                    </p>
                  )}

                  <Field label="Icon">
                    <div className="flex flex-wrap gap-1.5">
                      {ICONS.map(ic => (
                        <button key={ic} disabled={!canEdit}
                          onClick={() => setDraft(d => ({ ...d, icon: ic }))}
                          className={`font-mono text-xs px-2 py-1 rounded-md border transition disabled:opacity-50 ${
                            draft.icon === ic
                              ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-200'
                              : 'border-white/[0.07] text-gray-500 hover:text-gray-300'
                          }`}>
                          {ic}
                        </button>
                      ))}
                    </div>
                  </Field>

                  <Field label="Description">
                    <input value={draft.description || ''} disabled={!canEdit}
                      onChange={e => setDraft(d => ({ ...d, description: e.target.value }))}
                      placeholder="what this agent is for" className={field} />
                  </Field>

                  <Field label="Goal — the system prompt" hint="Written into the Agent class's `goal` in mod.py">
                    <textarea value={draft.goal || ''} disabled={!canEdit} rows={10}
                      onChange={e => setDraft(d => ({ ...d, goal: e.target.value }))}
                      placeholder="You are…"
                      className={`${field} font-mono leading-relaxed resize-y`} />
                  </Field>

                  <Field label="Tools" hint="Snap a whole toolbox on, or leave empty for every tool">
                    <div className="flex flex-wrap gap-1.5">
                      {boxes.map(tb => {
                        const on = (draft.tools || []).length > 0 && tb.tools.every(t => (draft.tools || []).includes(t))
                        return (
                          <button key={tb.name} disabled={!canEdit}
                            title={`${tb.description} — ${tb.tools.join(', ')}`}
                            onClick={() => setDraft(d => {
                              const cur = new Set(d.tools || [])
                              if (on) tb.tools.forEach(t => cur.delete(t))
                              else tb.tools.forEach(t => cur.add(t))
                              const next = [...cur]
                              return { ...d, tools: next.length ? next : null }
                            })}
                            className={`px-2.5 py-1 rounded-md text-[10px] border transition disabled:opacity-50 ${
                              on
                                ? 'bg-sky-400/10 border-sky-400/25 text-sky-300'
                                : 'border-white/[0.07] text-gray-500 hover:text-gray-300'
                            }`}>
                            {tb.name}
                          </button>
                        )
                      })}
                    </div>
                    <div className="mt-1.5 font-mono text-[9px] text-gray-600">
                      {(draft.tools || []).length ? (draft.tools || []).join(' · ') : 'empty = no restriction, every tool'}
                    </div>
                  </Field>

                  <div className="flex items-center gap-2 pt-0.5">
                    <button onClick={save} disabled={!canEdit || busy}
                      className="px-4 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-emerald-500/25 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-40 transition">
                      {busy ? 'saving…' : saved ? 'saved ✓' : 'save agent'}
                    </button>
                    <span className="text-[10px] text-gray-600">writes straight back to mod.py</span>
                  </div>

                  {/* run it */}
                  {onRun && (
                    <div className="pt-3 border-t border-white/[0.06] space-y-2">
                      <Field label="Run this agent">
                        <textarea value={prompt} rows={3}
                          onChange={e => setPrompt(e.target.value)}
                          placeholder="Give it a task…" className={`${field} resize-y`} />
                      </Field>
                      <div className="flex items-center gap-2 flex-wrap">
                        <button onClick={() => prompt.trim() && onRun(selected!, prompt.trim(), noteIds)}
                          disabled={!prompt.trim()}
                          className="px-4 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-sky-400/25 bg-sky-400/10 text-sky-300 hover:bg-sky-400/20 disabled:opacity-40 transition">
                          run
                        </button>
                        <span className="text-[10px] text-gray-600">
                          on {draft.model || (current.harness ? `the ${current.harness} CLI` : "the agent's default model")}
                          {noteIds.length ? ` · ${noteIds.length} memory note${noteIds.length > 1 ? 's' : ''}` : ''}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* ── MODELS: the whole catalog, and the one bound here ── */}
              {tab === 'models' && (
                <div className={`${card} p-4 space-y-4`}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`${legend} mb-0`}>Bound model</span>
                    <span className="font-mono text-[11px] text-emerald-300">{draft.model || '— agent default —'}</span>
                    {draft.model && canEdit && (
                      <button onClick={() => setDraft(d => ({ ...d, model: null }))}
                        className="text-[9px] uppercase tracking-wider px-2 py-0.5 rounded border border-red-500/25 text-red-300 hover:bg-red-500/10 transition">
                        unbind
                      </button>
                    )}
                    <button onClick={save} disabled={!canEdit || busy}
                      className="ml-auto px-3 py-1 rounded-md text-[10px] uppercase tracking-wider border border-emerald-500/25 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-40 transition">
                      {busy ? 'saving…' : saved ? 'saved ✓' : 'save'}
                    </button>
                  </div>

                  {providers.length === 0 && (
                    <p className="text-[11px] text-gray-600 leading-relaxed">
                      No providers reporting. Models come from this module&apos;s key vault — add or unlock a key there.
                    </p>
                  )}

                  {providers.map(p => (
                    <div key={p.key} className="space-y-2">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] text-gray-200 font-medium">{p.key}</span>
                        <span
                          title={p.keyless ? 'Runs without a key'
                            : p.configured ? (p.encrypted && !p.unlocked ? 'Key stored but locked' : 'Key configured')
                            : 'No key configured'}
                          className={`text-[8px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-white/[0.08] ${
                            p.keyless || (p.configured && (!p.encrypted || p.unlocked)) ? 'text-emerald-300' : 'text-amber-300'
                          }`}>
                          {p.keyless ? 'keyless' : p.configured ? (p.encrypted && !p.unlocked ? 'locked' : 'ready') : 'no key'}
                        </span>
                        {p.free && <span className="text-[8px] uppercase tracking-wider text-emerald-300/80">free</span>}
                        <span className="ml-auto text-[9px] text-gray-600">{p.models.length} models</span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {p.models.map(m => {
                          const on = draft.model === m
                          return (
                            <button key={`${p.key}/${m}`} disabled={!canEdit}
                              onClick={() => setDraft(d => ({ ...d, model: m }))}
                              title={`${p.key} · ${m}${p.default_model === m ? ' (provider default)' : ''}`}
                              className={`px-2.5 py-1 rounded-md font-mono text-[10px] border transition disabled:cursor-default ${
                                on
                                  ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-200'
                                  : 'border-white/[0.07] bg-white/[0.02] text-gray-400 hover:text-gray-200'
                              } ${p.configured || p.keyless ? '' : 'opacity-50'}`}>
                              {m}{p.default_model === m && <span className="text-gray-600"> ★</span>}
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* ── MEMORY: the component it is built with, and what it carries ── */}
              {tab === 'memory' && (
                <div className={`${card} p-4 space-y-4`}>
                  <Field label="Memory module" hint="Memory is a component — an agent is built with one the way it is built with a model">
                    <div className="flex flex-wrap gap-1.5">
                      {[{ name: '', label: 'module default' } as MemModule, ...memModules].map(m => {
                        const on = (draft.memory || '') === m.name
                        return (
                          <button key={m.name || 'default'} disabled={!canEdit}
                            onClick={() => setDraft(d => ({ ...d, memory: m.name || null }))}
                            title={m.description}
                            className={`px-2.5 py-1 rounded-md text-[10px] border transition disabled:opacity-50 ${
                              on
                                ? 'bg-violet-400/10 border-violet-400/30 text-violet-300'
                                : 'border-white/[0.07] text-gray-500 hover:text-gray-300'
                            }`}>
                            {m.label || m.name}
                          </button>
                        )
                      })}
                    </div>
                  </Field>

                  <Field label="The live subsystem" hint="This module's layered memory — its own process">
                    {memState ? (
                      <div className="grid grid-cols-2 gap-1.5">
                        {([
                          ['kind', memState.kind],
                          ['port', memState.port],
                          ['episodes', memState.episodes],
                          ['exchanges', memState.exchanges],
                          ['facts', memState.facts],
                          ['persist', memState.persist ? 'on' : 'off'],
                        ] as const).map(([k, v]) => (
                          <div key={k} className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md border border-white/[0.06] bg-white/[0.02]">
                            <span className="text-[10px] text-gray-600">{k}</span>
                            <span className="font-mono text-[10px] text-emerald-300 truncate">{String(v ?? '—')}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-[11px] text-gray-600">Memory subsystem not reporting.</p>
                    )}
                  </Field>

                  <Field label="Notes carried into a run" hint="Library memory notes attached as context whenever this agent is run from here">
                    {notes.length === 0 ? (
                      <p className="text-[11px] text-gray-600 leading-relaxed">
                        No memory notes in the library yet. Write one in the MEMORY tab and it shows up here to bind.
                      </p>
                    ) : (
                      <div className="space-y-1">
                        {notes.map(n => {
                          const on = noteIds.includes(n.id)
                          const title = n.name || (n.content || n.id).slice(0, 70)
                          return (
                            <button key={n.id}
                              onClick={() => setNoteIds(ids => on ? ids.filter(i => i !== n.id) : [...ids, n.id])}
                              className={`w-full text-left px-2.5 py-1.5 rounded-lg border flex items-start gap-2 transition ${
                                on ? 'bg-violet-400/[0.07] border-violet-400/25' : 'border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05]'
                              }`}>
                              <span className={`text-[11px] shrink-0 ${on ? 'text-violet-300' : 'text-gray-700'}`}>{on ? '●' : '○'}</span>
                              <span className="min-w-0">
                                <span className="block text-[11px] text-gray-200 truncate">{title}</span>
                                {!!n.tags?.length && (
                                  <span className="block font-mono text-[9px] text-gray-600">{n.tags.join(' · ')}</span>
                                )}
                              </span>
                            </button>
                          )
                        })}
                      </div>
                    )}
                  </Field>

                  <div className="flex items-center gap-2">
                    <button onClick={save} disabled={!canEdit || busy}
                      className="px-4 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-emerald-500/25 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-40 transition">
                      {busy ? 'saving…' : saved ? 'saved ✓' : 'save memory'}
                    </button>
                    <span className="text-[10px] text-gray-600 leading-relaxed">
                      the module is written into mod.py; the notes bind per run, so they are remembered here and sent with each one
                    </span>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {host && (
        <div className="shrink-0 border-t border-white/[0.06] px-3 py-1.5 text-[9px] font-mono text-gray-700 truncate">
          host {host.slice(0, 10)}… — owns every agent nobody else claimed
        </div>
      )}
    </div>
  )
}
