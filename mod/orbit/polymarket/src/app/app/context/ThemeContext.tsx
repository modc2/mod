"use client";

// Theme switcher. Persists the chosen theme id in localStorage and stamps two
// attributes on <html>: `data-theme` (the palette) and `data-base` (dark or
// light). globals.css keys palettes on data-theme and all the generic
// light-mode legibility rules on data-base, so a new light theme inherits
// those for free. On first paint a tiny inline script (ThemeBoot) applies the
// stored value synchronously, avoiding a flash of the default palette.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

// The single source of truth for the theme set: the picker renders it, the
// boot script embeds the ids, and every entry needs a matching
// `[data-theme="id"]` token block in globals.css. `swatch` is [surface,
// accent, signal] — three dots sampled from the theme's own palette.
export const THEMES = [
  { id: "dark",   label: "MIDNIGHT", glyph: "☾",  base: "dark",  swatch: ["#0f0f14", "#34d399", "#7dd3fc"] },
  { id: "light",  label: "DAYLIGHT", glyph: "☀",  base: "light", swatch: ["#ffffff", "#15803d", "#2563eb"] },
  { id: "matrix", label: "MATRIX",   glyph: "▚",  base: "dark",  swatch: ["#010502", "#00ff7f", "#4defc9"] },
  { id: "neon",   label: "NEON",     glyph: "◢",  base: "dark",  swatch: ["#0a0416", "#ff2da0", "#0ff0d4"] },
  { id: "ember",  label: "EMBER",    glyph: "◉",  base: "dark",  swatch: ["#0c0603", "#ff9e2c", "#ffd75e"] },
  { id: "abyss",  label: "ABYSS",    glyph: "≋",  base: "dark",  swatch: ["#030b18", "#38bdf8", "#2dd4bf"] },
  { id: "warp",   label: "WARP",     glyph: "▦",  base: "dark",  swatch: ["#050712", "#2fb1f0", "#fbd000"] },
  { id: "paper",  label: "PAPER",    glyph: "▤",  base: "light", swatch: ["#f7f2e9", "#9a5b2c", "#15803d"] },
  { id: "win95",  label: "WIN95",    glyph: "▣",  base: "light", swatch: ["#c0c0c0", "#000080", "#008000"] },
  // Geometric glyphs only — an emoji mushroom here renders as tofu on any
  // machine without an emoji font, and the swatch already says MARIO.
  { id: "mario",  label: "MARIO",    glyph: "◈",  base: "light", swatch: ["#5c94fc", "#e52521", "#fbd000"] },
] as const;

export type Theme = (typeof THEMES)[number]["id"];
export type ThemeBase = "dark" | "light";

const DEFAULT_THEME: Theme = "dark";
const STORAGE_KEY = "poly_theme";

export const themeBase = (id: string): ThemeBase =>
  (THEMES.find((t) => t.id === id)?.base ?? "dark") as ThemeBase;

interface ThemeContextValue {
  theme: Theme;
  /** dark|light classification of the active theme — what chart/color code
   *  should branch on instead of `theme === "light"`. */
  base: ThemeBase;
  toggle: () => void;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: DEFAULT_THEME,
  base: "dark",
  toggle: () => {},
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

function applyTheme(t: Theme) {
  if (typeof document === "undefined") return;
  const el = document.documentElement;
  el.setAttribute("data-theme", t);
  el.setAttribute("data-base", themeBase(t));
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Always start on the default theme for the server render so SSR markup
  // matches the initial client render. ThemeBoot has already stamped the real
  // attributes in <head>, so users never see a flash even though React
  // hydrates with the default first.
  const [theme, setThemeState] = useState<Theme>(DEFAULT_THEME);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
      if (stored && THEMES.some((t) => t.id === stored)) {
        setThemeState(stored);
        applyTheme(stored);
      }
    } catch {}
  }, []);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    applyTheme(t);
    try { localStorage.setItem(STORAGE_KEY, t); } catch {}
  }, []);

  // Kept for keyboard/legacy callers: flips to the opposite base, landing on
  // the plain DAYLIGHT/MIDNIGHT pair rather than guessing a counterpart for
  // MATRIX or MARIO.
  const toggle = useCallback(() => {
    setTheme(themeBase(theme) === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return (
    <ThemeContext.Provider value={{ theme, base: themeBase(theme), toggle, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

// Inline script that runs before React hydrates. Without this, the page paints
// the default palette for one frame on every load for everyone else.
export function ThemeBoot() {
  const ids = JSON.stringify(THEMES.map((t) => t.id));
  const lightIds = JSON.stringify(THEMES.filter((t) => t.base === "light").map((t) => t.id));
  const code =
    `try{var t=localStorage.getItem(${JSON.stringify(STORAGE_KEY)}),A=${ids},L=${lightIds};` +
    `if(t&&A.indexOf(t)>=0){var d=document.documentElement;` +
    `d.setAttribute("data-theme",t);d.setAttribute("data-base",L.indexOf(t)>=0?"light":"dark")}}catch(e){}`;
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}
