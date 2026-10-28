'use client'

import type { Choropleth, LayerDef, PopulationQuery } from '@/lib/api'
import { REPORT_URL, reportCsv } from '@/lib/api'
import { byFormat } from '@/lib/format'
import { Field, Select } from './HousingControls'

type Props = {
  def: LayerDef | undefined
  query: PopulationQuery
  onChange: (patch: Partial<PopulationQuery>) => void
  data: Choropleth | null
  busy?: boolean
}

/**
 * Controls for the population layer: what to colour by, at what grain, and
 * the way out to the full brief. The metric and geography lists come from the
 * layer's catalogue entry, so a metric added server-side shows up here.
 */
export default function PopulationControls({ def, query, onChange, data, busy }: Props) {
  if (!def) return null
  const metrics = (def as any).metrics as Record<string, { label: string; format: string }>
  const geos = (def as any).geographies as Record<string, { label: string }>
  const city = (data?.meta as any)?.city as Record<string, number> | undefined

  return (
    <div className={`space-y-3 px-4 pb-3 pt-1 transition-opacity ${busy ? 'opacity-60' : ''}`}>
      <Field label="Colour by">
        <Select
          value={query.metric}
          onChange={(v) => onChange({ metric: v })}
          items={Object.entries(metrics).map(([k, v]) => [k, v.label])}
        />
      </Field>
      <Field label="Aggregate by">
        <Select
          value={query.geography}
          onChange={(v) => onChange({ geography: v })}
          items={Object.entries(geos).map(([k, v]) => [k, v.label])}
        />
      </Field>

      {city && (
        <dl className="grid grid-cols-2 gap-1.5">
          <CityStat label="RESIDENTS" value={byFormat(city.population, 'int')} />
          <CityStat label="PER SQ MI" value={byFormat(city.density, 'int')} />
          <CityStat label="MEDIAN INCOME" value={byFormat(city.median_income, 'usd')} />
          <CityStat label="MEDIAN RENT" value={byFormat(city.median_rent, 'usd')} />
          <CityStat label="RENT BURDENED" value={byFormat(city.rent_burden_pct, 'pct')} />
          <CityStat label="NEW HOMES 2020+" value={byFormat(city.new_units_since_2020, 'int')} />
        </dl>
      )}

      {/* The brief is a plain page, not part of this app: opening it in a new
          tab lets it be saved as one file and sent on. */}
      <div className="flex gap-1.5">
        <a href={REPORT_URL} target="_blank" rel="noreferrer"
           className="btn pixel tap flex-1 px-2 py-2.5 text-center text-[10px]">
          FULL REPORT
        </a>
        <a href={reportCsv(query.geography === 'tract' ? 'tract' : 'nta')}
           className="btn pixel tap px-2 py-2.5 text-center text-[10px]">
          CSV
        </a>
      </div>
      {data?.meta && (
        <p className="text-[10px] leading-snug text-nes-ink3">
          Census ACS {(data.meta as any).acs_vintage} 5-year estimates, DCP Housing
          Database and DOF sales. NYC citywide figures shown.
        </p>
      )}
    </div>
  )
}

function CityStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-black/40 px-2 py-1.5">
      <dt className="pixel text-[11px] leading-snug text-nes-ink3">{label}</dt>
      <dd className="text-[12.5px] font-medium tabular-nums text-white">{value}</dd>
    </div>
  )
}
