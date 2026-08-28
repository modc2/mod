'use client'

import { useEffect, useState } from 'react'
import {
  api, type HousingDataset, type HousingInventory, type OutlierBuilding,
  type ScoreReport,
} from '@/lib/api'

type Props = {
  /** Layers currently on the map, so a catalogued dataset can show its state. */
  active: string[]
  onToggleLayer: (id: string) => void
  onFlyTo: (lng: number, lat: number) => void
  onClose: () => void
}

type Tab = 'model' | 'data'

const ROLE_ORDER: HousingDataset['role'][] = ['layer', 'feature', 'table', 'closed']

const ROLE_LABEL: Record<HousingDataset['role'], string> = {
  layer: 'On the map',
  feature: 'Feeding the model',
  table: 'Open, but nothing to draw',
  closed: 'Not open data',
}

/**
 * The housing panel: everything the city publishes about housing, and the
 * model built on top of it.
 *
 * Two tabs because they answer two different questions. **Model** is "can open
 * data tell me which buildings are in trouble" — and leads with how wrong it
 * is, because a condition model that hides its error is worse than none.
 * **Data** is the inventory: every housing dataset, including the ones with
 * nothing to draw and the ones that are not open at all. A gap listed is more
 * useful than a gap papered over.
 */
export default function Housing({ active, onToggleLayer, onFlyTo, onClose }: Props) {
  const [tab, setTab] = useState<Tab>('model')

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-start gap-2 border-b border-line px-3.5 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="text-[12.5px] font-semibold leading-tight text-ink">Housing</h2>
          <p className="truncate text-[10px] leading-tight text-muted">
            The open record, and what it predicts
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

      <div className="flex gap-0.5 border-b border-line p-1.5">
        {([['model', 'Score model'], ['data', 'All the data']] as [Tab, string][]).map(
          ([id, label]) => (
            <button key={id} onClick={() => setTab(id)} aria-pressed={tab === id}
                    className="chip flex-1 px-2 py-1 text-[11px]">
              {label}
            </button>
          ))}
      </div>

      <div className="flex-1 overflow-y-auto px-2.5 py-2.5">
        {tab === 'model'
          ? <ModelTab active={active} onToggleLayer={onToggleLayer} onFlyTo={onFlyTo} />
          : <DataTab active={active} onToggleLayer={onToggleLayer} />}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// the model
// ─────────────────────────────────────────────────────────────────────────────

function ModelTab({ active, onToggleLayer, onFlyTo }: {
  active: string[]
  onToggleLayer: (id: string) => void
  onFlyTo: (lng: number, lat: number) => void
}) {
  const [report, setReport] = useState<ScoreReport | null>(null)
  const [worst, setWorst] = useState<OutlierBuilding[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.scoreModel()
      .then((r) => { if (alive) setReport(r) })
      .catch((e) => { if (alive) setError(String(e.message ?? e)) })
    api.outliers('under', 12)
      .then((o) => { if (alive) setWorst(o.buildings) })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  if (error) {
    return <p className="px-1 text-[11.5px] leading-snug text-muted">{error}</p>
  }
  if (!report) {
    return <p className="px-1 text-[11.5px] text-muted">Fitting the model…</p>
  }

  const on = active.includes('predicted_scores')
  const { model, baseline } = report.accuracy
  const lift = Math.round((1 - model.mae / baseline.mae) * 100)

  return (
    <div className="space-y-3">
      <p className="px-1 text-[11.5px] leading-relaxed text-ink-2">
        RentSafeTO scores a building out of 100 on the state of its common
        areas. This asks whether the <em>rest</em> of the open record — the
        building’s filing, its violations, its neighbourhood — can predict that
        score without an inspector.
      </p>

      <button
        onClick={() => onToggleLayer('predicted_scores')}
        aria-pressed={on}
        className={`w-full rounded-ctl px-2.5 py-1.5 text-[11.5px] ${
          on ? 'bg-accent text-accent-ink' : 'bg-inset text-ink-2 hover:bg-fill-hover hover:text-ink'}`}
      >
        {on ? '✓ Predicted vs actual is on the map' : 'Show predicted vs actual on the map'}
      </button>

      <div className="rounded-ctl bg-inset px-2.5 py-2">
        <div className="grid grid-cols-3 gap-2">
          <Figure label="typical miss" value={`${model.mae}`} unit="pts" />
          <Figure label="within 10 pts" value={`${model.within_10}`} unit="%" />
          <Figure label="variance explained" value={`${Math.round(model.r2 * 100)}`} unit="%" />
        </div>
        <p className="mt-2 border-t border-line pt-1.5 text-[10.5px] leading-snug text-muted">
          {lift}% better than guessing the city average ({baseline.mae} points off).
          {' '}{report.accuracy.validation}.
        </p>
      </div>

      <div>
        <SectionTitle>What predicts a score</SectionTitle>
        <ul className="space-y-1">
          {report.drivers.slice(0, 8).map((d) => (
            <li key={d.feature} className="flex items-center gap-2">
              <span className="w-[112px] shrink-0 truncate text-[10.5px] text-ink-2"
                    title={d.label}>{d.label}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-inset">
                <span className="block h-full rounded-full bg-accent"
                      style={{ width: `${bar(d.importance, report.drivers[0].importance)}%` }} />
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-1.5 text-[9.5px] leading-snug text-muted">
          Permutation importance — how much accuracy is lost when that one input
          is shuffled. Not a claim about cause.
        </p>
      </div>

      {worst && worst.length > 0 && (
        <div>
          <SectionTitle>Scoring worst against comparable buildings</SectionTitle>
          <ul className="space-y-1">
            {worst.map((b) => (
              <li key={b.rsn}>
                <button
                  onClick={() => {
                    if (!on) onToggleLayer('predicted_scores')
                    onFlyTo(b.lng, b.lat)
                  }}
                  className="flex w-full items-baseline gap-2 rounded-ctl px-1.5 py-1 text-left hover:bg-fill-hover"
                >
                  <span className="w-9 shrink-0 text-[11px] font-medium tabular-nums text-bad">
                    {b.residual.toFixed(0)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[11.5px] text-ink">{b.address}</span>
                    <span className="block truncate text-[9.5px] text-muted">
                      scored {b.score}, predicted {b.predicted.toFixed(0)}
                      {b.manager ? ` · ${b.manager}` : ''}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <SectionTitle>Built from</SectionTitle>
        <ul className="space-y-0.5">
          {report.sources.map((s) => (
            <li key={s.package} className="text-[10.5px] leading-snug text-muted">
              <span className="text-ink-2">{s.name}</span> — {s.role}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <SectionTitle>What it can’t do</SectionTitle>
        <ul className="space-y-1">
          {report.caveats.map((c, i) => (
            <li key={i} className="text-[10.5px] leading-snug text-muted">· {c}</li>
          ))}
          <li className="text-[10.5px] leading-snug text-muted">
            · Excluded on purpose: {report.features.excluded_by_design}.
          </li>
        </ul>
      </div>

      <p className="px-1 text-[9.5px] text-muted">
        {report.coverage.ever_evaluated.toLocaleString()} of{' '}
        {report.coverage.registered_buildings.toLocaleString()} registered
        buildings carry a score · {report.features.used} inputs · fitted{' '}
        {report.fitted_at.slice(0, 10)}
      </p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// the inventory
// ─────────────────────────────────────────────────────────────────────────────

function DataTab({ active, onToggleLayer }: {
  active: string[]
  onToggleLayer: (id: string) => void
}) {
  const [inv, setInv] = useState<HousingInventory | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.housing()
      .then((h) => { if (alive) setInv(h) })
      .catch((e) => { if (alive) setError(String(e.message ?? e)) })
    return () => { alive = false }
  }, [])

  if (error) return <p className="px-1 text-[11.5px] text-muted">{error}</p>
  if (!inv) return <p className="px-1 text-[11.5px] text-muted">Checking the portal…</p>

  return (
    <div className="space-y-3">
      <p className="px-1 text-[11.5px] leading-relaxed text-ink-2">
        Every open dataset the city publishes about housing — {inv.count} of
        them — and what this map does with each.
      </p>

      {ROLE_ORDER.map((role) => {
        const rows = inv.datasets.filter((d) => d.role === role)
        if (!rows.length) return null
        return (
          <div key={role}>
            <SectionTitle>
              {ROLE_LABEL[role]} <span className="text-muted">· {rows.length}</span>
            </SectionTitle>
            <ul className="space-y-1">
              {rows.map((d) => (
                <Dataset key={d.package ?? d.title} d={d}
                         on={!!d.layer && active.includes(d.layer)}
                         onToggle={onToggleLayer} />
              ))}
            </ul>
          </div>
        )
      })}

      {inv.checked && (
        <p className="px-1 text-[9.5px] text-muted">
          Checked against open.toronto.ca {inv.checked.slice(0, 10)}
          {inv.unavailable.length ? ` · ${inv.unavailable.length} moved or withdrawn` : ''}
        </p>
      )}
    </div>
  )
}

function Dataset({ d, on, onToggle }: {
  d: HousingDataset
  on: boolean
  onToggle: (id: string) => void
}) {
  const status = d.portal_status
  return (
    <li className="rounded-ctl bg-inset px-2.5 py-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[11.5px] font-medium leading-snug text-ink">{d.title}</p>
          <p className="mt-0.5 text-[10.5px] leading-snug text-muted">{d.what}</p>
          {d.note && (
            <p className="mt-1 text-[10px] leading-snug text-muted">
              <span className="text-ink-2">Why not on the map:</span> {d.note}
            </p>
          )}
          <p className="mt-1 flex flex-wrap gap-x-1.5 text-[9.5px] text-muted">
            {d.url
              ? <a href={d.url} target="_blank" rel="noreferrer"
                   className="text-accent-soft hover:underline">{d.portal} ↗</a>
              : <span>{d.portal}</span>}
            {status?.refreshed && <span>· updated {status.refreshed}</span>}
            {status && !status.available && <span className="text-bad">· unreachable</span>}
          </p>
        </div>
        {d.layer && (
          <button
            onClick={() => onToggle(d.layer!)}
            aria-pressed={on}
            className={`shrink-0 rounded-ctl px-2 py-1 text-[10.5px] ${
              on ? 'bg-accent text-accent-ink' : 'bg-fill-hover text-ink-2 hover:text-ink'}`}
          >
            {on ? 'On' : 'Show'}
          </button>
        )}
      </div>
    </li>
  )
}

// ─────────────────────────────────────────────────────────────────────────────

function Figure({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div>
      <div className="text-[16px] font-medium leading-none tabular-nums text-ink">
        {value}<span className="text-[10px] text-muted">{unit}</span>
      </div>
      <div className="mt-0.5 text-[9px] uppercase tracking-[0.1em] text-muted">{label}</div>
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div className="mb-1 px-1 text-[11px] font-medium text-ink">{children}</div>
}

/** Bars are relative to the strongest driver, so the shape is readable. */
function bar(v: number, top: number): number {
  return Math.max(3, Math.round((v / Math.max(top, 1e-6)) * 100))
}
