"use client";

import { useEffect, useRef, useState } from "react";
import { applyTheme, resolveTheme, THEMES, ThemeId, themeMeta } from "@/lib/theme";

const PaletteIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden>
    <path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.8 1.8-1.7 0-1.2-1-1.6-1-2.6 0-.8.7-1.4 1.6-1.4H16a5 5 0 0 0 5-5c0-4-4-7.3-9-7.3Z" />
    <circle cx="7.6" cy="12" r="1.1" fill="currentColor" stroke="none" />
    <circle cx="9.6" cy="8" r="1.1" fill="currentColor" stroke="none" />
    <circle cx="14.4" cy="7.6" r="1.1" fill="currentColor" stroke="none" />
    <circle cx="17.6" cy="10.6" r="1.1" fill="currentColor" stroke="none" />
  </svg>
);

/** Theme picker: a ghost icon button that drops a swatch list of every skin
 *  the store ships. Renders its state only after mount, so the label always
 *  matches whatever the pre-hydration script actually applied. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeId | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Re-assert the attributes on mount: React's post-hydration render of <html>
  // can wipe what the pre-hydration script set (no attribute lives in JSX).
  useEffect(() => {
    const t = resolveTheme();
    document.documentElement.dataset.theme = t;
    document.documentElement.dataset.skin = t.split("-")[0];
    setTheme(t);
  }, []);

  // click-away + escape close
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  const pick = (id: ThemeId) => {
    applyTheme(id);
    setTheme(id);
    setOpen(false);
  };

  return (
    <div className="theme-pick" ref={box}>
      <button
        className="ghost icon"
        onClick={() => setOpen((o) => !o)}
        title={theme ? `Theme — ${themeMeta(theme).name}` : "Theme"}
        aria-label="Pick a theme"
        aria-expanded={open}
      >
        <PaletteIcon />
      </button>
      {open && (
        <div className="theme-menu" role="menu">
          <div className="theme-menu-head">
            Theme <span>{THEMES.length} skins</span>
          </div>
          {THEMES.map((t) => (
            <button
              key={t.id}
              role="menuitemradio"
              aria-checked={theme === t.id}
              className={`theme-opt ${theme === t.id ? "on" : ""}`}
              onClick={() => pick(t.id)}
            >
              <span
                className="theme-swatch"
                style={{
                  background: `linear-gradient(135deg, ${t.swatch[0]} 0 40%, ${t.swatch[1]} 40% 72%, ${t.swatch[2]} 72%)`,
                }}
              />
              <span className="theme-opt-txt">
                <span className="theme-opt-name">{t.name}</span>
                <span className="theme-opt-blurb">{t.blurb}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
