// Shared types, API client, and a self-contained mirror of the prefab
// spec so the configurator renders instantly (and even fully offline)
// before /catalog and /styles return.

export const API_URL = process.env.NEXT_PUBLIC_API_URL || '/modcity/api'
export const DEFAULT_STYLE = 'brownstone'

export interface ModuleSpec {
  id: string
  name: string
  category: string
  tone: string
  price: number
  carbon_kg: number
  lead_days: number
  glass: boolean
  blurb: string
  color?: string | null
  owner?: string
  custom?: boolean
  public?: boolean
  mine?: boolean
  footprint_m2: number
  edge_m: number
}

export interface StyleSpec {
  id: string
  name: string
  material: string
  vibe: string
  accent: string
  sky: string
  palette: Record<string, string>
  price_mult: number
  carbon_mult: number
}

// A designable, shareable façade panel — distinct from a module/room.
// Carries a full prefab fabrication spec (real mm dims, assembly, RSI, weight).
export interface PanelSpec {
  id: string
  name: string
  kind: 'solid' | 'window' | 'curtain' | 'louver'
  color: string
  frame: string
  glazing: number      // 0–1 glazed fraction
  win_cols: number
  win_rows: number
  material: string     // wood | metal | concrete | glass | composite
  reveal: boolean      // horizontal cladding board lines
  // ── prefab fabrication spec ──
  width_mm: number
  height_mm: number
  thickness_mm: number
  weight_kg: number
  assembly: string
  r_value: number      // RSI
  connection: string
  fab_notes: string
  price: number
  carbon_kg: number
  lead_days: number
  custom?: boolean
  public?: boolean
  owner?: string
  mine?: boolean
}

export interface ComplianceRule { value: number | string; limit: number | string; ok: boolean }
export interface Compliance {
  ok: boolean
  budget?: ComplianceRule
  floors?: ComplianceRule
  carbon?: ComplianceRule
  occupancy?: ComplianceRule
  lot?: ComplianceRule
  // ── laneway / ADU code envelope ──
  height_m?: ComplianceRule
  storeys?: ComplianceRule
  gfa?: ComplianceRule
  footprint?: ComplianceRule
}

export interface Constraints {
  lot_w?: number
  lot_d?: number
  max_floors?: number
  max_budget?: number
  max_carbon_kg?: number
  min_occupancy?: number
  code?: string            // laneway/ADU jurisdiction preset id
  skin?: string            // active façade panel id (building skin)
}

// Canadian laneway / ADU building-code preset (mirror of mod.py LANEWAY_CODES)
export interface CodeSpec {
  id: string
  name: string
  region: string
  note: string
  source: string | null
  max_height_m: number | null
  max_storeys: number | null
  max_gfa_m2: number | null
  max_footprint_m2: number | null
  recommend: { lot_w: number; lot_d: number; max_floors: number } | null
}

export interface Estimate {
  module_count: number
  floors: number
  style: string
  price_usd: number
  price_per_m2: number
  floor_area_m2: number
  floor_area_ft2: number
  embodied_carbon_kg: number
  lead_time_days: number
  occupancy: number
  solar_modules: number
  green_modules: number
  net_positive_energy: boolean
  footprint_cells?: number
  height_m?: number
  footprint_m2?: number
  compliance?: Compliance
}

export interface Cell { x: number; z: number; stack: string[] }

export interface Design {
  id: string
  name: string
  owner: string
  description: string
  style: string
  cells: Cell[]
  public: boolean
  copies: number
  forked_from: string | null
  featured?: boolean
  cid: string | null
  constraints: Constraints | null
  created: number
  updated: number
  stats: Estimate
}

export interface PortableDoc {
  modcity: number
  kind: string
  name: string
  style: string
  cells: Cell[]
  constraints: Constraints | null
  bricks: Record<string, ModuleSpec>
  panels?: Record<string, PanelSpec>
  by?: string
  cid?: string
}

export async function api(path: string, opts?: { method?: string; body?: any }) {
  const res = await fetch(`${API_URL}/${path}`, {
    method: opts?.method || 'GET',
    headers: opts?.body ? { 'Content-Type': 'application/json' } : undefined,
    body: opts?.body ? JSON.stringify(opts.body) : undefined,
    cache: 'no-store',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || 'request failed')
  }
  return res.json()
}

export function ownerId(): string {
  if (typeof window === 'undefined') return ''
  let o = localStorage.getItem('modcity_owner')
  if (!o) {
    o = '0x' + Math.random().toString(16).slice(2, 10) + Math.random().toString(16).slice(2, 6)
    localStorage.setItem('modcity_owner', o)
  }
  return o
}

// ── Offline mirror of the prefab spec (keep in sync with mod.py) ──
const RAW: any[] = [
  ['studio', 'Studio Cube', 'living', 'warm', 18000, 2100, 21, false, 'A complete micro-home: bed, nook, storage. The atom of ModCity.'],
  ['bedroom', 'Bedroom Bay', 'living', 'warm', 16000, 1900, 21, false, 'Quiet sleeping module with a full-height window.'],
  ['living', 'Living Hall', 'living', 'warm', 20000, 2300, 24, false, 'Open lounge module — the social heart of any stack.'],
  ['kitchen', 'Galley Kitchen', 'service', 'steel', 22000, 2600, 28, false, 'Plug-and-play kitchen: pre-plumbed, appliances pre-fit.'],
  ['bath', 'Wet Core', 'service', 'steel', 14000, 1700, 18, false, 'Bathroom + utility riser. Snap it anywhere; pipes self-align.'],
  ['office', 'Work Pod', 'work', 'neutral', 17000, 1800, 21, false, 'Acoustically isolated home-office or maker space.'],
  ['atrium', 'Glass Atrium', 'light', 'glass', 26000, 2000, 30, true, 'Double-height glazed void that floods the stack with light.'],
  ['stair', 'Stair Core', 'structure', 'concrete', 12000, 1500, 14, false, 'Vertical circulation + structural spine. Stacks love a spine.'],
  ['solar', 'Solar Roof', 'roof', 'accent', 15000, 900, 16, false, 'Caps a stack: PV roof + rainwater catch. Net-positive energy.'],
  ['garden', 'Garden Deck', 'outdoor', 'green', 9000, 400, 12, false, 'Open-air terrace / green roof. Plant it, live on it.'],
  ['mezz', 'Mezzanine', 'living', 'warm', 19000, 2150, 24, false, 'Split-level loft insert — doubles usable area in a tall bay.'],
  ['retail', 'Ground Retail', 'commerce', 'neutral', 21000, 2400, 26, true, 'Shopfront / café module for the street level of a block.'],
  ['stoop', 'Stoop & Entry', 'structure', 'stone', 13000, 1400, 14, false, 'The classic NYC stoop: raised entry over a garden level.'],
  ['parlor', 'Parlor Floor', 'living', 'warm', 23000, 2400, 26, false, 'High-ceiling parlor with floor-to-ceiling brownstone windows.'],
  ['bay', 'Bay Window', 'living', 'warm', 21000, 2200, 24, false, 'Projecting three-sided bay — the brownstone signature face.'],
  ['cornice', 'Cornice Roof', 'roof', 'stone', 16000, 1100, 18, false, 'Ornamented sheet-metal cornice. Crowns the row in true NY style.'],
]
export const FALLBACK_CATALOG: ModuleSpec[] = RAW.map((r) => ({
  id: r[0], name: r[1], category: r[2], tone: r[3], price: r[4], carbon_kg: r[5],
  lead_days: r[6], glass: r[7], blurb: r[8], color: null, custom: false, public: true,
  footprint_m2: 9, edge_m: 3,
}))

// Built-in façade panels (mirror of mod.py PANELS) — shown instantly / offline.
export const FALLBACK_PANELS: PanelSpec[] = [
  { id: 'clt_solid', name: 'CLT Solid Wall', kind: 'solid', color: '#caa472', frame: '#7a5a3c', glazing: 0, win_cols: 0, win_rows: 0, material: 'wood', reveal: true, width_mm: 3000, height_mm: 3000, thickness_mm: 120, weight_kg: 850, assembly: 'CLT (cross-laminated timber)', r_value: 3.5, connection: 'screwed (self-tapping)', fab_notes: '5-ply CLT, CNC-routed service chases, factory-sealed.', price: 4200, carbon_kg: 520, lead_days: 18 },
  { id: 'sip_window', name: 'SIP Window Wall', kind: 'window', color: '#e7e2d8', frame: '#3a3a3a', glazing: 0.35, win_cols: 2, win_rows: 2, material: 'composite', reveal: false, width_mm: 3000, height_mm: 3000, thickness_mm: 200, weight_kg: 210, assembly: 'SIP (structural insulated panel)', r_value: 5.0, connection: 'screwed + spline', fab_notes: 'OSB/EPS/OSB core, factory-glazed triple-pane units, taped seams.', price: 3800, carbon_kg: 410, lead_days: 14 },
  { id: 'curtain_glass', name: 'Unitised Curtain Wall', kind: 'curtain', color: '#9fc4d4', frame: '#2b3138', glazing: 0.95, win_cols: 2, win_rows: 3, material: 'glass', reveal: false, width_mm: 3000, height_mm: 3000, thickness_mm: 60, weight_kg: 180, assembly: 'Unitised aluminium curtain wall', r_value: 0.7, connection: 'stack-joint clip', fab_notes: 'Factory-assembled stick units, low-e double glazing, drained/pressure-equalised.', price: 5600, carbon_kg: 720, lead_days: 24 },
  { id: 'precast_concrete', name: 'Precast Concrete', kind: 'solid', color: '#b9bcc2', frame: '#7e8186', glazing: 0.12, win_cols: 1, win_rows: 1, material: 'concrete', reveal: true, width_mm: 3000, height_mm: 3000, thickness_mm: 150, weight_kg: 1100, assembly: 'Precast concrete sandwich', r_value: 2.0, connection: 'bolted (cast-in ferrules)', fab_notes: 'Insulated wythe, form-liner reveal pattern, lifting anchors cast in.', price: 4900, carbon_kg: 1450, lead_days: 20 },
  { id: 'steel_louver', name: 'Steel Louver Screen', kind: 'louver', color: '#5b6066', frame: '#33373c', glazing: 0, win_cols: 0, win_rows: 0, material: 'metal', reveal: false, width_mm: 3000, height_mm: 3000, thickness_mm: 80, weight_kg: 140, assembly: 'Steel-stud + perforated/louvered metal', r_value: 1.0, connection: 'bolted', fab_notes: 'Solar-shading screen, powder-coated, ventilated rainscreen cavity.', price: 3100, carbon_kg: 380, lead_days: 12 },
  { id: 'timber_board', name: 'Timber Board Clad', kind: 'window', color: '#a8753f', frame: '#5c3d22', glazing: 0.2, win_cols: 1, win_rows: 1, material: 'wood', reveal: true, width_mm: 3000, height_mm: 3000, thickness_mm: 180, weight_kg: 190, assembly: 'Steel-stud + charred-timber rainscreen', r_value: 4.2, connection: 'screwed (clip rail)', fab_notes: 'Shou-sugi-ban board-on-board, ventilated cavity, factory-finished.', price: 3500, carbon_kg: 300, lead_days: 15 },
]
export const PANEL_INDEX_FALLBACK: Record<string, PanelSpec> = Object.fromEntries(FALLBACK_PANELS.map((p) => [p.id, p]))

// ── Prefab fabrication schedule (panel takeoff) ──────────────────
// Decomposes a building into the discrete factory-built panels a prefab shop
// fabricates: floor/roof cassettes + each exposed wall/curtain panel (party
// walls culled, exactly as rendered). Every row is a standard 3 m module panel
// with real mm dims, assembly, openings and weight — i.e. a factory cut sheet.
export interface FabRow {
  ref: string; element: string; cell: string; level: number; face: string
  width_mm: number; height_mm: number; thickness_mm: number; area_m2: number
  assembly: string; material: string; glazing_pct: number; openings: number; weight_kg: number
}
const FLOOR_CASSETTE = { assembly: 'CLT floor cassette', material: 'wood', thickness_mm: 200, weight_kg: 1200 }
const ROOF_CASSETTE = { assembly: 'CLT roof cassette + membrane', material: 'wood', thickness_mm: 240, weight_kg: 1000 }

export function buildFabSchedule(cells: Cell[], catalog: ModuleSpec[], skin?: PanelSpec): FabRow[] {
  const byId = Object.fromEntries(catalog.map((m) => [m.id, m]))
  const occ = new Map<string, string[]>(cells.map((c) => [`${c.x},${c.z}`, c.stack]))
  const specAt = (x: number, z: number, l: number) => byId[(occ.get(`${x},${z}`) || [])[l]]
  const isOpen = (s?: ModuleSpec) => !s || s.id === 'garden' || (s.category === 'outdoor' && !s.glass)
  const wall = skin && skin.kind !== 'curtain' ? skin : PANEL_INDEX_FALLBACK['clt_solid']
  const curtain = skin && skin.kind === 'curtain' ? skin : PANEL_INDEX_FALLBACK['curtain_glass']
  const rows: FabRow[] = []
  let n = 0
  const ref = (p: string) => `${p}-${String(++n).padStart(3, '0')}`
  for (const c of cells) {
    c.stack.forEach((id, level) => {
      const spec = byId[id]; if (!spec) return
      const open = spec.id === 'garden' || (spec.category === 'outdoor' && !spec.glass)
      rows.push({ ref: ref('FLR'), element: 'Floor cassette', cell: `${c.x},${c.z}`, level, face: '—', width_mm: 3000, height_mm: 3000, thickness_mm: FLOOR_CASSETTE.thickness_mm, area_m2: 9, assembly: FLOOR_CASSETTE.assembly, material: FLOOR_CASSETTE.material, glazing_pct: 0, openings: 0, weight_kg: FLOOR_CASSETTE.weight_kg })
      if (!open) {
        const glass = spec.glass
        for (const [dx, dz, f] of [[1, 0, '+X'], [-1, 0, '-X'], [0, 1, '+Z'], [0, -1, '-Z']] as const) {
          if (!isOpen(specAt(c.x + dx, c.z + dz, level))) continue
          const pn = glass ? curtain : wall
          const openings = pn.kind === 'window' ? pn.win_cols * pn.win_rows : (pn.kind === 'curtain' ? 1 : 0)
          rows.push({ ref: ref(glass ? 'CW' : 'WAL'), element: glass ? 'Curtain-wall panel' : 'Wall panel', cell: `${c.x},${c.z}`, level, face: f, width_mm: pn.width_mm, height_mm: pn.height_mm, thickness_mm: pn.thickness_mm, area_m2: Math.round(pn.width_mm * pn.height_mm / 1e6 * 10) / 10, assembly: pn.assembly, material: pn.material, glazing_pct: Math.round(pn.glazing * 100), openings, weight_kg: pn.weight_kg })
        }
        if (level === c.stack.length - 1 && id !== 'cornice') {
          rows.push({ ref: ref('ROF'), element: 'Roof cassette', cell: `${c.x},${c.z}`, level, face: 'top', width_mm: 3000, height_mm: 3000, thickness_mm: ROOF_CASSETTE.thickness_mm, area_m2: 9, assembly: ROOF_CASSETTE.assembly, material: ROOF_CASSETTE.material, glazing_pct: 0, openings: 0, weight_kg: ROOF_CASSETTE.weight_kg })
        }
      }
    })
  }
  return rows
}

export function fabScheduleCSV(rows: FabRow[], meta?: { name?: string; skin?: string }): string {
  const head = ['Ref', 'Element', 'Cell', 'Level', 'Face', 'Width_mm', 'Height_mm', 'Thickness_mm', 'Area_m2', 'Assembly', 'Material', 'Glazing_%', 'Openings', 'Weight_kg']
  const lines: string[] = []
  if (meta?.name) lines.push(`# ModCity fabrication schedule — ${meta.name}${meta.skin ? ` · skin: ${meta.skin}` : ''}`)
  lines.push(head.join(','))
  for (const r of rows) lines.push([r.ref, r.element, r.cell, r.level, r.face, r.width_mm, r.height_mm, r.thickness_mm, r.area_m2, `"${r.assembly}"`, r.material, r.glazing_pct, r.openings, r.weight_kg].join(','))
  const totW = rows.reduce((s, r) => s + r.weight_kg, 0)
  const totA = Math.round(rows.reduce((s, r) => s + r.area_m2, 0) * 10) / 10
  lines.push('')
  lines.push(`# ${rows.length} panels · ${totA} m² total panel area · ${totW} kg total fabricated weight (standard 3000mm module grid)`)
  return lines.join('\n')
}

export const FALLBACK_STYLES: StyleSpec[] = [
  { id: 'brownstone', name: 'NYC Brownstone', material: 'stone', vibe: 'Sandstone row houses, iron stoops, parlor windows. Pure New York.', accent: '#8a5a3c', sky: '#d9c7b0', price_mult: 1.1, carbon_mult: 0.9, palette: { warm: '#9c6b46', service: '#7a5a48', steel: '#3a3330', neutral: '#c9b9a3', glass: '#b8c4c0', concrete: '#6b5444', accent: '#8a5a3c', green: '#6e7a52', work: '#a8835c', stone: '#7a4a2b' } },
  { id: 'bauhaus', name: 'Bauhaus', material: 'matte', vibe: 'Primary colours, honest geometry, form follows function.', accent: '#e63946', sky: '#f1faee', price_mult: 1.0, carbon_mult: 1.0, palette: { warm: '#f4a261', service: '#457b9d', steel: '#457b9d', neutral: '#e9ecef', glass: '#a8dadc', concrete: '#adb5bd', accent: '#e63946', green: '#80b918', work: '#ffd166', stone: '#cbb799' } },
  { id: 'brutalist', name: 'Brutalist', material: 'concrete', vibe: 'Raw béton brut. Monolithic, heroic, unapologetic.', accent: '#6c757d', sky: '#ced4da', price_mult: 0.92, carbon_mult: 1.18, palette: { warm: '#adb5bd', service: '#868e96', steel: '#495057', neutral: '#ced4da', glass: '#9aa6b2', concrete: '#6c757d', accent: '#343a40', green: '#74896b', work: '#8d99ae', stone: '#8d8378' } },
  { id: 'scandi', name: 'Scandinavian', material: 'wood', vibe: 'Pale timber, white render, soft daylight. Hygge as a grid.', accent: '#dda15e', sky: '#fefae0', price_mult: 1.08, carbon_mult: 0.82, palette: { warm: '#e9d8c4', service: '#dee2e6', steel: '#ced4da', neutral: '#f8f9fa', glass: '#cfe8ef', concrete: '#e9ecef', accent: '#dda15e', green: '#a3b18a', work: '#e9d8c4', stone: '#e7dccb' } },
  { id: 'japandi', name: 'Japandi', material: 'wood', vibe: 'Warm wood + black steel + wabi-sabi calm.', accent: '#bb9457', sky: '#ede0d4', price_mult: 1.12, carbon_mult: 0.85, palette: { warm: '#c8a27c', service: '#8a817c', steel: '#3a3a3a', neutral: '#d6ccc2', glass: '#b7c4cf', concrete: '#7f7f7f', accent: '#bb9457', green: '#6b705c', work: '#a98467', stone: '#a08b73' } },
  { id: 'mediterranean', name: 'Mediterranean', material: 'stucco', vibe: 'Whitewash + terracotta, deep shade, sea light.', accent: '#e07a5f', sky: '#ade8f4', price_mult: 1.05, carbon_mult: 0.95, palette: { warm: '#f2cc8f', service: '#e9edc9', steel: '#cb997e', neutral: '#fefae0', glass: '#90e0ef', concrete: '#ddbea9', accent: '#e07a5f', green: '#83a98c', work: '#f4d58d', stone: '#e3c9a0' } },
  { id: 'neotokyo', name: 'Neo-Tokyo', material: 'neon', vibe: 'Dark monolith, neon edges, rain-slick cyberpunk skyline.', accent: '#00f5d4', sky: '#0b0c1a', price_mult: 1.2, carbon_mult: 1.05, palette: { warm: '#7209b7', service: '#3a0ca3', steel: '#4361ee', neutral: '#1b1b2f', glass: '#00b4d8', concrete: '#16213e', accent: '#00f5d4', green: '#06d6a0', work: '#f72585', stone: '#2a2f45' } },
  { id: 'adobe', name: 'Desert Adobe', material: 'earth', vibe: 'Sun-baked earth tones, thick walls, thermal mass.', accent: '#bc6c25', sky: '#fefae0', price_mult: 0.96, carbon_mult: 0.78, palette: { warm: '#dda15e', service: '#cb997e', steel: '#b08968', neutral: '#e6ccb2', glass: '#a5a58d', concrete: '#9c6644', accent: '#bc6c25', green: '#7f9172', work: '#d4a373', stone: '#c9a27a' } },
  { id: 'glasshouse', name: 'Glasshouse', material: 'glass', vibe: 'All-glass curtain wall. A building made of sky.', accent: '#48cae4', sky: '#caf0f8', price_mult: 1.28, carbon_mult: 1.1, palette: { warm: '#90e0ef', service: '#48cae4', steel: '#0096c7', neutral: '#ade8f4', glass: '#caf0f8', concrete: '#00b4d8', accent: '#0077b6', green: '#80ffdb', work: '#48cae4', stone: '#9fb8c4' } },
]

const SLEEPING = new Set(['studio', 'bedroom', 'mezz', 'parlor', 'bay'])

// ── Local estimator mirror (snappy HUD; matches backend formula) ──
export function localEstimate(cells: Cell[], style: StyleSpec, catalog: ModuleSpec[], constraints?: Constraints | null): Estimate {
  const byId = Object.fromEntries(catalog.map((m) => [m.id, m]))
  const flat: ModuleSpec[] = []
  let floors = 0, footprint = 0, maxAx = 0, maxAz = 0
  for (const c of cells) {
    if (c.stack.length) { footprint++; maxAx = Math.max(maxAx, Math.abs(c.x)); maxAz = Math.max(maxAz, Math.abs(c.z)) }
    floors = Math.max(floors, c.stack.length)
    for (const id of c.stack) if (byId[id]) flat.push(byId[id])
  }
  const basePrice = flat.reduce((s, m) => s + m.price, 0)
  const baseCarbon = flat.reduce((s, m) => s + m.carbon_kg, 0)
  const lead = flat.reduce((mx, x) => Math.max(mx, x.lead_days), 0)
  const area = Math.round(flat.length * 9 * 10) / 10
  const price = Math.round(basePrice * style.price_mult)
  const carbon = Math.round(baseCarbon * style.carbon_mult)
  const solar = flat.filter((m) => m.id === 'solar').length
  const green = flat.filter((m) => m.id === 'garden').length
  const occ = flat.filter((m) => SLEEPING.has(m.id) || (m.custom && m.category === 'living')).length
  const assembly = flat.length ? Math.max(1, Math.round(flat.length * 0.4)) : 0
  const est: Estimate = {
    module_count: flat.length, floors, style: style.id, price_usd: price,
    price_per_m2: area ? Math.round(price / area) : 0, floor_area_m2: area,
    floor_area_ft2: Math.round(area * 10.7639), embodied_carbon_kg: carbon,
    lead_time_days: flat.length ? lead + assembly : 0, occupancy: occ,
    solar_modules: solar, green_modules: green, net_positive_energy: solar > 0,
    footprint_cells: footprint,
    height_m: Math.round(floors * 3 * 10) / 10,
    footprint_m2: Math.round(footprint * 9 * 10) / 10,
  }
  if (constraints) est.compliance = checkConstraints(est, constraints, maxAx, maxAz)
  return est
}

export function checkConstraints(est: Estimate, c: Constraints, maxAx = 0, maxAz = 0): Compliance {
  const out: Compliance = { ok: true }
  const add = (k: keyof Compliance, value: number | string, limit: number | string, ok: boolean) => {
    ;(out as any)[k] = { value, limit, ok }; if (!ok) out.ok = false
  }
  if (c.max_budget) add('budget', est.price_usd, c.max_budget, est.price_usd <= c.max_budget)
  if (c.max_floors) add('floors', est.floors, c.max_floors, est.floors <= c.max_floors)
  if (c.max_carbon_kg) add('carbon', est.embodied_carbon_kg, c.max_carbon_kg, est.embodied_carbon_kg <= c.max_carbon_kg)
  if (c.min_occupancy) add('occupancy', est.occupancy, c.min_occupancy, est.occupancy >= c.min_occupancy)
  if (c.lot_w && c.lot_d) {
    const fits = maxAx <= Math.floor(c.lot_w / 2) && maxAz <= Math.floor(c.lot_d / 2)
    add('lot', `${maxAx * 2 + 1}×${maxAz * 2 + 1}`, `${c.lot_w}×${c.lot_d}`, fits)
  }
  // laneway / ADU code envelope
  const code = c.code ? CODE_INDEX[c.code] : undefined
  if (code) {
    const h = est.height_m ?? 0, fp = est.footprint_m2 ?? 0
    if (code.max_height_m != null) add('height_m', h, code.max_height_m, h <= code.max_height_m)
    if (code.max_storeys != null) add('storeys', est.floors, code.max_storeys, est.floors <= code.max_storeys)
    if (code.max_gfa_m2 != null) add('gfa', est.floor_area_m2, code.max_gfa_m2, est.floor_area_m2 <= code.max_gfa_m2)
    if (code.max_footprint_m2 != null) add('footprint', fp, code.max_footprint_m2, fp <= code.max_footprint_m2)
  }
  return out
}

// ── Canadian laneway / ADU code presets (mirror of mod.py LANEWAY_CODES) ──
export const CODES: CodeSpec[] = [
  { id: 'none', name: 'Freeform (no code)', region: '—', note: 'No building-code envelope applied.', source: null,
    max_height_m: null, max_storeys: null, max_gfa_m2: null, max_footprint_m2: null, recommend: null },
  { id: 'nbc_part9', name: 'NBC Part 9 — Houses & Small Buildings', region: 'Canada (national model code)',
    max_height_m: null, max_storeys: 3, max_gfa_m2: null, max_footprint_m2: 600,
    recommend: { lot_w: 3, lot_d: 4, max_floors: 2 },
    note: 'Part 9 scope: ≤3 storeys AND ≤600 m² building area (Group C residential). Every detached ADU sits well inside it. Provinces add energy tiers — BC Step Code is Step 3 now, Step 5 (net-zero-ready) by 2032.',
    source: 'https://nrc.canada.ca/en/certifications-evaluations-standards/codes-canada/codes-canada-publications/national-building-code-canada-2020' },
  { id: 'cmhc_adu', name: 'CMHC Housing Design Catalogue — ADU', region: 'Canada (pre-approved catalogue)',
    max_height_m: 8.5, max_storeys: 2, max_gfa_m2: 128, max_footprint_m2: null,
    recommend: { lot_w: 3, lot_d: 4, max_floors: 2 },
    note: 'Free national catalogue of ~50 standardized, near-permit-ready designs incl. detached ADUs (studio–2-bed, 1–2 storey, ≈46–128 m²). 14+ municipalities pre-review them to fast-track approvals. An architect must still adapt to the site.',
    source: 'https://www.housingcatalogue.cmhc-schl.gc.ca/' },
  { id: 'van_laneway', name: 'Vancouver — Laneway House (R1-1)', region: 'Vancouver, BC',
    max_height_m: 8.5, max_storeys: 2, max_gfa_m2: 186, max_footprint_m2: null,
    recommend: { lot_w: 3, lot_d: 4, max_floors: 2 },
    note: 'Zoning By-law §11. Floor area = lesser of 0.25×site area or 186 m² (FSR raised 0.16→0.25 in 2025). 2-storey form ≤8.5 m; 1½-storey ≤~6.7 m by roof pitch. Site coverage ≤50% (whole parcel), 4.9 m separation, no parking required. Verify §11.',
    source: 'https://bylaws.vancouver.ca/zoning/zoning-by-law-section-11.pdf' },
  { id: 'to_laneway', name: 'Toronto — Laneway Suite', region: 'Toronto, ON',
    max_height_m: 6.3, max_storeys: 2, max_gfa_m2: null, max_footprint_m2: 60,
    recommend: { lot_w: 3, lot_d: 4, max_floors: 2 },
    note: 'Zoning By-law 569-2013 Ch.150.8: lot must abut a public lane. Footprint ≤60 m² (≤10 m deep × 8 m wide); height 4.0 m at 5–7.5 m separation, 6.3 m beyond; GFA < main house; ancillary coverage ≤30%. O.Reg 462/24 cuts separation to 4.0 m for ≤4 m units & drops the angular plane.',
    source: 'https://www.toronto.ca/zoning/bylaw_amendments/ZBL_NewProvision_Chapter150_8.htm' },
  { id: 'to_garden', name: 'Toronto — Garden Suite', region: 'Toronto, ON',
    max_height_m: 6.0, max_storeys: 2, max_gfa_m2: null, max_footprint_m2: 60,
    recommend: { lot_w: 3, lot_d: 3, max_floors: 2 },
    note: 'Zoning By-law 569-2013 Ch.150.7 (as-of-right 2022, lots WITHOUT lane access). Footprint = lesser of 0.40×rear-yard or 60 m²; height 4.0 m within 7.5 m separation, 6.0 m beyond; GFA < main house; ancillary coverage ≤20%. O.Reg 462/24 overlay applies.',
    source: 'https://www.toronto.ca/zoning/bylaw_amendments/ZBL_NewProvision_Chapter150_7.htm' },
]
export const CODE_INDEX: Record<string, CodeSpec> = Object.fromEntries(CODES.map((c) => [c.id, c]))

// Pre-approved-style laneway templates (mirror of mod.py LANEWAY_TEMPLATES) —
// instant-load compositions that already fit their code envelope.
export interface LanewayTemplate { id: string; name: string; style: string; code: string; description: string; constraints: Constraints; cells: Cell[] }
export const LANEWAY_TEMPLATES: LanewayTemplate[] = [
  { id: 'dsn_lane_studio', name: 'Studio Laneway · 1-storey', style: 'scandi', code: 'van_laneway',
    description: '388 sq ft single-storey laneway studio. Vancouver-compliant.',
    constraints: { code: 'van_laneway', lot_w: 3, lot_d: 3, max_floors: 2 },
    cells: [{ x: 0, z: 0, stack: ['living'] }, { x: 1, z: 0, stack: ['kitchen'] },
            { x: 0, z: 1, stack: ['studio'] }, { x: 1, z: 1, stack: ['bath'] }] },
  { id: 'dsn_lane_onebed', name: 'One-Bed Laneway · 1½-storey', style: 'scandi', code: 'cmhc_adu',
    description: '775 sq ft 1-bed over two levels with solar roof. CMHC-catalogue style.',
    constraints: { code: 'cmhc_adu', lot_w: 3, lot_d: 3, max_floors: 2 },
    cells: [{ x: 0, z: 0, stack: ['living', 'bedroom'] }, { x: 1, z: 0, stack: ['kitchen', 'bath'] },
            { x: 0, z: 1, stack: ['bath', 'office'] }, { x: 1, z: 1, stack: ['stair', 'solar'] }] },
  { id: 'dsn_lane_twobed', name: 'Two-Bed Laneway · 2-storey', style: 'scandi', code: 'to_laneway',
    description: '969 sq ft 2-bed laneway suite with rear green deck. Toronto envelope.',
    constraints: { code: 'to_laneway', lot_w: 3, lot_d: 5, max_floors: 2 },
    cells: [{ x: 0, z: 0, stack: ['living', 'bedroom'] }, { x: 1, z: 0, stack: ['kitchen', 'bedroom'] },
            { x: 0, z: 1, stack: ['bath', 'bath'] }, { x: 1, z: 1, stack: ['stair', 'office'] },
            { x: 0, z: 2, stack: ['garden'] }, { x: 1, z: 2, stack: ['garden'] }] },
]

export const MANIFESTO = `Housing got slow, expensive, and ugly because we build every home from scratch, on site, by hand. ModCity treats buildings the way software treats components: a small library of standardized, factory-built panel modules — floors, walls, curtain-wall glazing — that assemble panel by panel into real architecture, then re-skin into any style you want. Forge your own panels, share them, remix anyone's building. Same panels. Infinite cities.`

export const SECTIONS = [
  { k: 'Prefab as a protocol', t: 'Panelised modules, one footprint.', b: 'Every ModCity module is an identical 3×3×3 m panel assembly — floor slab, exposed wall and curtain-wall panels, framed corners — for studios, kitchens, glass atriums, solar roofs, plus true NYC-brownstone parts: stoops, parlor floors, bay windows, cornices. Adjacent modules share party walls, so a stack reads as a real building, not stacked toy cubes. Price, carbon and lead time are transparent on every panel.' },
  { k: 'Forge your own', t: 'Design your own panel. Share it.', b: 'Design a custom panel module — colour, programme, price, embodied carbon — and it drops straight into your palette. Keep it private, or publish it to the community library so anyone can build with your piece.' },
  { k: 'Style is a layer', t: 'Brownstone today. Neo-Tokyo tomorrow.', b: 'Geometry and style are separated. The same stack re-skins instantly across nine architectures. A West Village row becomes a neon Hudson Yards spire with one tap — and the cost re-prices itself.' },
  { k: 'Private by default', t: 'Yours until you say so.', b: 'Every building you save is private. Publish to put it in the shared city, export it as a portable file, or share its content-addressed CID — and anyone can copy-and-remix it into their own.' },
  { k: 'Set the rules', t: 'Parameters & constraints, enforced live.', b: 'Set a lot size, a height cap, a budget and a carbon ceiling. The builder fences you to the lot, caps your floors, and the spec panel turns red the moment you bust a constraint — real developer pro-forma, in your browser.' },
  { k: 'Content-addressed', t: 'Buildings travel as CIDs.', b: 'Saving a building writes a self-contained, IPFS-style document — bundled custom panels included — through the localfs module. The CID is the building: load it on any node, anywhere, and it renders identically.' },
]
