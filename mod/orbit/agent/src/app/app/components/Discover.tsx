'use client'

import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { API_URL } from '../config'

// ── types ───────────────────────────────────────────────────────────

export type Found = {
  id: string
  source: string
  kind: 'skill' | 'mcp' | 'package'
  name: string
  title?: string
  description: string
  url: string
  repo?: string
  author?: string
  stars?: number | null
  downloads?: number | null
  tools?: number
  license?: string | null
  tags: string[]
  updated?: string | null
  installable?: boolean
  install?: Record<string, any>
  also?: string[]
  score?: number
}

type SourceState = { label: string; found: number; cached: boolean; error: string | null }

type ScanResult = {
  q: string
  items: Found[]
  total: number
  sources: Record<string, SourceState>
  facets: { sources: Record<string, number>; kinds: Record<string, number> }
  errors: Record<string, string>
  elapsed: number
  token: boolean
  error?: string
}

type SourceDef = { id: string; label: string; kind: string; about: string; auth: string }

type Detail = Found & {
  skills?: { path: string; name: string; unverified?: boolean }[]
  readme?: string
  version?: string
  topics?: string[]
  forks?: number
  error?: string
}

// an install lands in the library under the installer's address, so it
// carries the caller's signed token — without one the server refuses
type Props = { onInstalled?: () => void; token?: string | null }

// ── source styling (full literal classes for the tailwind JIT) ──────

const SRC: Record<string, { label: string; chip: string; badge: string; dot: string }> = {
  github:    { label: 'GitHub',    chip: 'bg-white/10 border-white/25 text-gray-100',            badge: 'bg-white/[0.07] text-gray-300',        dot: 'bg-gray-300' },
  topics:    { label: 'Topics',    chip: 'bg-slate-400/15 border-slate-400/30 text-slate-200',   badge: 'bg-slate-400/10 text-slate-300',       dot: 'bg-slate-300' },
  anthropic: { label: 'Anthropic', chip: 'bg-orange-400/15 border-orange-400/30 text-orange-200', badge: 'bg-orange-400/10 text-orange-300',    dot: 'bg-orange-400' },
  awesome:   { label: 'Awesome',   chip: 'bg-pink-400/15 border-pink-400/30 text-pink-200',      badge: 'bg-pink-400/10 text-pink-300',         dot: 'bg-pink-400' },
  npm:       { label: 'npm',       chip: 'bg-red-400/15 border-red-400/30 text-red-200',         badge: 'bg-red-400/10 text-red-300',           dot: 'bg-red-400' },
  mcp:       { label: 'MCP',       chip: 'bg-violet-400/15 border-violet-400/30 text-violet-200', badge: 'bg-violet-400/10 text-violet-300',    dot: 'bg-violet-400' },
  glama:     { label: 'Glama',     chip: 'bg-cyan-400/15 border-cyan-400/30 text-cyan-200',      badge: 'bg-cyan-400/10 text-cyan-300',         dot: 'bg-cyan-400' },
}

const KIND_LABEL: Record<string, string> = { skill: 'Skills', mcp: 'MCP servers', package: 'Packages' }

const src = (id: string) => SRC[id] || { label: id, chip: 'bg-white/10 border-white/20 text-gray-200', badge: 'bg-white/[0.06] text-gray-400', dot: 'bg-gray-400' }

const compact = (n?: number | null) =>
  n == null ? null : n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M`
    : n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : `${n}`

const SUGGESTIONS = ['pdf', 'postgres', 'security review', 'slides', 'figma', 'kubernetes', 'excel']

// ── component ───────────────────────────────────────────────────────

export default function Discover({ onInstalled, token }: Props) {
  const [input, setInput] = useState('')
  const [sources, setSources] = useState<SourceDef[]>([])
  const [off, setOff] = useState<Set<string>>(new Set())      // deselected sources
  const [kind, setKind] = useState<string | null>(null)
  const [res, setRes] = useState<ScanResult | null>(null)
  const [scanning, setScanning] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [selected, setSelected] = useState<Found | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [detailBusy, setDetailBusy] = useState(false)
  const [preview, setPreview] = useState<{ name: string; body: string } | null>(null)

  const [installing, setInstalling] = useState<string | null>(null)
  const [installed, setInstalled] = useState<Record<string, string>>({})  // origin id/url -> library id
  const [toast, setToast] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const scanSeq = useRef(0)

  // ── bootstrap: source catalog + what's already installed ─────────

  const loadInstalled = useCallback(() => {
    fetch(`${API_URL}/skills/installed`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => {
        const map: Record<string, string> = {}
        for (const s of d.skills || []) {
          if (s.origin_id) map[s.origin_id] = s.id
          if (s.url) map[s.url] = s.id
        }
        setInstalled(map)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetch(`${API_URL}/discover/sources`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => setSources(d.sources || []))
      .catch(() => setErr(`Aggregator unreachable at ${API_URL}`))
    loadInstalled()
  }, [loadInstalled])

  // ── scan ─────────────────────────────────────────────────────────

  const scan = useCallback((q: string, fresh = false) => {
    const seq = ++scanSeq.current
    setScanning(true)
    setErr(null)
    const picked = sources.filter(s => !off.has(s.id)).map(s => s.id)
    if (!picked.length) {              // every platform deselected — scan nothing
      setRes(null)
      setErr('Select at least one platform to scan')
      setScanning(false)
      return
    }
    const params = new URLSearchParams({ q, limit: '48' })
    if (picked.length !== sources.length) params.set('sources', picked.join(','))
    if (fresh) params.set('fresh', 'true')
    fetch(`${API_URL}/discover?${params}`, { signal: AbortSignal.timeout(90_000) })
      .then(r => r.json())
      .then((d: ScanResult) => {
        if (seq !== scanSeq.current) return          // a newer scan won
        if (d.error) setErr(d.error)
        setRes(d)
      })
      .catch(e => { if (seq === scanSeq.current) setErr(e?.message || 'scan failed') })
      .finally(() => { if (seq === scanSeq.current) setScanning(false) })
  }, [sources, off])

  // first scan once the source list lands — shows the ecosystem unprompted
  const booted = useRef(false)
  useEffect(() => {
    if (sources.length && !booted.current) { booted.current = true; scan('') }
  }, [sources, scan])

  // toggling a platform re-scans with the new selection (skip the first run,
  // which is the boot scan's job)
  const offTouched = useRef(false)
  useEffect(() => {
    if (!offTouched.current) { offTouched.current = true; return }
    if (booted.current) scan(searchRef.current?.value?.trim() ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [off])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setSelected(null); setDetail(null); setPreview(null) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const items = useMemo(
    () => (res?.items || []).filter(i => !kind || i.kind === kind),
    [res, kind])

  const rateLimited = useMemo(
    () => Object.values(res?.errors || {}).some(e => /rate limit/i.test(e)),
    [res])

  // ── detail + install ─────────────────────────────────────────────

  const open = (item: Found) => {
    setSelected(item)
    setDetail(null)
    setPreview(null)
    setDetailBusy(true)
    fetch(`${API_URL}/discover/item?id=${encodeURIComponent(item.id)}`,
      { signal: AbortSignal.timeout(30_000) })
      .then(r => r.json())
      .then((d: Detail) => setDetail(d?.error ? { ...item, error: d.error } : { ...item, ...d }))
      .catch(e => setDetail({ ...item, error: e?.message || 'lookup failed' }))
      .finally(() => setDetailBusy(false))
  }

  const showPreview = (id: string, path?: string) => {
    setPreview({ name: 'loading…', body: '' })
    const p = new URLSearchParams({ id })
    if (path) p.set('path', path)
    fetch(`${API_URL}/discover/doc?${p}`, { signal: AbortSignal.timeout(30_000) })
      .then(r => r.json())
      .then(d => setPreview(d?.error ? { name: 'error', body: d.error } : { name: d.name, body: d.body }))
      .catch(e => setPreview({ name: 'error', body: e?.message || 'preview failed' }))
  }

  const install = async (item: Found, path?: string) => {
    const tag = path ? `${item.id}|${path}` : item.id
    setInstalling(tag)
    try {
      const r = await fetch(`${API_URL}/discover/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: item.id, path, key: token }),
        signal: AbortSignal.timeout(60_000),
      })
      const d = await r.json()
      if (d?.error) { setToast(`✕ ${d.error}`) }
      else {
        setToast(`✓ installed “${d.name}” to your library`)
        setInstalled(prev => ({ ...prev, [d.origin_id || item.id]: d.id, [d.url]: d.id }))
        onInstalled?.()
        loadInstalled()
      }
    } catch (e: any) {
      setToast(`✕ ${e?.message || 'install failed'}`)
    }
    setInstalling(null)
    setTimeout(() => setToast(null), 3600)
  }

  const isInstalled = (item: Found) => !!(installed[item.id] || (item.url && installed[item.url]))

  // ── render ───────────────────────────────────────────────────────

  // read the field, not the state: a submit fired in the same tick as a
  // keystroke would otherwise scan the previous query
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    scan((searchRef.current?.value ?? input).trim())
  }

  return (
    <div className="flex flex-col h-full min-h-0 library-bg">
      {/* toolbar */}
      <div className="shrink-0 px-6 pt-6 pb-4 max-w-6xl w-full mx-auto">
        <div className="flex items-end justify-between gap-4 mb-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight text-gray-100">Discover</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Scan the internet for skills — GitHub, npm, the MCP registry, Glama and curated lists, in one search.
            </p>
          </div>
          <button
            onClick={() => scan(res?.q ?? input.trim(), true)}
            disabled={scanning}
            title="Re-scan, bypassing the cache"
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-white/[0.08] text-gray-400 hover:text-gray-200 hover:border-white/20 transition disabled:opacity-40"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className={scanning ? 'animate-spin' : ''}>
              <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
            </svg>
            Rescan
          </button>
        </div>

        {/* search */}
        <form onSubmit={submit} className="relative">
          <svg className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-600" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            ref={searchRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="Search every platform — “pdf”, “postgres”, “security review”…"
            className="w-full bg-white/[0.03] border border-white/[0.08] rounded-xl pl-10 pr-24 py-2.5 text-sm text-gray-200 outline-none placeholder:text-gray-600 focus:border-sky-500/40 focus:bg-white/[0.05] focus:shadow-[0_0_0_3px_rgb(var(--i-400)/0.08)] transition-all"
          />
          <button type="submit" disabled={scanning}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 px-3.5 py-1.5 rounded-lg text-xs font-medium bg-sky-600/90 hover:bg-sky-500 text-white transition disabled:opacity-50">
            {scanning ? 'Scanning…' : 'Scan'}
          </button>
        </form>

        {/* source pills — click to include/exclude a platform */}
        <div className="flex items-center gap-1.5 mt-3 flex-wrap">
          {sources.map(s => {
            const S = src(s.id)
            const on = !off.has(s.id)
            const st = res?.sources?.[s.id]
            return (
              <button key={s.id}
                title={`${s.about}${st?.error ? ` — ${st.error}` : ''}`}
                onClick={() => setOff(prev => {
                  const next = new Set(prev)
                  next.has(s.id) ? next.delete(s.id) : next.add(s.id)
                  return next
                })}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition ${
                  on ? S.chip : 'border-white/[0.06] text-gray-600 hover:text-gray-400 line-through decoration-white/20'
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${st?.error ? 'bg-red-400' : S.dot} ${on ? '' : 'opacity-30'}`} />
                {s.label}
                {on && st && <span className="opacity-60">{st.found}</span>}
                {on && st?.cached && <span className="text-[9px] opacity-40" title="served from cache">◷</span>}
              </button>
            )
          })}
        </div>

        {/* kind filter + scan stats */}
        {res && (
          <div className="flex items-center gap-1.5 mt-2.5 flex-wrap">
            <button onClick={() => setKind(null)}
              className={`px-2.5 py-1 rounded-md text-[11px] border transition ${
                kind === null ? 'bg-white/10 border-white/20 text-white' : 'border-white/[0.06] text-gray-500 hover:text-gray-300'
              }`}>
              All <span className="opacity-60">{res.total}</span>
            </button>
            {Object.entries(res.facets?.kinds || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
              <button key={k} onClick={() => setKind(kind === k ? null : k)}
                className={`px-2.5 py-1 rounded-md text-[11px] border transition ${
                  kind === k ? 'bg-sky-500/15 border-sky-500/30 text-sky-200' : 'border-white/[0.06] text-gray-500 hover:text-gray-300'
                }`}>
                {KIND_LABEL[k] || k} <span className="opacity-50">{n}</span>
              </button>
            ))}
            <span className="ml-auto text-[10px] text-gray-600">
              {res.total} results · {res.elapsed}s
              {!res.token && <span className="text-gray-700"> · anonymous GitHub</span>}
            </span>
          </div>
        )}

        {rateLimited && (
          <div className="mt-2.5 text-[11px] text-amber-300/80 bg-amber-400/[0.06] border border-amber-400/20 rounded-lg px-3 py-2">
            GitHub is rate-limiting anonymous scans. Cached results are still shown — add a GitHub
            token (<code className="text-amber-200/90">POST /discover/token</code>, owner only) for full headroom.
          </div>
        )}
      </div>

      {/* results */}
      <div className="flex-1 overflow-y-auto min-h-0 px-6 pb-8">
        <div className="max-w-6xl mx-auto">
          {scanning && !res ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mt-1">
              {Array.from({ length: 9 }).map((_, i) => (
                <div key={i} className="h-32 rounded-xl bg-white/[0.02] border border-white/[0.05] animate-pulse" />
              ))}
            </div>
          ) : err && !items.length ? (
            <div className="text-center mt-16">
              <p className="text-sm text-red-400/80">{err}</p>
              {/unreachable|failed to fetch/i.test(err) && (
                <p className="text-xs text-gray-600 mt-2">Start the API with: <code className="text-gray-500">m agent/serve</code></p>
              )}
            </div>
          ) : !items.length ? (
            <div className="text-center mt-16 text-gray-600">
              <div className="w-12 h-12 rounded-2xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center mx-auto mb-4">
                <span className="text-gray-500">⌕</span>
              </div>
              <p className="text-sm text-gray-500">
                {res ? `Nothing found${res.q ? ` for “${res.q}”` : ''}` : 'Search across every platform at once'}
              </p>
              <div className="flex items-center justify-center gap-1.5 mt-3 flex-wrap">
                {SUGGESTIONS.map(s => (
                  <button key={s} onClick={() => { setInput(s); scan(s) }}
                    className="px-2.5 py-1 rounded-full text-[11px] border border-white/[0.08] text-gray-500 hover:text-sky-200 hover:border-sky-400/30 transition">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mt-1">
              {items.map(item => {
                const S = src(item.source)
                const done = isInstalled(item)
                return (
                  <div key={item.id}
                    onClick={() => open(item)}
                    className="lib-card group text-left rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 transition-all duration-150 hover:bg-white/[0.04] hover:-translate-y-[1px] hover:border-sky-400/25 cursor-pointer"
                  >
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium ${S.badge}`}>{S.label}</span>
                      {item.kind !== 'skill' && (
                        <span className="text-[9px] text-gray-600 border border-white/[0.06] rounded px-1 py-0.5">{item.kind}</span>
                      )}
                      {done && (
                        <span className="text-[9px] text-emerald-300/90 bg-emerald-400/10 rounded px-1.5 py-0.5">installed</span>
                      )}
                      <span className="ml-auto flex items-center gap-2 text-[10px] text-gray-600">
                        {item.stars != null && <span title="stars">★ {compact(item.stars)}</span>}
                        {item.downloads != null && <span title="monthly downloads">⤓ {compact(item.downloads)}</span>}
                        {!!item.tools && <span title="tools">⚒ {item.tools}</span>}
                      </span>
                    </div>
                    <div className="text-sm font-medium text-gray-200 truncate">{item.name}</div>
                    <div className="text-xs text-gray-500 mt-1 line-clamp-2 leading-relaxed min-h-[2rem]">
                      {item.description || item.title || '—'}
                    </div>
                    <div className="flex items-center gap-1 mt-2.5 flex-wrap">
                      {(item.also || []).slice(0, 3).map(a => (
                        <span key={a} className={`text-[10px] rounded px-1.5 py-0.5 ${src(a).badge}`} title={`also on ${src(a).label}`}>
                          +{src(a).label}
                        </span>
                      ))}
                      {item.tags.slice(0, item.also?.length ? 2 : 3).map(t => (
                        <span key={t} className="text-[10px] text-gray-600 border border-white/[0.05] rounded px-1.5 py-0.5 truncate max-w-[9rem]">{t}</span>
                      ))}
                      <button
                        onClick={e => { e.stopPropagation(); install(item) }}
                        disabled={installing === item.id}
                        className="ml-auto text-[10px] px-2 py-0.5 rounded-md border border-white/[0.08] text-gray-500 opacity-0 group-hover:opacity-100 hover:text-emerald-200 hover:border-emerald-400/30 transition disabled:opacity-60"
                      >
                        {installing === item.id ? '…' : done ? 'reinstall' : 'install'}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* detail overlay */}
      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm"
          onClick={() => { setSelected(null); setDetail(null); setPreview(null) }}>
          <div className="w-full max-w-2xl max-h-[84vh] flex flex-col bg-surface-2 border border-white/10 rounded-2xl shadow-2xl overflow-hidden lib-pop"
            onClick={e => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-white/[0.06] flex items-center gap-3 shrink-0">
              <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium ${src(selected.source).badge}`}>
                {src(selected.source).label}
              </span>
              <span className="text-sm font-medium text-gray-100 truncate">{selected.name}</span>
              {isInstalled(selected) && (
                <span className="text-[9px] text-emerald-300/90 bg-emerald-400/10 rounded px-1.5 py-0.5">installed</span>
              )}
              <button onClick={() => { setSelected(null); setDetail(null); setPreview(null) }}
                className="ml-auto text-gray-600 hover:text-gray-300 transition p-1">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-4 min-h-0 space-y-4">
              {selected.description && (
                <p className="text-xs text-gray-400 leading-relaxed">{selected.description}</p>
              )}

              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
                {selected.author && <div className="text-gray-600">author <span className="text-gray-400">{selected.author}</span></div>}
                {selected.stars != null && <div className="text-gray-600">stars <span className="text-gray-400">{compact(selected.stars)}</span></div>}
                {(detail?.license || selected.license) && <div className="text-gray-600">license <span className="text-gray-400">{detail?.license || selected.license}</span></div>}
                {detail?.version && <div className="text-gray-600">version <span className="text-gray-400">{detail.version}</span></div>}
                {selected.updated && <div className="text-gray-600">updated <span className="text-gray-400">{String(selected.updated).slice(0, 10)}</span></div>}
                {!!selected.tools && <div className="text-gray-600">tools <span className="text-gray-400">{selected.tools}</span></div>}
              </div>

              <div className="flex items-center gap-2 flex-wrap">
                <a href={selected.url} target="_blank" rel="noreferrer"
                  className="text-[11px] text-sky-300/90 hover:text-sky-200 underline underline-offset-2 truncate max-w-full">
                  {selected.url}
                </a>
              </div>

              {detail?.error && (
                <div className="text-[11px] text-amber-300/80 bg-amber-400/[0.06] border border-amber-400/20 rounded-lg px-3 py-2">
                  {detail.error}
                </div>
              )}

              {/* per-skill install: repos that ship several SKILL.md files */}
              {detailBusy && !detail && (
                <div className="h-16 rounded-lg bg-white/[0.02] border border-white/[0.05] animate-pulse" />
              )}
              {!!detail?.skills?.length && (
                <div>
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-2">
                    skills in this repo ({detail.skills.length})
                  </div>
                  <div className="space-y-1 max-h-56 overflow-y-auto pr-1">
                    {detail.skills.map(s => (
                      <div key={s.path} className="flex items-center gap-2 text-xs bg-white/[0.02] border border-white/[0.05] rounded-lg px-3 py-2">
                        <span className="text-gray-300 truncate">{s.name}</span>
                        <span className="text-[10px] text-gray-600 truncate">{s.path}</span>
                        <button onClick={() => showPreview(selected.id, s.path)}
                          className="ml-auto text-[10px] text-gray-500 hover:text-gray-200 transition">preview</button>
                        <button onClick={() => install(selected, s.path)}
                          disabled={installing === `${selected.id}|${s.path}`}
                          className="text-[10px] px-2 py-0.5 rounded-md border border-white/[0.08] text-gray-400 hover:text-emerald-200 hover:border-emerald-400/30 transition disabled:opacity-50">
                          {installing === `${selected.id}|${s.path}` ? '…' : 'install'}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* install hints for MCP servers / packages */}
              {!!selected.install && Object.keys(selected.install).length > 0 && (
                <div>
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-2">install</div>
                  <pre className="text-[11px] text-gray-300 bg-white/[0.03] border border-white/[0.06] rounded-lg p-3 whitespace-pre-wrap break-all font-mono">
                    {selected.install.npm || selected.install.remote || selected.install.package
                      || (selected.install.tools || []).join(', ')}
                  </pre>
                </div>
              )}

              {preview && (
                <div>
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-2">
                    document preview — {preview.name}
                  </div>
                  <pre className="text-[11px] text-gray-300 bg-white/[0.03] border border-white/[0.06] rounded-lg p-3 whitespace-pre-wrap leading-relaxed font-sans max-h-72 overflow-y-auto">
                    {preview.body?.slice(0, 6000) || '…'}
                  </pre>
                </div>
              )}

              {detail?.readme && !preview && (
                <div>
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-2">readme</div>
                  <pre className="text-[11px] text-gray-400 bg-white/[0.02] border border-white/[0.05] rounded-lg p-3 whitespace-pre-wrap leading-relaxed font-sans max-h-56 overflow-y-auto">
                    {detail.readme.slice(0, 4000)}
                  </pre>
                </div>
              )}
            </div>

            <div className="px-5 py-3.5 border-t border-white/[0.06] flex items-center gap-2 shrink-0">
              <button onClick={() => showPreview(selected.id)}
                className="px-3 py-1.5 rounded-lg text-xs border border-white/[0.08] text-gray-400 hover:text-gray-200 hover:border-white/20 transition">
                Preview document
              </button>
              <span className="text-[10px] text-gray-600 ml-1">
                Installs as a document — nothing is executed.
              </span>
              <button onClick={() => install(selected)} disabled={installing === selected.id}
                className="ml-auto px-3.5 py-1.5 rounded-lg text-xs font-medium bg-emerald-600/90 hover:bg-emerald-500 text-white transition disabled:opacity-50">
                {installing === selected.id ? 'Installing…' : isInstalled(selected) ? 'Reinstall' : 'Install to library'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[60] px-4 py-2.5 rounded-xl bg-surface-2 border border-white/10 shadow-2xl text-xs text-gray-200">
          {toast}
        </div>
      )}
    </div>
  )
}
