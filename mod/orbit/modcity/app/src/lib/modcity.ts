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

export interface ComplianceRule { value: number | string; limit: number | string; ok: boolean }
export interface Compliance {
  ok: boolean
  budget?: ComplianceRule
  floors?: ComplianceRule
  carbon?: ComplianceRule
  occupancy?: ComplianceRule
  lot?: ComplianceRule
}

export interface Constraints {
  lot_w?: number
  lot_d?: number
  max_floors?: number
  max_budget?: number
  max_carbon_kg?: number
  min_occupancy?: number
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
  ['living', 'Living Hall', 'living', 'warm', 20000, 2300, 24, false, 'Open lounge brick — the social heart of any stack.'],
  ['kitchen', 'Galley Kitchen', 'service', 'steel', 22000, 2600, 28, false, 'Plug-and-play kitchen: pre-plumbed, appliances pre-fit.'],
  ['bath', 'Wet Core', 'service', 'steel', 14000, 1700, 18, false, 'Bathroom + utility riser. Snap it anywhere; pipes self-align.'],
  ['office', 'Work Pod', 'work', 'neutral', 17000, 1800, 21, false, 'Acoustically isolated home-office or maker space.'],
  ['atrium', 'Glass Atrium', 'light', 'glass', 26000, 2000, 30, true, 'Double-height glazed void that floods the stack with light.'],
  ['stair', 'Stair Core', 'structure', 'concrete', 12000, 1500, 14, false, 'Vertical circulation + structural spine. Stacks love a spine.'],
  ['solar', 'Solar Roof', 'roof', 'accent', 15000, 900, 16, false, 'Caps a stack: PV roof + rainwater catch. Net-positive energy.'],
  ['garden', 'Garden Deck', 'outdoor', 'green', 9000, 400, 12, false, 'Open-air terrace / green roof. Plant it, live on it.'],
  ['mezz', 'Mezzanine', 'living', 'warm', 19000, 2150, 24, false, 'Split-level loft insert — doubles usable area in a tall bay.'],
  ['retail', 'Ground Retail', 'commerce', 'neutral', 21000, 2400, 26, true, 'Shopfront / café brick for the street level of a block.'],
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
  return out
}

export const MANIFESTO = `Housing got slow, expensive, and ugly because we build every home from scratch, on site, by hand. ModCity treats buildings the way software treats components: a small library of standardized, factory-built modules that snap together on a grid like LEGO — then re-skin into any architecture style you want. Forge your own bricks, share them, remix anyone's building. Same bricks. Infinite cities.`

export const SECTIONS = [
  { k: 'Prefab as a protocol', t: 'Snap-together bricks, one footprint.', b: 'Every ModCity module is an identical 3×3×3 m unit — studios, kitchens, glass atriums, solar roofs, plus true NYC-brownstone parts: stoops, parlor floors, bay windows, cornices. Anything snaps to anything, and price, carbon and lead time are transparent on every brick.' },
  { k: 'Forge your own', t: 'Build your own LEGO. Share it.', b: 'Design a custom brick — colour, programme, price, embodied carbon — and it drops straight into your palette. Keep it private, or publish it to the community library so anyone can build with your piece.' },
  { k: 'Style is a layer', t: 'Brownstone today. Neo-Tokyo tomorrow.', b: 'Geometry and style are separated. The same stack re-skins instantly across nine architectures. A West Village row becomes a neon Hudson Yards spire with one tap — and the cost re-prices itself.' },
  { k: 'Private by default', t: 'Yours until you say so.', b: 'Every building you save is private. Publish to put it in the shared city, export it as a portable file, or share its content-addressed CID — and anyone can copy-and-remix it into their own.' },
  { k: 'Set the rules', t: 'Parameters & constraints, enforced live.', b: 'Set a lot size, a height cap, a budget and a carbon ceiling. The builder fences you to the lot, caps your floors, and the spec panel turns red the moment you bust a constraint — real developer pro-forma, in your browser.' },
  { k: 'Content-addressed', t: 'Buildings travel as CIDs.', b: 'Saving a building writes a self-contained, IPFS-style document — bundled custom bricks included — through the localfs module. The CID is the building: load it on any node, anywhere, and it renders identically.' },
]
