'use client'

import { useState } from 'react'
import type { Catalog, LayerDef } from '@/lib/api'
import { LAYER_COLOR } from '@/lib/palette'
import { Coin, QuestionBlock } from './Sprites'
import Section from './Section'

type Props = {
  catalog: Catalog | null
  active: string[]
  loading: string[]
  errors: Record<string, string>
  opacity: Record<string, number>
  counts: Record<string, number>
  onToggle: (id: string) => void
  onOpacity: (id: string, v: number) => void
  isOpen: (id: string) => boolean
  onToggleSection: (id: string) => void
}

export default function LayerPanel({
  catalog, active, loading, errors, opacity, counts, onToggle, onOpacity,
  isOpen, onToggleSection,
}: Props) {
  const [expanded, setExpanded] = useState<string | null>(null)

  if (!catalog) {
    return <div className="pixel px-4 py-4 text-[11px] text-nes-ink3">Loading layers…</div>
  }

  return (
    <div className="flex flex-col">
      {catalog.categories.map((cat) => {
        const shown = cat.layers.filter((id) => active.includes(id)).length
        return (
        <Section
          key={cat.name}
          title={cat.name}
          open={isOpen(`cat:${cat.name}`)}
          onToggle={() => onToggleSection(`cat:${cat.name}`)}
          summary={shown ? `${shown} on` : ''}
        >
          <ul className="pb-1">
            {cat.layers.map((id) => {
              const def = catalog.layers.find((l) => l.id === id)
              if (!def) return null
              const on = active.includes(id)
              const busy = loading.includes(id)
              const err = errors[id]
              return (
                <li key={id} className="px-2">
                  <div
                    className={`tap group flex items-center gap-2.5 px-2 py-1.5 transition-colors ${
                      on ? 'bg-white/[0.07]' : 'hover:bg-white/[0.04]'
                    }`}
                  >
                    {/* The checkbox is also the layer's legend swatch, so the
                        colour has to survive the restyle — only the frame
                        becomes a block. */}
                    <button
                      onClick={() => onToggle(id)}
                      aria-pressed={on}
                      aria-label={`Toggle ${def.title}`}
                      // The pseudo-element extends the tap target past the 18px
                      // swatch without moving anything on the row.
                      className="relative h-[18px] w-[18px] shrink-0 rounded-[5px] border border-white/15
                                 after:absolute after:-inset-2.5 after:content-['']"
                      style={{
                        background: on ? swatch(def) : '#1b2130',
                        boxShadow: on
                          ? 'inset 0 1px 0 rgba(255,255,255,.25)'
                          : 'inset 0 1px 0 rgba(255,255,255,.06)',
                      }}
                    >
                      {on && (
                        <svg viewBox="0 0 12 12" className="absolute inset-0 h-full w-full p-[1px]">
                          <path d="M2.5 6.2 L4.8 8.5 L9.5 3.6" fill="none"
                                stroke="rgba(0,0,0,0.85)" strokeWidth="2"
                                strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      )}
                    </button>

                    <button
                      onClick={() => onToggle(id)}
                      className={`flex-1 truncate text-left text-[13px] ${
                        on ? 'text-white' : 'text-nes-ink2'
                      }`}
                    >
                      {def.title}
                    </button>

                    {busy && (
                      <span className="coin-spin shrink-0">
                        <Coin size={13} />
                      </span>
                    )}
                    {!busy && on && counts[id] !== undefined && (
                      <span className="pixel shrink-0 text-[10px] tabular-nums text-nes-coin">
                        {counts[id].toLocaleString()}
                      </span>
                    )}
                    <button
                      onClick={() => setExpanded(expanded === id ? null : id)}
                      aria-label={`About ${def.title}`}
                      aria-expanded={expanded === id}
                      className="tap -my-2 grid shrink-0 place-items-center px-1.5 opacity-70
                                 transition-opacity hover:opacity-100"
                    >
                      <QuestionBlock size={14} />
                    </button>
                  </div>

                  {err && (
                    <p className="px-2 pb-1.5 text-[11px] text-nes-red">
                      Couldn’t load: {err}
                    </p>
                  )}

                  {expanded === id && (
                    <div className="mb-1.5 ml-2 mr-2 rounded-lg border border-white/10 bg-black/40 px-2.5 py-2">
                      <p className="text-[11.5px] leading-snug text-nes-ink2">
                        {def.description}
                      </p>
                      <a href={def.source.url} target="_blank" rel="noreferrer"
                         className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-nes-sky hover:underline">
                        {def.source.name}
                        <span className="text-nes-ink3">↗</span>
                      </a>
                      {on && (
                        <label className="pixel mt-2.5 flex items-center gap-2 text-[10px] text-nes-ink3">
                          FADE
                          <input
                            type="range" min={0.1} max={1} step={0.05}
                            value={opacity[id] ?? 1}
                            onChange={(e) => onOpacity(id, Number(e.target.value))}
                            className="h-6 flex-1 accent-nes-coin md:h-1.5"
                          />
                        </label>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </Section>
        )
      })}

      <footer className="px-4 py-3 text-[10.5px] leading-relaxed text-nes-ink3">
        Open data from{' '}
        {catalog.attribution.map((a, i) => (
          <span key={a.name}>
            <a href={a.url} target="_blank" rel="noreferrer" className="underline decoration-white/25 hover:text-nes-coin">
              {a.name}
            </a>
            {i < catalog.attribution.length - 1 ? ' · ' : ''}
          </span>
        ))}
        . No API keys, no proprietary sources.
      </footer>
    </div>
  )
}

/** The checkbox doubles as the layer's legend swatch. */
function swatch(def: LayerDef): string {
  if (def.id === 'subway_lines') {
    return 'linear-gradient(90deg,#0062CF 0 33%,#EB6800 33% 66%,#00A65C 66%)'
  }
  if (def.id === 'housing_prices') {
    return 'linear-gradient(90deg,#184f95,#3987e5,#9ec5f4)'
  }
  if (def.id === 'evacuation_zones') {
    return 'linear-gradient(90deg,#f2a0a0,#d95926,#5f7a52)'
  }
  if (def.id === 'affordable_rents') {
    return 'linear-gradient(90deg,#0d366b,#3987e5,#cde2fb)'
  }
  return LAYER_COLOR[def.id] || '#3987e5'
}
