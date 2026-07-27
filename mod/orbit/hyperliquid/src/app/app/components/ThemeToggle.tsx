"use client";

// Sun/moon theme flip. The inline script in layout.tsx sets the initial
// class pre-hydration; this only reads it after mount, so SSR markup is
// theme-agnostic and never mismatches.

import { useEffect, useState } from "react";

type Theme = "dark" | "light";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    setTheme(document.documentElement.classList.contains("light") ? "light" : "dark");
  }, []);

  const flip = () => {
    const next: Theme = theme === "light" ? "dark" : "light";
    const el = document.documentElement;
    el.classList.add("theme-switching"); // opt-in cross-fade (globals.css)
    el.classList.remove("light", "dark");
    el.classList.add(next);
    window.setTimeout(() => el.classList.remove("theme-switching"), 350);
    // Shared modc2 origin — storage can be full; theming must never throw.
    try { localStorage.setItem("hl.theme", next); } catch {}
    setTheme(next);
  };

  return (
    <button
      className="btn !px-2"
      onClick={flip}
      title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
      aria-label={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
    >
      {theme === "light" ? (
        /* moon */
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      ) : (
        /* sun */
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
      )}
    </button>
  );
}
