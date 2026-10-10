"use client";

// Skin switcher. Every skin is a full token block in globals.css keyed on
// `[data-theme="<id>"]`; this stamps that attribute on <html> along with
// `data-base`, which tells the generic light-field rules (grille, glow,
// input bevel, weight bump) that they apply — they key on the base, not on
// any one id, so a new light skin needs no new CSS beyond its tokens.
// The choice persists in localStorage and a tiny inline script (ThemeBoot)
// replays it before first paint, so there's no flash of the wrong cabinet.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

// The list the picker renders and the boot script validates against. Order
// is the menu order: the two house skins first, then the cabinets.
// `chips` are three colours sampled from the skin's own palette
// (field, accent, gain) — the swatch you read before switching.
export const THEMES = [
  { id: "dark",     label: "ARCADE",   base: "dark",  chips: ["#0a0614", "#22f0ff", "#2bff88"] },
  { id: "light",    label: "FLYER",    base: "light", chips: ["#e8e3f5", "#6b2fd6", "#067a41"] },
  { id: "manual",   label: "MANUAL",   base: "light", chips: ["#efe7d5", "#17557f", "#1c7a35"] },
  { id: "gameboy",  label: "GAMEBOY",  base: "light", chips: ["#8bac0f", "#0f380f", "#0f4d14"] },
  { id: "phosphor", label: "PHOSPHOR", base: "dark",  chips: ["#010a04", "#12f0a8", "#3dff7a"] },
  { id: "amber",    label: "AMBER",    base: "dark",  chips: ["#0d0700", "#ffcf6b", "#a8e02c"] },
  { id: "c64",      label: "C64",      base: "dark",  chips: ["#322a8e", "#aaffee", "#aaff66"] },
  { id: "miami",    label: "MIAMI",    base: "dark",  chips: ["#16030f", "#ff2d95", "#2bff9e"] },
  { id: "vector",   label: "VECTOR",   base: "dark",  chips: ["#000000", "#33ffff", "#33ff66"] },
  // DINN has no third colour to sample — its chips are the whole palette:
  // paper, ink, and the grey a 50% dither averages to.
  { id: "dinn",     label: "DINN",     base: "light", chips: ["#ffffff", "#000000", "#7f7f7f"] },
] as const;

export type Theme = (typeof THEMES)[number]["id"];

const DEFAULT_THEME: Theme = "dark";
const STORAGE_KEY = "ct_theme";

const baseOf = (id: string): "dark" | "light" =>
  THEMES.find((t) => t.id === id)?.base ?? "dark";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: DEFAULT_THEME,
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

function applyTheme(t: Theme) {
  if (typeof document === "undefined") return;
  const el = document.documentElement;
  el.setAttribute("data-theme", t);
  el.setAttribute("data-base", baseOf(t));
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Always start on the default for the server render so SSR markup matches
  // the first client render. ThemeBoot has already stamped the real skin on
  // <html> before paint, so nothing flashes even though React hydrates here
  // holding the default.
  const [theme, setThemeState] = useState<Theme>(DEFAULT_THEME);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored && THEMES.some((t) => t.id === stored)) {
        setThemeState(stored as Theme);
        applyTheme(stored as Theme);
      }
    } catch {}
  }, []);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    applyTheme(t);
    try { localStorage.setItem(STORAGE_KEY, t); } catch {}
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

// Inline script that runs before React hydrates. Without it the page paints
// the default cabinet for one frame on every load. The id lists are baked
// into the string rather than imported — this has to be self-contained.
export function ThemeBoot() {
  const ids = THEMES.map((t) => t.id).join(",");
  const lights = THEMES.filter((t) => t.base === "light").map((t) => t.id).join(",");
  const code =
    `try{var t=localStorage.getItem("${STORAGE_KEY}"),A="${ids}".split(","),` +
    `L="${lights}".split(",");if(t&&A.indexOf(t)>=0){var d=document.documentElement;` +
    `d.setAttribute("data-theme",t);d.setAttribute("data-base",L.indexOf(t)>=0?"light":"dark")}}catch(e){}`;
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}

// ── Chart colours ───────────────────────────────────────────────────────
// Recharts and our own SVGs paint through `stroke`/`fill` *attributes*,
// where `var(--neon-lime)` isn't reliably honoured. So charts read the
// skin's palette through this hook instead: it resolves the tokens off
// <html> and re-resolves whenever the skin changes. The literals below are
// ARCADE's, used for the server render and the first frame only.
export interface ThemeColors {
  lime: string;
  red: string;
  cyan: string;
  magenta: string;
  amber: string;
  fg: string;
  fgDim: string;
  panel: string;
  border: string;
}

const ARCADE_COLORS: ThemeColors = {
  lime: "#2bff88",
  red: "#ff3355",
  cyan: "#22f0ff",
  magenta: "#ff2fb9",
  amber: "#ffcf28",
  fg: "#f4f0ff",
  fgDim: "#6f6392",
  panel: "#17102b",
  border: "#56399a",
};

const COLOR_VARS: Record<keyof ThemeColors, string> = {
  lime: "--neon-lime",
  red: "--neon-red",
  cyan: "--neon-cyan",
  magenta: "--neon-magenta",
  amber: "--neon-amber",
  fg: "--fg",
  fgDim: "--fg-dim",
  panel: "--panel-from",
  border: "--border-strong",
};

export function useThemeColors(): ThemeColors {
  const { theme } = useTheme();
  const [colors, setColors] = useState<ThemeColors>(ARCADE_COLORS);

  useEffect(() => {
    const cs = getComputedStyle(document.documentElement);
    const next = { ...ARCADE_COLORS };
    (Object.keys(COLOR_VARS) as (keyof ThemeColors)[]).forEach((k) => {
      const v = cs.getPropertyValue(COLOR_VARS[k]).trim();
      if (v) next[k] = v;
    });
    setColors(next);
  }, [theme]);

  return colors;
}
