import { storageGet, storageSet } from "./safeStorage";

// Theme choice is shared with the pre-hydration script in layout.tsx, which
// reads the same key so the first paint already matches.
const THEME_KEY = "store:theme";

export type Theme = "light" | "dark";

export function currentTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  storageSet(THEME_KEY, t);
}

export function storedTheme(): Theme | null {
  const t = storageGet(THEME_KEY);
  return t === "light" || t === "dark" ? t : null;
}

/** Persisted choice, else the OS preference — what the page should be showing. */
export function resolveTheme(): Theme {
  const stored = storedTheme();
  if (stored) return stored;
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches) return "light";
  return "dark";
}
