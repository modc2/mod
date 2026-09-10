'use client'

import { useState } from 'react'
import type { McpSurface, ToolDef } from '@/lib/api'

/**
 * The tool reference, generated from the live registry rather than written by
 * hand — a tool added to `nycgis/tools.py` documents itself here, which is the
 * only way a page like this stays true a year later.
 *
 * Tools are grouped the way the registry groups them, and each one opens to
 * show its arguments. Collapsed by default: sixteen tools with their full
 * signatures expanded is a wall, and the question a reader arrives with is
 * "what can it do", not "what are the parameter defaults".
 */

const GROUP_LABELS: Record<string, { label: string; blurb: string }> = {
  city: {
    label: 'THE CITY',
    blurb: 'Orientation — the boroughs, and turning a place name into coordinates.',
  },
  layers: {
    label: 'MAP LAYERS',
    blurb: 'Subway, bike network, parks, evacuation zones, traffic injuries, boundaries.',
  },
  housing: {
    label: 'HOUSING',
    blurb: 'Every recorded deed in the five boroughs since 2016, plus affordable housing.',
  },
  traffic: {
    label: 'TRAFFIC',
    blurb: 'Where traffic is moving right now, and the 24-hour count profile '
      + 'that says which hour to drive instead.',
  },
  open_data: {
    label: 'THE WHOLE PORTAL',
    blurb: 'Search, describe and query any dataset NYC or NY State publishes — '
      + 'thousands of them, not just what the map draws.',
  },
}

export default function ToolTable({ surface }: { surface: McpSurface }) {
  const byName = new Map(surface.tools.map((t) => [t.name, t]))
  const groups = Object.entries(surface.groups)

  return (
    <div className="space-y-8">
      <p>
        {surface.count} tools, every one of them read-only. Names are stable —
        an assistant that learned <code className="code">nyc_housing</code> last
        month still calls it that.
      </p>
      {groups.map(([key, names]) => {
        const meta = GROUP_LABELS[key] ?? { label: key.toUpperCase(), blurb: '' }
        return (
          <div key={key}>
            <h3 className="pixel mb-1.5 text-[8.5px] leading-[2] text-nes-sky">
              {meta.label}
            </h3>
            {meta.blurb && (
              <p className="mb-3 text-[13px] leading-relaxed text-nes-ink3">
                {meta.blurb}
              </p>
            )}
            <div className="space-y-2">
              {names.map((n) => {
                const tool = byName.get(n)
                return tool ? <ToolRow key={n} tool={tool} /> : null
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function ToolRow({ tool }: { tool: ToolDef }) {
  const [open, setOpen] = useState(false)
  const props = Object.entries(tool.inputSchema?.properties ?? {})
  const required = new Set(tool.inputSchema?.required ?? [])

  return (
    <div className="blk overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="tap flex w-full items-baseline gap-3 px-4 py-3 text-left"
      >
        <code className="shrink-0 text-[13px] font-semibold text-nes-coin">
          {tool.name}
        </code>
        <span className="min-w-0 flex-1 text-[13px] leading-snug text-nes-ink2">
          {tool.description}
        </span>
        <span
          aria-hidden
          className={`pixel shrink-0 text-[7px] text-nes-ink3 ${props.length ? '' : 'invisible'}`}
        >
          {open ? '−' : `+${props.length}`}
        </span>
      </button>

      {open && props.length > 0 && (
        <dl className="border-t-[3px] border-black bg-black/30 px-4 py-3">
          {props.map(([name, spec]) => (
            <div key={name} className="flex flex-col gap-0.5 py-1.5 sm:flex-row sm:gap-3">
              <dt className="shrink-0 sm:w-[9.5rem]">
                <code className="text-[12.5px] text-nes-ink">{name}</code>
                {required.has(name) ? (
                  <span className="ml-1 text-[12px] text-nes-red" title="required">*</span>
                ) : null}
                <span className="ml-1.5 text-[11px] text-nes-ink3">{spec.type}</span>
              </dt>
              <dd className="text-[12.5px] leading-relaxed text-nes-ink2">
                {spec.description}
                {spec.default !== undefined && (
                  <span className="text-nes-ink3">
                    {' '}— default <code className="text-nes-coin">{String(spec.default)}</code>
                  </span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
