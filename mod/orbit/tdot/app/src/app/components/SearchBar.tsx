'use client'

import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'

type Hit = { name: string; lat: number; lng: number; type: string }

export default function SearchBar({
  onPick,
}: {
  onPick: (h: { lat: number; lng: number }) => void
}) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[]>([])
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  // Debounced: Nominatim's usage policy asks for at most one request a second,
  // and a keystroke-per-request search would blow straight through that.
  useEffect(() => {
    if (q.trim().length < 3) { setHits([]); return }
    const t = setTimeout(() => {
      setBusy(true)
      api.where(q.trim())
        .then((h) => { setHits(h); setOpen(true) })
        .catch(() => setHits([]))
        .finally(() => setBusy(false))
    }, 450)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  return (
    <div ref={box} className="relative w-[260px]">
      <div className="field flex items-center gap-2 px-2.5 py-1.5">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" className="shrink-0 text-muted">
          <circle cx="7" cy="7" r="4.6" stroke="currentColor" strokeWidth="1.5" />
          <path d="M10.6 10.6L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => hits.length && setOpen(true)}
          placeholder="Search an address or place…"
          className="w-full bg-transparent text-[12.5px] text-ink outline-none placeholder:text-muted"
        />
        {busy && (
          <span className="h-3 w-3 shrink-0 animate-spin rounded-full border border-line-strong border-t-transparent" />
        )}
      </div>

      {open && hits.length > 0 && (
        <ul className="panel-solid absolute z-30 mt-1 w-full overflow-hidden">
          {hits.map((h, i) => (
            <li key={i}>
              <button
                onClick={() => { onPick(h); setOpen(false); setQ(h.name.split(',')[0]) }}
                className="w-full px-2.5 py-1.5 text-left text-[11.5px] leading-snug text-ink-2 hover:bg-fill-hover hover:text-ink"
              >
                <span className="block truncate font-medium text-ink">
                  {h.name.split(',')[0]}
                </span>
                <span className="block truncate text-[10.5px] text-muted">
                  {h.name.split(',').slice(1, 4).join(',').trim()}
                </span>
              </button>
            </li>
          ))}
          <li className="border-t border-line px-2.5 py-1 text-[9.5px] text-muted">
            Geocoded by OpenStreetMap Nominatim
          </li>
        </ul>
      )}
    </div>
  )
}
