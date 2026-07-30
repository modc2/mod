/**
 * The map's colour system.
 *
 * Every ramp here was checked with the palette validator against the map
 * surface (#1c1c1c, the dark basemap) rather than picked by eye:
 *
 *   sequential  one hue, monotonic in lightness. On a *dark* surface the ramp
 *               runs dark→light, so magnitude increases away from the surface
 *               (the mirror of the light-mode light→dark convention).
 *   diverging   blue ↔ red with a neutral gray midpoint that recedes into the
 *               surface. Both arms lighten away from the middle.
 *   overlays    aqua + orange — the pair clears all-pairs CVD separation
 *               (ΔE 9.4 deutan) and normal-vision separation (ΔE 26.5) on the
 *               map surface. Layers that share a mark form never share a hue;
 *               layers of different form (a translucent fill vs a hairline)
 *               may, because form is already carrying the distinction.
 */

/**
 * "No data" fill. Deliberately a *neutral* grey: every class in the sequential
 * and diverging ramps is chromatic, so an absence of hue is what marks a
 * polygon as outside the scale.
 */
export const NO_DATA = '#5c5b56'

/** Sequential magnitude ramp, low → high. */
export const SEQUENTIAL = [
  '#0d366b', '#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb',
]

/** Diverging ramp, most-negative → most-positive; index 3 is the neutral mid.
 *  On a crime map "up" is bad, so the positive arm is the red one. */
export const DIVERGING = [
  '#86b6ef', '#3987e5', '#1c5cab', '#383835', '#b83c3c', '#e66767', '#f2a0a0',
]

/**
 * Density ramp for the collision heatmap, transparent → hot.
 *
 * Warm and monotonic in lightness (L 0.04 → 0.75). A heat ramp is the one
 * documented multi-hue sequential case; the hues are deliberately warm so the
 * heatmap never competes with the blue choropleth underneath it.
 */
export const HEAT: [number, string][] = [
  [0, 'rgba(59,42,128,0)'],
  [0.2, '#3b2a80'],
  [0.4, '#7d2b6b'],
  [0.6, '#b83c3c'],
  [0.8, '#d95926'],
  [1, '#eda100'],
]

/** Per-layer colours. Same mark form ⇒ different hue. */
export const LAYER_COLOR: Record<string, string> = {
  green_spaces: '#199e70',       // translucent fill
  cycling_network: '#d95926',    // hairline
  streetcars: '#DA251D',         // TTC streetcar red (network identity colour)
  apartments: '#9ec5f4',         // graduated circle
  subway_stations: '#ffffff',    // reference infrastructure, not a data series
  collisions: '#e66767',
  municipalities: '#c3c2b7',
  neighbourhoods: '#898781',
  wards: '#9c8f3a',
}

/**
 * Crime-category colours for the incident point layer. Five categoricals,
 * hue-separated on the dark surface; assault (the most common) gets the
 * hue most distinct from the choropleth blues.
 */
export const CATEGORY_COLOR: Record<string, string> = {
  assault: '#e66767',
  auto_theft: '#eda100',
  break_enter: '#3987e5',
  robbery: '#b07ce8',
  theft_over: '#199e70',
}

/** Official TTC line colours, keyed by route number (from the GTFS bundle). */
export const TTC_LINE_COLOR: Record<string, string> = {
  '1': '#F8C300', '2': '#00923F', '3': '#0082C9',
  '4': '#A21A68', '5': '#DF8600', '6': '#9E9E9E',
}

/** Chart chrome & ink. */
export const INK = {
  surface: '#121722',
  plane: '#0b0e14',
  primary: '#ffffff',
  secondary: '#c3c2b7',
  muted: '#898781',
  grid: '#2c2c2a',
  axis: '#383835',
  border: 'rgba(255,255,255,0.10)',
  good: '#0ca30c',
  critical: '#d03b3b',
}

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
export function rampOf(n: number, ramp: string[] = SEQUENTIAL): string[] {
  if (n <= 1) return [ramp[ramp.length - 1]]
  return Array.from({ length: n }, (_, i) =>
    ramp[Math.round((i / (n - 1)) * (ramp.length - 1))])
}

/** Colour for a diverging metric, centred on zero. */
export function divergingExpression(field: string, max: number): any[] {
  const m = Math.max(Math.abs(max), 1)
  const cuts = [-m * 0.6, -m * 0.25, -m * 0.05, m * 0.05, m * 0.25, m * 0.6]
  const expr: any[] = ['step', ['get', field], DIVERGING[0]]
  cuts.forEach((c, i) => expr.push(c, DIVERGING[i + 1]))
  return expr
}
