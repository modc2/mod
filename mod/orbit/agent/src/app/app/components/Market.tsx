'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import { API_URL } from '../config'
import Upload from './Upload'
import type { LibItem, LibKind, LibAuth } from './Library'

// ── the market rail ─────────────────────────────────────────────────
// The full Library is a page you go to; this is the same catalogue docked
// beside the console so a run can be dressed without leaving it. Clicking a
// row attaches it to the next run — prompt as system prompt, memory note and
// installed tool doc as context, agent as the persona. The + in the header adds
// to the market from here: an agent (this module's loop, or an external CLI
// harness like Claude Code / Codex), a prompt, or a memory note.

const KINDS: Record<LibKind, { plural: string; glyph: string; dot: string; on: string; chip: string }> = {
  prompt: {
    plural: 'Prompts', glyph: '¶', dot: 'bg-amber-400',
    on: 'bg-amber-400/10 border-amber-400/30',
    chip: 'bg-amber-400/10 border-amber-400/25 text-amber-300',
  },
  tool: {
    plural: 'Tools', glyph: '⌘', dot: 'bg-sky-400',
    on: 'bg-sky-400/10 border-sky-400/30',
    chip: 'bg-sky-400/10 border-sky-400/25 text-sky-300',
  },
  memory: {
    plural: 'Memory', glyph: '◈', dot: 'bg-emerald-400',
    on: 'bg-emerald-400/10 border-emerald-400/30',
    chip: 'bg-emerald-400/10 border-emerald-400/25 text-emerald-300',
  },
  agent: {
    plural: 'Agents', glyph: '◆', dot: 'bg-violet-400',
    on: 'bg-violet-400/10 border-violet-400/30',
    chip: 'bg-violet-400/10 border-violet-400/25 text-violet-300',
  },
}

const KIND_ORDER: LibKind[] = ['prompt', 'tool', 'memory', 'agent']

// what the rail can make on the spot — a tool doc is written from the internet
// (Discover) or uploaded, so it isn't in the make-one list. `upload` swaps the
// form for a drop zone: a file (or a shared CID) can land as any kind, agents
// included.
type NewKind = LibKind | 'upload'
const NEW_KINDS: NewKind[] = ['agent', 'prompt', 'memory', 'upload']

// an external agent CLI this host can hand a run to, from GET /harnesses
type HarnessInfo = { name: string; label: string; description?: string; install?: string; available: boolean }

type Props = {
  auth?: LibAuth
  host?: string | null
  // what the console has attached right now, so the rail can show it as on
  activeAgent: string
  activePromptId?: string | null
  memSel: string[]
  toolSel: string[]
  onSelectAgent: (name: string) => void
  onSelectPrompt: (item: LibItem) => void
  onToggleMemory: (id: string) => void
  onToggleTool: (id: string) => void
  // anything the rail can't do inline (install a published agent, edit, QR)
  // hands off to the full library page
  onOpenLibrary: () => void
  onClose: () => void
  // the rail can add to the market too — the console refetches its own lists
  onCreated?: (kind: LibKind | 'upload', name: string) => void
  onSignIn?: () => void
  // bumped by the console after it changes the library, to refetch
  refreshKey?: number
}

export default function Market({
  auth, host, activeAgent, activePromptId, memSel, toolSel,
  onSelectAgent, onSelectPrompt, onToggleMemory, onToggleTool,
  onOpenLibrary, onClose, onCreated, onSignIn, refreshKey = 0,
}: Props) {
  const [items, setItems] = useState<LibItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<LibKind | null>(null)
  const [mineOnly, setMineOnly] = useState(false)

  // ── the add form ──
  const [adding, setAdding] = useState(false)
  const [newKind, setNewKind] = useState<NewKind>('agent')
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [body, setBody] = useState('')
  const [harness, setHarness] = useState('')      // '' = this module's own loop
  const [harnesses, setHarnesses] = useState<HarnessInfo[]>([])
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  const refresh = useCallback(() => {
    fetch(`${API_URL}/library`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => { setItems(d.items || []); setError(null) })
      .catch(() => setError('market unreachable'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh, refreshKey])

  // which agent CLIs this host can hand a run to
  useEffect(() => {
    fetch(`${API_URL}/harnesses`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => setHarnesses(d.harnesses || []))
      .catch(() => {})
  }, [])

  const isHost = !!auth?.isOwner || host === ''
  const ownedByMe = (i: LibItem) =>
    !!auth && i.owner_source === 'item' &&
    (i.owner || '').toLowerCase() === auth.address.toLowerCase()
  const ownerLabel = (i: LibItem) =>
    ownedByMe(i) ? 'you'
      : i.owner_source === 'host' ? 'host'
      : i.owner ? `${i.owner.slice(0, 6)}…${i.owner.slice(-4)}` : ''

  const searched = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return items.filter(i => {
      if (mineOnly && !ownedByMe(i)) return false
      if (!ql) return true
      return i.name.toLowerCase().includes(ql) ||
        (i.description || '').toLowerCase().includes(ql) ||
        i.tags.some(t => t.includes(ql))
    })
    // ownedByMe closes over auth, which is in the dep list via auth?.address
  }, [items, q, mineOnly, auth?.address])

  const kindCounts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const i of searched) c[i.kind] = (c[i.kind] || 0) + 1
    return c
  }, [searched])

  const filtered = useMemo(
    () => searched.filter(i => !kind || i.kind === kind),
    [searched, kind])

  // is this item riding along on the next run?
  const attached = (i: LibItem) =>
    i.kind === 'prompt' ? activePromptId === i.id
      : i.kind === 'memory' ? memSel.includes(i.id)
      : i.kind === 'tool' ? toolSel.includes(i.id)
      : i.kind === 'agent' ? !activePromptId && activeAgent === i.name
      : false

  // what a click does — anything needing an install or an editor goes to the
  // full library instead
  const inlineUsable = (i: LibItem) =>
    i.kind === 'prompt' ? true
      : i.kind === 'memory' ? true
      : i.kind === 'tool' ? !!i.external
      : i.kind === 'agent' ? i.tags.includes('installed')
      : false

  const use = (i: LibItem) => {
    if (!inlineUsable(i)) { onOpenLibrary(); return }
    if (i.kind === 'prompt') onSelectPrompt(i)
    else if (i.kind === 'memory') onToggleMemory(i.id)
    else if (i.kind === 'tool') onToggleTool(i.id)
    else if (i.kind === 'agent') onSelectAgent(i.name)
  }

  const useTitle = (i: LibItem) =>
    !inlineUsable(i)
      ? i.kind === 'agent' ? 'published agent — open the library to install it'
        : 'built-in tool — always available to the agent'
      : i.kind === 'prompt' ? 'Run with this as the system prompt'
      : i.kind === 'memory' ? 'Attach this note to the run context'
      : i.kind === 'tool' ? 'Attach this tool document to the run context'
      : i.harness ? `Run the task on the ${i.harness} CLI installed on this host`
      : 'Run as this agent'

  const attachedCount = memSel.length + toolSel.length + (activePromptId ? 1 : 0)

  // ── adding to the market from the rail ──────────────────────────────
  // Everything made here is filed under the wallet that made it, so the form
  // only opens once you're signed in — the server refuses an unsigned create.

  const slug = (s: string) => s.trim().toLowerCase().replace(/[\s_]+/g, '-')

  const openAdd = () => {
    setSaveErr(null)
    // signing in is a wallet round-trip: kick it off and open the form anyway,
    // so filling it in happens while the signature lands
    if (!auth) onSignIn?.()
    setAdding(a => !a)
  }

  const resetAdd = () => {
    setName(''); setDesc(''); setBody(''); setHarness(''); setSaveErr(null)
  }

  const create = async () => {
    const n = name.trim()
    if (!n || saving || newKind === 'upload') return
    if (newKind !== 'agent' && !body.trim()) {
      setSaveErr(newKind === 'prompt' ? 'a prompt needs its text' : 'a note needs its content')
      return
    }
    setSaving(true)
    setSaveErr(null)
    const key = auth?.token
    const route = newKind === 'agent' ? 'agents' : newKind === 'prompt' ? 'prompts' : 'memory'
    const payload: Record<string, unknown> =
      newKind === 'agent'
        ? { name: n, description: desc.trim(), goal: body.trim(),
            icon: harness === 'claude' ? '⬡' : harness === 'codex' ? '◇' : '>_',
            harness: harness || null, key }
        : newKind === 'prompt'
          ? { name: n, text: body.trim(), description: desc.trim(), key }
          : { name: n, content: body.trim(), key }
    try {
      const r = await fetch(`${API_URL}/${route}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(x => x.json())
      if (r?.error) { setSaveErr(String(r.error)); return }
      resetAdd()
      setAdding(false)
      refresh()
      onCreated?.(newKind, newKind === 'agent' ? slug(n) : n)
      if (newKind === 'agent') onSelectAgent(slug(n))
    } catch (e: any) {
      setSaveErr(e?.message || 'could not save')
    } finally {
      setSaving(false)
    }
  }

  const addForm = (
    <div className="px-2.5 py-2 border-b border-white/[0.06] shrink-0 space-y-1.5 bg-white/[0.02]">
      <div className="flex items-center gap-1">
        {NEW_KINDS.map(k => (
          <button key={k} onClick={() => { setNewKind(k); setSaveErr(null) }}
            title={k === 'upload' ? 'Upload a file you wrote — or install one from a CID' : undefined}
            className={`px-1.5 py-0.5 rounded-md text-[10px] border transition ${
              newKind !== k ? 'border-white/[0.08] text-gray-500 hover:text-gray-300'
                : k === 'upload' ? 'border-white/25 text-gray-100 bg-white/[0.07]'
                : KINDS[k].chip
            }`}>
            {k}
          </button>
        ))}
        <button onClick={() => { setAdding(false); resetAdd() }}
          className="ml-auto text-[10px] text-gray-600 hover:text-gray-300 px-1">✕</button>
      </div>
      {newKind === 'upload' ? (
        <Upload compact token={auth?.token} onSignIn={onSignIn}
          onDone={() => { refresh(); onCreated?.('upload', '') }}
          onAgent={name => { onCreated?.('agent', name); onSelectAgent(name) }} />
      ) : (
      <>
      <input
        value={name} onChange={e => setName(e.target.value)} autoFocus
        placeholder={newKind === 'agent' ? 'agent name' : newKind === 'prompt' ? 'prompt name' : 'note name'}
        className="w-full bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition"
      />
      {newKind !== 'memory' && (
        <input
          value={desc} onChange={e => setDesc(e.target.value)}
          placeholder="one line — what it's for"
          className="w-full bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-[11px] text-gray-300 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition"
        />
      )}
      <textarea
        value={body} onChange={e => setBody(e.target.value)} rows={3}
        placeholder={newKind === 'agent'
          ? harness ? 'extra instructions for the CLI (optional)' : 'the goal — how this agent should work'
          : newKind === 'prompt' ? 'the prompt text' : 'what the agent should remember'}
        className="w-full bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-[11px] text-gray-300 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition resize-none"
      />
      {newKind === 'agent' && (
        <div className="space-y-1">
          <div className="text-[9px] text-gray-600 uppercase tracking-wider">runs on</div>
          <div className="flex flex-wrap items-center gap-1">
            <button onClick={() => setHarness('')}
              title="This module's own loop — its tools, your provider key, sandboxed per user"
              className={`px-1.5 py-0.5 rounded-md text-[10px] border transition ${
                harness === '' ? 'border-white/20 text-gray-200 bg-white/[0.06]' : 'border-white/[0.08] text-gray-500 hover:text-gray-300'
              }`}>
              this agent
            </button>
            {harnesses.map(h => (
              <button key={h.name}
                onClick={() => h.available && setHarness(h.name)}
                disabled={!h.available}
                title={h.available
                  ? `${h.description || h.label} — the run is handed to the ${h.name} CLI on this host (owner only)`
                  : `${h.label} is not installed here — ${h.install}`}
                className={`px-1.5 py-0.5 rounded-md text-[10px] border transition ${
                  harness === h.name ? KINDS.agent.chip : 'border-white/[0.08] text-gray-500 hover:text-gray-300'
                } ${h.available ? '' : 'opacity-40 cursor-not-allowed'}`}>
                {h.label}
              </button>
            ))}
          </div>
          {harness && (
            <p className="text-[9px] text-gray-600 leading-relaxed">
              Runs the CLI on this host with its own tools and model — host owner only.
            </p>
          )}
        </div>
      )}
      {saveErr && <div className="text-[10px] text-red-400">{saveErr}</div>}
      <button onClick={create} disabled={!name.trim() || saving}
        className="w-full px-2 py-1.5 rounded-md text-[10px] uppercase tracking-wider border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40 transition">
        {saving ? 'saving…' : `add ${newKind}`}
      </button>
      </>
      )}
    </div>
  )

  const row = (i: LibItem) => {
    const K = KINDS[i.kind]
    const on = attached(i)
    const usable = inlineUsable(i)
    return (
      <div
        key={`${i.kind}:${i.id}`}
        role="button"
        tabIndex={0}
        onClick={() => use(i)}
        onKeyDown={e => { if (e.key === 'Enter') use(i) }}
        title={useTitle(i)}
        className={`w-full text-left px-2.5 py-2 rounded-lg text-sm transition cursor-pointer border ${
          on ? K.on : `border-transparent hover:bg-white/[0.04] ${usable ? '' : 'opacity-60'}`
        }`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className={`text-[11px] shrink-0 w-3.5 text-center ${on ? '' : 'opacity-70'}`}>
            {i.kind === 'agent' && i.icon ? i.icon : K.glyph}
          </span>
          <span className="truncate flex-1 text-[13px] text-gray-300">{i.name}</span>
          {i.harness && (
            <span className="text-[8px] shrink-0 px-1 py-px rounded bg-violet-400/10 border border-violet-400/25 text-violet-300/90 font-mono uppercase"
              title={`runs on the ${i.harness} CLI installed on this host — owner only`}>
              {i.harness}
            </span>
          )}
          {on && <span className="text-[9px] shrink-0 text-gray-400 font-mono">on</span>}
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${K.dot} ${on ? '' : 'opacity-40'}`} />
        </div>
        <div className="flex items-center gap-1.5 mt-0.5 pl-[22px] text-[10px] text-gray-600 min-w-0">
          {ownerLabel(i) && (
            <span className={`font-mono shrink-0 ${
              ownedByMe(i) ? 'text-emerald-300/70' : i.owner_source === 'host' ? 'text-gray-600' : 'text-violet-300/70'
            }`}>{ownerLabel(i)}</span>
          )}
          {i.description && <span className="truncate">· {i.description}</span>}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* header */}
      <div className="px-3 py-2 border-b border-white/[0.06] flex items-center gap-2 shrink-0">
        <span className="text-[10px] text-gray-500 uppercase tracking-wider font-medium">Market</span>
        <span className="text-[10px] text-gray-600 font-mono">{items.length || ''}</span>
        {attachedCount > 0 && (
          <span className="text-[9px] px-1 py-px rounded bg-emerald-500/15 border border-emerald-500/25 text-emerald-300 font-mono"
            title={`${attachedCount} item${attachedCount !== 1 ? 's' : ''} attached to the next run`}>
            +{attachedCount}
          </span>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          <button onClick={openAdd}
            className={`w-6 h-6 flex items-center justify-center rounded-md border transition text-sm leading-none ${
              adding ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-200'
                     : 'text-emerald-300/90 border-emerald-500/25 hover:bg-emerald-500/10'
            }`}
            title={auth ? 'Add an agent, prompt or memory note — or upload your own file'
                        : 'Add to the market — signs you in first'}>+</button>
          <button onClick={refresh}
            className="w-6 h-6 flex items-center justify-center rounded-md text-gray-600 hover:text-gray-300 hover:bg-white/5 transition"
            title="Refresh the market">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
          </button>
          <button onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-md text-gray-600 hover:text-gray-300 hover:bg-white/5 transition"
            title="Collapse the market">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        </div>
      </div>

      {/* search */}
      <div className="px-2.5 py-2 border-b border-white/[0.06] shrink-0 space-y-1.5">
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Search the market…"
          className="w-full bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition"
        />
        <div className="flex flex-wrap items-center gap-1">
          <button onClick={() => setKind(null)}
            className={`px-1.5 py-0.5 rounded-md text-[10px] border transition ${
              kind === null ? 'border-white/20 text-gray-200 bg-white/[0.06]' : 'border-white/[0.08] text-gray-500 hover:text-gray-300'
            }`}>
            all {searched.length}
          </button>
          {KIND_ORDER.map(k => (
            <button key={k} onClick={() => setKind(kind === k ? null : k)}
              className={`flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] border transition ${
                kind === k ? KINDS[k].chip : 'border-white/[0.08] text-gray-500 hover:text-gray-300'
              }`}>
              <span className={`w-1 h-1 rounded-full ${KINDS[k].dot} ${kind === k ? '' : 'opacity-50'}`} />
              {KINDS[k].plural.toLowerCase()} <span className="opacity-60">{kindCounts[k] || 0}</span>
            </button>
          ))}
          {auth && (
            <button onClick={() => setMineOnly(v => !v)}
              title="Only what this wallet made"
              className={`ml-auto px-1.5 py-0.5 rounded-md text-[10px] border transition ${
                mineOnly ? 'border-emerald-500/30 text-emerald-300 bg-emerald-500/10' : 'border-white/[0.08] text-gray-500 hover:text-gray-300'
              }`}>
              mine
            </button>
          )}
        </div>
      </div>

      {/* add to the market without leaving the console */}
      {adding && addForm}

      {/* the list */}
      <div className="flex-1 overflow-y-auto min-h-0 p-1.5 space-y-0.5">
        {error ? (
          /* a dead API is a state worth explaining and retrying, not two grey
             words dropped in the middle of an empty column */
          <div className="text-center py-10 px-4 flex flex-col items-center gap-2">
            <div className="w-9 h-9 rounded-xl border border-amber-500/20 bg-amber-500/[0.05] flex items-center justify-center text-amber-300/70 text-sm">
              ⚠
            </div>
            <p className="text-xs text-gray-400">Can&rsquo;t reach the library</p>
            <p className="text-[10px] text-gray-600 leading-relaxed max-w-[190px]">
              The API didn&rsquo;t answer at <span className="font-mono">{API_URL}</span>. Prompts,
              tools and agents load from there.
            </p>
            <button onClick={() => { setLoading(true); refresh() }}
              className="mt-1 px-2.5 py-1.5 rounded-md text-[10px] uppercase tracking-wider border border-white/[0.1] text-gray-400 hover:text-emerald-300 hover:border-emerald-500/30 transition">
              Try again
            </button>
          </div>
        ) : loading ? (
          <div className="text-center text-gray-600 py-10 px-3 text-xs">loading…</div>
        ) : filtered.length === 0 ? (
          <div className="text-center text-gray-600 py-10 px-3">
            <p className="text-xs text-gray-500">Nothing matches</p>
          </div>
        ) : kind ? (
          filtered.map(row)
        ) : (
          KIND_ORDER.map(k => {
            const rows = filtered.filter(i => i.kind === k)
            if (rows.length === 0) return null
            return (
              <div key={k}>
                <div className="px-2 pt-2 pb-1 text-[9px] text-gray-600 uppercase tracking-wider">
                  {KINDS[k].plural}
                </div>
                {rows.map(row)}
              </div>
            )
          })
        )}
      </div>

      {/* the full page, for anything the rail can't do inline */}
      <div className="px-2 py-1.5 border-t border-white/[0.06] shrink-0 flex items-center gap-1.5">
        <button onClick={onOpenLibrary}
          className="flex-1 px-2 py-1.5 rounded-md text-[10px] uppercase tracking-wider text-gray-500 hover:text-emerald-300 hover:bg-emerald-500/[0.07] border border-white/[0.06] transition">
          {isHost ? 'full library · admin' : 'open full library'}
        </button>
      </div>
    </div>
  )
}
