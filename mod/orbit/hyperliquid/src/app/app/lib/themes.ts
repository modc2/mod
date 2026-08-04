// The theme set — one source of truth for the picker, the boot script and
// globals.css. Every id needs a matching `html[data-theme="id"]` palette
// block in globals.css; `base` says which of the two `data-base` treatments
// (glass polarity, panel sheen, shadows) that palette sits on, so a new
// light theme inherits all the light-mode work for free.
//
// `swatch` is [surface, accent, second] sampled from the palette itself —
// the three dots the picker draws.

export type ThemeBase = "dark" | "light";

export interface ThemeDef {
  id: string;
  label: string;
  base: ThemeBase;
  swatch: [string, string, string];
}

export const THEMES: ThemeDef[] = [
  { id: "dark",    label: "Midnight", base: "dark",  swatch: ["#04060a", "#3ce0c8", "#5cc8ff"] },
  { id: "light",   label: "Daylight", base: "light", swatch: ["#f3f6fa", "#0a8f80", "#0476be"] },
  { id: "abyss",   label: "Abyss",    base: "dark",  swatch: ["#020814", "#38bdf8", "#2dd4bf"] },
  { id: "matrix",  label: "Matrix",   base: "dark",  swatch: ["#010502", "#00ff7f", "#4defc9"] },
  { id: "neon",    label: "Neon",     base: "dark",  swatch: ["#0a0416", "#ff2da0", "#0ff0d4"] },
  { id: "ember",   label: "Ember",    base: "dark",  swatch: ["#0c0603", "#ff9e2c", "#ffd75e"] },
  { id: "nebula",  label: "Nebula",   base: "dark",  swatch: ["#080614", "#a78bfa", "#f472b6"] },
  { id: "bullion", label: "Bullion",  base: "dark",  swatch: ["#080704", "#e8bb4a", "#8abeff"] },
  { id: "paper",   label: "Paper",    base: "light", swatch: ["#f6f1e8", "#9a5b2c", "#166534"] },
  { id: "arctic",  label: "Arctic",   base: "light", swatch: ["#f0f5fb", "#2563eb", "#0891b2"] },
];

export const STORAGE_KEY = "hl.theme";
export const DEFAULT_THEME = "dark";

export const themeBase = (id: string): ThemeBase =>
  THEMES.find((t) => t.id === id)?.base ?? "dark";

/** Stamp a theme on <html>, with a brief cross-fade (globals.css keys the
 *  transition on .theme-switching so normal hovers stay untouched).
 *  Storage is best-effort: the shared modc2 origin can be full or blocked,
 *  and theming must never throw. */
export function applyTheme(id: string, animate = true) {
  const el = document.documentElement;
  if (animate) {
    el.classList.add("theme-switching");
    window.setTimeout(() => el.classList.remove("theme-switching"), 350);
  }
  el.setAttribute("data-theme", id);
  el.setAttribute("data-base", themeBase(id));
  try { localStorage.setItem(STORAGE_KEY, id); } catch {}
}

/** Read the stamped theme back off <html> — what the picker shows after
 *  mount. SSR renders the default, so components must not read this during
 *  render or hydration mismatches. */
export function currentTheme(): string {
  return document.documentElement.getAttribute("data-theme") ?? DEFAULT_THEME;
}

/** Inline <head> script: applies the saved theme (or the OS preference)
 *  before first paint, so returning users never see a flash of Midnight.
 *  Built from THEMES so the id list can't drift from the palettes. */
export function themeBootScript(): string {
  const light = THEMES.filter((t) => t.base === "light").map((t) => t.id);
  return (
    `(function(){var t;try{t=localStorage.getItem(${JSON.stringify(STORAGE_KEY)})}catch(e){}` +
    `var A=${JSON.stringify(THEMES.map((t) => t.id))},L=${JSON.stringify(light)};` +
    `if(A.indexOf(t)<0){try{t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}catch(e){t="dark"}}` +
    `var d=document.documentElement;d.setAttribute("data-theme",t);` +
    `d.setAttribute("data-base",L.indexOf(t)>=0?"light":"dark")})();`
  );
}
