'use client'

import { useState } from 'react'
import type { Catalog, LayerDef } from '@/lib/api'
import { LAYER_COLOR } from '@/lib/palette'

type Props = {
  catalog: Catalog | null
  active: string[]
  loading: string[]
  errors: Record<string, string>
  opacity: Record<string, number>
  counts: Record<string, number>
  onToggle: (id: string) => void
  onOpacity: (id: string, v: number) => void
}

export default function LayerPanel({
  catalog, active, loading, errors, opacity, counts, onToggle, onOpacity,
}: Props) {
  const [expanded, setExpanded] = useState<string | null>(null)

  if (!catalog) {
    return <div className="px-4 py-3 text-sm text-[#898781]">Loading layers…</div>
  }

  return (
    <div className="flex flex-col">
      {catalog.categories.map((cat) => (
        <section key={cat.name} className="border-b border-white/10">
          <h3 className="px-4 pt-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#898781]">
            {cat.name}
          </h3>
          <ul>
            {cat.layers.map((id) => {
              const def = catalog.layers.find((l) => l.id === id)
              if (!def) return null
              const on = active.includes(id)
              const busy = loading.includes(id)
              const err = errors[id]
              return (
                <li key={id} className="px-2">
                  <div
                    className={`group flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors ${
                      on ? 'bg-white/[0.06]' : 'hover:bg-white/[0.03]'
                    }`}
                  >
                    <button
                      onClick={() => onToggle(id)}
                      aria-pressed={on}
                      aria-label={`Toggle ${def.title}`}
                      className={`relative h-4 w-4 shrink-0 rounded-[4px] border transition-colors ${
                        on ? 'border-transparent' : 'border-white/25 group-hover:border-white/45'
                      }`}
                      style={on ? { background: swatch(def) } : undefined}
                    >
                      {on && (
                        <svg viewBox="0 0 12 12" className="absolute inset-0 h-full w-full p-[1px]">
                          <path d="M2.5 6.2 L4.8 8.5 L9.5 3.6" fill="none"
                                stroke="#0b0e14" strokeWidth="1.8"
                                strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      )}
                    </button>

                    <button
                      onClick={() => onToggle(id)}
                      className="flex-1 truncate text-left text-[13px] text-[#e6e8ee]"
                    >
                      {def.title}
                    </button>

                    {busy && (
                      <span className="h-3 w-3 shrink-0 animate-spin rounded-full border border-white/25 border-t-transparent" />
                    )}
                    {!busy && on && counts[id] !== undefined && (
                      <span className="shrink-0 text-[10px] tabular-nums text-[#898781]">
                        {counts[id].toLocaleString()}
                      </span>
                    )}
                    <button
                      onClick={() => setExpanded(expanded === id ? null : id)}
                      aria-label={`About ${def.title}`}
                      className="shrink-0 rounded px-1 text-[#898781] hover:text-[#e6e8ee]"
                    >
                      <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
                        <circle cx="8" cy="8" r="6.5" stroke="currentColor" />
                        <path d="M8 7v4.2M8 4.7v.9" stroke="currentColor"
                              strokeWidth="1.4" strokeLinecap="round" />
                      </svg>
                    </button>
                  </div>

                  {err && (
                    <p className="px-2 pb-1.5 text-[11px] text-[#e66767]">
                      Couldn’t load: {err}
                    </p>
                  )}

                  {expanded === id && (
                    <div className="mb-1.5 ml-2 mr-2 rounded-md bg-black/25 px-2.5 py-2">
                      <p className="text-[11.5px] leading-snug text-[#c3c2b7]">
                        {def.description}
                      </p>
                      <a href={def.source.url} target="_blank" rel="noreferrer"
                         className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-[#6da7ec] hover:underline">
                        {def.source.name}
                        <span className="text-[#898781]">↗</span>
                      </a>
                      {on && (
                        <label className="mt-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-[#898781]">
                          Opacity
                          <input
                            type="range" min={0.1} max={1} step={0.05}
                            value={opacity[id] ?? 1}
                            onChange={(e) => onOpacity(id, Number(e.target.value))}
                            className="h-1 flex-1 accent-[#3987e5]"
                          />
                        </label>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}

      <footer className="px-4 py-3 text-[10.5px] leading-relaxed text-[#898781]">
        Open data from{' '}
        {catalog.attribution.map((a, i) => (
          <span key={a.name}>
            <a href={a.url} target="_blank" rel="noreferrer" className="hover:text-[#c3c2b7] underline decoration-white/20">
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
  return LAYER_COLOR[def.id] || '#3987e5'
}
