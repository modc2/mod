'use client'

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react'
import {
  DEFAULT_THEME, THEMES, THEME_IDS, THEME_KEY, themeOf,
  type Theme, type ThemeBase,
} from '@/lib/theme'
import { mapPalette, type MapPalette } from '@/lib/palette'

type Ctx = {
  theme: Theme
  base: ThemeBase
  palette: MapPalette
  setTheme: (id: string) => void
}

const ThemeCtx = createContext<Ctx | null>(null)

/**
 * Owns the active theme.
 *
 * The document already carries `data-theme`/`data-base` before React boots —
 * layout.tsx server-renders the default and a blocking script corrects it from
 * localStorage, so the first paint is already the final palette. This provider
 * mirrors that into React state (so the picker and the map palette agree) and
 * writes both back on every change.
 */
export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [id, setId] = useState<string>(DEFAULT_THEME)

  // Read once on mount rather than in useState's initialiser: the server render
  // has no localStorage, and disagreeing with it would be a hydration mismatch.
  useEffect(() => {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved && THEME_IDS.includes(saved)) setId(saved)
  }, [])

  const setTheme = useCallback((next: string) => {
    setId(next)
    const t = themeOf(next)
    const d = document.documentElement
    d.setAttribute('data-theme', t.id)
    d.setAttribute('data-base', t.base)
    try {
      localStorage.setItem(THEME_KEY, t.id)
    } catch {
      // A full quota on the shared origin must not cost the user the theme
      // they just picked — it just won't survive a reload.
    }
  }, [])

  // Keep the DOM in step with the value read back from storage on mount.
  useEffect(() => {
    const t = themeOf(id)
    document.documentElement.setAttribute('data-theme', t.id)
    document.documentElement.setAttribute('data-base', t.base)
  }, [id])

  const value = useMemo<Ctx>(() => {
    const theme = themeOf(id)
    return { theme, base: theme.base, palette: mapPalette(theme.base), setTheme }
  }, [id, setTheme])

  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>
}

export function useTheme(): Ctx {
  const ctx = useContext(ThemeCtx)
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>')
  return ctx
}

/** The map ramps and mark colours valid on the current theme's surface. */
export function usePalette(): MapPalette {
  return useTheme().palette
}

export { THEMES }
