'use client'

import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { API_URL } from '../config'

// ── types ───────────────────────────────────────────────────────────

export type LibKind = 'prompt' | 'skill' | 'memory' | 'agent'

export type LibItem = {
  kind: LibKind
  id: string
  name: string
  label?: string
  description: string
  tags: string[]
  body?: string
  params?: Record<string, { type?: string; required?: boolean; default?: any }>
  icon?: string
  skills?: string[] | null
  model?: string | null
  cid?: string
  updated?: number
  builtin?: boolean
}

type Props = {
  onUsePrompt: (text: string) => void
  onSelectAgent: (name: string) => void
  onAgentsChanged?: () => void
}

// ── kind styling (full literal classes so tailwind JIT keeps them) ──

const KINDS: Record<LibKind, {
  label: string; plural: string; dot: string; text: string; chip: string
  cardHover: string; badge: string; glyph: string
}> = {
  prompt: {
    label: 'Prompt', plural: 'Prompts', glyph: '¶',
    dot: 'bg-amber-400', text: 'text-amber-300',
    chip: 'bg-amber-400/10 border-amber-400/25 text-amber-300',
    cardHover: 'hover:border-amber-400/30 hover:shadow-[0_0_24px_rgba(251,191,36,0.07)]',
    badge: 'bg-amber-400/10 text-amber-300/90',
  },
  skill: {
    label: 'Skill', plural: 'Skills', glyph: '⌘',
    dot: 'bg-sky-400', text: 'text-sky-300',
    chip: 'bg-sky-400/10 border-sky-400/25 text-sky-300',
    cardHover: 'hover:border-sky-400/30 hover:shadow-[0_0_24px_rgba(56,189,248,0.07)]',
    badge: 'bg-sky-400/10 text-sky-300/90',
  },
  memory: {
    label: 'Memory', plural: 'Memory', glyph: '◈',
    dot: 'bg-emerald-400', text: 'text-emerald-300',
    chip: 'bg-emerald-400/10 border-emerald-400/25 text-emerald-300',
    cardHover: 'hover:border-emerald-400/30 hover:shadow-[0_0_24px_rgba(52,211,153,0.07)]',
    badge: 'bg-emerald-400/10 text-emerald-300/90',
  },
  agent: {
    label: 'Agent', plural: 'Agents', glyph: '◆',
    dot: 'bg-violet-400', text: 'text-violet-300',
    chip: 'bg-violet-400/10 border-violet-400/25 text-violet-300',
    cardHover: 'hover:border-violet-400/30 hover:shadow-[0_0_24px_rgba(167,139,250,0.07)]',
    badge: 'bg-violet-400/10 text-violet-300/90',
  },
}

const KIND_ORDER: LibKind[] = ['prompt', 'skill', 'memory', 'agent']

// ── component ───────────────────────────────────────────────────────

export default function Library({ onUsePrompt, onSelectAgent, onAgentsChanged }: Props) {
  const [items, setItems] = useState<LibItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [q, setQ] = useState('')
  const [kind, setKind] = useState<LibKind | null>(null)
  const [tag, setTag] = useState<string | null>(null)

  const [selected, setSelected] = useState<LibItem | null>(null)
  const [creating, setCreating] = useState<LibKind | null>(null)
  const [editing, setEditing] = useState<LibItem | null>(null)
  const [showNewMenu, setShowNewMenu] = useState(false)
  const [copied, setCopied] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(() => {
    fetch(`${API_URL}/library`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => { setItems(d.items || []); setError(null) })
      .catch(() => setError(`Library unreachable at ${API_URL}`))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // "/" focuses search, Escape closes overlays
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setSelected(null); setCreating(null); setEditing(null); setShowNewMenu(false) }
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
        e.preventDefault(); searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ── client-side filtering (search is instant) ───────────────────

  const searched = useMemo(() => {
    if (!q.trim()) return items
    const ql = q.trim().toLowerCase()
    return items.filter(i =>
      i.name.toLowerCase().includes(ql) ||
      (i.description || '').toLowerCase().includes(ql) ||
      (i.body || '').toLowerCase().includes(ql) ||
      i.tags.some(t => t.includes(ql))
    )
  }, [items, q])

  const kindCounts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const i of searched) c[i.kind] = (c[i.kind] || 0) + 1
    return c
  }, [searched])

  const tagCounts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const i of searched) {
      if (kind && i.kind !== kind) continue
      for (const t of i.tags) c[t] = (c[t] || 0) + 1
    }
    return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 14)
  }, [searched, kind])

  const filtered = useMemo(() =>
    searched
      .filter(i => !kind || i.kind === kind)
      .filter(i => !tag || i.tags.includes(tag)),
    [searched, kind, tag])

  // ── mutations ────────────────────────────────────────────────────

  const del = async (item: LibItem) => {
    const route = item.kind === 'prompt' ? `prompts/${item.id}`
      : item.kind === 'memory' ? `memory/${item.id}`
      : item.kind === 'agent' ? `agents/${item.name}` : null
    if (!route) return
    await fetch(`${API_URL}/${route}`, { method: 'DELETE' }).catch(() => {})
    setSelected(null)
    refresh()
    if (item.kind === 'agent') onAgentsChanged?.()
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }

  const useItem = (item: LibItem) => {
    if (item.kind === 'prompt') onUsePrompt(item.body || '')
    else if (item.kind === 'skill') onUsePrompt(`Use the ${item.name} skill to `)
    else if (item.kind === 'memory') onUsePrompt(`Context to remember:\n${item.body}\n\nTask: `)
    else if (item.kind === 'agent' && item.tags.includes('installed')) onSelectAgent(item.name)
    setSelected(null)
  }

  const useLabel = (k: LibKind, installed: boolean) =>
    k === 'prompt' ? 'Use prompt' : k === 'skill' ? 'Try in console'
    : k === 'memory' ? 'Use as context' : installed ? 'Chat with agent' : ''

  // ── render helpers ───────────────────────────────────────────────

  const clearFilters = () => { setQ(''); setKind(null); setTag(null) }
  const hasFilters = q.trim() !== '' || kind !== null || tag !== null

  return (
    <div className="flex flex-col h-full min-h-0 library-bg">
      {/* toolbar */}
      <div className="shrink-0 px-6 pt-6 pb-4 max-w-6xl w-full mx-auto">
        <div className="flex items-end justify-between gap-4 mb-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight text-gray-100">Library</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Prompts, skills, memory and agents — one searchable market.
            </p>
          </div>

          {/* + new */}
          <div className="relative">
            <button
              onClick={() => setShowNewMenu(v => !v)}
              className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium bg-blue-600/90 hover:bg-blue-500 text-white transition shadow-[0_0_18px_rgba(59,130,246,0.25)]"
            >
              <span className="text-sm leading-none">+</span> New
              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className={`transition-transform ${showNewMenu ? 'rotate-180' : ''}`}>
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>
            {showNewMenu && (
              <div className="absolute right-0 top-full mt-1.5 w-44 bg-[#141416] border border-white/10 rounded-xl overflow-hidden z-50 shadow-2xl p-1">
                {(['prompt', 'memory', 'agent'] as LibKind[]).map(k => (
                  <button key={k}
                    onClick={() => { setCreating(k); setShowNewMenu(false) }}
                    className="w-full text-left px-3 py-2 text-xs rounded-lg hover:bg-white/[0.06] transition flex items-center gap-2.5">
                    <span className={`w-1.5 h-1.5 rounded-full ${KINDS[k].dot}`} />
                    <span className="text-gray-300">{k === 'memory' ? 'Memory note' : KINDS[k].label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* search */}
        <div className="relative">
          <svg className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-600" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            ref={searchRef}
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search prompts, skills, memory, agents…"
            className="w-full bg-white/[0.03] border border-white/[0.08] rounded-xl pl-10 pr-16 py-2.5 text-sm text-gray-200 outline-none placeholder:text-gray-600 focus:border-blue-500/40 focus:bg-white/[0.05] focus:shadow-[0_0_0_3px_rgba(59,130,246,0.08)] transition-all"
          />
          <kbd className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-gray-600 border border-white/[0.08] rounded px-1.5 py-0.5 select-none">/</kbd>
        </div>

        {/* kind pills */}
        <div className="flex items-center gap-1.5 mt-3 flex-wrap">
          <button
            onClick={() => { setKind(null); setTag(null) }}
            className={`px-3 py-1.5 rounded-full text-xs font-medium border transition ${
              kind === null
                ? 'bg-white/10 border-white/20 text-white'
                : 'border-white/[0.08] text-gray-500 hover:text-gray-300 hover:border-white/[0.16]'
            }`}
          >
            All <span className="opacity-60 ml-0.5">{searched.length}</span>
          </button>
          {KIND_ORDER.map(k => (
            <button key={k}
              onClick={() => { setKind(kind === k ? null : k); setTag(null) }}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition ${
                kind === k ? KINDS[k].chip : 'border-white/[0.08] text-gray-500 hover:text-gray-300 hover:border-white/[0.16]'
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${KINDS[k].dot} ${kind === k ? '' : 'opacity-50'}`} />
              {KINDS[k].plural} <span className="opacity-60">{kindCounts[k] || 0}</span>
            </button>
          ))}
          {hasFilters && (
            <button onClick={clearFilters} className="ml-1 text-[11px] text-gray-600 hover:text-gray-300 transition">
              clear ✕
            </button>
          )}
        </div>

        {/* tag chips */}
        {tagCounts.length > 0 && (
          <div className="flex items-center gap-1.5 mt-2.5 flex-wrap">
            <span className="text-[10px] text-gray-600 uppercase tracking-wider mr-0.5">tags</span>
            {tagCounts.map(([t, n]) => (
              <button key={t}
                onClick={() => setTag(tag === t ? null : t)}
                className={`px-2 py-0.5 rounded-md text-[11px] border transition ${
                  tag === t
                    ? 'bg-blue-500/15 border-blue-500/30 text-blue-300'
                    : 'border-white/[0.06] text-gray-500 hover:text-gray-300 hover:border-white/[0.14]'
                }`}
              >
                {t} <span className="opacity-50">{n}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* results grid */}
      <div className="flex-1 overflow-y-auto min-h-0 px-6 pb-8">
        <div className="max-w-6xl mx-auto">
          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mt-1">
              {Array.from({ length: 9 }).map((_, i) => (
                <div key={i} className="h-32 rounded-xl bg-white/[0.02] border border-white/[0.05] animate-pulse" />
              ))}
            </div>
          ) : error ? (
            <div className="text-center mt-16">
              <p className="text-sm text-red-400/80">{error}</p>
              <p className="text-xs text-gray-600 mt-2">Start the API with: <code className="text-gray-500">m agent/serve</code></p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center mt-16 text-gray-600">
              <div className="w-12 h-12 rounded-2xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center mx-auto mb-4">
                <span className="text-gray-500">∅</span>
              </div>
              <p className="text-sm text-gray-500">Nothing matches{q ? ` “${q}”` : ''}</p>
              {hasFilters && (
                <button onClick={clearFilters} className="mt-3 text-xs text-blue-400 hover:text-blue-300 transition">
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mt-1">
              {filtered.map(item => {
                const K = KINDS[item.kind]
                return (
                  <button key={item.id}
                    onClick={() => setSelected(item)}
                    className={`lib-card text-left rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 transition-all duration-150 hover:bg-white/[0.04] hover:-translate-y-[1px] ${K.cardHover}`}
                  >
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium ${K.badge}`}>
                        {K.label.toLowerCase()}
                      </span>
                      {item.kind === 'agent' && item.icon && (
                        <span className="text-xs text-gray-500 font-mono">{item.icon}</span>
                      )}
                      {item.builtin && item.kind === 'prompt' && (
                        <span className="text-[9px] text-gray-600 border border-white/[0.06] rounded px-1 py-0.5">built-in</span>
                      )}
                      <span className={`ml-auto w-1.5 h-1.5 rounded-full ${K.dot} opacity-40`} />
                    </div>
                    <div className="text-sm font-medium text-gray-200 truncate">
                      {item.label || item.name}
                    </div>
                    <div className="text-xs text-gray-500 mt-1 line-clamp-2 leading-relaxed min-h-[2rem]">
                      {item.description || item.body?.slice(0, 120) || '—'}
                    </div>
                    <div className="flex items-center gap-1 mt-2.5 flex-wrap">
                      {item.tags.slice(0, 4).map(t => (
                        <span key={t} className="text-[10px] text-gray-600 border border-white/[0.05] rounded px-1.5 py-0.5">
                          {t}
                        </span>
                      ))}
                      {item.kind === 'skill' && item.params && (
                        <span className="text-[10px] text-gray-700 ml-auto">
                          {Object.keys(item.params).length} params
                        </span>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* detail overlay */}
      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm" onClick={() => setSelected(null)}>
          <div
            className="w-full max-w-xl max-h-[80vh] flex flex-col bg-[#121214] border border-white/10 rounded-2xl shadow-2xl overflow-hidden lib-pop"
            onClick={e => e.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-white/[0.06] flex items-center gap-3 shrink-0">
              <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium ${KINDS[selected.kind].badge}`}>
                {KINDS[selected.kind].label.toLowerCase()}
              </span>
              <span className="text-sm font-medium text-gray-100 truncate">
                {selected.icon ? `${selected.icon} ` : ''}{selected.label || selected.name}
              </span>
              <button onClick={() => setSelected(null)} className="ml-auto text-gray-600 hover:text-gray-300 transition p-1">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-4 min-h-0">
              {selected.description && (
                <p className="text-xs text-gray-500 mb-3 leading-relaxed">{selected.description}</p>
              )}
              {selected.tags.length > 0 && (
                <div className="flex items-center gap-1 mb-4 flex-wrap">
                  {selected.tags.map(t => (
                    <span key={t} className="text-[10px] text-gray-500 border border-white/[0.07] rounded px-1.5 py-0.5">{t}</span>
                  ))}
                </div>
              )}

              {/* body */}
              {selected.body && (
                <pre className="text-xs text-gray-300 bg-white/[0.03] border border-white/[0.06] rounded-lg p-3.5 whitespace-pre-wrap leading-relaxed font-sans">
                  {selected.body}
                </pre>
              )}

              {/* skill params */}
              {selected.kind === 'skill' && selected.params && Object.keys(selected.params).length > 0 && (
                <div className="mt-1">
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-2">parameters</div>
                  <div className="space-y-1">
                    {Object.entries(selected.params).map(([pname, p]) => (
                      <div key={pname} className="flex items-center gap-2 text-xs bg-white/[0.02] border border-white/[0.05] rounded-lg px-3 py-2">
                        <span className="font-mono text-sky-300/90">{pname}</span>
                        <span className="text-gray-600 truncate">{p.type?.replace(/<class '|'>/g, '') || 'Any'}</span>
                        <span className="ml-auto text-[10px] text-gray-600">
                          {p.required ? <span className="text-amber-400/80">required</span> : `default: ${JSON.stringify(p.default)}`}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* agent meta */}
              {selected.kind === 'agent' && (
                <div className="mt-3 space-y-1.5 text-xs">
                  {selected.model && <div className="text-gray-500">model: <span className="text-gray-400 font-mono">{selected.model}</span></div>}
                  {selected.skills && <div className="text-gray-500">skills: <span className="text-gray-400 font-mono">{selected.skills.join(', ')}</span></div>}
                  {selected.cid && (
                    <div className="flex items-center gap-2 text-gray-500">
                      cid: <span className="text-gray-400 font-mono truncate">{selected.cid}</span>
                      <button onClick={() => copy(selected.cid!)} className="text-blue-400 hover:text-blue-300 transition shrink-0">
                        {copied ? 'copied' : 'copy'}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* actions */}
            <div className="px-5 py-3.5 border-t border-white/[0.06] flex items-center gap-2 shrink-0">
              {useLabel(selected.kind, selected.tags.includes('installed')) && (
                <button onClick={() => useItem(selected)}
                  className="px-3.5 py-2 rounded-lg text-xs font-medium bg-blue-600/90 hover:bg-blue-500 text-white transition">
                  {useLabel(selected.kind, selected.tags.includes('installed'))}
                </button>
              )}
              {selected.body && (
                <button onClick={() => copy(selected.body!)}
                  className="px-3 py-2 rounded-lg text-xs border border-white/10 text-gray-400 hover:text-gray-200 hover:border-white/20 transition">
                  {copied ? 'Copied ✓' : 'Copy'}
                </button>
              )}
              {(selected.kind === 'prompt' || selected.kind === 'memory') && (
                <button onClick={() => { setEditing(selected); setSelected(null) }}
                  className="px-3 py-2 rounded-lg text-xs border border-white/10 text-gray-400 hover:text-gray-200 hover:border-white/20 transition">
                  Edit
                </button>
              )}
              {(selected.kind === 'prompt' || selected.kind === 'memory' ||
                (selected.kind === 'agent' && selected.tags.includes('custom'))) && (
                <button onClick={() => del(selected)}
                  className="ml-auto px-3 py-2 rounded-lg text-xs text-red-400/70 hover:text-red-300 hover:bg-red-500/10 transition">
                  Delete
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* create / edit overlay */}
      {(creating || editing) && (
        <EditorModal
          kind={editing ? editing.kind : creating!}
          item={editing}
          onClose={() => { setCreating(null); setEditing(null) }}
          onSaved={() => {
            setCreating(null); setEditing(null); refresh()
            if (creating === 'agent') onAgentsChanged?.()
          }}
        />
      )}
    </div>
  )
}

// ── create / edit modal ─────────────────────────────────────────────

function EditorModal({ kind, item, onClose, onSaved }: {
  kind: LibKind
  item: LibItem | null
  onClose: () => void
  onSaved: () => void
}) {
  const K = KINDS[kind]
  const [name, setName] = useState(item?.name || '')
  const [description, setDescription] = useState(item?.description || '')
  const [body, setBody] = useState(item?.body || '')
  const [tags, setTags] = useState((item?.tags || []).filter(t => !['installed', 'custom', 'published'].includes(t)).join(', '))
  const [icon, setIcon] = useState('>_')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const bodyLabel = kind === 'prompt' ? 'Prompt text' : kind === 'memory' ? 'Content' : 'System prompt / goal'

  const save = async () => {
    if (!name.trim() || !body.trim()) { setErr('name and content are required'); return }
    setSaving(true)
    setErr(null)
    const tagList = tags.split(',').map(t => t.trim().toLowerCase()).filter(Boolean)
    try {
      let res: Response
      if (kind === 'prompt') {
        res = await fetch(`${API_URL}/prompts`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: item?.id, name: name.trim(), text: body, description, tags: tagList }),
        })
      } else if (kind === 'memory') {
        res = await fetch(`${API_URL}/memory`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: item?.id, name: name.trim(), content: body, tags: tagList }),
        })
      } else {
        res = await fetch(`${API_URL}/agents`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name.trim(), description, goal: body, icon }),
        })
      }
      const data = await res.json()
      if (data.error) { setErr(data.error); setSaving(false); return }
      onSaved()
    } catch (e: any) {
      setErr(e.message)
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-lg bg-[#121214] border border-white/10 rounded-2xl shadow-2xl overflow-hidden lib-pop" onClick={e => e.stopPropagation()}>
        <div className="px-5 py-4 border-b border-white/[0.06] flex items-center gap-2.5">
          <span className={`w-1.5 h-1.5 rounded-full ${K.dot}`} />
          <span className="text-sm font-medium text-gray-100">
            {item ? 'Edit' : 'New'} {kind === 'memory' ? 'memory note' : K.label.toLowerCase()}
          </span>
          <button onClick={onClose} className="ml-auto text-gray-600 hover:text-gray-300 transition p-1">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div className="flex gap-2">
            <input value={name} onChange={e => setName(e.target.value)} placeholder={kind === 'agent' ? 'name (slug)' : 'Name'}
              disabled={kind === 'agent' && !!item}
              className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-gray-200 outline-none placeholder:text-gray-600 focus:border-blue-500/40 transition" />
            {kind === 'agent' && (
              <input value={icon} onChange={e => setIcon(e.target.value)} maxLength={4} placeholder="icon"
                className="w-16 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-gray-200 outline-none text-center focus:border-blue-500/40 transition" />
            )}
          </div>
          {kind !== 'memory' && (
            <input value={description} onChange={e => setDescription(e.target.value)} placeholder="Short description"
              className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-gray-200 outline-none placeholder:text-gray-600 focus:border-blue-500/40 transition" />
          )}
          <textarea value={body} onChange={e => setBody(e.target.value)} placeholder={bodyLabel} rows={6}
            className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-gray-200 outline-none placeholder:text-gray-600 focus:border-blue-500/40 transition resize-none leading-relaxed" />
          {kind !== 'agent' && (
            <input value={tags} onChange={e => setTags(e.target.value)} placeholder="tags, comma, separated"
              className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-xs text-gray-300 outline-none placeholder:text-gray-600 focus:border-blue-500/40 transition" />
          )}
          {err && <p className="text-xs text-red-400">{err}</p>}
        </div>

        <div className="px-5 py-3.5 border-t border-white/[0.06] flex items-center gap-2">
          <button onClick={save} disabled={saving}
            className="px-4 py-2 rounded-lg text-xs font-medium bg-blue-600/90 hover:bg-blue-500 disabled:bg-white/5 disabled:text-gray-600 text-white transition">
            {saving ? 'Saving…' : item ? 'Save changes' : 'Create'}
          </button>
          <button onClick={onClose} className="px-3 py-2 rounded-lg text-xs text-gray-500 hover:text-gray-300 transition">
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
