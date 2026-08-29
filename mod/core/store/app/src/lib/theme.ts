import { storageGet, storageSet } from "./safeStorage";

// Theme choice is shared with the pre-hydration script in layout.tsx, which
// reads the same key and applies the same two attributes so the first paint
// already matches. Keep THEME_KEY, THEMES ids and skinOf() in sync with it.
const THEME_KEY = "store:theme";

export type ThemeId =
  | "8bit-underground"
  | "8bit-overworld"
  | "crt-green"
  | "crt-amber"
  | "soft-midnight"
  | "soft-porcelain"
  | "neon-synthwave"
  | "print-broadsheet"
  | "blueprint-draft";

export type Theme = {
  id: ThemeId;
  name: string;
  blurb: string;
  mode: "dark" | "light";
  /** three colors for the picker swatch: surface, accent, hot */
  swatch: [string, string, string];
};

/** Every theme the store ships. Order is the order of the picker. */
export const THEMES: Theme[] = [
  {
    id: "8bit-underground",
    name: "Underground",
    blurb: "8-bit console, night level",
    mode: "dark",
    swatch: ["#0b1024", "#2fb1f0", "#fbd000"],
  },
  {
    id: "8bit-overworld",
    name: "Overworld",
    blurb: "8-bit console, blue sky",
    mode: "light",
    swatch: ["#5c94fc", "#f4efdc", "#e52521"],
  },
  {
    id: "soft-midnight",
    name: "Midnight",
    blurb: "quiet modern dark",
    mode: "dark",
    swatch: ["#11141f", "#5b8cff", "#a78bfa"],
  },
  {
    id: "soft-porcelain",
    name: "Porcelain",
    blurb: "quiet modern light",
    mode: "light",
    swatch: ["#f6f7fb", "#3563e9", "#12996b"],
  },
  {
    id: "crt-green",
    name: "Phosphor",
    blurb: "green CRT terminal",
    mode: "dark",
    swatch: ["#06120a", "#35ff7a", "#d4ff3f"],
  },
  {
    id: "crt-amber",
    name: "Amber",
    blurb: "amber CRT terminal",
    mode: "dark",
    swatch: ["#150d04", "#ffb42e", "#ff6a3d"],
  },
  {
    id: "neon-synthwave",
    name: "Synthwave",
    blurb: "neon grid, glowing rims",
    mode: "dark",
    swatch: ["#150726", "#ff2e97", "#23e5ff"],
  },
  {
    id: "print-broadsheet",
    name: "Broadsheet",
    blurb: "newsprint, serif headlines",
    mode: "light",
    swatch: ["#f8f4e9", "#221f1a", "#9c2a1f"],
  },
  {
    id: "blueprint-draft",
    name: "Blueprint",
    blurb: "drafting sheet, cyan ink",
    mode: "dark",
    swatch: ["#0c2242", "#6fd3ff", "#ffd166"],
  },
];

export const DEFAULT_DARK: ThemeId = "8bit-underground";
export const DEFAULT_LIGHT: ThemeId = "8bit-overworld";

const IDS = new Set<string>(THEMES.map((t) => t.id));

/** The structural family — the part before the first dash. */
export function skinOf(id: ThemeId | string): string {
  return String(id).split("-")[0];
}

export function themeMeta(id: ThemeId): Theme {
  return THEMES.find((t) => t.id === id) ?? THEMES[0];
}

/** Accepts a stored value, including the pre-multi-theme "dark"/"light". */
function normalize(v: string | null): ThemeId | null {
  if (!v) return null;
  if (IDS.has(v)) return v as ThemeId;
  if (v === "dark") return DEFAULT_DARK;
  if (v === "light") return DEFAULT_LIGHT;
  return null;
}

export function currentTheme(): ThemeId {
  if (typeof document === "undefined") return DEFAULT_DARK;
  return normalize(document.documentElement.dataset.theme ?? null) ?? DEFAULT_DARK;
}

export function applyTheme(t: ThemeId) {
  document.documentElement.dataset.theme = t;
  document.documentElement.dataset.skin = skinOf(t);
  storageSet(THEME_KEY, t);
}

export function storedTheme(): ThemeId | null {
  return normalize(storageGet(THEME_KEY));
}

/** Persisted choice, else the OS preference — what the page should be showing. */
export function resolveTheme(): ThemeId {
  const stored = storedTheme();
  if (stored) return stored;
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches) {
    return DEFAULT_LIGHT;
  }
  return DEFAULT_DARK;
}
