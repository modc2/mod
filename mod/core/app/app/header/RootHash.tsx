"use client"

import { useCallback, useEffect, useRef, useState } from 'react'
import { userContext } from '@/context'
import Client from '@/client'
import { copyToClipboard, timeAgo } from '@/utils'

/**
 * Root hash chip — one hash over the source of every module on this node.
 *
 * Everyone sees the hash and whether it still matches the committed root.
 * The node owner can force a rehash or pin the current hash as committed;
 * everyone else just gets the local cache, refreshed once an hour.
 */

const CACHE_KEY = 'root_hash_cache'
const REFRESH_MS = 60 * 60 * 1000 // rehash locally every 1 hour
const TICK_MS = 60 * 1000

interface RootHashData {
  root: string | null
  n_mods: number
  n_files: number
  n_skipped: number
  ms: number
  time: number
  age: number
  committed: string | null
  committed_time: number | null
  committed_cid: string | null
  valid: boolean
  changed: { mod: string; status: string }[]
  owners: string[]
}

interface Cached {
  data: RootHashData
  fetchedAt: number
}

function readCache(): Cached | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed?.data?.root || !parsed?.fetchedAt) return null
    return parsed as Cached
  } catch {
    return null
  }
}

export function RootHash() {
  const { user, client } = userContext()
  const [data, setData] = useState<RootHashData | null>(null)
  const [fetchedAt, setFetchedAt] = useState<number>(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)
  const inFlight = useRef(false)

  const isOwner = !!user?.key && !!data?.owners?.includes(user.key.toLowerCase())

  const load = useCallback(async (update = false) => {
    if (inFlight.current) return
    inFlight.current = true
    setLoading(true)
    setError(null)
    try {
      // public read — works signed out too, so use a bare client as fallback
      const c = client || new Client()
      const result = await c.call('root_hash', { update }, true, {}, 120000)
      if (result?.error) throw new Error(result.error)
      const now = Date.now()
      setData(result)
      setFetchedAt(now)
      localStorage.setItem(CACHE_KEY, JSON.stringify({ data: result, fetchedAt: now }))
    } catch (e: any) {
      setError(e?.message || 'failed')
    } finally {
      inFlight.current = false
      setLoading(false)
    }
  }, [client])

  // Hydrate from the local cache, then refresh only once the hour is up.
  useEffect(() => {
    const cached = readCache()
    if (cached) {
      setData(cached.data)
      setFetchedAt(cached.fetchedAt)
    }
    if (!cached || Date.now() - cached.fetchedAt >= REFRESH_MS) load()
    const id = setInterval(() => {
      const c = readCache()
      if (!c || Date.now() - c.fetchedAt >= REFRESH_MS) load()
    }, TICK_MS)
    return () => clearInterval(id)
  }, [load])

  // Close the panel on outside click / escape
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const commit = async () => {
    if (!client) return
    setLoading(true)
    setMsg(null)
    try {
      const result = await client.call('commit_root_hash', {}, true, {}, 120000)
      if (result?.error) throw new Error(result.error)
      setMsg('committed')
      await load(true)
    } catch (e: any) {
      setMsg(e?.message || 'commit failed')
    } finally {
      setLoading(false)
      setTimeout(() => setMsg(null), 4000)
    }
  }

  const copy = async () => {
    if (!data?.root) return
    if (await copyToClipboard(data.root)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  // green = matches the committed root, amber = drifted, grey = nothing committed
  const color = !data?.root
    ? 'var(--text-tertiary)'
    : !data.committed
      ? 'var(--text-tertiary)'
      : data.valid
        ? '#10b981'
        : '#fbbf24'

  const label = data?.root ? data.root.slice(0, 8) : '········'
  const status = !data?.root ? 'unknown' : !data.committed ? 'uncommitted' : data.valid ? 'clean' : 'drifted'

  const nextRefreshMin = fetchedAt
    ? Math.max(0, Math.ceil((REFRESH_MS - (Date.now() - fetchedAt)) / 60000))
    : 0

  return (
    <div className="relative flex-shrink-0" ref={panelRef}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2 py-1 transition-all"
        style={{
          fontFamily: 'var(--font-digital), monospace',
          fontSize: '12px',
          color: 'var(--text-tertiary)',
          border: `1px solid ${open ? 'var(--border-color)' : 'transparent'}`,
          background: open ? 'var(--hover-bg)' : 'transparent',
          borderRadius: '4px',
        }}
        title={`root hash — ${status}`}
      >
        <span
          className="w-1.5 h-1.5 rounded-full flex-shrink-0"
          style={{ background: color, boxShadow: `0 0 6px ${color}`, opacity: loading ? 0.4 : 1 }}
        />
        <span style={{ letterSpacing: '0.5px' }}>{label}</span>
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-[90]"
          style={{
            width: '320px',
            background: 'var(--bg-secondary)',
            backdropFilter: 'blur(20px)',
            WebkitBackdropFilter: 'blur(20px)',
            border: '1px solid var(--border-color)',
            borderRadius: '8px',
            boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
            fontFamily: 'var(--font-digital), monospace',
          }}
        >
          <div className="px-3 py-2 flex items-center justify-between border-b" style={{ borderColor: 'var(--border-color)' }}>
            <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
              ROOT HASH
            </span>
            <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color }}>
              {status}
            </span>
          </div>

          <div className="p-3 space-y-2.5">
            {/* Current root — click to copy */}
            <button
              onClick={copy}
              className="w-full text-left px-2 py-1.5"
              style={{
                background: 'var(--bg-input)',
                border: '1px solid var(--border-color)',
                borderRadius: '4px',
                color: 'var(--text-primary)',
                fontSize: '10px',
                wordBreak: 'break-all',
                lineHeight: 1.5,
              }}
              title="Copy full hash"
            >
              {data?.root || '—'}
            </button>
            {copied && (
              <div className="text-[10px] uppercase" style={{ color: '#10b981' }}>copied</div>
            )}

            {/* Coverage */}
            <div className="flex items-center justify-between text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
              <span>{data?.n_mods ?? 0} mods · {data?.n_files ?? 0} files</span>
              <span>{data?.ms ?? 0}ms</span>
            </div>

            {/* Committed root */}
            <div className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
              {data?.committed ? (
                <>
                  committed {data.committed.slice(0, 8)}
                  {data.committed_time ? ` · ${timeAgo(data.committed_time * 1000)}` : ''}
                </>
              ) : (
                'no committed root yet'
              )}
            </div>

            {/* Drift */}
            {data && !data.valid && data.changed?.length > 0 && (
              <div
                className="px-2 py-1.5 max-h-[120px] overflow-y-auto"
                style={{ background: 'rgba(251,191,36,0.06)', border: '1px solid rgba(251,191,36,0.3)', borderRadius: '4px' }}
              >
                <div className="text-[10px] font-bold uppercase mb-1" style={{ color: '#fbbf24' }}>
                  {data.changed.length} CHANGED
                </div>
                {data.changed.slice(0, 20).map((c) => (
                  <div key={c.mod} className="text-[10px] flex justify-between gap-2" style={{ color: 'var(--text-secondary)' }}>
                    <span className="truncate">{c.mod}</span>
                    <span style={{ opacity: 0.6 }}>{c.status}</span>
                  </div>
                ))}
                {data.changed.length > 20 && (
                  <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                    +{data.changed.length - 20} more
                  </div>
                )}
              </div>
            )}

            {/* Freshness */}
            <div className="text-[10px]" style={{ color: 'var(--text-tertiary)', opacity: 0.8 }}>
              {fetchedAt ? `checked ${timeAgo(fetchedAt)} · auto every 1h (${nextRefreshMin}m)` : 'not checked yet'}
            </div>

            {/* Actions */}
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => load(isOwner)}
                disabled={loading}
                className="flex-1 py-1.5 text-[11px] font-bold uppercase tracking-wider transition-all disabled:opacity-30"
                style={{
                  fontFamily: 'var(--font-digital), monospace',
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  color: 'var(--text-secondary)',
                  background: 'transparent',
                }}
                title={isOwner ? 'Recompute the root hash now' : 'Refresh from the node'}
              >
                {loading ? '···' : isOwner ? 'REHASH' : 'CHECK'}
              </button>
              {isOwner && (
                <button
                  onClick={commit}
                  disabled={loading}
                  className="flex-1 py-1.5 text-[11px] font-bold uppercase tracking-wider transition-all disabled:opacity-30"
                  style={{
                    fontFamily: 'var(--font-digital), monospace',
                    border: '1px solid rgba(16,185,129,0.5)',
                    borderRadius: '4px',
                    color: '#10b981',
                    background: 'rgba(16,185,129,0.06)',
                  }}
                  title="Pin the current hash as the committed root"
                >
                  COMMIT
                </button>
              )}
            </div>

            {(msg || error) && (
              <div className="text-[10px] uppercase" style={{ color: error ? '#ef4444' : 'var(--text-tertiary)' }}>
                {error || msg}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
