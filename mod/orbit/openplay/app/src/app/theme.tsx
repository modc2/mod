"use client";

// ── World select ──────────────────────────────────────────────────
// Every world is a full token block in globals.css keyed on
// `[data-theme="<id>"]`; this stamps that attribute on <html> along with
// `data-base`, which tells the generic light-field rules that they apply —
// they key on the base, not on any one id, so a new light world needs no
// new CSS beyond its tokens. The choice persists in localStorage and a tiny
// inline script (ThemeBoot) replays it before first paint, so you never see
// the wrong world for a frame.

import {
  createContext, useCallback, useContext, useEffect, useState, type ReactNode,
} from 'react'

// Order is the menu order. `chips` are three colours sampled from the
// world's own palette (field, brick, coin) — the swatch you read before
// switching. `tiles` picks the basemap that world's map is drawn on.
export const THEMES = [
  { id: 'overworld',   label: 'OVERWORLD',  world: '1-1', base: 'light', tiles: 'voyager', chips: ['#5c94fc', '#c84c0c', '#fcbc3c'] },
  { id: 'underground', label: 'UNDERGROUND', world: '1-2', base: 'dark',  tiles: 'dark',    chips: ['#000000', '#0080a8', '#fcbc3c'] },
  { id: 'underwater',  label: 'UNDERWATER', world: '2-2', base: 'dark',  tiles: 'dark',    chips: ['#2038ec', '#00a8a8', '#6cfcfc'] },
  { id: 'castle',      label: 'CASTLE',     world: '8-4', base: 'dark',  tiles: 'dark',    chips: ['#0c0808', '#9c8c84', '#f83800'] },
  { id: 'night',       label: 'NIGHT RUN',  world: '3-1', base: 'dark',  tiles: 'dark',    chips: ['#0b1030', '#6c4c9c', '#fcbc3c'] },
  { id: 'snow',        label: 'ICE WORLD',  world: '6-1', base: 'light', tiles: 'light',   chips: ['#cfe8fc', '#4c8cc8', '#1878b8'] },
  { id: 'desert',      label: 'DESERT',     world: '2-1', base: 'light', tiles: 'light',   chips: ['#f8d878', '#c86c1c', '#38200c'] },
  { id: 'star',        label: 'STAR POWER', world: '★',   base: 'dark',  tiles: 'dark',    chips: ['#200030', '#a018a0', '#fce000'] },
  { id: 'luigi',       label: 'LUIGI',      world: 'P2',  base: 'dark',  tiles: 'dark',    chips: ['#0c3018', '#2c8c3c', '#58f858'] },
  { id: 'dmg',         label: 'GAME BOY',   world: 'GB',  base: 'light', tiles: 'light',   chips: ['#8bac0f', '#306230', '#0f380f'] },
  { id: 'paper',       label: 'MANUAL',     world: 'DOC', base: 'light', tiles: 'light',   chips: ['#efe7d5', '#b0511a', '#17557f'] },
] as const

export type Theme = (typeof THEMES)[number]['id']

const DEFAULT_THEME: Theme = 'overworld'
const STORAGE_KEY = 'openplay.world'

const themeOf = (id: string) => THEMES.find(t => t.id === id)
const baseOf = (id: string): 'dark' | 'light' => themeOf(id)?.base ?? 'light'

// Carto basemaps, matched to the world so a dark level isn't lit by a
// white city and an ice world isn't drawn on soot.
const TILE_URLS: Record<string, string> = {
  dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  light: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
  voyager: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
}
export function tileUrlFor(theme: string): string {
  return TILE_URLS[themeOf(theme)?.tiles ?? 'voyager'] || TILE_URLS.voyager
}

interface ThemeContextValue { theme: Theme; setTheme: (t: Theme) => void }
const ThemeContext = createContext<ThemeContextValue>({ theme: DEFAULT_THEME, setTheme: () => {} })
export function useTheme() { return useContext(ThemeContext) }

function applyTheme(t: Theme) {
  if (typeof document === 'undefined') return
  const el = document.documentElement
  el.setAttribute('data-theme', t)
  el.setAttribute('data-base', baseOf(t))
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Start on the default for the server render so SSR markup matches the
  // first client render. ThemeBoot has already stamped the real world on
  // <html> before paint, so nothing flashes even though React hydrates
  // holding the default.
  const [theme, setThemeState] = useState<Theme>(DEFAULT_THEME)

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored && THEMES.some(t => t.id === stored)) {
        setThemeState(stored as Theme)
        applyTheme(stored as Theme)
      }
    } catch {}
  }, [])

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t)
    applyTheme(t)
    try { localStorage.setItem(STORAGE_KEY, t) } catch {}
  }, [])

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>
}

// Inline script that runs before React hydrates. Without it the page paints
// world 1-1 for one frame on every load. The id lists are baked into the
// string rather than imported — this has to be self-contained.
export function ThemeBoot() {
  const ids = THEMES.map(t => t.id).join(',')
  const darks = THEMES.filter(t => t.base === 'dark').map(t => t.id).join(',')
  const code =
    `try{var t=localStorage.getItem("${STORAGE_KEY}"),A="${ids}".split(","),` +
    `D="${darks}".split(",");if(t&&A.indexOf(t)>=0){var d=document.documentElement;` +
    `d.setAttribute("data-theme",t);d.setAttribute("data-base",D.indexOf(t)>=0?"dark":"light")}}catch(e){}`
  return <script dangerouslySetInnerHTML={{ __html: code }} />
}
