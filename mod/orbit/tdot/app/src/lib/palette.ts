/**
 * The map's colour system.
 *
 * Every ramp here was checked with the palette validator against the surface it
 * is drawn on rather than picked by eye. There are two surfaces — the dark
 * basemap (#1c1c1c) and the light one (#f4f2ee) — and a ramp is only valid on
 * one of them, so the tables come in pairs and `mapPalette(base)` picks:
 *
 *   sequential  one hue, monotonic in lightness, running *away* from the
 *               surface. Dark surface ⇒ dark→light; light surface ⇒ light→dark.
 *               Reusing one direction on both is what makes a themed map read
 *               inside-out.
 *   diverging   blue ↔ red with a neutral midpoint that recedes into the
 *               surface. Both arms move away from the middle in lightness.
 *   overlays    aqua + orange — the pair clears all-pairs CVD separation
 *               (ΔE 9.4 deutan) and normal-vision separation (ΔE 26.5) on the
 *               map surface. Layers that share a mark form never share a hue;
 *               layers of different form (a translucent fill vs a hairline)
 *               may, because form is already carrying the distinction.
 *
 * The UI chrome is *not* here — that lives in CSS custom properties (see
 * globals.css), because it is themed per theme rather than per surface.
 */

import type { ThemeBase } from './theme'

/** What a status *means*, independent of what colour says so on this surface. */
export type SemanticClass = 'live' | 'good' | 'warn' | 'bad' | 'muted'

export type MapPalette = {
  /** Sequential magnitude ramp, low → high. */
  SEQUENTIAL: string[]
  /**
   * A second sequential ramp, for the non-crime choropleths (short-term
   * rentals, rooming houses, anything a user adds with no coordinates). Two
   * choropleths can be switched on at once, so the second one needs its own
   * hue — same lightness discipline, different family.
   */
  SEQUENTIAL_ALT: string[]
  /** Diverging ramp, most-negative → most-positive; index 3 is the neutral mid. */
  DIVERGING: string[]
  /**
   * Outcome colours, for the "where does this stand" fields the planning
   * datasets are full of. A layer spec names the *meaning* (`live`, `good`,
   * `warn`, `bad`, `muted`) and the surface decides the hue — which is why
   * backend specs carry no hex.
   */
  SEMANTIC: Record<SemanticClass, string>
  /**
   * Fallback series for layers nobody has styled — anything added from the
   * portal at runtime. Picked by hashing the layer id, so a given dataset
   * keeps its colour across reloads, and hue-separated on the surface.
   */
  SERIES: string[]
  /** Density ramp for the collision heatmap, transparent → hot. */
  HEAT: [number, string][]
  /** Per-layer colours. Same mark form ⇒ different hue. */
  LAYER_COLOR: Record<string, string>
  /** Crime-category colours for the incident point layer. */
  CATEGORY_COLOR: Record<string, string>
  /**
   * "No data" fill. Deliberately a *neutral* grey: every class in the
   * sequential and diverging ramps is chromatic, so an absence of hue is what
   * marks a polygon as outside the scale.
   */
  NO_DATA: string
  /** RentSafeTO score classes, [below 65, 65–79]; 80+ uses LAYER_COLOR.apartments. */
  SCORE: [string, string]
  /** A collision that killed someone, against the layer's own hue. */
  FATAL: string
  /** Fallback marks for features the feed didn't colour. */
  POINT_DEFAULT: string
  LINE_DEFAULT: string
  /** Halo/ring drawn *behind* a mark to hold it off the basemap. */
  HALO: string
  /** Hairline between choropleth polygons. */
  HAIRLINE: string
  /** Label ink on the basemap. */
  LABEL: string
}

const DARK: MapPalette = {
  SEQUENTIAL: ['#0d366b', '#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'],
  SEQUENTIAL_ALT: ['#0b3d31', '#0f5a45', '#13795b', '#199e70', '#3cba8c', '#7ad3b0', '#b6e8d3'],
  // On a crime map "up" is bad, so the positive arm is the red one.
  DIVERGING: ['#86b6ef', '#3987e5', '#1c5cab', '#383835', '#b83c3c', '#e66767', '#f2a0a0'],
  SEMANTIC: {
    live: '#3987e5',      // in front of the city right now
    good: '#199e70',      // cleared, approved, built
    warn: '#eda100',      // appealed, contested
    bad: '#e66767',       // refused
    muted: '#8a8983',     // closed — present, but not news
  },
  SERIES: ['#3987e5', '#eda100', '#199e70', '#b07ce8', '#e66767', '#22d3ee', '#f0abfc'],
  // Warm and monotonic in lightness (L 0.04 → 0.75). A heat ramp is the one
  // documented multi-hue sequential case; the hues are deliberately warm so the
  // heatmap never competes with the blue choropleth underneath it.
  HEAT: [
    [0, 'rgba(59,42,128,0)'],
    [0.2, '#3b2a80'],
    [0.4, '#7d2b6b'],
    [0.6, '#b83c3c'],
    [0.8, '#d95926'],
    [1, '#eda100'],
  ],
  LAYER_COLOR: {
    green_spaces: '#199e70',       // translucent fill
    cycling_network: '#d95926',    // hairline
    streetcars: '#DA251D',         // TTC streetcar red (network identity colour)
    apartments: '#9ec5f4',         // graduated circle
    subway_stations: '#ffffff',    // reference infrastructure, not a data series
    collisions: '#e66767',
    municipalities: '#c3c2b7',
    neighbourhoods: '#898781',
    wards: '#9c8f3a',
    // Real estate & housing. The two planning layers are coloured by status
    // (SEMANTIC) rather than by layer, so what is listed here is their swatch;
    // the rest are single-hue circles sized by how much building they are.
    development_applications: '#3987e5',
    development_pipeline: '#3987e5',
    affordable_housing: '#b07ce8',
    city_realty: '#f0abfc',
    rental_buildings: '#22d3ee',
  },
  // Five categoricals, hue-separated on the dark surface; assault (the most
  // common) gets the hue most distinct from the choropleth blues.
  CATEGORY_COLOR: {
    assault: '#e66767',
    auto_theft: '#eda100',
    break_enter: '#3987e5',
    robbery: '#b07ce8',
    theft_over: '#199e70',
  },
  NO_DATA: '#5c5b56',
  SCORE: ['#e66767', '#eda100'],
  FATAL: '#f2a0a0',
  POINT_DEFAULT: '#9ec5f4',
  LINE_DEFAULT: '#8b93a7',
  HALO: '#11151c',
  HAIRLINE: 'rgba(255,255,255,0.22)',
  LABEL: '#e6e8ee',
}

const LIGHT: MapPalette = {
  SEQUENTIAL: ['#e3edfb', '#c2d9f6', '#98bdec', '#6b9ee0', '#4177c9', '#27539f', '#143567'],
  SEQUENTIAL_ALT: ['#dcf0e6', '#b4e0cd', '#83cbaf', '#4fb08e', '#2c8e6d', '#186a50', '#0c4633'],
  DIVERGING: ['#14417d', '#2f6cb4', '#8db4e2', '#dedcd6', '#e39898', '#c04a4a', '#8c1f1f'],
  SEMANTIC: {
    live: '#1d6fd0',
    good: '#0f7a55',
    warn: '#b57400',
    bad: '#c93b3b',
    muted: '#7d7c76',
  },
  SERIES: ['#1d6fd0', '#b57400', '#0f7a55', '#7a44b8', '#c93b3b', '#0e7490', '#a21caf'],
  // The light-surface heat ramp runs the other way in lightness for the same
  // reason the sequential one does — hot has to be the end furthest from paper.
  HEAT: [
    [0, 'rgba(255,209,102,0)'],
    [0.2, '#ffd166'],
    [0.4, '#f4a13c'],
    [0.6, '#e2662a'],
    [0.8, '#b81f3f'],
    [1, '#6b1046'],
  ],
  LAYER_COLOR: {
    green_spaces: '#0f7a55',
    cycling_network: '#b8451a',
    streetcars: '#c01b14',
    apartments: '#3b6fa8',
    subway_stations: '#111827',   // on paper the reference mark is ink, not white
    collisions: '#c0392b',
    municipalities: '#4a4a44',
    neighbourhoods: '#6b6a64',
    wards: '#7a6f24',
    development_applications: '#1d6fd0',
    development_pipeline: '#1d6fd0',
    affordable_housing: '#7a44b8',
    city_realty: '#a21caf',
    rental_buildings: '#0e7490',
  },
  CATEGORY_COLOR: {
    assault: '#c93b3b',
    auto_theft: '#b57400',
    break_enter: '#1d6fd0',
    robbery: '#7a44b8',
    theft_over: '#0f7a55',
  },
  NO_DATA: '#b9b7b0',
  SCORE: ['#c93b3b', '#b57400'],
  FATAL: '#7e1a12',
  POINT_DEFAULT: '#3b6fa8',
  LINE_DEFAULT: '#6b7280',
  HALO: '#ffffff',
  HAIRLINE: 'rgba(0,0,0,0.20)',
  LABEL: '#16202f',
}

/** The ramps and mark colours valid on a `base`-lit map surface. */
export function mapPalette(base: ThemeBase): MapPalette {
  return base === 'light' ? LIGHT : DARK
}

/**
 * Official TTC line colours, keyed by route number. These are the lines the
 * GTFS bundle actually ships — Line 3 (Scarborough RT) closed in 2023 and is
 * gone from the feed, so it is not a key here either. Network identity, so it
 * is the one table that does not vary with the surface.
 */
export const TTC_LINE_COLOR: Record<string, string> = {
  '1': '#F8C300', '2': '#00923F',
  '4': '#A21A68', '5': '#DF8600', '6': '#9E9E9E',
}

/** Line 1's yellow and Line 6's grey need dark type on the bullet to stay legible. */
export const TTC_LINE_NEEDS_DARK_TYPE = ['#F8C300', '#9E9E9E']

/**
 * Build a MapLibre `step` expression from class breaks.
 *
 * Breaks are quantiles, not equal intervals: incident counts are heavily
 * right-skewed, and equal intervals paint the whole city one colour.
 */
export function stepExpression(field: string, stops: number[], ramp: string[]): any[] {
  const colors = rampOf(stops.length, ramp)
  const expr: any[] = ['step', ['coalesce', ['get', field], -1e12], colors[0]]
  stops.slice(1).forEach((s, i) => expr.push(s, colors[i + 1]))
  return expr
}

/** Sample `n` evenly-spaced colours out of a ramp, always keeping both ends. */
export function rampOf(n: number, ramp: string[]): string[] {
  if (n <= 1) return [ramp[ramp.length - 1]]
  return Array.from({ length: n }, (_, i) =>
    ramp[Math.round((i / (n - 1)) * (ramp.length - 1))])
}

/**
 * Colour for a *categorical* field, from a spec's `classes` map.
 *
 * The spec says what each value means (`{"Refused": "bad"}`) and the surface
 * says what that looks like. Values the spec didn't name fall through to the
 * layer's own hue rather than to an arbitrary colour, so an unexpected status
 * reads as "some other kind" instead of as a category of its own.
 */
export function classExpression(
  field: string,
  classes: Record<string, SemanticClass>,
  pal: MapPalette,
  fallback: string,
): any[] {
  const expr: any[] = ['match', ['coalesce', ['get', field], '']]
  for (const [value, cls] of Object.entries(classes)) {
    expr.push(value, pal.SEMANTIC[cls] ?? fallback)
  }
  expr.push(fallback)
  return expr
}

/**
 * Radius that scales with √value, so a building with twice the units draws
 * twice the ink rather than four times it. `norm` is the value that should
 * read as a typical mark.
 */
export function sizeExpression(field: string, norm: number, scale = 1): any[] {
  const at = (z: number, k: number): any[] =>
    ['*', k * scale, ['sqrt', ['/', ['max', ['coalesce', ['get', field], 1], 1], Math.max(norm, 1)]]]
  return ['interpolate', ['linear'], ['zoom'], 10, at(10, 1.6), 14, at(14, 4.4), 17, at(17, 9)]
}

/** Stable per-layer colour for anything nobody styled — hashed from the id. */
export function seriesColor(id: string, pal: MapPalette): string {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0
  return pal.SERIES[Math.abs(h) % pal.SERIES.length]
}

/** Colour for a diverging metric, centred on zero. */
export function divergingExpression(field: string, max: number, ramp: string[]): any[] {
  const m = Math.max(Math.abs(max), 1)
  const cuts = [-m * 0.6, -m * 0.25, -m * 0.05, m * 0.05, m * 0.25, m * 0.6]
  const expr: any[] = ['step', ['get', field], ramp[0]]
  cuts.forEach((c, i) => expr.push(c, ramp[i + 1]))
  return expr
}
