'use client'

import { useState } from 'react'
import type { Catalog, LayerDef } from '@/lib/api'
import { usePalette } from './ThemeProvider'
import { seriesColor, type MapPalette, type SemanticClass } from '@/lib/palette'

type Props = {
  catalog: Catalog | null
  active: string[]
  loading: string[]
  errors: Record<string, string>
  opacity: Record<string, number>
  counts: Record<string, number>
  onToggle: (id: string) => void
  onOpacity: (id: string, v: number) => void
  /** Only offered for layers somebody added; builtins can't be removed. */
  onRemove: (id: string) => void
}

export default function LayerPanel({
  catalog, active, loading, errors, opacity, counts, onToggle, onOpacity, onRemove,
}: Props) {
  const pal = usePalette()
  const [expanded, setExpanded] = useState<string | null>(null)

  if (!catalog) {
    return <div className="px-4 py-3 text-sm text-muted">Loading layers…</div>
  }

  return (
    <div className="flex flex-col">
      {catalog.categories.map((cat) => (
        <section key={cat.name} className="border-b border-line">
          <h3 className="px-4 pt-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
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
                    className={`group flex items-center gap-2.5 rounded-ctl px-2 py-1.5 transition-colors ${
                      on ? 'bg-fill' : 'hover:bg-fill'
                    }`}
                  >
                    <button
                      onClick={() => onToggle(id)}
                      aria-pressed={on}
                      aria-label={`Toggle ${def.title}`}
                      className={`relative h-4 w-4 shrink-0 rounded-[4px] border transition-colors ${
                        on ? 'border-transparent' : 'border-line-strong group-hover:border-line-strong'
                      }`}
                      style={on ? { background: swatch(def, pal) } : undefined}
                    >
                      {on && (
                        <svg viewBox="0 0 12 12" className="absolute inset-0 h-full w-full p-[1px]">
                          <path d="M2.5 6.2 L4.8 8.5 L9.5 3.6" fill="none"
                                stroke="var(--surface-solid)" strokeWidth="1.8"
                                strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      )}
                    </button>

                    <button
                      onClick={() => onToggle(id)}
                      className="flex flex-1 items-center gap-1.5 truncate text-left text-[13px] text-ink"
                    >
                      <span className="truncate">{def.title}</span>
                      {def.custom && (
                        <span title="Added from the open-data portal"
                              className="shrink-0 rounded-full bg-fill-strong px-1.5 py-px text-[9px] uppercase tracking-wider text-muted">
                          added
                        </span>
                      )}
                    </button>

                    {busy && (
                      <span className="h-3 w-3 shrink-0 animate-spin rounded-full border border-line-strong border-t-transparent" />
                    )}
                    {!busy && on && counts[id] !== undefined && (
                      <span className="shrink-0 text-[10px] tabular-nums text-muted">
                        {counts[id].toLocaleString()}
                      </span>
                    )}
                    <button
                      onClick={() => setExpanded(expanded === id ? null : id)}
                      aria-label={`About ${def.title}`}
                      className="shrink-0 rounded-ctl px-1 text-muted hover:text-ink"
                    >
                      <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
                        <circle cx="8" cy="8" r="6.5" stroke="currentColor" />
                        <path d="M8 7v4.2M8 4.7v.9" stroke="currentColor"
                              strokeWidth="1.4" strokeLinecap="round" />
                      </svg>
                    </button>
                  </div>

                  {err && (
                    <p className="px-2 pb-1.5 text-[11px] text-bad">
                      Couldn’t load: {err}
                    </p>
                  )}

                  {expanded === id && (
                    <div className="mb-1.5 ml-2 mr-2 rounded-ctl bg-inset px-2.5 py-2">
                      <p className="text-[11.5px] leading-snug text-ink-2">
                        {def.description}
                      </p>
                      <a href={def.source.url} target="_blank" rel="noreferrer"
                         className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-accent-soft hover:underline">
                        {def.source.name}
                        <span className="text-muted">↗</span>
                      </a>
                      {on && (
                        <label className="mt-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted">
                          Opacity
                          <input
                            type="range" min={0.1} max={1} step={0.05}
                            value={opacity[id] ?? 1}
                            onChange={(e) => onOpacity(id, Number(e.target.value))}
                            className="h-1 flex-1 accent-accent"
                          />
                        </label>
                      )}
                      {def.custom && (
                        <button
                          onClick={() => onRemove(id)}
                          className="mt-2 text-[11px] text-muted hover:text-bad"
                        >
                          Remove this layer
                        </button>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}

      <footer className="px-4 py-3 text-[10.5px] leading-relaxed text-muted">
        Open data from{' '}
        {catalog.attribution.map((a, i) => (
          <span key={a.name}>
            <a href={a.url} target="_blank" rel="noreferrer" className="hover:text-ink-2 underline decoration-line-strong">
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
function swatch(def: LayerDef, pal: MapPalette): string {
  if (def.id === 'ttc_lines') {
    // Lines 1, 2 and 5 — the three the eye picks the network out by.
    return 'linear-gradient(90deg,#F8C300 0 33%,#00923F 33% 66%,#DF8600 66%)'
  }
  if (def.id === 'crime') {
    const [lo, mid, hi] = [pal.SEQUENTIAL[1], pal.SEQUENTIAL[3], pal.SEQUENTIAL[5]]
    return `linear-gradient(90deg,${lo},${mid},${hi})`
  }
  if (def.id === 'incidents') {
    const c = pal.CATEGORY_COLOR
    return `linear-gradient(90deg,${c.assault} 0 33%,${c.auto_theft} 33% 66%,${c.break_enter} 66%)`
  }
  if (def.kind === 'area') {
    const [lo, mid, hi] = [pal.SEQUENTIAL_ALT[1], pal.SEQUENTIAL_ALT[3], pal.SEQUENTIAL_ALT[5]]
    return `linear-gradient(90deg,${lo},${mid},${hi})`
  }
  // A layer coloured by an outcome shows the outcomes, not one hue.
  const classes = def.style?.classes as Record<string, SemanticClass> | undefined
  if (classes) {
    const seen = [...new Set(Object.values(classes))].slice(0, 3)
    if (seen.length > 1) {
      const stops = seen.map((c, i) =>
        `${pal.SEMANTIC[c]} ${Math.round((i / seen.length) * 100)}% ${Math.round(((i + 1) / seen.length) * 100)}%`)
      return `linear-gradient(90deg,${stops.join(',')})`
    }
  }
  return pal.LAYER_COLOR[def.id] || seriesColor(def.id, pal)
}
