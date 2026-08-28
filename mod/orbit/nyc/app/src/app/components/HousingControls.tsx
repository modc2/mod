'use client'

import type { HousingQuery, Options } from '@/lib/api'

type Props = {
  options: Options | null
  query: HousingQuery
  onChange: (patch: Partial<HousingQuery>) => void
  /** The section header carries the title; a refetch dims the controls here. */
  busy?: boolean
}

/**
 * Time presets. The comparison metric (price_change) contrasts the chosen
 * window against the equally-long window immediately before it, so a preset is
 * a statement about both halves of the comparison.
 */
const WINDOWS: { label: string; hud: string; since: string; hint: string }[] = [
  // `hud` is the button face and is ASCII-only: it renders in Press Start 2P,
  // which has no en dash. `label` is the prose form used in the note below.
  { label: '2025–now', hud: '2025-NOW', since: '2025-01-01', hint: 'vs the year before' },
  { label: '2024–now', hud: '2024-NOW', since: '2024-01-01', hint: 'vs 2022–23' },
  { label: '2022–now', hud: '2022-NOW', since: '2022-01-01', hint: 'vs 2019–21' },
  { label: 'All (2016–)', hud: 'ALL 2016+', since: '2016-01-01', hint: 'whole record' },
]

export default function HousingControls({ options, query, onChange, busy }: Props) {
  if (!options) return null
  const win = WINDOWS.find((w) => w.since === query.since)

  return (
    <div className={`space-y-3 px-4 pb-3 pt-1 transition-opacity ${busy ? 'opacity-60' : ''}`}>
      <Field label="Colour by">
        <Select
          value={query.metric}
          onChange={(v) => onChange({ metric: v })}
          items={Object.entries(options.metrics).map(([k, v]) => [k, v.label])}
        />
      </Field>

      <Field label="Aggregate by">
        <Select
          value={query.geography}
          onChange={(v) => onChange({ geography: v })}
          items={Object.entries(options.geographies).map(([k, v]) => [k, v.label])}
        />
      </Field>

      <Field label="Property type">
        <Select
          value={query.property_type}
          onChange={(v) => onChange({ property_type: v })}
          items={Object.entries(options.property_types).map(([k, v]) => [k, v.label])}
        />
      </Field>

      <Field label="Sales from">
        <div className="grid grid-cols-2 gap-1.5 pb-1">
          {WINDOWS.map((w) => (
            <button
              key={w.since}
              onClick={() => onChange({ since: w.since })}
              className={`btn pixel px-1 py-2 text-[7.5px] ${
                query.since === w.since ? 'btn-on' : ''
              }`}
            >
              {w.hud}
            </button>
          ))}
        </div>
      </Field>

      {query.metric === 'price_change' && win && (
        <p className="border-2 border-black bg-black/40 px-2.5 py-2 text-[11px] leading-snug text-nes-ink3">
          Comparing {win.label} against {win.hint}. Areas with fewer than 5
          sales on either side are left uncoloured.
        </p>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="pixel mb-1.5 block text-[7px] leading-none text-nes-ink3">
        {label}
      </span>
      {children}
    </label>
  )
}

function Select({ value, onChange, items }: {
  value: string
  onChange: (v: string) => void
  items: [string, string][]
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full appearance-none border-2 border-black bg-nes-raised px-2.5 py-2 pr-8 text-[12.5px] text-white outline-none focus:bg-[#232d6e]"
        style={{ boxShadow: 'inset 0 2px 0 rgba(0,0,0,.4), inset 0 -2px 0 rgba(255,255,255,.08)' }}
      >
        {items.map(([k, label]) => (
          <option key={k} value={k} className="bg-[#0e1330] text-white">
            {label}
          </option>
        ))}
      </select>
      {/* A whole-pixel caret, to match the section arrows. */}
      <svg className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-nes-coin"
           width="8" height="8" viewBox="0 0 8 8" shapeRendering="crispEdges" fill="currentColor">
        <rect x="0" y="2" width="8" height="2" />
        <rect x="1" y="4" width="6" height="2" />
        <rect x="3" y="6" width="2" height="2" />
      </svg>
    </div>
  )
}
