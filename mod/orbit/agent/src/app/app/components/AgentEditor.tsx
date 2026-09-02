'use client'

// AgentEditor — create and change an agent from the rail, without leaving the
// console.
//
// The AGENT canvas wires the same thing as a graph; this is that agent as a
// form, docked next to the list it edits, so making one and running it is one
// place instead of two views. Every field maps 1:1 onto the config the API
// stores, and nothing here is canvas-only:
//
//   name / icon / description  — how it reads in the list
//   goal                       — its system prompt
//   model                      — a provider override ('' = the console's model)
//   tools                      — the exact loadout (empty = every tool)
//   harness                    — hand the run to a CLI instead of this loop
//
// New agents POST /agents; an existing one PUTs, using the explicit clear_*
// flags so emptying a field means "unset" rather than "not passed".

import { useState, useEffect, useCallback, useMemo } from 'react'
import { API_URL } from '../config'
import Select from './Select'

type ToolInfo = { description?: string; kind?: string }
type Toolbox = { name: string; description?: string; tools: string[]; builtin?: boolean }
type Harness = { name: string; label?: string; description?: string; available?: boolean; install?: string }
type ProviderInfo = { key: string; models: string[]; default_model: string; configured?: boolean; keyless?: boolean; free?: boolean }

type Props = {
  /** null = a fresh agent; a slug = edit that one */
  name: string | null
  /** prefill a NEW agent from this one — the fork path, for an agent you
      can't change because it isn't yours */
  from?: string | null
  token?: string
  isHost: boolean
  /** the signed-in address, so the editor knows whose agent this is */
  address?: string | null
  /** the caller's default agent — the one an unnamed run lands on */
  defaultAgent?: string | null
  /** called with the slug that was written, after a successful save */
  onSaved: (name: string) => void
  onClose: () => void
  /** hand this agent to the full canvas (tool graph, memory nodes, prompts) */
  onOpenCanvas: (name: string | null) => void
  /** save-and-run: select the agent in the console */
  onUse?: (name: string) => void
  /** make the saved agent the one unnamed runs land on */
  onMakeDefault?: (name: string) => Promise<string | null> | void
}

// the icons a terminal skin can actually draw — a picker beats a text field
// nobody knows what to type into
const ICONS = ['>_', '◆', '△', '◉', '⬡', '⟳', '✦', '◎', '◇', '☰', '⌘', '¶', '⚑', '★']

const slugify = (s: string) =>
  s.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)

// the name a copy is offered under. Not the source name — saving would then
// be a 403 (or, for the host, an overwrite of the thing being copied)
const copyName = (s: string) => slugify(`${s}-copy`)

// the palette search doubles as a fleet search once typing stops — hundreds of
// modules, so they're fetched per query rather than held
function useFleetSearch(query: string) {
  const [fleet, setFleet] = useState<Record<string, ToolInfo>>({})
  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) { setFleet({}); return }
    const t = setTimeout(() => {
      fetch(`${API_URL}/tools?mods=true&q=${encodeURIComponent(q)}&limit=25`,
        { signal: AbortSignal.timeout(10000) })
        .then(r => r.json())
        .then(d => setFleet(Object.fromEntries((d.tools || [])
          .filter((t: any) => t.kind === 'mod')
          .map((t: any) => [t.name, { description: t.description, kind: 'mod' }]))))
        .catch(() => {})
    }, 250)
    return () => clearTimeout(t)
  }, [query])
  return fleet
}

export default function AgentEditor({
  name, from, token, isHost, address, defaultAgent,
  onSaved, onClose, onOpenCanvas, onUse, onMakeDefault,
}: Props) {
  // `copy` is the save-as-new path: you opened an agent, changed it, and the
  // change belongs in an agent of your own rather than in that one. It is a
  // mode rather than a second form, because everything on screen is already
  // what the copy would be.
  const [copy, setCopy] = useState(!!from)
  const stored = !!name        // an agent that exists on the server
  const isNew = !stored || copy

  // ── the agent being written ──
  const [slug, setSlug] = useState(name || '')
  const [icon, setIcon] = useState('>_')
  const [description, setDescription] = useState('')
  const [goal, setGoal] = useState('')
  const [model, setModel] = useState('')
  const [provider, setProvider] = useState('openrouter')
  const [harness, setHarness] = useState('')
  const [tools, setTools] = useState<string[]>([])
  const [builtin, setBuiltin] = useState(false)
  // who the loaded agent belongs to — 'host' means nobody recorded one, so
  // the module owner does
  const [ownerAddr, setOwnerAddr] = useState<string | null>(null)
  const [ownerSource, setOwnerSource] = useState<string | null>(null)
  // tick this after saving so "make it my default" runs on the new slug
  const [makeDefault, setMakeDefault] = useState(false)
  const [defaultBusy, setDefaultBusy] = useState(false)

  // ── catalogues ──
  const [allTools, setAllTools] = useState<Record<string, ToolInfo>>({})
  const [boxes, setBoxes] = useState<Toolbox[]>([])
  const [harnesses, setHarnesses] = useState<Harness[]>([])
  const [providers, setProviders] = useState<ProviderInfo[]>([])

  const [loading, setLoading] = useState(!isNew)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [showTools, setShowTools] = useState(false)
  const [toolQ, setToolQ] = useState('')
  const fleet = useFleetSearch(toolQ)

  useEffect(() => {
    fetch(`${API_URL}/tools`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => setAllTools(Object.fromEntries((d.tools || [])
        .map((t: any) => [t.name, { description: t.description, kind: t.kind }]))))
      .catch(() => {})
    fetch(`${API_URL}/toolboxes`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setBoxes(d.toolboxes || [])).catch(() => {})
    fetch(`${API_URL}/harnesses`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setHarnesses(d.harnesses || [])).catch(() => {})
    fetch(`${API_URL}/providers`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setProviders(d.providers || [])).catch(() => {})
  }, [])

  // editing: the stored config is the truth, not the list row's summary.
  // Forking loads the same config and files it under a name of your own —
  // one fetch, two destinations.
  const source = name || from || null
  useEffect(() => {
    if (!source) return
    let live = true
    setLoading(true)
    fetch(`${API_URL}/agents/${encodeURIComponent(source)}`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(cfg => {
        if (!live || cfg?.error) { if (live) setMsg({ ok: false, text: cfg?.error || 'not found' }); return }
        setSlug(name ? name : copyName(source))
        setIcon(cfg.icon || '>_')
        setDescription(cfg.description || '')
        setGoal(cfg.goal || '')
        setModel(cfg.model || '')
        // a copy made by anyone but the host drops the harness: the CLI runs
        // on the host's own shell, so carrying it over would mint an agent
        // its own author is refused at run time. The field isn't even shown
        // to them, so it would be an invisible one at that.
        setHarness(!name && !isHost ? '' : cfg.harness || '')
        setTools(Array.isArray(cfg.tools) ? cfg.tools : [])
        // a copy is a new agent under your address — never a built-in
        setBuiltin(!name ? false : !!cfg.builtin)
        setOwnerAddr(cfg.owner || null)
        setOwnerSource(cfg.owner_source || null)
      })
      .catch(e => { if (live) setMsg({ ok: false, text: e?.message || 'load failed' }) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [source, name])

  // whether the agent on screen is one this caller may write back to. Anything
  // else is still editable — it just saves as a copy of your own.
  const mine = ownerSource === 'item' && !!address &&
    (ownerAddr || '').toLowerCase() === address.toLowerCase()
  const canWriteBack = stored && (isHost || mine)

  // an agent you can't change opens straight in copy mode: the form is not
  // read-only, it just lands somewhere you own
  useEffect(() => {
    if (stored && !loading && !canWriteBack && !copy) {
      setCopy(true)
      setSlug(s => (s === name ? copyName(name!) : s))
    }
  }, [stored, loading, canWriteBack, copy, name])

  // a model belongs to a provider — keep the two in step when one is loaded
  useEffect(() => {
    if (!model || !providers.length) return
    const p = providers.find(p => p.models.includes(model))
    if (p) setProvider(p.key)
  }, [model, providers])

  const toggleTool = (t: string) =>
    setTools(cur => cur.includes(t) ? cur.filter(x => x !== t) : [...cur, t])

  // a box is on when every tool in it is; clicking it adds or removes the lot
  const toggleBox = (box: Toolbox) => {
    const on = box.tools.every(t => tools.includes(t))
    setTools(cur => on
      ? cur.filter(t => !box.tools.includes(t))
      : Array.from(new Set([...cur, ...box.tools])))
  }

  const shown = useMemo(() => {
    const q = toolQ.trim().toLowerCase()
    const merged: Record<string, ToolInfo> = { ...allTools, ...fleet }
    // whatever is already picked stays visible, so nothing switches on
    // off-screen where it can't be switched back off
    return Object.entries(merged)
      .filter(([n, i]) => !q || n.toLowerCase().includes(q) || (i.description || '').toLowerCase().includes(q))
      .sort(([a], [b]) => (tools.includes(b) ? 1 : 0) - (tools.includes(a) ? 1 : 0) || a.localeCompare(b))
  }, [allTools, fleet, toolQ, tools])

  const flash = (ok: boolean, text: string) => setMsg({ ok, text })

  const save = useCallback(async (thenUse: boolean) => {
    const s = isNew ? slugify(slug) : name!
    if (!s) { flash(false, 'give the agent a name'); return }
    if (isNew && s === name) { flash(false, 'a copy needs a name of its own'); return }
    if (!goal.trim()) { flash(false, 'an agent needs a system prompt'); return }
    if (!token) { flash(false, 'sign in to save an agent'); return }
    if (!isNew && builtin && !isHost) { flash(false, `"${s}" is a built-in — only the host can change it. Save it as a new agent instead.`); return }
    setSaving(true)
    try {
      const common = { description, goal, icon }
      const res = isNew
        ? await fetch(`${API_URL}/agents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              name: s, ...common, key: token,
              tools: tools.length ? tools : null,
              model: model || null,
              // one rule, enforced where it counts: only the host can mint an
              // agent that hands its run to a CLI on the host's own shell
              harness: (isHost && harness) || null,
            }),
          })
        : await fetch(`${API_URL}/agents/${encodeURIComponent(s)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ...common, key: token,
              ...(tools.length ? { tools } : { clear_tools: true }),
              ...(model ? { model } : { clear_model: true }),
              ...(harness ? { harness } : { clear_harness: true }),
            }),
          })
      const data = await res.json()
      if (data?.error) { flash(false, data.error); return }
      let note = isNew ? `created "${s}"` : `saved "${s}"`
      // the default rides along with the save, so "this is the one I want to
      // land on" is the same click as writing it
      if (makeDefault && onMakeDefault) {
        const err = await onMakeDefault(s)
        note = err ? `${note} — default not set: ${err}` : `${note} · now your default`
        if (!err) setMakeDefault(false)
      }
      flash(true, note)
      onSaved(s)
      if (thenUse) onUse?.(s)
    } catch (e: any) {
      flash(false, e?.message || 'save failed')
    } finally {
      setSaving(false)
    }
  }, [isNew, slug, name, goal, token, builtin, isHost, description, icon, tools,
      model, harness, makeDefault, onMakeDefault, onSaved, onUse])

  // make the agent already on the server the default, without a save
  const setAsDefault = useCallback(async () => {
    if (!onMakeDefault || !name) return
    setDefaultBusy(true)
    const err = await onMakeDefault(name)
    setDefaultBusy(false)
    flash(!err, err ? err : `"${name}" is now your default agent`)
  }, [onMakeDefault, name])

  const field = 'w-full bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition'
  const legend = 'text-[9px] uppercase tracking-wider text-gray-600 mb-1 flex items-center gap-1.5'

  const providerModels = providers.find(p => p.key === provider)?.models || []

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* head — what you're editing, and the way out */}
      <div className="px-2.5 py-2 border-b border-white/[0.06] shrink-0 flex items-center gap-2">
        <button onClick={onClose}
          className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-gray-500 hover:text-gray-300 transition shrink-0"
          title="Back to the agent list">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          agents
        </button>
        <span className="text-[10px] text-gray-400 truncate min-w-0">
          {!stored ? (from ? `new — from ${from}` : 'new agent') : copy ? `copy of ${name}` : slug}
        </span>
        {builtin && !isNew && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-white/[0.06] text-gray-500 shrink-0">built-in</span>
        )}
        {stored && !copy && name === defaultAgent && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/15 text-emerald-300/90 shrink-0"
            title="unnamed runs land on this agent">default</span>
        )}
        <button onClick={() => onOpenCanvas(stored && !copy ? name : null)}
          className="ml-auto text-[10px] px-1.5 py-0.5 rounded border border-violet-400/25 text-violet-300/90 hover:bg-violet-500/10 transition shrink-0"
          title="Open this agent on the canvas — the same fields, wired as a graph">
          canvas ↗
        </button>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-xs text-gray-600">loading…</div>
      ) : (
      <div className="flex-1 overflow-y-auto min-h-0 p-2.5 space-y-3">
        {/* identity */}
        <div>
          <div className={legend}>name</div>
          <div className="flex items-center gap-1.5">
            <Select value={icon} accent="emerald" size="sm" className="w-14 shrink-0"
              title="Icon" onChange={setIcon}
              options={ICONS.map(i => ({ value: i, label: i }))} />
            <input
              value={slug}
              disabled={!isNew}
              onChange={e => setSlug(e.target.value)}
              onBlur={() => isNew && setSlug(s => slugify(s))}
              placeholder="my-agent"
              className={`${field} font-mono ${!isNew ? 'opacity-60 cursor-not-allowed' : ''}`}
            />
          </div>
          {isNew && slug && slugify(slug) !== slug && (
            <div className="text-[9px] text-gray-600 mt-1 font-mono">saves as {slugify(slug) || '—'}</div>
          )}
          {/* Two ways out of "I changed an agent that isn't mine to change":
              the name field is live in copy mode, and the button below turns
              any edit into one. A name is an agent's identity, so renaming is
              the same operation as copying. */}
          {stored && !copy && (
            <div className="flex items-center gap-1.5 mt-1">
              <span className="text-[9px] text-gray-600 min-w-0 truncate">
                {canWriteBack ? 'saving writes back to this agent' : "this agent isn't yours"}
              </span>
              <button onClick={() => { setCopy(true); setSlug(copyName(name!)); setMsg(null) }}
                className="ml-auto shrink-0 text-[9px] px-1.5 py-0.5 rounded border border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10 transition"
                title="Keep this agent as it is and save what's on screen as a new one of your own">
                save as new
              </button>
            </div>
          )}
          {stored && copy && (
            <div className="flex items-center gap-1.5 mt-1">
              <span className="text-[9px] text-emerald-300/80 min-w-0 truncate">
                saves as a new agent — {name} is left as it is
              </span>
              {canWriteBack && (
                <button onClick={() => { setCopy(false); setSlug(name!); setMsg(null) }}
                  className="ml-auto shrink-0 text-[9px] px-1.5 py-0.5 rounded border border-white/10 text-gray-500 hover:text-gray-300 transition"
                  title={`Write the changes back to ${name} instead`}>
                  edit {name} instead
                </button>
              )}
            </div>
          )}
          {!stored && from && (
            <div className="text-[9px] text-gray-600 mt-1">copied from {from} — it stays as it is</div>
          )}
        </div>

        <div>
          <div className={legend}>description</div>
          <input value={description} onChange={e => setDescription(e.target.value)}
            placeholder="what it's for — one line" className={field} />
        </div>

        {/* the actual agent */}
        <div>
          <div className={legend}>system prompt</div>
          <textarea value={goal} onChange={e => setGoal(e.target.value)} rows={8}
            placeholder="You are…&#10;&#10;How it should work: what to do first, what to never do, when it's done."
            className={`${field} resize-y leading-relaxed`} />
        </div>

        {/* model — empty is not "broken", it's the console's own pick */}
        <div>
          <div className={legend}>model</div>
          <div className="space-y-1.5">
            <Select value={provider} accent="sky" size="sm" className="w-full"
              onChange={v => { setProvider(v); setModel('') }}
              options={providers.map(p => ({
                value: p.key, label: p.key,
                hint: p.free ? 'free' : p.configured || p.keyless ? '' : 'no key',
              }))} />
            <Select value={model} accent="sky" size="sm" className="w-full" searchable
              placeholder="default model"
              onChange={setModel}
              options={[{ value: '', label: 'default model', hint: 'the console decides' },
                ...(model && !providerModels.includes(model) ? [{ value: model, label: model }] : []),
                ...providerModels.map(m => ({ value: m, label: m }))]} />
          </div>
          {!model && (
            <div className="text-[9px] text-gray-600 mt-1">no override — it runs on whatever the console is set to</div>
          )}
        </div>

        {/* tools — empty means every tool, which is the useful default */}
        <div>
          <div className={legend}>
            tools
            <span className="text-gray-700 normal-case tracking-normal">
              · {tools.length ? `${tools.length} picked` : 'all tools'}
            </span>
            <button onClick={() => setShowTools(s => !s)}
              className="ml-auto text-[9px] px-1.5 py-0.5 rounded border border-white/10 text-gray-500 hover:text-emerald-300 hover:border-emerald-500/30 transition">
              {showTools ? 'done' : 'pick'}
            </button>
          </div>

          {tools.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-1.5">
              {tools.map(t => (
                <button key={t} onClick={() => toggleTool(t)}
                  title={`Remove ${t}`}
                  className={`text-[9px] px-1.5 py-0.5 rounded border transition ${
                    t.startsWith('mod.')
                      ? 'border-violet-400/25 text-violet-300/90 hover:bg-violet-500/10'
                      : 'border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10'
                  }`}>
                  {t} ✕
                </button>
              ))}
              <button onClick={() => setTools([])}
                className="text-[9px] px-1.5 py-0.5 rounded border border-white/10 text-gray-500 hover:text-red-300 hover:border-red-400/30 transition"
                title="Back to every tool">clear</button>
            </div>
          )}

          {showTools && (
            <div className="border border-white/[0.07] rounded-md p-1.5 space-y-1.5 bg-white/[0.02]">
              {/* presets first — one click is usually the whole answer */}
              <div className="flex flex-wrap gap-1">
                {boxes.map(b => {
                  const on = b.tools.length > 0 && b.tools.every(t => tools.includes(t))
                  return (
                    <button key={b.name} onClick={() => toggleBox(b)} title={b.description}
                      className={`text-[9px] px-1.5 py-0.5 rounded border transition ${
                        on ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                           : 'border-white/10 text-gray-500 hover:text-gray-300'
                      }`}>
                      {b.name}
                    </button>
                  )
                })}
              </div>
              <input value={toolQ} onChange={e => setToolQ(e.target.value)}
                placeholder="filter tools — 2+ letters also searches the fleet…"
                className={field} />
              <div className="max-h-56 overflow-y-auto space-y-0.5 pr-0.5">
                {shown.length === 0 ? (
                  <div className="text-[10px] text-gray-600 text-center py-4">nothing matches</div>
                ) : shown.map(([n, info]) => {
                  const on = tools.includes(n)
                  return (
                    <button key={n} onClick={() => toggleTool(n)}
                      className={`w-full text-left px-1.5 py-1 rounded flex items-center gap-1.5 transition ${
                        on ? 'bg-emerald-500/10 text-gray-200' : 'text-gray-500 hover:bg-white/[0.04]'
                      }`}>
                      <span className={`w-3 shrink-0 text-[10px] ${on ? 'text-emerald-300' : 'text-gray-700'}`}>
                        {on ? '✓' : '·'}
                      </span>
                      {/* the name is the thing you're picking — it never gives
                          its room up to the description */}
                      <span className={`text-[10px] font-mono shrink-0 ${info.kind === 'mod' ? 'text-violet-300/90' : ''}`}>{n}</span>
                      {info.description && (
                        <span className="text-[9px] text-gray-600 truncate min-w-0">{info.description}</span>
                      )}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {/* harness — the run leaves this loop entirely, so it's host-gated */}
        {isHost && (
          <div>
            <div className={legend}>harness</div>
            <Select value={harness} accent="violet" size="sm" className="w-full"
              placeholder="this module's own loop"
              onChange={setHarness}
              options={[{ value: '', label: 'native', hint: "this module's own loop" },
                ...harnesses.map(h => ({
                  value: h.name, label: h.label || h.name,
                  hint: h.available === false ? 'not installed' : h.description,
                }))]} />
            <div className="text-[9px] text-gray-600 mt-1 leading-relaxed">
              {harness
                ? 'the model, tools and prompt above are ignored — the CLI brings its own. Its events still render as steps in the console.'
                : 'the run stays on this module’s own loop'}
            </div>
          </div>
        )}

        {msg && (
          <div className={`text-[10px] px-2 py-1.5 rounded-md border ${
            msg.ok ? 'border-emerald-500/25 text-emerald-300 bg-emerald-500/[0.07]'
                   : 'border-red-500/25 text-red-300 bg-red-500/[0.07]'
          }`}>
            {msg.text}
          </div>
        )}
      </div>
      )}

      {/* foot — the default line, then save / save and run as it */}
      <div className="border-t border-white/[0.06] shrink-0">
        {onMakeDefault && (
          <div className="px-2.5 pt-2 flex items-center gap-1.5">
            {stored && !copy && name === defaultAgent ? (
              <span className="text-[9px] text-emerald-300/80 flex items-center gap-1">
                <span>★</span> your default — every unnamed run lands here
              </span>
            ) : stored && !copy ? (
              <button onClick={setAsDefault} disabled={defaultBusy || loading}
                className="text-[9px] px-1.5 py-0.5 rounded border border-white/10 text-gray-500 hover:text-emerald-300 hover:border-emerald-500/30 disabled:opacity-50 transition"
                title="Make this the agent unnamed runs land on">
                {defaultBusy ? '…' : '★ make it my default'}
              </button>
            ) : (
              <label className="text-[9px] text-gray-500 flex items-center gap-1.5 cursor-pointer select-none"
                title="Set it as the agent unnamed runs land on, as soon as it saves">
                <span className={`w-3 h-3 rounded-sm border flex items-center justify-center text-[8px] ${
                  makeDefault ? 'bg-emerald-500/25 border-emerald-400/50 text-emerald-200'
                              : 'border-white/20 text-transparent'
                }`}>✓</span>
                <input type="checkbox" className="hidden" checked={makeDefault}
                  onChange={e => setMakeDefault(e.target.checked)} />
                make it my default agent
              </label>
            )}
          </div>
        )}
        <div className="px-2.5 py-2 flex items-center gap-1.5">
          <button onClick={() => save(false)} disabled={saving || loading}
            className="flex-1 px-2 py-1.5 rounded-md text-[10px] uppercase tracking-wider border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50 transition">
            {saving ? '…' : !stored ? 'create' : copy ? 'save as new' : 'save'}
          </button>
          {onUse && (
            <button onClick={() => save(true)} disabled={saving || loading}
              className="px-2 py-1.5 rounded-md text-[10px] uppercase tracking-wider border border-white/10 text-gray-500 hover:text-emerald-300 hover:border-emerald-500/30 disabled:opacity-50 transition"
              title="Save it and run the next message as this agent">
              save + use
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
