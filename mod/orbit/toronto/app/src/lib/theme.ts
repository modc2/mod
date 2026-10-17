/**
 * The theme registry.
 *
 * Every entry has a matching `[data-theme="id"]` token block in globals.css.
 * `base` classifies the theme dark or light: it lands on the document as
 * `data-base`, which drives the generic light/dark rules (MapLibre's chrome,
 * `color-scheme`) and — the part unique to a map — which *map* palette the
 * ramps come from. A sequential ramp has to run away from its surface, so a
 * light-base theme can't reuse the dark ramp and stay readable.
 *
 * `basemap` is the tile set the theme wants under it. It is applied on every
 * theme change and can then be overridden by hand; the next theme change wins
 * again, which keeps "pick a theme" a single, complete gesture.
 *
 * `swatch` = [surface, accent, signal], sampled from the theme's own tokens.
 */

export type ThemeBase = 'dark' | 'light'
export type BasemapId = 'dark' | 'light' | 'streets'

export type Theme = {
  id: string
  label: string
  glyph: string
  base: ThemeBase
  basemap: BasemapId
  swatch: [string, string, string]
}

export const THEMES = [
  { id: 'dark',     label: 'GLASS',     glyph: '◆', base: 'dark',  basemap: 'dark',    swatch: ['#0b0e14', '#3987e5', '#9ec5f4'] },
  { id: 'day',      label: 'DAYLIGHT',  glyph: '☀', base: 'light', basemap: 'light',   swatch: ['#eef1f6', '#1d6fd0', '#0a7d32'] },
  { id: 'paper',    label: 'PAPER',     glyph: '▤', base: 'light', basemap: 'light',   swatch: ['#ece3d2', '#9a5b2c', '#3f6b23'] },
  { id: 'ttc',      label: 'TTC',       glyph: '⬤', base: 'dark',  basemap: 'dark',    swatch: ['#08090c', '#DA251D', '#F8C300'] },
  { id: 'matrix',   label: 'MATRIX',    glyph: '▚', base: 'dark',  basemap: 'dark',    swatch: ['#010502', '#00ff7f', '#4defc9'] },
  { id: 'neon',     label: 'NEON',      glyph: '◢', base: 'dark',  basemap: 'dark',    swatch: ['#0a0416', '#ff2da0', '#0ff0d4'] },
  { id: 'ember',    label: 'EMBER',     glyph: '◉', base: 'dark',  basemap: 'dark',    swatch: ['#0c0603', '#ff9e2c', '#ffd75e'] },
  { id: 'abyss',    label: 'ABYSS',     glyph: '≋', base: 'dark',  basemap: 'dark',    swatch: ['#030b18', '#38bdf8', '#2dd4bf'] },
  { id: 'win95',    label: 'WIN95',     glyph: '▣', base: 'light', basemap: 'streets', swatch: ['#c0c0c0', '#000080', '#008000'] },
  { id: 'contrast', label: 'HI-CON',    glyph: '◐', base: 'light', basemap: 'light',   swatch: ['#ffffff', '#0b4fd8', '#a80000'] },
] as const satisfies readonly Theme[]

export type ThemeId = (typeof THEMES)[number]['id']

export const DEFAULT_THEME: ThemeId = 'dark'

/** localStorage key. The origin is shared with every other mod, so it's namespaced. */
export const THEME_KEY = 'tdot_theme'

export const THEME_IDS: readonly string[] = THEMES.map((t) => t.id)

export function themeOf(id: string): Theme {
  return THEMES.find((t) => t.id === id) ?? THEMES[0]
}

export function themeBase(id: string): ThemeBase {
  return themeOf(id).base
}
