"use client";

// Skin switcher — the same cabinet copytensor wears, on a different board.
// Every skin is a full token block in globals.css keyed on
// `[data-theme="<id>"]`; this stamps that attribute on <html> along with
// `data-base`, which tells the generic light-field rules (grille, glow, input
// bevel) that they apply — they key on the base, not on any one id. The
// choice persists in localStorage and a tiny inline script (ThemeBoot) replays
// it before first paint, so there's no flash of the wrong cabinet.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

// Menu order: the two house skins first, then the cabinets. `chips` are three
// colours sampled from the skin's own palette (field, accent, gain) — the
// swatch you read before switching.
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
  { id: "dinn",     label: "DINN",     base: "light", chips: ["#ffffff", "#000000", "#7f7f7f"] },
] as const;

export type Theme = (typeof THEMES)[number]["id"];

const DEFAULT_THEME: Theme = "dark";
// Every mod on this host shares one localStorage origin, so the key is
// namespaced rather than "theme".
const STORAGE_KEY = "lq_theme";

const baseOf = (id: string): "dark" | "light" =>
  THEMES.find((t) => t.id === id)?.base ?? "dark";

const ThemeContext = createContext<{ theme: Theme; setTheme: (t: Theme) => void }>({
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
  // The server render always uses the default so SSR markup matches the first
  // client render; ThemeBoot has already stamped the real skin on <html>
  // before paint, so nothing flashes even though React hydrates on default.
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

// Runs before React hydrates. Without it the page paints the default cabinet
// for one frame on every load. The id lists are baked into the string rather
// than imported — this has to be self-contained.
export function ThemeBoot() {
  const ids = THEMES.map((t) => t.id).join(",");
  const lights = THEMES.filter((t) => t.base === "light").map((t) => t.id).join(",");
  const code =
    `try{var t=localStorage.getItem("${STORAGE_KEY}"),A="${ids}".split(","),` +
    `L="${lights}".split(",");if(t&&A.indexOf(t)>=0){var d=document.documentElement;` +
    `d.setAttribute("data-theme",t);d.setAttribute("data-base",L.indexOf(t)>=0?"light":"dark")}}catch(e){}`;
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}
