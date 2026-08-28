"use client";

import { useEffect, useRef, useState } from "react";
import { THEMES, useTheme, type Theme } from "../context/ThemeContext";

// The cabinet's DIP-switch panel. The cap on the rail carries three chips off
// the current palette; pressing it drops a hard menu of every cabinet, each
// row wearing its own three. No preview, no animation: you flip the switch and
// the machine is a different machine.
export default function SkinPicker() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const current = THEMES.find((t) => t.id === theme) ?? THEMES[0];

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={wrap} className="relative shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="pixel-btn topbar-ctl gap-2 px-3"
        title={`Skin: ${current.label}`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Chips chips={current.chips} />
        <span className="hidden md:inline">{current.label}</span>
      </button>

      {open && (
        <div className="skin-menu" role="menu" aria-label="Skin">
          {THEMES.map((t) => (
            <button
              key={t.id}
              role="menuitemradio"
              aria-checked={t.id === theme}
              className="skin-menu__item"
              onClick={() => { setTheme(t.id as Theme); setOpen(false); }}
            >
              <span className="skin-menu__cursor" aria-hidden>
                {t.id === theme ? "▸" : ""}
              </span>
              <span>{t.label}</span>
              <Chips chips={t.chips} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Chips({ chips }: { chips: readonly string[] }) {
  return (
    <span className="skin-chips" aria-hidden>
      {chips.map((c) => (
        <span key={c} className="skin-chip" style={{ background: c }} />
      ))}
    </span>
  );
}
