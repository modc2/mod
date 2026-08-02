'use client'

import { useEffect, useRef, useState } from 'react'

/* The theme table. Each entry names a token block in globals.css (generated
 * by scripts/gen_theme_tokens.py) plus the two structural attributes the CSS
 * keys on: `skin` picks the chrome (8-bit vs modern) and `base` tells the
 * legibility floor whether the palette is dark or light. `swatch` is sampled
 * from that palette — surface, accent, second accent — so the picker shows
 * what you're about to get. Keep this list in step with THEMES in the
 * generator and with the id list in layout.tsx's blocking script. */
export const THEMES = [
  { id: 'arcade',   label: 'Arcade',   skin: 'pixel', base: 'dark',  swatch: ['#0a0a0a', '#10b981', '#f59e0b'] },
  { id: 'matrix',   label: 'Matrix',   skin: 'pixel', base: 'dark',  swatch: ['#010502', '#00e56f', '#2dd4bf'] },
  { id: 'win95',    label: 'Win95',    skin: 'pixel', base: 'light', swatch: ['#c0c0c0', '#000080', '#8a6d00'] },
  { id: 'midnight', label: 'Midnight', skin: 'soft',  base: 'dark',  swatch: ['#0b0b13', '#6366f1', '#a855f7'] },
  { id: 'abyss',    label: 'Abyss',    skin: 'soft',  base: 'dark',  swatch: ['#040d1a', '#0ea5e9', '#22d3ee'] },
  { id: 'ember',    label: 'Ember',    skin: 'soft',  base: 'dark',  swatch: ['#100a06', '#f97316', '#facc15'] },
  { id: 'neon',     label: 'Neon',     skin: 'soft',  base: 'dark',  swatch: ['#0c0518', '#ec4899', '#22d3ee'] },
  { id: 'paper',    label: 'Paper',    skin: 'soft',  base: 'light', swatch: ['#f7f2e8', '#15803d', '#b45309'] },
  { id: 'daylight', label: 'Daylight', skin: 'soft',  base: 'light', swatch: ['#f6f7fa', '#0d9488', '#2563eb'] },
] as const

export type ThemeId = (typeof THEMES)[number]['id']
export const DEFAULT_THEME: ThemeId = 'arcade'
const STORAGE_KEY = 'agent_theme'

const themeOf = (id: string) => THEMES.find(t => t.id === id)

export function applyTheme(id: ThemeId) {
  const t = themeOf(id) || themeOf(DEFAULT_THEME)!
  const el = document.documentElement
  el.setAttribute('data-theme', t.id)
  el.setAttribute('data-skin', t.skin)
  el.setAttribute('data-base', t.base)
}

/** Reads the saved theme once on mount and keeps <html> and storage in step.
 *  The first paint is already correct — layout.tsx's blocking script sets the
 *  same attributes before React runs — so this never flashes. */
export function useTheme(): [ThemeId, (id: ThemeId) => void] {
  const [theme, setTheme] = useState<ThemeId>(DEFAULT_THEME)

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved && themeOf(saved)) setTheme(saved as ThemeId)
    } catch {}
  }, [])

  useEffect(() => {
    applyTheme(theme)
    try { localStorage.setItem(STORAGE_KEY, theme) } catch {}
  }, [theme])

  return [theme, setTheme]
}

/** The header portal: a three-band swatch button that opens the full list. */
export function ThemePicker({ theme, onPick }: { theme: ThemeId; onPick: (id: ThemeId) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const current = themeOf(theme) || THEMES[0]

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen(o => !o)}
        className="theme-toggle"
        title={`Theme — ${current.label}`}
        aria-label="Pick a theme"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <span className="theme-toggle__slice">
          {current.swatch.map(c => <i key={c} style={{ background: c }} />)}
        </span>
      </button>
      {open && (
        <div className="theme-menu" role="menu" aria-label="Themes">
          {THEMES.map(t => (
            <button
              key={t.id}
              role="menuitemradio"
              aria-checked={t.id === theme}
              className="theme-menu__item"
              onClick={() => { onPick(t.id); setOpen(false) }}
            >
              <span>{t.label}</span>
              <span className="theme-menu__kind">{t.skin === 'pixel' ? '8-bit' : ''}</span>
              <span className="theme-menu__swatch">
                {t.swatch.map(c => <span key={c} className="theme-menu__dot" style={{ background: c }} />)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
