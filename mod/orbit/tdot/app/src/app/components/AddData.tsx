'use client'

import { useState } from 'react'
import { api, type DataHit } from '@/lib/api'

type Props = {
  /** Called with the new layer's id once it is on the map. */
  onAdded: (layerId: string) => void
  onClose: () => void
}

/** A few of the city's ~500 datasets, as a way in rather than a blank box. */
const SUGGESTIONS = ['schools', 'fire stations', 'libraries', 'shelters',
                     'film permits', 'tree canopy', 'traffic cameras']

export default function AddData({ onAdded, onClose }: Props) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<DataHit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [adding, setAdding] = useState<string | null>(null)
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null)

  async function search(query: string) {
    const term = query.trim()
    if (term.length < 2) return
    setQ(term)
    setSearching(true)
    setNote(null)
    try {
      setHits((await api.findData(term, 12)).results)
    } catch (e: any) {
      setNote({ text: String(e.message ?? e), bad: true })
    } finally {
      setSearching(false)
    }
  }

  async function add(hit: DataHit) {
    setAdding(hit.package)
    setNote(null)
    try {
      const out = await api.addData({ package: hit.package, title: hit.title })
      setNote({ text: `Added ${out.title} — ${out.features?.toLocaleString() ?? '?'} features, placed by ${out.mode}.` })
      onAdded(out.dataset)
    } catch (e: any) {
      // The backend says *why* a dataset can't be mapped; that's the useful bit.
      setNote({ text: String(e.message ?? e), bad: true })
    } finally {
      setAdding(null)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-start gap-2 border-b border-line px-3.5 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="text-[12.5px] font-semibold leading-tight text-ink">Add open data</h2>
          <p className="truncate text-[10px] leading-tight text-muted">
            Anything on open.toronto.ca, as a new layer
          </p>
        </div>
        <button onClick={onClose} aria-label="Close"
                className="rounded-ctl p-1 text-muted hover:bg-fill-hover hover:text-ink">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5"
                  strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <form onSubmit={(e) => { e.preventDefault(); search(q) }}
            className="border-b border-line p-2.5">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search the city's datasets…"
          className="w-full rounded-ctl bg-inset px-2.5 py-1.5 text-[12.5px] text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </form>

      <div className="flex-1 overflow-y-auto px-2.5 py-2.5">
        {note && (
          <p className={`mb-2 rounded-ctl px-2.5 py-1.5 text-[11.5px] leading-snug ${
            note.bad ? 'bg-inset text-bad' : 'bg-inset text-good'}`}>
            {note.text}
          </p>
        )}

        {!hits && !searching && (
          <div className="space-y-2 px-1">
            <p className="text-[11.5px] leading-relaxed text-ink-2">
              Every layer here is a description of a dataset, not code — so
              anything the portal publishes can join the map. Coordinates,
              geometry columns and the city’s own MTM grid are all handled; a
              table with no location at all is counted per ward instead.
            </p>
            <div className="flex flex-wrap gap-1.5 pt-1">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => search(s)}
                        className="rounded-full bg-inset px-2.5 py-1 text-[11px] text-ink-2 hover:bg-fill-hover hover:text-ink">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {searching && <p className="px-1 text-[11.5px] text-muted">Searching the portal…</p>}

        {hits?.length === 0 && !searching && (
          <p className="px-1 text-[11.5px] text-muted">Nothing matched “{q}”.</p>
        )}

        <ul className="space-y-1.5">
          {hits?.map((h) => (
            <li key={h.package} className="rounded-ctl bg-inset px-2.5 py-2">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-medium leading-snug text-ink">{h.title}</p>
                  {h.notes && (
                    <p className="mt-0.5 line-clamp-3 text-[11px] leading-snug text-muted">
                      {h.notes}
                    </p>
                  )}
                  <p className="mt-1 text-[10px] text-muted">
                    {h.formats.slice(0, 4).join(' · ')}
                    {h.refreshed ? ` · updated ${h.refreshed.toLowerCase()}` : ''}
                  </p>
                </div>
                <button
                  onClick={() => add(h)}
                  disabled={!!adding}
                  className="shrink-0 rounded-ctl bg-accent px-2 py-1 text-[11px] text-accent-ink disabled:opacity-40"
                >
                  {adding === h.package ? 'Adding…' : 'Add'}
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
