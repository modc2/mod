'use client'

import type { CrimeQuery, Options } from '@/lib/api'

type Props = {
  options: Options | null
  query: CrimeQuery
  onChange: (patch: Partial<CrimeQuery>) => void
  busy: boolean
}

/**
 * Time presets. The comparison metric (change) contrasts the chosen window
 * against the equally-long window immediately before it, so a preset is a
 * statement about both halves of the comparison.
 */
const WINDOWS: { label: string; since: string; hint: string }[] = [
  { label: '2026', since: '2026-01-01', hint: 'vs the same span of 2025' },
  { label: '2025–now', since: '2025-01-01', hint: 'vs 2023–24' },
  { label: '2022–now', since: '2022-01-01', hint: 'vs 2017–21' },
  { label: 'All (2014–)', since: '2014-01-01', hint: 'whole record' },
]

export default function CrimeControls({ options, query, onChange, busy }: Props) {
  if (!options) return null
  const win = WINDOWS.find((w) => w.since === query.since)

  return (
    <div className="space-y-3 px-4 py-3">
      <div className="flex items-center justify-between">
        <h3 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
          Crime choropleth
        </h3>
        {busy && (
          <span className="h-3 w-3 animate-spin rounded-full border border-line-strong border-t-transparent" />
        )}
      </div>

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

      <Field label="Crime type">
        <Select
          value={query.category}
          onChange={(v) => onChange({ category: v })}
          items={Object.entries(options.categories).map(([k, v]) => [k, v.label])}
        />
      </Field>

      <Field label="Occurred from">
        <div className="grid grid-cols-2 gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w.since}
              onClick={() => onChange({ since: w.since })}
              aria-pressed={query.since === w.since}
              className="chip bg-fill px-2 py-1.5 text-[11.5px]"
            >
              {w.label}
            </button>
          ))}
        </div>
      </Field>

      {query.metric === 'change' && win && (
        <p className="rounded-ctl bg-inset px-2.5 py-2 text-[11px] leading-snug text-muted">
          Comparing {win.label} against {win.hint}. Areas with fewer than 10
          incidents in the prior window are left uncoloured.
        </p>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] uppercase tracking-wider text-muted">
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
        className="field w-full appearance-none px-2.5 py-1.5 pr-7 text-[12.5px]"
      >
        {items.map(([k, label]) => (
          <option key={k} value={k} className="bg-surface-solid text-ink">
            {label}
          </option>
        ))}
      </select>
      <svg className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-muted"
           width="10" height="6" viewBox="0 0 10 6" fill="none">
        <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    </div>
  )
}
