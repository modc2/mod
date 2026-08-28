'use client'

import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { Coin } from './Sprites'

type Hit = { name: string; lat: number; lng: number; type: string }

export default function SearchBar({
  onPick,
  autoFocus,
}: {
  onPick: (h: { lat: number; lng: number }) => void
  /** Set when the phone HUD opens the field, so the keyboard comes straight up. */
  autoFocus?: boolean
}) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[]>([])
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)

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
    // `pointerdown` covers both a click and a tap; listening for `mousedown`
    // alone leaves the result list open behind a finger on a touch screen,
    // where the synthetic mouse event may never arrive.
    const onDoc = (e: Event) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onDoc)
    return () => document.removeEventListener('pointerdown', onDoc)
  }, [])

  return (
    <div ref={box} className="relative w-full md:w-[260px]">
      <div className="blk flex items-center gap-2 px-2.5 py-2">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" className="shrink-0">
          <circle cx="7" cy="7" r="4.6" stroke="#fbd000" strokeWidth="2" />
          <path d="M10.6 10.6L14 14" stroke="#fbd000" strokeWidth="2" strokeLinecap="square" />
        </svg>
        <input
          ref={input}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => hits.length && setOpen(true)}
          autoFocus={autoFocus}
          // A phone keyboard should offer "search", not a newline, and should
          // not try to capitalise or autocorrect a street name.
          type="search"
          enterKeyHint="search"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          placeholder="Search an address or place…"
          className="w-full min-w-0 bg-transparent text-[12.5px] text-white outline-none placeholder:text-nes-ink3
                     [&::-webkit-search-cancel-button]:hidden"
        />
        {busy && (
          <span className="coin-spin shrink-0">
            <Coin size={13} />
          </span>
        )}
        {!busy && q && (
          <button
            onClick={() => { setQ(''); setHits([]); setOpen(false) }}
            aria-label="Clear search"
            className="tap -mr-1.5 grid shrink-0 place-items-center px-1.5 text-nes-ink3 hover:text-nes-red"
          >
            <svg width="10" height="10" viewBox="0 0 10 10" shapeRendering="crispEdges"
                 fill="currentColor" aria-hidden>
              <rect x="0" y="0" width="2" height="2" /><rect x="2" y="2" width="2" height="2" />
              <rect x="4" y="4" width="2" height="2" /><rect x="6" y="2" width="2" height="2" />
              <rect x="8" y="0" width="2" height="2" /><rect x="6" y="6" width="2" height="2" />
              <rect x="8" y="8" width="2" height="2" /><rect x="2" y="6" width="2" height="2" />
              <rect x="0" y="8" width="2" height="2" />
            </svg>
          </button>
        )}
      </div>

      {open && hits.length > 0 && (
        <ul className="blk absolute z-30 mt-1.5 max-h-[52dvh] w-full overflow-y-auto">
          {hits.map((h, i) => (
            <li key={i}>
              <button
                onClick={() => {
                  onPick(h)
                  setOpen(false)
                  setQ(h.name.split(',')[0])
                  // Drop the keyboard, or it covers the place just flown to.
                  input.current?.blur()
                }}
                className="w-full px-2.5 py-2.5 text-left text-[11.5px] leading-snug hover:bg-nes-red md:py-1.5"
              >
                <span className="block truncate font-medium text-white">
                  {h.name.split(',')[0]}
                </span>
                <span className="block truncate text-[10.5px] text-nes-ink3">
                  {h.name.split(',').slice(1, 4).join(',').trim()}
                </span>
              </button>
            </li>
          ))}
          <li className="border-t-2 border-black px-2.5 py-1.5 text-[9.5px] text-nes-ink3">
            Geocoded by OpenStreetMap Nominatim
          </li>
        </ul>
      )}
    </div>
  )
}
