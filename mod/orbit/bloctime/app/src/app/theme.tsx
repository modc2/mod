"use client";

// Skin switcher. Every skin is a full token block in globals.css keyed on
// `[data-theme="<id>"]`; this stamps that attribute on <html> along with
// `data-base`, which tells the light-field rules they apply — they key on
// the base, not on any one id, so a new light skin needs no CSS beyond its
// tokens. The choice persists in localStorage and ThemeBoot replays it
// before first paint, so there's no flash of the wrong console.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

// The list the picker renders and the boot script validates against; order
// is menu order. `chips` are three colours sampled from the skin's own
// tokens (field, accent, second voice) — the swatch you read before
// switching. `note` is the one line that says what the cabinet is for.
export const THEMES = [
  { id: 'midnight',  label: 'Midnight',  base: 'dark',  note: 'deep indigo, cyan instrument', chips: ['#090a12', '#38e0ff', '#a78bfa'] },
  { id: 'slate',     label: 'Slate',     base: 'dark',  note: 'graphite, no hue in the field', chips: ['#0e1115', '#4f9dff', '#3dd68c'] },
  { id: 'vault',     label: 'Vault',     base: 'dark',  note: 'black felt and one metal',      chips: ['#0c0a07', '#e8b53c', '#8fc757'] },
  { id: 'terminal',  label: 'Terminal',  base: 'dark',  note: 'phosphor tube, scanlines on',   chips: ['#020b06', '#3dff94', '#d2ff6a'] },
  { id: 'amber',     label: 'Amber',     base: 'dark',  note: 'gas plasma, 1983',              chips: ['#0d0802', '#ffba40', '#bae24a'] },
  { id: 'neon',      label: 'Neon',      base: 'dark',  note: 'magenta and cyan, loud',        chips: ['#10031a', '#2de9ff', '#ff3dbe'] },
  { id: 'blueprint', label: 'Blueprint', base: 'dark',  note: 'cyanotype, square linework',    chips: ['#081a38', '#7ad6ff', '#ffce7a'] },
  { id: 'pixel',     label: 'Pixel',     base: 'dark',  note: '8-bit, bitmap type, no radius', chips: ['#0a0614', '#22f0ff', '#2bff88'] },
  { id: 'paper',     label: 'Paper',     base: 'light', note: 'ledger stock, printed ink',     chips: ['#f6f4ee', '#2052be', '#ad7a10'] },
  { id: 'mint',      label: 'Mint',      base: 'light', note: 'clinic white, teal instrument', chips: ['#f0f6f3', '#00848c', '#0e8a54'] },
  { id: 'solar',     label: 'Solar',     base: 'light', note: 'cream stock, hot orange',       chips: ['#fdf6e8', '#c8540c', '#823ca8'] },
] as const

export type Theme = (typeof THEMES)[number]['id']

export const DEFAULT_THEME: Theme = 'midnight'
const STORAGE_KEY = 'bloctime_theme'

const baseOf = (id: string): 'dark' | 'light' =>
  THEMES.find(t => t.id === id)?.base ?? 'dark'

interface ThemeContextValue {
  theme: Theme
  setTheme: (t: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: DEFAULT_THEME,
  setTheme: () => {},
})

export const useTheme = () => useContext(ThemeContext)

function applyTheme(t: Theme) {
  if (typeof document === 'undefined') return
  const el = document.documentElement
  el.setAttribute('data-theme', t)
  el.setAttribute('data-base', baseOf(t))
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Start on the default so the server render matches the first client
  // render. ThemeBoot has already stamped the real skin on <html> before
  // paint, so nothing flashes even though React hydrates holding the default.
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

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

// Inline script that runs before React hydrates. Without it the page paints
// the default skin for one frame on every load. The id lists are baked into
// the string rather than imported — this has to be self-contained.
export function ThemeBoot() {
  const ids = THEMES.map(t => t.id).join(',')
  const lights = THEMES.filter(t => t.base === 'light').map(t => t.id).join(',')
  const code =
    `try{var t=localStorage.getItem("${STORAGE_KEY}"),A="${ids}".split(","),` +
    `L="${lights}".split(",");if(t&&A.indexOf(t)>=0){var d=document.documentElement;` +
    `d.setAttribute("data-theme",t);d.setAttribute("data-base",L.indexOf(t)>=0?"light":"dark")}}catch(e){}`
  return <script dangerouslySetInnerHTML={{ __html: code }} />
}

// ── Chart colours ───────────────────────────────────────────────────────
// SVG paints through `stroke`/`fill` *attributes*, where `var(--accent)`
// isn't honoured. Charts read the skin's palette through this hook instead:
// it resolves the tokens off <html> and re-resolves whenever the skin
// changes. The literals below are MIDNIGHT's — the first frame only.

export interface ThemeColors {
  accent: string
  up: string
  gold: string
  iris: string
  down: string
  ink: string
  mute: string
  faint: string
  line: string
  panel: string
}

const MIDNIGHT: ThemeColors = {
  accent: 'rgb(56 224 255)',
  up:     'rgb(52 227 155)',
  gold:   'rgb(247 201 72)',
  iris:   'rgb(167 139 250)',
  down:   'rgb(255 92 114)',
  ink:    'rgb(233 237 250)',
  mute:   'rgb(126 135 162)',
  faint:  'rgb(86 94 118)',
  line:   'rgb(56 62 88)',
  panel:  'rgb(16 18 28)',
}

const VARS: Record<keyof ThemeColors, string> = {
  accent: '--accent-rgb',
  up: '--up-rgb',
  gold: '--gold-rgb',
  iris: '--iris-rgb',
  down: '--down-rgb',
  ink: '--ink-rgb',
  mute: '--mute-rgb',
  faint: '--faint-rgb',
  line: '--line-rgb',
  panel: '--panel-rgb',
}

export function useThemeColors(): ThemeColors {
  const { theme } = useTheme()
  const [colors, setColors] = useState<ThemeColors>(MIDNIGHT)

  useEffect(() => {
    const cs = getComputedStyle(document.documentElement)
    const next = { ...MIDNIGHT }
    ;(Object.keys(VARS) as (keyof ThemeColors)[]).forEach(k => {
      const v = cs.getPropertyValue(VARS[k]).trim()
      if (v) next[k] = `rgb(${v})`
    })
    setColors(next)
  }, [theme])

  return colors
}

// Same channels, but as `r g b` so a caller can mix its own alpha:
// `rgb(${ch.accent} / 0.2)`.
export function useThemeChannels(): ThemeColors {
  const colors = useThemeColors()
  const out = { ...colors }
  ;(Object.keys(out) as (keyof ThemeColors)[]).forEach(k => {
    out[k] = out[k].replace(/^rgb\(|\)$/g, '')
  })
  return out
}
