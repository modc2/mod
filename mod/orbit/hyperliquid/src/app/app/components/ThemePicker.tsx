"use client";

// Palette picker. The inline boot script (layout.tsx) stamps the initial
// theme pre-hydration; this reads it back only after mount, so SSR markup
// stays theme-agnostic and never mismatches.

import { useEffect, useRef, useState } from "react";
import { THEMES, applyTheme, currentTheme, DEFAULT_THEME } from "../lib/themes";

function Swatch({ colors, size = 8 }: { colors: readonly string[]; size?: number }) {
  return (
    <span className="flex items-center gap-[3px]" aria-hidden="true">
      {colors.map((c, i) => (
        <span
          key={i}
          className="rounded-full ring-1 ring-white/20"
          style={{ width: size, height: size, background: c }}
        />
      ))}
    </span>
  );
}

export default function ThemePicker() {
  const [theme, setTheme] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => setTheme(currentTheme()), []);

  // Close on outside click or Escape — a dropdown that traps the page is
  // worse than no dropdown.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (id: string) => {
    applyTheme(id);
    setTheme(id);
    setOpen(false);
  };

  const active = THEMES.find((t) => t.id === (theme ?? DEFAULT_THEME)) ?? THEMES[0];

  return (
    <div className="relative" ref={wrap}>
      <button
        className="btn !px-2.5 gap-2"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`Theme: ${active.label}`}
        aria-label={`Theme: ${active.label}. Change theme`}
      >
        <Swatch colors={active.swatch} />
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
          className={`transition-transform duration-150 ${open ? "rotate-180" : ""}`}>
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          // bg-panel over the glass fill: a menu floating above dense table
          // content needs an opaque surface or the rows read straight through.
          className="panel bg-panel absolute right-0 mt-2 w-52 p-1.5 z-50 shadow-lift animate-fadeUp"
        >
          <div className="label px-2 pt-1">Theme</div>
          {THEMES.map((t) => {
            const on = t.id === active.id;
            return (
              <button
                key={t.id}
                role="menuitemradio"
                aria-checked={on}
                onClick={() => pick(t.id)}
                className={`w-full flex items-center gap-2.5 px-2 py-1.5 rounded text-[11px]
                  font-medium uppercase tracking-wider transition-colors
                  ${on ? "text-accent bg-accent/10" : "text-muted hover:text-ink hover:bg-white/[0.05]"}`}
              >
                <Swatch colors={t.swatch} size={9} />
                <span className="flex-1 text-left">{t.label}</span>
                <span className="text-[9px] text-dim normal-case tracking-normal">{t.base}</span>
                {on && (
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
