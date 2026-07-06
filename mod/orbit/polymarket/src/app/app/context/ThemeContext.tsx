"use client";

// Light/dark theme switcher. Persists the user's choice in localStorage and
// applies `data-theme="light"` to <html> (CSS vars in globals.css do the
// rest — dark is the default, light is the override). On first paint a tiny
// inline script (see ThemeBoot) reads the stored value and stamps the html
// attribute synchronously, avoiding a dark→light flash for light-mode users.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type Theme = "dark" | "light";

interface ThemeContextValue {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
}

const STORAGE_KEY = "poly_theme";

const ThemeContext = createContext<ThemeContextValue>({
  theme: "dark",
  toggle: () => {},
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

function applyTheme(t: Theme) {
  if (typeof document === "undefined") return;
  if (t === "light") document.documentElement.setAttribute("data-theme", "light");
  else document.documentElement.removeAttribute("data-theme");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Always start as "dark" on the server render so SSR markup matches the
  // initial client render. The ThemeBoot script in <head> stamps the real
  // attribute before paint, so users never see a flash even though React
  // hydrates with "dark" first.
  const [theme, setThemeState] = useState<Theme>("dark");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
      if (stored === "light" || stored === "dark") {
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

  const toggle = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return (
    <ThemeContext.Provider value={{ theme, toggle, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

// Inline script that runs before React hydrates. Without this, the page
// paints dark for one frame on every load for light-mode users.
export function ThemeBoot() {
  const code = `try{var t=localStorage.getItem("${STORAGE_KEY}");if(t==="light")document.documentElement.setAttribute("data-theme","light");}catch(e){}`;
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}
