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
const WINDOWS: { label: string; since: string; hint: string }[] = [
  { label: '2025–now', since: '2025-01-01', hint: 'vs the year before' },
  { label: '2024–now', since: '2024-01-01', hint: 'vs 2022–23' },
  { label: '2022–now', since: '2022-01-01', hint: 'vs 2019–21' },
  { label: 'All (2016–)', since: '2016-01-01', hint: 'whole record' },
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
        <div className="grid grid-cols-2 gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w.since}
              onClick={() => onChange({ since: w.since })}
              className={`rounded-md px-2 py-1.5 text-[11.5px] transition-colors ${
                query.since === w.since
                  ? 'bg-[#3987e5] text-white'
                  : 'bg-white/[0.06] text-[#c3c2b7] hover:bg-white/[0.1]'
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </Field>

      {query.metric === 'price_change' && win && (
        <p className="rounded-md bg-black/25 px-2.5 py-2 text-[11px] leading-snug text-[#898781]">
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
      <span className="mb-1 block text-[10px] uppercase tracking-wider text-[#898781]">
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
        className="w-full appearance-none rounded-md border border-white/10 bg-white/[0.06] px-2.5 py-1.5 pr-7 text-[12.5px] text-[#e6e8ee] outline-none focus:border-[#3987e5]"
      >
        {items.map(([k, label]) => (
          <option key={k} value={k} className="bg-[#121722] text-[#e6e8ee]">
            {label}
          </option>
        ))}
      </select>
      <svg className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2"
           width="10" height="6" viewBox="0 0 10 6" fill="none">
        <path d="M1 1l4 4 4-4" stroke="#898781" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    </div>
  )
}
