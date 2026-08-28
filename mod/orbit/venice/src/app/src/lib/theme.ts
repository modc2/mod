/**
 * Visual modes. Every theme is pure CSS — a `data-theme` attribute on <html>
 * swaps a block of custom properties. Nothing here touches app state, so a
 * mode change can never break a session.
 *
 * Structure is 8-bit by default (hard edges, 3px frames, offset shadows,
 * Press Start 2P chrome); a theme may opt out by setting `pixel: false`, which
 * only means it also overrides the structural variables in globals.css.
 *
 * The same key + id list is read by the inline no-flash script in layout.tsx;
 * keep the literals in sync with it.
 */

export type ThemeId =
  // dark
  | "arcade"
  | "atelier"
  | "noir"
  | "lagoon"
  | "commodore"
  | "vapor"
  | "matrix"
  | "velvet"
  // light
  | "paper"
  | "gameboy"
  | "bloom";

export type Base = "dark" | "light";

export const THEME_KEY = "venice:theme";
export const LAST_DARK_KEY = "venice:theme:dark";
export const LAST_LIGHT_KEY = "venice:theme:light";

export const DEFAULT_DARK: ThemeId = "arcade";
export const DEFAULT_LIGHT: ThemeId = "paper";
export const DEFAULT_THEME: ThemeId = DEFAULT_DARK;

export type ThemeDef = {
  id: ThemeId;
  label: string;
  hint: string;
  base: Base;
  pixel: boolean;
  /** four colours, drawn as a 2×2 pixel swatch in the picker */
  swatch: [string, string, string, string];
};

export const THEMES: ThemeDef[] = [
  {
    id: "arcade",
    label: "Arcade",
    hint: "NES cabinet — red, gold and midnight blue",
    base: "dark",
    pixel: true,
    swatch: ["#e43b44", "#ffd83d", "#3d3d63", "#10101c"],
  },
  {
    id: "atelier",
    label: "Atelier",
    hint: "Venetian gold leaf on obsidian, in 8 bits",
    base: "dark",
    pixel: true,
    swatch: ["#e8b04b", "#e3203a", "#4a3524", "#0c0a10"],
  },
  {
    id: "noir",
    label: "Noir",
    hint: "Monochrome ink and brushed steel",
    base: "dark",
    pixel: true,
    swatch: ["#e8edf5", "#8ea0bd", "#39445c", "#0e1117"],
  },
  {
    id: "lagoon",
    label: "Lagoon",
    hint: "The water at dawn — aqua on deep teal",
    base: "dark",
    pixel: true,
    swatch: ["#2bd6c6", "#7ef2c9", "#12525a", "#04181f"],
  },
  {
    id: "commodore",
    label: "Commodore",
    hint: "C64 boot screen — periwinkle on royal blue",
    base: "dark",
    pixel: true,
    swatch: ["#8f88ff", "#cfc9ff", "#5245c8", "#31289e"],
  },
  {
    id: "vapor",
    label: "Vapor",
    hint: "Neon dusk — magenta, cyan, chrome",
    base: "dark",
    pixel: true,
    swatch: ["#ff5dd2", "#3df0ff", "#4b2c8f", "#160f2e"],
  },
  {
    id: "matrix",
    label: "Phosphor",
    hint: "Green CRT terminal, scanlines and all",
    base: "dark",
    pixel: true,
    swatch: ["#39ff8b", "#b7ffd4", "#14512f", "#020806"],
  },
  {
    id: "velvet",
    label: "Velvet",
    hint: "No pixels — obsidian glass and gold gradients",
    base: "dark",
    pixel: false,
    swatch: ["#e3203a", "#e8b04b", "#6c6bf2", "#07060a"],
  },
  {
    id: "paper",
    label: "Paper",
    hint: "Daylight — ink on warm newsprint",
    base: "light",
    pixel: true,
    swatch: ["#d1345b", "#1d5fd6", "#c4bba6", "#f6f1e4"],
  },
  {
    id: "gameboy",
    label: "Game Boy",
    hint: "DMG-01 — four shades of pea soup",
    base: "light",
    pixel: true,
    swatch: ["#0f380f", "#306230", "#8bac0f", "#cfdf6b"],
  },
  {
    id: "bloom",
    label: "Bloom",
    hint: "Soft light — rose, cream and ripe plum",
    base: "light",
    pixel: true,
    swatch: ["#d6336c", "#7048e8", "#f0c9d8", "#fdf7f9"],
  },
];

export const THEME_BY_ID: Record<string, ThemeDef> = Object.fromEntries(
  THEMES.map((t) => [t.id, t])
) as Record<string, ThemeDef>;

/** Ids in declaration order — mirrored by the no-flash script in layout.tsx. */
export const THEME_IDS = THEMES.map((t) => t.id);

export function isTheme(v: unknown): v is ThemeId {
  return typeof v === "string" && (THEME_IDS as string[]).includes(v);
}

export function themeDef(id: ThemeId): ThemeDef {
  return THEME_BY_ID[id] ?? THEME_BY_ID[DEFAULT_THEME];
}

/**
 * The stored mode. With nothing stored we follow the OS: a machine in light
 * mode should not get a black screen on first paint. Safe before hydration.
 */
export function readTheme(): ThemeId {
  if (typeof window === "undefined") return DEFAULT_THEME;
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (isTheme(v)) return v;
  } catch {
    /* storage blocked — fall through to the OS preference */
  }
  try {
    if (window.matchMedia?.("(prefers-color-scheme: light)").matches) return DEFAULT_LIGHT;
  } catch {
    /* no matchMedia */
  }
  return DEFAULT_THEME;
}

/** Paint a mode. Persisting is best-effort — the origin's quota is shared. */
export function applyTheme(id: ThemeId): void {
  if (typeof document === "undefined") return;
  const def = themeDef(id);
  document.documentElement.dataset.theme = id;
  document.documentElement.dataset.base = def.base;
  // Native form controls / scrollbars follow the mode too.
  document.documentElement.style.colorScheme = def.base;
  try {
    localStorage.setItem(THEME_KEY, id);
    localStorage.setItem(def.base === "light" ? LAST_LIGHT_KEY : LAST_DARK_KEY, id);
  } catch {
    /* full origin — the mode still applies for this session */
  }
}

/**
 * The theme the light/dark toggle should jump to: whichever mode of the
 * opposite base was last used, else that base's default.
 */
export function counterpart(current: ThemeId): ThemeId {
  const want: Base = themeDef(current).base === "light" ? "dark" : "light";
  try {
    const remembered = localStorage.getItem(want === "light" ? LAST_LIGHT_KEY : LAST_DARK_KEY);
    if (isTheme(remembered) && themeDef(remembered).base === want) return remembered;
  } catch {
    /* storage blocked */
  }
  return want === "light" ? DEFAULT_LIGHT : DEFAULT_DARK;
}
