'use client'

import { useEffect, useRef, useState } from 'react'
import { THEMES } from '@/lib/theme'
import { useTheme } from './ThemeProvider'

/** The header's theme portal: current glyph, opening a menu of every theme. */
export default function ThemePicker() {
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={box} className="relative shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="theme-toggle"
        title={`Theme: ${theme.label}`}
        aria-label="Pick a theme"
        aria-expanded={open}
      >
        {theme.glyph}
      </button>

      {open && (
        <div className="theme-menu panel-solid" role="menu" aria-label="Themes">
          {THEMES.map((t) => (
            <button
              key={t.id}
              role="menuitemradio"
              aria-checked={t.id === theme.id}
              className="theme-menu__item"
              onClick={() => {
                setTheme(t.id)
                setOpen(false)
              }}
            >
              <span className="theme-menu__glyph">{t.glyph}</span>
              <span>{t.label}</span>
              <span className="theme-menu__swatch">
                {t.swatch.map((c) => (
                  <span key={c} className="theme-menu__dot" style={{ background: c }} />
                ))}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
