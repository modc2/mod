'use client'

import type { Breaks, Catalog, LayerDef, Options } from '@/lib/api'
import { byFormat } from '@/lib/format'
import {
  TTC_LINE_COLOR, TTC_LINE_NEEDS_DARK_TYPE, rampOf, seriesColor,
  type MapPalette, type SemanticClass,
} from '@/lib/palette'
import { usePalette } from './ThemeProvider'

type Props = {
  breaks: Breaks | null
  metric: string
  options: Options | null
  active: string[]
  areasWithData?: number
  totalAreas?: number
  /** Needed to key spec-driven layers, including ones added at runtime. */
  catalog: Catalog | null
  layerData: Record<string, GeoJSON.FeatureCollection>
}

/** Layers whose key is hand-written below; everything else is keyed generically. */
const HAND_KEYED = new Set(['crime', 'incidents', 'collisions', 'apartments',
                            'ttc_lines', 'subway_stations', 'cycling_network',
                            'green_spaces', 'streetcars', 'municipalities',
                            'neighbourhoods', 'wards'])

const CATEGORY_LABEL: Record<string, string> = {
  assault: 'Assault',
  auto_theft: 'Auto theft',
  break_enter: 'Break & enter',
  robbery: 'Robbery',
  theft_over: 'Theft over $5k',
}

/**
 * The map's key. Every active layer that carries an encoding gets a row —
 * identity is never left to colour alone, and a value scale always states its
 * units and its "no data" class explicitly.
 */
export default function Legend({
  breaks, metric, options, active, areasWithData, totalAreas, catalog, layerData,
}: Props) {
  const pal = usePalette()
  const rows: React.ReactNode[] = []
  const meta = options?.metrics?.[metric]
  const fmt = meta?.format ?? 'count'

  if (active.includes('crime') && breaks && breaks.stops.length > 0) {
    const diverging = metric === 'change'
    const colors = diverging ? pal.DIVERGING : rampOf(breaks.stops.length, pal.SEQUENTIAL)
    const labels = diverging
      ? divergingLabels(breaks)
      : breaks.stops.map((s) => byFormat(s, fmt))

    rows.push(
      <div key="crime">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="text-[11px] font-medium text-ink">{meta?.label ?? metric}</span>
          {areasWithData !== undefined && totalAreas !== undefined && (
            <span className="text-[10px] tabular-nums text-muted">
              {areasWithData}/{totalAreas} areas
            </span>
          )}
        </div>
        <div className="flex h-2.5 overflow-hidden rounded-[3px]">
          {colors.map((c, i) => (
            <div key={i} className="flex-1" style={{ background: c }} />
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[9.5px] tabular-nums text-muted">
          <span>{labels[0]}</span>
          {labels.length > 2 && <span>{labels[Math.floor(labels.length / 2)]}</span>}
          <span>{diverging ? labels[labels.length - 1] : byFormat(breaks.max, fmt)}</span>
        </div>
        {diverging && (
          <>
            <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-muted">
              <span className="h-2.5 w-2.5 rounded-[2px]" style={{ background: pal.NO_DATA }} />
              Too few prior incidents to compare
            </div>
            {breaks.true_min !== undefined && (
              <p className="mt-1 text-[9.5px] leading-snug text-muted">
                Scale clipped to the middle 90% ({breaks.true_min?.toFixed(0)}% to
                {' '}+{breaks.true_max?.toFixed(0)}% in full); thin-volume areas sit
                past the ends.
              </p>
            )}
          </>
        )}
      </div>,
    )
  }

  if (active.includes('incidents')) {
    rows.push(
      <Row key="incidents" title="Incident type">
        <ul className="grid grid-cols-2 gap-x-2 gap-y-0.5">
          {Object.entries(CATEGORY_LABEL).map(([k, label]) => (
            <li key={k} className="flex items-center gap-1.5 text-[10px] text-ink-2">
              <span className="h-2 w-2 shrink-0 rounded-full ring-1 ring-line-strong"
                    style={{ background: pal.CATEGORY_COLOR[k] }} />
              <span className="truncate">{label}</span>
            </li>
          ))}
        </ul>
      </Row>,
    )
  }

  if (active.includes('collisions')) {
    rows.push(
      <Row key="collisions" title="Killed or seriously injured">
        <div className="h-2.5 overflow-hidden rounded-[3px]"
             style={{
               background: `linear-gradient(90deg,${pal.HEAT.slice(1).map(([, c]) => c).join(',')})`,
             }} />
        <div className="mt-1 flex justify-between text-[9.5px] text-muted">
          <span>fewer</span><span>more collisions</span>
        </div>
        <p className="mt-1 text-[9.5px] text-muted">Individual crashes appear past zoom 14</p>
      </Row>,
    )
  }

  if (active.includes('apartments')) {
    rows.push(
      <Row key="apartments" title="Building evaluation score">
        <div className="flex items-center gap-1">
          {[[pal.SCORE[0], '<65'], [pal.SCORE[1], '65–79'], [pal.LAYER_COLOR.apartments, '80+']].map(
            ([c, label]) => (
              <div key={label} className="flex flex-1 flex-col items-center gap-0.5">
                <span className="h-2.5 w-full rounded-[2px]" style={{ background: c }} />
                <span className="text-[9px] tabular-nums text-muted">{label}</span>
              </div>
            ))}
        </div>
        <p className="mt-1 text-[9.5px] text-muted">Circle size = units in the building</p>
      </Row>,
    )
  }

  // Every spec-driven layer keys itself from its own `style` block. This is
  // what keeps a dataset added at runtime from arriving as an unexplained
  // blob: whatever it encodes, the key says so.
  for (const def of catalog?.layers ?? []) {
    if (!active.includes(def.id) || HAND_KEYED.has(def.id)) continue
    const row = specKey(def, layerData[def.id], pal)
    if (row) rows.push(<Row key={def.id} title={def.title}>{row}</Row>)
  }

  const dots: [string, string, string][] = []
  if (active.includes('subway_stations'))
    dots.push(['subway_stations', 'Subway station', ''])
  if (active.includes('cycling_network'))
    dots.push(['cycling_network', 'Cycling network', 'thick = protected'])
  if (active.includes('green_spaces'))
    dots.push(['green_spaces', 'Parks & green space', ''])
  if (active.includes('streetcars'))
    dots.push(['streetcars', 'Streetcar network', ''])
  for (const b of ['municipalities', 'neighbourhoods', 'wards'] as const) {
    if (active.includes(b)) dots.push([b, BOUNDARY_LABEL[b], 'boundary'])
  }

  if (dots.length) {
    rows.push(
      <Row key="marks" title="">
        <ul className="space-y-1">
          {dots.map(([id, label, hint]) => (
            <li key={id} className="flex items-center gap-2 text-[10.5px] text-ink-2">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-line-strong"
                style={{ background: pal.LAYER_COLOR[id] }}
              />
              <span>{label}</span>
              {hint && <span className="text-[9.5px] text-muted">· {hint}</span>}
            </li>
          ))}
        </ul>
      </Row>,
    )
  }

  if (active.includes('ttc_lines')) {
    rows.push(
      <Row key="ttc" title="Subway lines">
        <div className="flex flex-wrap gap-1">
          {Object.entries(TTC_LINE_COLOR).map(([n, c]) => (
            <span key={n}
                  className="flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold"
                  style={{ background: c, color: TTC_LINE_NEEDS_DARK_TYPE.includes(c) ? '#111827' : '#ffffff' }}>
              {n}
            </span>
          ))}
        </div>
        <p className="mt-1 text-[9.5px] leading-snug text-muted">
          Drawn in each line’s official TTC colour.
        </p>
      </Row>,
    )
  }

  if (!rows.length) return null

  return (
    <div className="panel pointer-events-auto w-[228px] space-y-3 p-3">
      {rows}
    </div>
  )
}

const BOUNDARY_LABEL: Record<string, string> = {
  municipalities: 'Former municipalities',
  neighbourhoods: 'Neighbourhoods',
  wards: 'Wards',
}

/**
 * The key for a layer defined by a spec rather than by hand.
 *
 * A layer encodes at most two things — what a mark's colour means and what its
 * size means — so the key has at most two parts. Returns null for a layer that
 * encodes nothing, which needs no key beyond its swatch in the layer panel.
 */
function specKey(def: LayerDef, data: GeoJSON.FeatureCollection | undefined,
                 pal: MapPalette): React.ReactNode {
  const style = def.style ?? {}
  const parts: React.ReactNode[] = []

  if (def.kind === 'area') {
    const b = (data as any)?.breaks as Breaks | undefined
    const colors = rampOf(Math.max(b?.stops?.length ?? 5, 2), pal.SEQUENTIAL_ALT)
    parts.push(
      <div key="ramp">
        <div className="flex h-2.5 overflow-hidden rounded-[3px]">
          {colors.map((c, i) => <div key={i} className="flex-1" style={{ background: c }} />)}
        </div>
        <div className="mt-1 flex justify-between text-[9.5px] tabular-nums text-muted">
          <span>{b?.min ?? 0}</span>
          <span>{b?.metric ?? 'count'} per ward</span>
          <span>{b?.max ?? ''}</span>
        </div>
      </div>,
    )
  } else if (style.diverging) {
    // A signed metric centred on zero. The scale is the model's own typical
    // error rather than the extremes, so the labels have to say so — "±11"
    // means nothing without "twice what the model usually gets wrong".
    const err = (data as any)?.meta?.typical_error as number | undefined
    const ramp = style.diverging === 'down_bad'
      ? [...pal.DIVERGING].reverse() : pal.DIVERGING
    parts.push(
      <div key="diverging">
        <div className="flex h-2.5 overflow-hidden rounded-[3px]">
          {ramp.map((c, i) => <div key={i} className="flex-1" style={{ background: c }} />)}
        </div>
        <div className="mt-1 flex justify-between text-[9.5px] tabular-nums text-muted">
          <span>{err ? `−${(err * 2).toFixed(0)}` : 'below'}</span>
          <span>as predicted</span>
          <span>{err ? `+${(err * 2).toFixed(0)}` : 'above'}</span>
        </div>
        <p className="mt-1 text-[9.5px] leading-snug text-muted">
          Points below what comparable buildings score
          {err ? `. The model is typically within ${err} points.` : '.'}
        </p>
      </div>,
    )
  } else if (style.classes) {
    // One swatch per *meaning*, listing the statuses that map onto it — five
    // outcome colours read; twelve raw status strings do not.
    const byClass = new Map<SemanticClass, string[]>()
    for (const [value, cls] of Object.entries(style.classes as Record<string, SemanticClass>)) {
      byClass.set(cls, [...(byClass.get(cls) ?? []), value])
    }
    parts.push(
      <ul key="classes" className="space-y-0.5">
        {[...byClass].map(([cls, values]) => (
          <li key={cls} className="flex items-start gap-1.5 text-[10px] text-ink-2">
            <span className="mt-[3px] h-2 w-2 shrink-0 rounded-full ring-1 ring-line-strong"
                  style={{ background: pal.SEMANTIC[cls] }} />
            <span className="leading-snug">{values.slice(0, 3).join(', ')}
              {values.length > 3 ? ` +${values.length - 3}` : ''}</span>
          </li>
        ))}
      </ul>,
    )
  } else {
    parts.push(
      <div key="dot" className="flex items-center gap-2 text-[10.5px] text-ink-2">
        <span className="h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-line-strong"
              style={{ background: pal.LAYER_COLOR[def.id] || seriesColor(def.id, pal) }} />
        <span>{def.geometry === 'point' ? 'One mark per record' : 'Area'}</span>
      </div>,
    )
  }

  if (style.size_by) {
    parts.push(
      <p key="size" className="mt-1 text-[9.5px] text-muted">
        Circle size = {String(style.size_by).replace(/_/g, ' ')}
      </p>,
    )
  }
  return parts.length ? <>{parts}</> : null
}

function Row({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      {title && <div className="mb-1 text-[11px] font-medium text-ink">{title}</div>}
      {children}
    </div>
  )
}

function divergingLabels(breaks: Breaks): string[] {
  const m = Math.max(Math.abs(breaks.min ?? 0), Math.abs(breaks.max ?? 0))
  return [`−${m.toFixed(0)}%`, '', '', '0%', '', '', `+${m.toFixed(0)}%`]
}
