"use client";

import { useEffect, useRef, useState } from "react";
import {
  Base,
  DEFAULT_THEME,
  ThemeDef,
  ThemeId,
  THEMES,
  applyTheme,
  counterpart,
  readTheme,
  themeDef,
} from "@/lib/theme";
import Pix from "./Pix";

/** The 2×2 pixel swatch that stands in for a whole palette. */
export function Swatch({ def, big }: { def: ThemeDef; big?: boolean }) {
  return (
    <span className={`sw${big ? " lg" : ""}`} aria-hidden="true">
      {def.swatch.map((c, i) => (
        <i key={i} style={{ background: c }} />
      ))}
    </span>
  );
}

type Props = {
  /** open the popover upwards (for a picker pinned to the bottom of a column) */
  up?: boolean;
  /** anchor the popover's left edge instead of its right */
  left?: boolean;
  /** hide the mode name, leaving just the swatch */
  compact?: boolean;
};

/**
 * Mode switcher: a swatch button that opens the palette list, plus a one-tap
 * light/dark flip. Theme state lives entirely in <html> + localStorage, so
 * nothing here can disturb a session.
 */
export default function ThemePicker({ up, left, compact }: Props) {
  const [theme, setTheme] = useState<ThemeId>(DEFAULT_THEME);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Read after mount: the server render can't know the stored mode.
  useEffect(() => {
    const t = readTheme();
    setTheme(t);
    applyTheme(t);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (id: ThemeId) => {
    applyTheme(id);
    setTheme(id);
    setOpen(false);
  };

  const def = themeDef(theme);
  const other = themeDef(counterpart(theme));
  const groups: { base: Base; label: string }[] = [
    { base: "dark", label: "Dark modes" },
    { base: "light", label: "Light modes" },
  ];

  return (
    <div className="tp" ref={box}>
      <button
        className="tp-btn sm"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={`${def.label} — ${def.hint}`}
      >
        <Swatch def={def} />
        {!compact && <span className="tp-label">{def.label}</span>}
      </button>

      <button
        className="tp-flip sm"
        onClick={() => pick(other.id)}
        title={`Switch to ${other.base} mode (${other.label})`}
        aria-label={`Switch to ${other.base} mode`}
      >
        <Pix name={def.base === "dark" ? "sun" : "moon"} size={14} />
      </button>

      {open && (
        <div className={`tp-pop${up ? " up" : ""}${left ? " left" : ""}`} role="listbox">
          {groups.map((g) => (
            <div className="tp-group" key={g.base}>
              <div className="sec-title">{g.label}</div>
              {THEMES.filter((t) => t.base === g.base).map((t) => (
                <button
                  key={t.id}
                  className={`tp-item${t.id === theme ? " on" : ""}`}
                  role="option"
                  aria-selected={t.id === theme}
                  onClick={() => pick(t.id)}
                >
                  <Swatch def={t} big />
                  <span>
                    <span className="tp-name">{t.label}</span>
                    <span className="tp-hint">{t.hint}</span>
                  </span>
                  {t.id === theme && (
                    <span className="tp-check">
                      <Pix name="check" size={12} />
                    </span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
