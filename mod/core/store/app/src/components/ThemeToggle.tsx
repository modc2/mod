"use client";

import { useEffect, useState } from "react";
import { applyTheme, currentTheme, resolveTheme, Theme } from "@/lib/theme";

const SunIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
    <circle cx="12" cy="12" r="4.2" />
    <path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5 5l1.7 1.7M17.3 17.3 19 19M19 5l-1.7 1.7M6.7 17.3 5 19" />
  </svg>
);

const MoonIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5a8.5 8.5 0 1 0 10.7 10.7Z" />
  </svg>
);

/** Ghost icon button that flips light/dark; renders after mount so the icon
 *  always matches the theme the pre-hydration script applied. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);
  // Re-assert the attribute on mount: React's post-hydration render of <html>
  // can wipe what the pre-hydration script set (no attribute lives in JSX).
  useEffect(() => {
    const t = resolveTheme();
    document.documentElement.dataset.theme = t;
    setTheme(t);
  }, []);

  const flip = () => {
    const next: Theme = currentTheme() === "light" ? "dark" : "light";
    applyTheme(next);
    setTheme(next);
  };

  return (
    <button
      className="ghost icon"
      onClick={flip}
      title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
      aria-label="Toggle color theme"
    >
      {theme === "light" ? <MoonIcon /> : <SunIcon />}
    </button>
  );
}
