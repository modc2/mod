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

/**
 * Per-theme magnitude ramps.
 *
 * A theme that only skins the panels leaves the *map* — the thing the console
 * exists to show — identical under all ten. So the theme reaches the choropleth
 * too, and this is the table that lets it.
 *
 * What is themed and what is not is a deliberate split:
 *
 *   themed      SEQUENTIAL / SEQUENTIAL_ALT — magnitude. "How much" carries no
 *               meaning in its hue, only in its position along the ramp, so the
 *               hue is free to be the theme's.
 *   not themed  DIVERGING (polarity: up-is-bad has to stay red), SEMANTIC
 *               (reserved status colours), CATEGORY_COLOR (identity — a crime
 *               category must not change colour when you change skin), and TTC
 *               line colours (network identity). Re-hueing those would make the
 *               theme change what the map *means*, not just how it looks.
 *
 * Each ramp was generated in OKLCH against the lightness/chroma schedule of the
 * two validated base ramps above, with chroma binary-searched to the sRGB gamut
 * boundary, then checked with the dataviz `validateOrdinal` gate: all twenty
 * pass single-hue, monotone lightness, and the ≥0.06 adjacent-ΔL step gap. They
 * sit at the same light-end contrast as the shipped baseline (1.47:1 vs 1.46:1)
 * — the pale end of a choropleth recedes into the basemap on purpose; that end
 * is "least", not a mark that has to stand alone.
 */
const THEME_RAMPS: Record<string, Pick<MapPalette, 'SEQUENTIAL' | 'SEQUENTIAL_ALT'>> = {
  // GLASS — hue 255° on the dark surface, alt at 45°.
  dark: {
    SEQUENTIAL: ['#07376b', '#135095', '#216abf', '#3987e5', '#70a6ec', '#9fc5f4', '#cee2fb'],
    SEQUENTIAL_ALT: ['#5e2200', '#853400', '#ae4701', '#d36022', '#e08b63', '#eeb297', '#f8d8ca'],
  },
  // DAYLIGHT — hue 256° on the light surface, alt at 46°.
  day: {
    SEQUENTIAL: ['#e3edfb', '#c3d9f6', '#99bdec', '#6c9ee0', '#3b79c8', '#19559e', '#0e3667'],
    SEQUENTIAL_ALT: ['#fae8e0', '#f2cebd', '#e5ab90', '#d5855e', '#b95925', '#8d3900', '#5c2300'],
  },
  // PAPER — hue 55° on the light surface, alt at 205°.
  paper: {
    SEQUENTIAL: ['#f9e9df', '#f0cfba', '#e2ad8a', '#d18855', '#b55d09', '#844200', '#562800'],
    SEQUENTIAL_ALT: ['#dcf1f3', '#b4e0e6', '#7bc9d2', '#21afbc', '#008894', '#00626b', '#003f45'],
  },
  // TTC — hue 29° on the dark surface, alt at 179°.
  ttc: {
    SEQUENTIAL: ['#621c15', '#892d24', '#b14034', '#d6594b', '#e48679', '#f0afa5', '#fad6d0'],
    SEQUENTIAL_ALT: ['#004238', '#005f51', '#007d6c', '#009d88', '#35bba5', '#87d3c2', '#c4e9e0'],
  },
  // MATRIX — hue 151° on the dark surface, alt at 301°.
  matrix: {
    SEQUENTIAL: ['#00441d', '#00622c', '#00813d', '#17a151', '#68b97d', '#9cd1a8', '#cde8d2'],
    SEQUENTIAL_ALT: ['#412763', '#5d3b8b', '#7b51b2', '#986bd7', '#b092e2', '#cab6ed', '#e4daf7'],
  },
  // NEON — hue 354° on the dark surface, alt at 144°.
  neon: {
    SEQUENTIAL: ['#5d1b3b', '#832b56', '#a93e71', '#cd578e', '#dd83a9', '#ebadc5', '#f7d5e1'],
    SEQUENTIAL_ALT: ['#08440d', '#14611a', '#248029', '#3d9e40', '#74b773', '#a3cfa1', '#d0e7cf'],
  },
  // EMBER — hue 64° on the dark surface, alt at 214°.
  ember: {
    SEQUENTIAL: ['#522d00', '#754300', '#995900', '#c07100', '#d7934f', '#e6b78c', '#f4dbc4'],
    SEQUENTIAL_ALT: ['#003f4a', '#005b6a', '#00788b', '#0097af', '#29b6d1', '#83cfe1', '#c2e7f0'],
  },
  // ABYSS — hue 233° on the dark surface, alt at 23°.
  abyss: {
    SEQUENTIAL: ['#003d55', '#005879', '#00749f', '#0092c7', '#49b0e1', '#8dcbec', '#c6e5f7'],
    SEQUENTIAL_ALT: ['#621a1d', '#892b2e', '#b13e40', '#d65758', '#e48581', '#f1aeaa', '#fad6d3'],
  },
  // WIN95 — hue 264° on the light surface, alt at 54°.
  win95: {
    SEQUENTIAL: ['#e5ecfc', '#c7d7f7', '#a1baed', '#789ae1', '#4d74ca', '#2f519f', '#1c3368'],
    SEQUENTIAL_ALT: ['#f9e9df', '#f1cfba', '#e2ad8b', '#d18855', '#b55d0d', '#854100', '#572800'],
  },
  // HI-CON — hue 262° on the light surface, alt at 52°.
  contrast: {
    SEQUENTIAL: ['#e4edfb', '#c6d8f7', '#9fbbed', '#769be1', '#4a75c9', '#2b529f', '#1a3467'],
    SEQUENTIAL_ALT: ['#f9e9df', '#f1cfbb', '#e3ac8c', '#d28757', '#b65c14', '#874000', '#582700'],
  },
}

/**
 * The ramps and mark colours valid on a `base`-lit map surface, with the
 * theme's own magnitude ramps swapped in.
 *
 * `theme` is optional so a caller that only knows the surface still gets a
 * correct palette — it just gets the house blue rather than the theme's hue.
 */
export function mapPalette(base: ThemeBase, theme?: string): MapPalette {
  const surface = base === 'light' ? LIGHT : DARK
  const ramps = theme ? THEME_RAMPS[theme] : undefined
  return ramps ? { ...surface, ...ramps } : surface
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
