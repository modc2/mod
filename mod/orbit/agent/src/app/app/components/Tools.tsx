'use client'

// Tools — the console's TOOLS tab.
//
// Two things live here. TRACE is what the selected run actually called, step
// by step. REGISTRY is what it *could* call: every shipped skill plus the
// custom tools added right here. A custom tool is a described, parameterised
// shell command — the agent sees it in its tool list exactly like a built-in.
//
// The registry is also where the loadout is set — which of those tools the
// model actually gets. A toolbox chip snaps a whole bundle on; the per-tool
// switch refines from there into an exact list; "save as box" turns whatever
// you landed on into a named bundle you can snap back later.

import { useState, useEffect, useCallback } from 'react'
import { API_URL } from '../config'

export type ToolParam = { type?: string; required?: boolean; default?: any; hint?: string }
export type ToolEntry = {
  name: string
  kind: 'skill' | 'custom'
  builtin: boolean
  active: boolean
  description: string
  params: Record<string, ToolParam>
  command?: string
  cwd?: string | null
  timeout?: number
  owner?: string | null
  cid?: string | null
}
type Snapped = { snapped: string[]; skills: string[]; filtered: boolean; source?: 'all' | 'toolboxes' | 'selection' }
type Box = { name: string; description: string; tools: string[]; builtin: boolean }

const shortAddr = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`

// every {placeholder} in a command template, in order, de-duped — the same
// rule the server uses to infer params, so the form previews what it'll get
const placeholders = (cmd: string): string[] => {
  const found = cmd.match(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g) || []
  return Array.from(new Set(found.map(p => p.slice(1, -1)))).filter(p => p !== 'cwd' && p !== 'timeout')
}

export default function Tools({ token, isHost, onCount }: {
  token?: string | null
  isHost: boolean
  onCount?: (counts: { total: number; custom: number }) => void
}) {
  const [tools, setTools] = useState<ToolEntry[]>([])
  const [boxes, setBoxes] = useState<Box[]>([])
  const [snapped, setSnapped] = useState<Snapped | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'skill' | 'custom' | 'active'>('all')
  const [open, setOpen] = useState<string | null>(null)
  const [editing, setEditing] = useState<ToolEntry | 'new' | null>(null)
  const [saveBox, setSaveBox] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(() => {
    fetch(`${API_URL}/tools`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => {
        const list: ToolEntry[] = d.tools || []
        setTools(list)
        setBoxes(d.toolboxes || [])
        setSnapped(d.snapped || null)
        setLoaded(true)
        onCount?.({ total: list.length, custom: list.filter(t => t.kind === 'custom').length })
      })
      .catch(() => setLoaded(true))
  }, [onCount])

  useEffect(() => { load() }, [load])

  // snap/unsnap/select all answer with the new loadout — apply it in place so
  // the switches flip without a round trip through the registry
  const applyState = (s: any) => {
    if (s?.error) { setErr(s.error); return }
    if (!Array.isArray(s?.skills)) { load(); return }
    const on = new Set<string>(s.skills)
    setErr(null)
    setSnapped(s)
    setTools(ts => ts.map(t => ({ ...t, active: on.has(t.name) })))
  }

  const keyq = token ? `?key=${encodeURIComponent(token)}` : ''

  const toggleBox = async (b: Box) => {
    const verb = snapped?.snapped?.includes(b.name) ? 'unsnap' : 'snap'
    try {
      applyState(await fetch(`${API_URL}/toolboxes/${encodeURIComponent(b.name)}/${verb}${keyq}`,
        { method: 'POST' }).then(r => r.json()))
    } catch (e: any) { setErr(e?.message || `${verb} failed`) }
  }

  const select = async (names: string[] | null) => {
    try {
      applyState(await fetch(`${API_URL}/tools/select`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tools: names, key: token }),
      }).then(r => r.json()))
    } catch (e: any) { setErr(e?.message || 'select failed') }
  }

  // one call: drops every snapped box and any hand-picked list
  const resetLoadout = async () => {
    try {
      applyState(await fetch(`${API_URL}/toolboxes/unsnap${keyq}`, { method: 'POST' }).then(r => r.json()))
    } catch (e: any) { setErr(e?.message || 'reset failed') }
  }

  const toggleTool = (t: ToolEntry) => {
    const next = tools.filter(x => (x.name === t.name ? !x.active : x.active)).map(x => x.name)
    if (next.length === 0) { setErr('a loadout needs at least one tool'); return }
    select(next)
  }

  const removeBox = async (b: Box) => {
    if (!confirm(`Delete toolbox "${b.name}"?`)) return
    try {
      const r = await fetch(`${API_URL}/toolboxes/${encodeURIComponent(b.name)}${keyq}`,
        { method: 'DELETE' }).then(x => x.json())
      if (r?.error) { setErr(r.error); return }
      setErr(null)
      load()
    } catch (e: any) { setErr(e?.message || 'delete failed') }
  }

  const remove = async (t: ToolEntry) => {
    if (!confirm(`Delete tool "${t.name}"?`)) return
    try {
      const r = await fetch(`${API_URL}/tools/${encodeURIComponent(t.name)}${keyq}`, { method: 'DELETE' }).then(x => x.json())
      if (r?.error) { setErr(r.error); return }
      setErr(null)
      load()
    } catch (e: any) { setErr(e?.message || 'delete failed') }
  }

  const custom = tools.filter(t => t.kind === 'custom')
  const shown = tools.filter(t => {
    if (filter === 'skill' && t.kind !== 'skill') return false
    if (filter === 'custom' && t.kind !== 'custom') return false
    if (filter === 'active' && !t.active) return false
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return t.name.includes(q) || (t.description || '').toLowerCase().includes(q) ||
      (t.command || '').toLowerCase().includes(q)
  })

  const activeNames = tools.filter(t => t.active).map(t => t.name)

  if (editing) return (
    <ToolForm tool={editing === 'new' ? null : editing} token={token} boxes={boxes}
      onClose={() => setEditing(null)}
      onSaved={() => { setEditing(null); setErr(null); load() }} />
  )

  if (saveBox) return (
    <BoxForm tools={activeNames} token={token} onClose={() => setSaveBox(false)}
      onSaved={() => { setSaveBox(false); setErr(null); load() }} />
  )

  return (
    <div className="p-3 max-w-4xl mx-auto w-full space-y-2">
      {/* the loadout: which tools the model gets. a box snaps a bundle on,
          the switches below refine it, and what's left can become a box */}
      <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-gray-600">Loadout</span>
          <span className="text-[10px] text-gray-500">
            {snapped?.filtered
              ? <><span className="text-emerald-400">{snapped.skills.length}</span> of {tools.length} reach the model
                  {snapped.source === 'selection' && <span className="text-gray-600"> · hand-picked</span>}</>
              : <>all {tools.length} tools reach the model</>}
          </span>
          <button onClick={() => setSaveBox(true)} disabled={!activeNames.length}
            title="Save the current loadout as a toolbox"
            className="ml-auto px-2 py-0.5 rounded text-[10px] uppercase tracking-wider text-gray-600 hover:text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40 transition">
            save as box
          </button>
        </div>
        <div className="flex items-center gap-1 flex-wrap">
          <button onClick={resetLoadout} title="Every tool on"
            className={`px-2 py-0.5 rounded-full text-[10px] border transition ${
              snapped?.filtered ? 'bg-white/[0.03] border-white/[0.06] text-gray-600 hover:text-gray-300'
                                : 'bg-emerald-500/15 border-emerald-500/25 text-emerald-300'
            }`}>
            everything
          </button>
          {boxes.map(b => {
            const on = !!snapped?.snapped?.includes(b.name)
            return (
              <span key={b.name} className={`group inline-flex items-center rounded-full border transition ${
                on ? 'bg-emerald-500/15 border-emerald-500/25' : 'bg-white/[0.03] border-white/[0.06] hover:border-white/[0.12]'
              }`}>
                <button onClick={() => toggleBox(b)} title={b.description || b.name}
                  className={`px-2 py-0.5 text-[10px] transition ${on ? 'text-emerald-300' : 'text-gray-500 hover:text-gray-300'}`}>
                  {b.name}<span className="ml-1 text-gray-600">{b.tools.length}</span>
                </button>
                {!b.builtin && (
                  <button onClick={() => removeBox(b)} title="Delete this toolbox"
                    className="pr-1.5 text-[10px] text-gray-700 hover:text-red-400 transition">×</button>
                )}
              </span>
            )
          })}
        </div>
      </div>

      {/* what's on, and how to add more */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search tools…"
          className="flex-1 min-w-[140px] bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition" />
        {(['all', 'skill', 'custom', 'active'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-2.5 py-1.5 rounded-lg text-[10px] uppercase tracking-wider transition border ${
              filter === f ? 'bg-emerald-500/15 border-emerald-500/25 text-emerald-300'
                           : 'bg-white/[0.03] border-white/[0.06] text-gray-600 hover:text-gray-300'
            }`}>
            {f === 'skill' ? 'shipped' : f}
          </button>
        ))}
        <button onClick={() => { setErr(null); setEditing('new') }}
          title={isHost ? 'Define a new tool' : 'Custom tools run shell — the host adds these'}
          className="px-3 py-1.5 rounded-lg text-[10px] uppercase tracking-wider bg-emerald-600/90 hover:bg-emerald-500 text-white transition">
          + new tool
        </button>
      </div>

      <div className="flex items-center gap-2 text-[10px] text-gray-600">
        <span>{tools.length - custom.length} shipped</span>
        <span className="text-gray-800">·</span>
        <span className={custom.length ? 'text-emerald-400/70' : ''}>{custom.length} custom</span>
        {!!snapped?.snapped?.length && (
          <>
            <span className="text-gray-800">·</span>
            <span className="text-amber-400/80">{snapped.snapped.join(' + ')} snapped</span>
          </>
        )}
      </div>

      {err && <p className="text-xs text-red-400">{err}</p>}

      {!loaded ? (
        <p className="text-sm text-gray-600 text-center mt-8">Loading the registry…</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-gray-600 text-center mt-8">Nothing matches</p>
      ) : (
        <div className="space-y-0.5">
          {shown.map(t => (
            <ToolRow key={t.name} tool={t} token={token}
              open={open === t.name}
              onToggle={() => setOpen(open === t.name ? null : t.name)}
              onToggleActive={() => toggleTool(t)}
              onEdit={() => { setErr(null); setEditing(t) }}
              onDelete={() => remove(t)} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── one tool: a row that opens into its params, and for custom tools its
//    command and a try-it runner ────────────────────────────────────────
function ToolRow({ tool, token, open, onToggle, onToggleActive, onEdit, onDelete }: {
  tool: ToolEntry
  token?: string | null
  open: boolean
  onToggle: () => void
  onToggleActive: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const [args, setArgs] = useState<Record<string, string>>({})
  const [result, setResult] = useState<any>(null)
  const [running, setRunning] = useState(false)

  const names = Object.keys(tool.params || {})
  const isCustom = tool.kind === 'custom'

  const tryIt = async () => {
    setRunning(true)
    setResult(null)
    try {
      const r = await fetch(`${API_URL}/tools/${encodeURIComponent(tool.name)}/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params: args, key: token }),
      }).then(x => x.json())
      setResult(r.error ? { stderr: r.error, success: false } : r.result)
    } catch (e: any) {
      setResult({ stderr: e?.message || 'run failed', success: false })
    }
    setRunning(false)
  }

  return (
    <div className={`text-xs rounded-md border transition ${
      isCustom ? 'bg-emerald-500/[0.03] border-emerald-500/15 hover:border-emerald-500/25'
               : 'bg-white/[0.02] border-white/[0.05] hover:border-white/[0.1]'
    } ${tool.active ? '' : 'opacity-60'}`}>
      <div className="flex items-center gap-2 px-2.5 py-2">
        {/* the switch is the loadout: off means the model never sees this tool */}
        <button onClick={onToggleActive} title={tool.active ? 'On — click to drop it from the loadout' : 'Off — click to hand it to the model'}
          className={`w-6 h-3.5 rounded-full shrink-0 relative transition ${
            tool.active ? 'bg-emerald-500/70' : 'bg-white/10'
          }`}>
          <span className={`absolute top-0.5 w-2.5 h-2.5 rounded-full bg-black/60 transition-all ${
            tool.active ? 'left-3' : 'left-0.5'
          }`} />
        </button>
        <button className="flex-1 min-w-0 text-left flex items-center gap-2" onClick={onToggle}>
          <span className="text-gray-600 shrink-0">{open ? '▼' : '▶'}</span>
          <span className={`font-mono shrink-0 ${isCustom ? 'text-emerald-300' : 'text-sky-300'}`}>{tool.name}</span>
          {isCustom && <span className="text-[9px] uppercase tracking-wider text-emerald-500/70 shrink-0">custom</span>}
          <span className="text-gray-600 truncate">{tool.description}</span>
          {names.length > 0 && (
            <span className="ml-auto text-[10px] text-gray-700 font-mono shrink-0">{names.length}p</span>
          )}
        </button>
      </div>

      {open && (
        <div className="px-2.5 pb-2.5 space-y-2">
          {isCustom && tool.command && (
            <pre className="text-[11px] text-amber-300/80 overflow-x-auto border-l border-amber-500/25 pl-2 leading-relaxed">{tool.command}</pre>
          )}

          {names.length === 0 ? (
            <p className="text-[11px] text-gray-600">No parameters</p>
          ) : (
            <div className="space-y-1">
              {names.map(p => {
                const spec = tool.params[p] || {}
                return (
                  <div key={p} className="flex items-baseline gap-2">
                    <span className="font-mono text-[11px] text-gray-300 w-28 shrink-0 truncate">{p}</span>
                    <span className="text-[10px] text-gray-600 w-16 shrink-0">{cleanType(spec.type)}</span>
                    <span className={`text-[10px] shrink-0 ${spec.required ? 'text-amber-400/70' : 'text-gray-700'}`}>
                      {spec.required ? 'required' : spec.default !== undefined ? `= ${String(spec.default)}` : 'optional'}
                    </span>
                    {spec.hint && <span className="text-[10px] text-gray-600 truncate">{spec.hint}</span>}
                  </div>
                )
              })}
            </div>
          )}

          {isCustom && (
            <div className="pt-1.5 border-t border-white/[0.06] space-y-1.5">
              {names.length > 0 && (
                <div className="flex gap-1.5 flex-wrap">
                  {names.map(p => (
                    <input key={p} value={args[p] ?? ''} placeholder={p}
                      onChange={e => setArgs(a => ({ ...a, [p]: e.target.value }))}
                      className="flex-1 min-w-[110px] bg-white/[0.04] border border-white/[0.08] rounded px-2 py-1 text-[11px] font-mono text-gray-200 outline-none placeholder:text-gray-700 focus:border-emerald-500/40 transition" />
                  ))}
                </div>
              )}
              <div className="flex items-center gap-1.5">
                <button onClick={tryIt} disabled={running}
                  className="px-2.5 py-1 rounded text-[10px] uppercase tracking-wider bg-emerald-600/80 hover:bg-emerald-500 disabled:bg-white/5 disabled:text-gray-600 text-white transition">
                  {running ? 'running…' : 'try it'}
                </button>
                <button onClick={onEdit}
                  className="px-2.5 py-1 rounded text-[10px] uppercase tracking-wider text-gray-500 hover:text-emerald-300 hover:bg-emerald-500/10 transition">
                  edit
                </button>
                <button onClick={onDelete}
                  className="px-2.5 py-1 rounded text-[10px] uppercase tracking-wider text-gray-600 hover:text-red-400 hover:bg-red-500/10 transition">
                  delete
                </button>
                <span className="ml-auto text-[10px] text-gray-700 truncate">
                  {tool.owner ? `by ${shortAddr(tool.owner)}` : 'host'}
                  {tool.timeout ? ` · ${tool.timeout}s` : ''}
                </span>
              </div>
              {result && (
                <pre className={`text-[11px] overflow-x-auto max-h-52 leading-relaxed border-l pl-2 ${
                  result.success ? 'text-gray-400 border-emerald-500/20' : 'text-red-400 border-red-500/30'
                }`}>
                  {result.command ? `$ ${result.command}\n` : ''}
                  {result.stdout || ''}{result.stderr || ''}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── save the live loadout as a toolbox: a set you tuned once, snappable
//    from then on ────────────────────────────────────────────────────────
function BoxForm({ tools, token, onClose, onSaved }: {
  tools: string[]
  token?: string | null
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const save = async () => {
    if (!name.trim()) { setErr('a box needs a name'); return }
    setSaving(true)
    setErr(null)
    try {
      const r = await fetch(`${API_URL}/toolboxes`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), tools, description: description.trim(), key: token }),
      }).then(x => x.json())
      if (r?.error) { setErr(r.error); setSaving(false); return }
      onSaved()
    } catch (e: any) {
      setErr(e?.message || 'save failed')
      setSaving(false)
    }
  }

  return (
    <div className="p-3 max-w-3xl mx-auto w-full space-y-2.5">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-300 font-medium">Save loadout as toolbox</span>
        <button onClick={onClose} className="ml-auto text-[10px] uppercase tracking-wider text-gray-600 hover:text-gray-300 transition">
          cancel
        </button>
      </div>

      <div className="flex gap-1.5">
        <input value={name} onChange={e => setName(e.target.value)} placeholder="box-name" autoFocus
          className="w-44 bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-xs font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition" />
        <input value={description} onChange={e => setDescription(e.target.value)}
          placeholder="What this loadout is for"
          className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition" />
      </div>

      <div className="border-t border-white/[0.06] pt-2">
        <p className="text-[10px] uppercase tracking-wider text-gray-600 mb-1">{tools.length} tools</p>
        <div className="flex gap-1 flex-wrap">
          {tools.map(t => (
            <span key={t} className="px-1.5 py-0.5 rounded bg-white/[0.04] border border-white/[0.06] text-[10px] font-mono text-gray-400">{t}</span>
          ))}
        </div>
      </div>

      {err && <p className="text-xs text-red-400">{err}</p>}

      <div className="flex items-center gap-2 pt-1">
        <button onClick={save} disabled={saving}
          className="px-4 py-1.5 rounded-lg text-[11px] font-medium bg-emerald-600/90 hover:bg-emerald-500 disabled:bg-white/5 disabled:text-gray-600 text-white transition">
          {saving ? 'Saving…' : 'Create toolbox'}
        </button>
        <span className="text-[10px] text-gray-600">
          Snap it back any time from the loadout bar. Built-in presets can&apos;t be overwritten.
        </span>
      </div>
    </div>
  )
}

// python annotations arrive as "<class 'str'>" — show the word, not the repr
const cleanType = (t?: string) => {
  if (!t) return 'any'
  const m = t.match(/'([^']+)'/)
  return (m ? m[1] : t).replace('typing.', '')
}

// ── the tool editor: a command template is the whole definition ─────────
function ToolForm({ tool, token, boxes, onClose, onSaved }: {
  tool: ToolEntry | null
  token?: string | null
  boxes: Box[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(tool?.name || '')
  const [description, setDescription] = useState(tool?.description || '')
  const [command, setCommand] = useState(tool?.command || '')
  const [cwd, setCwd] = useState(tool?.cwd || '')
  const [timeout, setTimeoutS] = useState(String(tool?.timeout ?? 60))
  const [specs, setSpecs] = useState<Record<string, ToolParam>>(tool?.params || {})
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // the template drives the param list — type a {placeholder}, get a param
  const found = placeholders(command)
  useEffect(() => {
    setSpecs(prev => {
      const next: Record<string, ToolParam> = {}
      for (const p of found) next[p] = prev[p] || { type: 'string', required: true }
      return next
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [command])

  const setSpec = (p: string, patch: Partial<ToolParam>) =>
    setSpecs(s => ({ ...s, [p]: { ...s[p], ...patch } }))

  const save = async () => {
    if (!name.trim() || !command.trim()) { setErr('name and command are required'); return }
    setSaving(true)
    setErr(null)
    try {
      const r = await fetch(`${API_URL}/tools`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(), command: command.trim(), description: description.trim(),
          params: specs, cwd: cwd.trim() || null, timeout: Number(timeout) || 60, key: token,
        }),
      }).then(x => x.json())
      if (r?.error) { setErr(r.error); setSaving(false); return }
      onSaved()
    } catch (e: any) {
      setErr(e?.message || 'save failed')
      setSaving(false)
    }
  }

  const usedIn = boxes.filter(b => tool && b.tools.includes(tool.name)).map(b => b.name)

  return (
    <div className="p-3 max-w-3xl mx-auto w-full space-y-2.5">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-300 font-medium">{tool ? `Edit ${tool.name}` : 'New tool'}</span>
        <button onClick={onClose} className="ml-auto text-[10px] uppercase tracking-wider text-gray-600 hover:text-gray-300 transition">
          cancel
        </button>
      </div>

      <div className="flex gap-1.5">
        <input value={name} onChange={e => setName(e.target.value)} disabled={!!tool}
          placeholder="tool-name"
          className="w-44 bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-xs font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 disabled:text-gray-500 transition" />
        <input value={description} onChange={e => setDescription(e.target.value)}
          placeholder="What it does — this is what the model reads to decide when to call it"
          className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition" />
      </div>

      <div>
        <textarea value={command} onChange={e => setCommand(e.target.value)} rows={3}
          placeholder="rg --json {pattern} {path}"
          className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-2 text-xs font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition resize-none leading-relaxed" />
        <p className="mt-1 text-[10px] text-gray-600">
          Shell command. Every <span className="font-mono text-amber-300/70">{'{placeholder}'}</span> becomes a
          parameter and is quoted when it's filled in, so a value can't start a second command.
        </p>
      </div>

      {found.length > 0 && (
        <div className="space-y-1 border-t border-white/[0.06] pt-2">
          <p className="text-[10px] uppercase tracking-wider text-gray-600">Parameters</p>
          {found.map(p => {
            const spec = specs[p] || {}
            return (
              <div key={p} className="flex items-center gap-1.5">
                <span className="font-mono text-[11px] text-emerald-300 w-28 shrink-0 truncate">{p}</span>
                <select value={spec.type || 'string'} onChange={e => setSpec(p, { type: e.target.value })}
                  className="bg-white/[0.04] border border-white/[0.08] rounded px-1.5 py-1 text-[10px] text-gray-300 outline-none focus:border-emerald-500/40 transition">
                  <option value="string">string</option>
                  <option value="number">number</option>
                  <option value="boolean">boolean</option>
                </select>
                <label className="flex items-center gap-1 text-[10px] text-gray-500 cursor-pointer shrink-0">
                  <input type="checkbox" checked={spec.required !== false}
                    onChange={e => setSpec(p, { required: e.target.checked })}
                    className="accent-emerald-500" />
                  required
                </label>
                <input value={spec.default ?? ''} onChange={e => setSpec(p, { default: e.target.value || undefined })}
                  placeholder="default"
                  className="w-24 bg-white/[0.04] border border-white/[0.08] rounded px-1.5 py-1 text-[10px] font-mono text-gray-300 outline-none placeholder:text-gray-700 focus:border-emerald-500/40 transition" />
                <input value={spec.hint ?? ''} onChange={e => setSpec(p, { hint: e.target.value || undefined })}
                  placeholder="hint for the model"
                  className="flex-1 min-w-0 bg-white/[0.04] border border-white/[0.08] rounded px-1.5 py-1 text-[10px] text-gray-300 outline-none placeholder:text-gray-700 focus:border-emerald-500/40 transition" />
              </div>
            )
          })}
        </div>
      )}

      <div className="flex gap-1.5 items-center">
        <input value={cwd} onChange={e => setCwd(e.target.value)} placeholder="working directory (optional)"
          className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-[11px] font-mono text-gray-300 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition" />
        <input value={timeout} onChange={e => setTimeoutS(e.target.value.replace(/\D/g, ''))} placeholder="60"
          className="w-20 bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-[11px] font-mono text-gray-300 outline-none text-center focus:border-emerald-500/40 transition" />
        <span className="text-[10px] text-gray-600">sec</span>
      </div>

      {usedIn.length > 0 && (
        <p className="text-[10px] text-gray-600">Bundled in toolbox: {usedIn.join(', ')}</p>
      )}
      {err && <p className="text-xs text-red-400">{err}</p>}

      <div className="flex items-center gap-2 pt-1">
        <button onClick={save} disabled={saving}
          className="px-4 py-1.5 rounded-lg text-[11px] font-medium bg-emerald-600/90 hover:bg-emerald-500 disabled:bg-white/5 disabled:text-gray-600 text-white transition">
          {saving ? 'Saving…' : tool ? 'Replace tool' : 'Create tool'}
        </button>
        <span className="text-[10px] text-gray-600">
          Custom tools run shell on the host — adding one needs admin access.
        </span>
      </div>
    </div>
  )
}
