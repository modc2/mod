'use client'

import { useEffect, useState } from 'react'
import { api, type BuildingDetail, type Catalog, type TrendPoint } from '@/lib/api'
import { count, percent, rate, titleCase } from '@/lib/format'
import { TTC_LINE_COLOR, TTC_LINE_NEEDS_DARK_TYPE } from '@/lib/palette'
import { usePalette } from './ThemeProvider'
import TrendChart from './TrendChart'

export type Selection = { layerId: string; props: Record<string, any> }

type Props = {
  selection: Selection | null
  catalog: Catalog | null
  category: string
  onClose: () => void
}

/** The five crime types the warehouse breaks every area down by. */
const CAT_LABEL: [string, string][] = [
  ['assault', 'Assault'],
  ['auto_theft', 'Auto theft'],
  ['break_enter', 'Break & enter'],
  ['robbery', 'Robbery'],
  ['theft_over', 'Theft over $5k'],
]

/**
 * The feature inspector: what you get when you click the map.
 *
 * Each layer gets a hand-written body, because a generic key/value dump of raw
 * open-data column names ("AREA_LONG_CODE", "cls") is not something anyone can
 * read. Unmapped layers still fall back to a readable table.
 */
export default function Inspector({ selection, catalog, category, onClose }: Props) {
  const pal = usePalette()
  const [trend, setTrend] = useState<TrendPoint[] | null>(null)
  const [partial, setPartial] = useState<number | null>(null)
  const [trendFor, setTrendFor] = useState<string | null>(null)

  const area = selection?.layerId === 'crime' ? selection.props.area : null

  useEffect(() => {
    if (!area) { setTrend(null); setTrendFor(null); return }
    let alive = true
    setTrend(null)
    setTrendFor(area)
    api.trend({ area, category })
      .then((t) => { if (alive) { setTrend(t.series); setPartial(t.partial_year ?? null) } })
      .catch(() => { if (alive) setTrend([]) })
    return () => { alive = false }
  }, [area, category])

  if (!selection) return null
  const def = catalog?.layers.find((l) => l.id === selection.layerId)
  const p = selection.props

  return (
    <aside className="panel pointer-events-auto flex max-h-[calc(100vh-88px)] w-[288px] flex-col overflow-hidden">
      <header className="flex items-start gap-2 border-b border-line px-3.5 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="text-[9.5px] uppercase tracking-[0.14em] text-muted">
            {def?.title ?? selection.layerId}
          </div>
          <h2 className="truncate text-[14px] font-medium text-ink">
            {headline(selection)}
          </h2>
        </div>
        <button onClick={onClose} aria-label="Close"
                className="shrink-0 rounded-ctl p-0.5 text-muted hover:text-ink">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.6"
                  strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-3.5 py-3">
        {selection.layerId === 'crime' && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Incidents" value={count(p.incidents)} big />
              <Stat
                label="vs prior period"
                value={percent(p.change)}
                // On a crime map an increase is the bad direction.
                tone={p.change === null || p.change === undefined ? undefined
                  : p.change > 0 ? 'bad' : 'good'}
                big
              />
              <Stat label="Per km²" value={rate(p.per_km2)} />
              <Stat label="Per month" value={rate(p.per_month)} />
            </div>

            {p.change === null && p.prior_incidents !== undefined && (
              <p className="text-[10.5px] leading-snug text-muted">
                Too few incidents in the prior window ({count(p.prior_incidents)})
                for a comparison to mean anything, so no change is shown.
              </p>
            )}

            <div className="border-t border-line pt-2.5">
              {trendFor === p.area && trend === null && (
                <p className="text-[11px] text-muted">Loading history…</p>
              )}
              {trend && trend.length > 0 && (
                <TrendChart series={trend} partialYear={partial} />
              )}
              {trend && trend.length === 0 && (
                <p className="text-[11px] text-muted">No history available.</p>
              )}
            </div>

            <CrimeMix props={p} categoryColor={pal.CATEGORY_COLOR} />

            <Meta rows={[
              ['Area', `${p.km2} km²`],
              ['Area code', p.area],
            ]} />
          </div>
        )}

        {selection.layerId === 'incidents' && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-line-strong"
                    style={{ background: pal.CATEGORY_COLOR[p.cat] || pal.POINT_DEFAULT }} />
              <span className="text-[12.5px] text-ink">{p.category}</span>
            </div>
            <Meta rows={[
              ['Offence', p.offence],
              ['Occurred', p.date],
              ['Premises', p.premises],
              ['Neighbourhood', p.neighbourhood],
            ]} />
            <p className="text-[10.5px] leading-snug text-muted">
              The police service offsets every incident to the nearest road
              intersection, so a point marks the block, not the address.
            </p>
          </div>
        )}

        {(selection.layerId === 'ttc_lines' || selection.layerId === 'streetcars') && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="h-3 w-7 shrink-0 rounded-full"
                    style={{ background: p.color || pal.LINE_DEFAULT }} />
              <span className="text-[12.5px] text-ink">Route {p.route}</span>
            </div>
            <Meta rows={[
              ['Direction', p.direction === 0 ? 'Outbound' : 'Inbound'],
            ]} />
          </div>
        )}

        {selection.layerId === 'subway_stations' && (
          <div className="space-y-3">
            <LineBullets lines={String(p.lines ?? '')} />
            <Meta rows={[
              ['Platforms', count(p.platforms)],
            ]} />
          </div>
        )}

        {selection.layerId === 'cycling_network' && (
          <div className="space-y-3">
            <Stat label="Protection" value={p.protection || '—'} big />
            <Meta rows={[
              ['Facility', p.facility],
              ['Installed', p.installed || '—'],
            ]} />
          </div>
        )}

        {selection.layerId === 'green_spaces' && (
          <Meta rows={[
            ['Type', p.class],
          ]} />
        )}

        {selection.layerId === 'apartments' && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Evaluation score" value={p.score != null ? `${p.score}` : '—'} big
                    tone={p.score == null ? undefined
                      : p.score < 65 ? 'bad' : p.score >= 80 ? 'good' : undefined} />
              <Stat label="Units" value={count(p.units)} big />
            </div>
            <Meta rows={[
              ['Ward', p.ward],
              ['Storeys', count(p.storeys)],
              ['Built', p.year_built],
              ['Last evaluated', p.evaluated],
              ['Owner type', titleCase(String(p.property_type || ''))],
            ]} />
            <p className="text-[10.5px] leading-snug text-muted">
              RentSafeTO scores buildings out of 100 on common areas, mechanical
              systems and unit condition. Below 65 triggers a re-evaluation.
            </p>
          </div>
        )}

        {selection.layerId === 'predicted_scores' && (
          <PredictedScore props={p} />
        )}

        {selection.layerId === 'collisions' && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Killed" value={count(p.killed)} big
                    tone={p.killed > 0 ? 'bad' : undefined} />
              <Stat label="Seriously injured" value={count(p.serious)} big />
            </div>
            <Meta rows={[
              ['Date', p.date],
              ['Location', titleCase(p.street || '—')],
              ['Neighbourhood', p.neighbourhood],
              ['Road class', p.road_class],
              ['Light', p.light],
              ['Involved', involved(p)],
            ]} />
          </div>
        )}

        {selection.layerId === 'municipalities' && (
          <div className="space-y-2">
            <p className="text-[12px] leading-relaxed text-ink-2">
              One of the six municipalities amalgamated into the City of Toronto
              on 1 January 1998.
            </p>
          </div>
        )}

        {(selection.layerId === 'neighbourhoods' || selection.layerId === 'wards') && (
          <Meta rows={[
            ['Code', p.AREA_SHORT_CODE || p.AREA_LONG_CODE],
            ['Designation', p.CLASSIFICATION],
          ]} />
        )}

        {!KNOWN.includes(selection.layerId) && (
          <Meta rows={Object.entries(p).map(([k, v]) => [k, String(v)])} />
        )}
      </div>

      {def && (
        <footer className="border-t border-line px-3.5 py-2">
          <a href={def.source.url} target="_blank" rel="noreferrer"
             className="text-[10.5px] text-accent-soft hover:underline">
            Source: {def.source.name} ↗
          </a>
        </footer>
      )}
    </aside>
  )
}

const KNOWN = [
  'crime', 'incidents', 'ttc_lines', 'streetcars', 'subway_stations',
  'cycling_network', 'green_spaces', 'apartments', 'predicted_scores',
  'collisions', 'municipalities', 'neighbourhoods', 'wards',
]

/**
 * One building against the model.
 *
 * The residual leads, because it is the only number here that is *about* this
 * building rather than about the stock: everything else it shares with a
 * thousand others. The per-building drivers are fetched on click rather than
 * shipped with the layer — 3,371 explanations would be most of the payload
 * and nobody opens more than a handful.
 */
function PredictedScore({ props: p }: { props: Record<string, any> }) {
  const [detail, setDetail] = useState<BuildingDetail | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!p.rsn) return
    let alive = true
    setDetail(null)
    setFailed(false)
    api.building(String(p.rsn))
      .then((d) => { if (alive) setDetail(d) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [p.rsn])

  const gap: number | null = typeof p.residual === 'number' ? p.residual : null
  const err = detail?.typical_error

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Actually scored" big
              value={p.score != null ? `${p.score}` : 'never evaluated'}
              tone={p.score == null ? undefined
                : p.score < 65 ? 'bad' : p.score >= 80 ? 'good' : undefined} />
        <Stat label="Model predicts" value={`${p.predicted}`} big />
      </div>

      {gap !== null && (
        <div className="rounded-ctl bg-inset px-3 py-2">
          <div className="text-[9.5px] uppercase tracking-[0.14em] text-muted">
            vs comparable buildings
          </div>
          <div className={`text-[19px] font-medium tabular-nums ${
            gap <= -5 ? 'text-bad' : gap >= 5 ? 'text-good' : 'text-ink'}`}>
            {gap > 0 ? '+' : ''}{gap.toFixed(1)}
          </div>
          <p className="mt-0.5 text-[10.5px] leading-snug text-muted">
            {err && Math.abs(gap) <= err
              ? 'Within the model’s usual error — this building scores about what its stock does.'
              : gap < 0
              ? 'Scoring below buildings of the same age, size, systems and neighbourhood.'
              : 'Scoring above comparable buildings.'}
          </p>
        </div>
      )}

      <Meta rows={[
        ['Ward', p.ward],
        ['Managed by', titleCase(String(p.manager || '')) || '—'],
        ['Units', count(p.units)],
        ['Storeys', count(p.storeys)],
        ['Built', p.year_built ? String(Math.round(p.year_built)) : '—'],
        ['Last evaluated', p.evaluated || 'never'],
        ['Health hazards at address', count(p.health_hazards)],
        ['Fire-code violations', count(p.fire_violations)],
      ]} />

      {detail && detail.drivers.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] font-medium text-ink">
            What moves this prediction
          </div>
          <ul className="space-y-1">
            {detail.drivers.slice(0, 6).map((d) => (
              <li key={d.feature} className="flex items-baseline justify-between gap-2 text-[10.5px]">
                <span className="min-w-0 flex-1 truncate text-ink-2" title={d.label}>
                  {d.label}
                </span>
                <span className={`shrink-0 tabular-nums ${
                  d.effect < 0 ? 'text-bad' : 'text-good'}`}>
                  {d.effect > 0 ? '+' : ''}{d.effect.toFixed(1)}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[9.5px] leading-snug text-muted">
            {detail.note}
          </p>
        </div>
      )}

      {!detail && !failed && (
        <p className="text-[10.5px] text-muted">Working out what drives this one…</p>
      )}

      {failed && (
        <p className="text-[10.5px] text-muted">
          Per-building drivers unavailable — the model is still fitting.
        </p>
      )}

      <p className="text-[10.5px] leading-snug text-muted">
        Predicted from the building’s own registration filing, nearby health and
        fire violations, and its neighbourhood — never from the inspection
        itself. {err ? `Typically within ${err} points.` : ''}
      </p>
    </div>
  )
}

function headline(sel: Selection): string {
  const p = sel.props
  switch (sel.layerId) {
    case 'crime': return p.name || p.area
    case 'incidents': return p.offence || p.category || 'Incident'
    case 'ttc_lines':
    case 'streetcars': return p.name
    case 'subway_stations': return String(p.name || '').replace(/ Station$/, '')
    case 'cycling_network': return titleCase(p.street || 'Cycling route')
    case 'green_spaces': return p.name || 'Green space'
    case 'apartments':
    case 'predicted_scores': return titleCase(p.address || 'Building')
    case 'collisions':
      return p.killed > 0
        ? `${p.killed} killed`
        : `${p.serious || 0} seriously injured`
    case 'municipalities': return titleCase(p.AREA_NAME || '')
    case 'neighbourhoods':
    case 'wards': return p.AREA_NAME
    default: return sel.layerId
  }
}

function involved(p: Record<string, any>): string {
  const who = []
  if (p.pedestrian === true || p.pedestrian === 'true') who.push('pedestrian')
  if (p.cyclist === true || p.cyclist === 'true') who.push('cyclist')
  return who.length ? titleCase(who.join(', ')) : ''
}

/**
 * The area's crime mix — a stacked bar rather than five numbers, because the
 * question it answers ("what kind of crime is this?") is about proportion.
 */
function CrimeMix({ props: p, categoryColor }: {
  props: Record<string, any>; categoryColor: Record<string, string>
}) {
  // Largest first, so the bar and the key below it read in the same order.
  const parts = CAT_LABEL
    .map(([k, label]) => ({ k, label, n: Number(p[k]) || 0 }))
    .filter((x) => x.n > 0)
    .sort((a, b) => b.n - a.n)
  const total = parts.reduce((s, x) => s + x.n, 0)
  if (total <= 0) return null

  return (
    <div className="border-t border-line pt-2.5">
      <div className="mb-1.5 text-[10px] uppercase tracking-wider text-muted">
        Mix over the window
      </div>
      <div className="flex h-2.5 overflow-hidden rounded-[3px]">
        {parts.map((x) => (
          <div key={x.k} title={`${x.label}: ${x.n}`}
               style={{ width: `${(x.n / total) * 100}%`, background: categoryColor[x.k] }} />
        ))}
      </div>
      <ul className="mt-1.5 space-y-0.5">
        {parts.map((x) => (
          <li key={x.k} className="flex items-center gap-1.5 text-[10.5px] text-ink-2">
            <span className="h-2 w-2 shrink-0 rounded-[2px]"
                  style={{ background: categoryColor[x.k] }} />
            <span className="flex-1 truncate">{x.label}</span>
            <span className="tabular-nums text-ink">{x.n.toLocaleString()}</span>
            <span className="w-9 text-right tabular-nums text-muted">
              {Math.round((x.n / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Stat({ label, value, big, tone }: {
  label: string; value: string; big?: boolean; tone?: 'good' | 'bad'
}) {
  const color = tone === 'good' ? 'var(--good)' : tone === 'bad' ? 'var(--bad)' : 'var(--ink)'
  return (
    <div className="rounded-ctl bg-fill px-2.5 py-2">
      <div className="text-[9.5px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`${big ? 'text-[16px]' : 'text-[13px]'} font-medium tabular-nums`}
           style={{ color }}>
        {value}
      </div>
    </div>
  )
}

function Meta({ rows }: { rows: (string | number | null | undefined)[][] }) {
  const clean = rows.filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== '—')
  if (!clean.length) return null
  return (
    <dl className="space-y-1">
      {clean.map(([k, v], i) => (
        <div key={i} className="flex items-baseline justify-between gap-3 text-[11.5px]">
          <dt className="shrink-0 text-muted">{k}</dt>
          <dd className="truncate text-right text-ink">{String(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

/** TTC line bullets, in the network's own colours. */
const LINE_NAME: Record<string, string> = {
  '1': 'Yonge–University',
  '2': 'Bloor–Danforth',
  '4': 'Sheppard',
  '5': 'Eglinton',
  '6': 'Finch West',
}

function LineBullets({ lines }: { lines: string }) {
  const pal = usePalette()
  const list = lines.split(/[\s,/]+/).filter(Boolean)
  if (!list.length) return null
  return (
    <ul className="space-y-1.5">
      {list.map((r) => {
        const bg = TTC_LINE_COLOR[r] || pal.LINE_DEFAULT
        // Line 1's yellow and Line 6's grey need dark type to stay legible.
        const dark = TTC_LINE_NEEDS_DARK_TYPE.includes(bg)
        return (
          <li key={r} className="flex items-center gap-2">
            <span
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[12px] font-bold"
              style={{ background: bg, color: dark ? '#111827' : '#ffffff' }}>
              {r}
            </span>
            <span className="truncate text-[12px] text-ink-2">
              {LINE_NAME[r] ?? `Line ${r}`}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
