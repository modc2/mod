'use client'

// Memory — the console's MEMORY tab.
//
// The agent remembers in two places and this tab shows both:
//
//   NOTES — library memory notes. You pick which ones ride along with a run,
//           so they are context you hand over deliberately.
//   FACTS — the semantic layer. The agent recalls these on its own, scored
//           against the query, without anyone selecting them.
//
// Editing either one replaces it in place: notes upsert by id, facts upsert
// by their name slug.

import { useState, useEffect, useCallback } from 'react'
import { API_URL } from '../config'

export type MemNote = { id: string; name: string; content: string; tags?: string[]; cid?: string; owner?: string | null; owner_source?: 'item' | 'host' | null }
export type MemFact = { id: string; name: string; content: string; tags?: string[]; updated?: number }
type MemState = { session?: string; working_keys?: string[]; episodes?: number; facts?: number; dir?: string }

const shortAddr = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`

const ago = (ts?: number) => {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

type Draft = { kind: 'note' | 'fact'; id?: string; name: string; content: string; tags: string }

export default function Memory({ token, memSel, onToggleMem, onNotesChanged }: {
  token?: string | null
  // ids of the notes the composer will attach to the next run
  memSel: string[]
  onToggleMem: (id: string) => void
  onNotesChanged?: () => void
}) {
  const [layer, setLayer] = useState<'notes' | 'facts'>('notes')
  const [notes, setNotes] = useState<MemNote[]>([])
  const [facts, setFacts] = useState<MemFact[]>([])
  const [state, setState] = useState<MemState | null>(null)
  const [search, setSearch] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(() => {
    const t = AbortSignal.timeout(8000)
    fetch(`${API_URL}/memory`, { signal: t }).then(r => r.json())
      .then(d => setNotes(d.memory || [])).catch(() => {})
    fetch(`${API_URL}/memory/facts`, { signal: t }).then(r => r.json())
      .then(d => setFacts(d.facts || [])).catch(() => {})
    fetch(`${API_URL}/memory/state`, { signal: t }).then(r => r.json())
      .then(d => setState(d && !d.error ? d : null)).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    if (!draft) return
    if (!draft.name.trim() || !draft.content.trim()) { setErr('name and content are required'); return }
    setBusy(true)
    setErr(null)
    const tags = draft.tags.split(',').map(t => t.trim().toLowerCase()).filter(Boolean)
    try {
      const body = draft.kind === 'note'
        ? { id: draft.id, name: draft.name.trim(), content: draft.content, tags, key: token }
        : { name: draft.name.trim(), content: draft.content, tags, key: token }
      const r = await fetch(`${API_URL}/memory${draft.kind === 'fact' ? '/remember' : ''}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(x => x.json())
      if (r?.error) { setErr(r.error); setBusy(false); return }
      setDraft(null)
      setBusy(false)
      load()
      if (draft.kind === 'note') onNotesChanged?.()
    } catch (e: any) {
      setErr(e?.message || 'save failed')
      setBusy(false)
    }
  }

  const remove = async (kind: 'note' | 'fact', id: string, label: string) => {
    if (!confirm(`Forget "${label}"?`)) return
    const q = token ? `?key=${encodeURIComponent(token)}` : ''
    const route = kind === 'note' ? `memory/${encodeURIComponent(id)}` : `memory/facts/${encodeURIComponent(id)}`
    try {
      const r = await fetch(`${API_URL}/${route}${q}`, { method: 'DELETE' }).then(x => x.json())
      if (r?.error) { setErr(r.error); return }
      setErr(null)
      load()
      if (kind === 'note') onNotesChanged?.()
    } catch (e: any) { setErr(e?.message || 'delete failed') }
  }

  const match = (name: string, content: string) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return name.toLowerCase().includes(q) || content.toLowerCase().includes(q)
  }

  if (draft) return (
    <div className="p-3 max-w-3xl mx-auto w-full space-y-2.5">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-300 font-medium">
          {draft.id ? `Replace ${draft.kind}` : `New ${draft.kind}`}
        </span>
        <button onClick={() => { setDraft(null); setErr(null) }}
          className="ml-auto text-[10px] uppercase tracking-wider text-gray-600 hover:text-gray-300 transition">
          cancel
        </button>
      </div>
      <input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })}
        placeholder="Name"
        className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition" />
      <textarea value={draft.content} onChange={e => setDraft({ ...draft, content: e.target.value })}
        rows={8} placeholder="What the agent should remember"
        className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-2 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition resize-none leading-relaxed" />
      <input value={draft.tags} onChange={e => setDraft({ ...draft, tags: e.target.value })}
        placeholder="tags, comma, separated"
        className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-[11px] text-gray-300 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition" />
      {err && <p className="text-xs text-red-400">{err}</p>}
      <div className="flex items-center gap-2">
        <button onClick={save} disabled={busy}
          className="px-4 py-1.5 rounded-lg text-[11px] font-medium bg-emerald-600/90 hover:bg-emerald-500 disabled:bg-white/5 disabled:text-gray-600 text-white transition">
          {busy ? 'Saving…' : draft.id ? 'Replace' : 'Remember'}
        </button>
        <span className="text-[10px] text-gray-600">
          {draft.kind === 'note'
            ? 'Notes attach to a run when you tick them — pinned to localfs and shareable by CID.'
            : 'Facts are recalled automatically, scored against the query. Same name replaces the old fact.'}
        </span>
      </div>
    </div>
  )

  const list = layer === 'notes' ? notes : facts

  return (
    <div className="p-3 max-w-4xl mx-auto w-full space-y-2">
      <div className="flex items-center gap-1.5 flex-wrap">
        {(['notes', 'facts'] as const).map(l => (
          <button key={l} onClick={() => setLayer(l)}
            className={`px-2.5 py-1.5 rounded-lg text-[10px] uppercase tracking-wider transition border ${
              layer === l ? 'bg-emerald-500/15 border-emerald-500/25 text-emerald-300'
                          : 'bg-white/[0.03] border-white/[0.06] text-gray-600 hover:text-gray-300'
            }`}>
            {l}
            <span className="ml-1 text-gray-600 normal-case">{l === 'notes' ? notes.length : facts.length}</span>
          </button>
        ))}
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search memory…"
          className="flex-1 min-w-[140px] bg-white/[0.04] border border-white/[0.08] rounded-lg px-2.5 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition" />
        <button onClick={() => { setErr(null); setDraft({ kind: layer === 'notes' ? 'note' : 'fact', name: '', content: '', tags: '' }) }}
          className="px-3 py-1.5 rounded-lg text-[10px] uppercase tracking-wider bg-emerald-600/90 hover:bg-emerald-500 text-white transition">
          + {layer === 'notes' ? 'note' : 'fact'}
        </button>
      </div>

      <div className="flex items-center gap-2 text-[10px] text-gray-600 flex-wrap">
        <span>{layer === 'notes' ? `${memSel.length} attached to the next run` : 'recalled automatically'}</span>
        {state && (
          <>
            <span className="text-gray-800">·</span>
            <span>{state.episodes ?? 0} episodes</span>
            <span className="text-gray-800">·</span>
            <span>{(state.working_keys || []).length} working keys</span>
            {state.session && <><span className="text-gray-800">·</span><span className="font-mono">{state.session}</span></>}
          </>
        )}
      </div>

      {err && <p className="text-xs text-red-400">{err}</p>}

      {list.length === 0 ? (
        <p className="text-sm text-gray-600 text-center mt-8">
          {layer === 'notes' ? 'No memory notes yet' : 'Nothing durable remembered yet'}
        </p>
      ) : (
        <div className="space-y-0.5">
          {layer === 'notes'
            ? notes.filter(n => match(n.name, n.content)).map(n => (
              <MemRow key={n.id} name={n.name} content={n.content} tags={n.tags}
                meta={n.owner ? `by ${shortAddr(n.owner)}` : 'host'}
                selected={memSel.includes(n.id)}
                onSelect={() => onToggleMem(n.id)}
                onEdit={() => { setErr(null); setDraft({ kind: 'note', id: n.id, name: n.name, content: n.content, tags: (n.tags || []).join(', ') }) }}
                onDelete={() => remove('note', n.id, n.name)} />
            ))
            : facts.filter(f => match(f.name, f.content)).map(f => (
              <MemRow key={f.id} name={f.name} content={f.content} tags={f.tags}
                meta={ago(f.updated)}
                onEdit={() => { setErr(null); setDraft({ kind: 'fact', id: f.id, name: f.name, content: f.content, tags: (f.tags || []).join(', ') }) }}
                onDelete={() => remove('fact', f.id, f.name)} />
            ))}
        </div>
      )}
    </div>
  )
}

function MemRow({ name, content, tags, meta, selected, onSelect, onEdit, onDelete }: {
  name: string
  content: string
  tags?: string[]
  meta?: string
  selected?: boolean
  onSelect?: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`text-xs rounded-md border transition ${
      selected ? 'bg-emerald-500/[0.06] border-emerald-500/25' : 'bg-white/[0.02] border-white/[0.05] hover:border-white/[0.1]'
    }`}>
      <div className="flex items-center gap-2 px-2.5 py-2">
        {onSelect && (
          <button onClick={onSelect} title={selected ? 'Drop from the next run' : 'Attach to the next run'}
            className={`w-3.5 h-3.5 rounded border shrink-0 flex items-center justify-center text-[9px] transition ${
              selected ? 'bg-emerald-500/25 border-emerald-500/50 text-emerald-300' : 'border-white/15 text-transparent hover:border-emerald-500/40'
            }`}>✓</button>
        )}
        <button className="flex-1 text-left flex items-center gap-2 min-w-0" onClick={() => setOpen(o => !o)}>
          <span className="text-gray-600 shrink-0">{open ? '▼' : '▶'}</span>
          <span className="text-gray-200 shrink-0 truncate max-w-[45%]">{name}</span>
          {!open && <span className="text-gray-600 truncate">{content.replace(/\s+/g, ' ')}</span>}
        </button>
        {meta && <span className="text-[10px] text-gray-700 shrink-0">{meta}</span>}
        <button onClick={onEdit} title="Replace this entry"
          className="text-[10px] uppercase tracking-wider text-gray-600 hover:text-emerald-300 transition shrink-0">edit</button>
        <button onClick={onDelete} title="Forget it"
          className="text-[10px] uppercase tracking-wider text-gray-700 hover:text-red-400 transition shrink-0">✕</button>
      </div>
      {open && (
        <div className="px-2.5 pb-2 space-y-1.5">
          <p className="whitespace-pre-wrap text-[11px] text-gray-400 leading-relaxed border-l border-white/[0.06] pl-2">{content}</p>
          {tags && tags.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              {tags.map(t => (
                <span key={t} className="px-1.5 py-0.5 rounded bg-white/5 text-[9px] text-gray-500">{t}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
