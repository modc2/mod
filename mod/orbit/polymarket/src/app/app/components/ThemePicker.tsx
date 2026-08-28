"use client";

import { useEffect, useRef, useState } from "react";
import { THEMES, useTheme } from "../context/ThemeContext";

// Theme picker chip for the top bar. Collapsed it's the active theme's swatch
// trio; open it's the full palette list. Rows are painted with each theme's
// OWN colors (not the active one's) so the list previews itself — you pick by
// looking, not by reading labels.
export default function ThemePicker() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const active = THEMES.find((t) => t.id === theme) ?? THEMES[0];

  // Close on outside click / Escape — same idiom as NavMenu.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
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

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={`Theme — ${active.label}`}
        aria-expanded={open}
        aria-label="Theme"
        className="inline-flex items-center gap-1.5 rounded-full border pl-1.5 pr-2 py-1 transition-colors"
        style={{
          background: open ? "var(--btn-bg-hover)" : "var(--btn-bg)",
          borderColor: "var(--border-strong)",
        }}
      >
        <Swatch colors={active.swatch} />
        <span className="text-[11px] leading-none" style={{ color: "var(--fg-muted)" }}>
          {active.glyph}
        </span>
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1.5 z-50 w-[178px] rounded-[var(--radius-sm)] backdrop-blur-md p-1.5 flex flex-col gap-0.5"
          style={{
            background:
              "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.96), rgb(var(--pixel-bg-rgb)/0.94))",
            border: "1px solid var(--border)",
            boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
          }}
        >
          {THEMES.map((t) => {
            const isActive = t.id === theme;
            return (
              <button
                key={t.id}
                onClick={() => {
                  setTheme(t.id);
                  setOpen(false);
                }}
                className="flex items-center gap-2.5 rounded-[var(--radius-sm)] px-2 py-1.5 transition-colors text-left"
                style={{
                  background: isActive ? `${t.swatch[1]}1f` : "transparent",
                  color: isActive ? "var(--fg)" : "var(--fg-muted)",
                  boxShadow: isActive ? `inset 2px 0 0 ${t.swatch[1]}` : "none",
                }}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.background = "var(--btn-bg-hover)";
                }}
                onMouseLeave={(e) => {
                  if (!isActive) e.currentTarget.style.background = "transparent";
                }}
              >
                <Swatch colors={t.swatch} />
                <span className="text-[11px] font-semibold tracking-[0.14em] whitespace-nowrap">
                  {t.label}
                </span>
                <span className="ml-auto text-[11px] leading-none opacity-70">{t.glyph}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// [surface, accent, signal] as three overlapping dots — a legible palette
// preview at 34px wide.
function Swatch({ colors }: { colors: readonly string[] }) {
  return (
    <span className="inline-flex shrink-0">
      {colors.map((c, i) => (
        <span
          key={i}
          className="w-[10px] h-[10px] rounded-full"
          style={{
            background: c,
            marginLeft: i ? -3 : 0,
            border: "1px solid var(--border-strong)",
            zIndex: colors.length - i,
          }}
        />
      ))}
    </span>
  );
}
