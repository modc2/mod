"use client";

// A pill-shaped picker that replaces the native <select> in the console
// chrome. Native selects paint their menu with the OS widget — a gray sheet
// with system fonts, jarring next to everything else here. This renders the
// menu itself: same glass/accent language as the hub cards, mono type, a
// check on the active row, and hints for rows that aren't selectable yet.
//
// The menu is portaled to <body> and position:fixed against the trigger's
// measured rect. It has to be a portal: the PARAMS panel it usually lives in
// carries a backdrop-filter, and that makes the panel the containing block
// AND the stacking context for fixed children — an in-place menu lands at the
// wrong offset and paints *under* the page.

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export type PillOption = {
  value: string;
  label: string;
  /** Accent for the row + the trigger when this option is active. */
  color?: string;
  /** Small trailing note on the row, e.g. "soon". */
  note?: string;
  /** Longer tooltip for the row. */
  hint?: string;
  disabled?: boolean;
};

type Props = {
  value: string;
  options: PillOption[];
  onChange: (value: string) => void;
  /** Fallback accent when the active option has no color of its own. */
  accent?: string;
  /** Rendered instead of the active option's label (empty selections). */
  placeholder?: string;
  title?: string;
  maxWidth?: number;
  menuWidth?: number;
  className?: string;
  /** Extra styles merged over the trigger pill (wins on conflict) — lets a
      host strip restyle the trigger to match its sibling chips. */
  triggerStyle?: React.CSSProperties;
  /** Render a search box pinned to the top of the menu — for lists too long
      to scan (the module registry). Rows filter client-side as you type. */
  searchable?: boolean;
  /** Fires with the query as the user types (and with "" when the menu
      closes after a search) — lets a server-backed list refetch. */
  onSearch?: (q: string) => void;
  /** Fires when the menu opens — the hook for lazy-loading options. */
  onOpen?: () => void;
  searchPlaceholder?: string;
  /** Stacking layer for the portaled menu (and its click-away scrim). The
      default clears the console chrome; a picker used INSIDE a modal has to
      be raised above that modal's own backdrop or its menu opens underneath
      it — invisible and unclickable. */
  menuZ?: number;
  "aria-label"?: string;
};

const MENU_MAX_HEIGHT = 320;
const MENU_Z = 200;

/** Accents arrive as hex ("#f472b6") or as CSS vars — color-mix handles both. */
const tint = (color: string, pct: number) => `color-mix(in srgb, ${color} ${pct}%, transparent)`;

export function PillSelect({
  value,
  options,
  onChange,
  accent = "var(--text-secondary)",
  placeholder,
  title,
  maxWidth = 220,
  menuWidth,
  className = "",
  triggerStyle,
  searchable = false,
  onSearch,
  onOpen,
  searchPlaceholder,
  menuZ = MENU_Z,
  "aria-label": ariaLabel,
}: Props) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ x: number; y: number; w: number; up: boolean } | null>(null);
  const [cursor, setCursor] = useState(0);
  const [query, setQuery] = useState("");
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  const active = options.find((o) => o.value === value);
  const color = active?.color || accent;
  // The rows actually shown: the search filters client-side on top of
  // whatever refetch onSearch triggered, so typing narrows immediately.
  const q = query.trim().toLowerCase();
  const visible = searchable && q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
  const selectable = visible.filter((o) => !o.disabled);

  const place = useCallback(() => {
    const el = btnRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // Flip above the trigger when the viewport can't fit the menu below it —
    // the composer bar sits at the bottom of the screen.
    const below = window.innerHeight - r.bottom;
    const up = below < Math.min(MENU_MAX_HEIGHT, options.length * 30 + 12) && r.top > below;
    setAnchor({ x: r.left, y: up ? r.top - 4 : r.bottom + 4, w: r.width, up });
  }, [options.length]);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => place();
    window.addEventListener("resize", onScroll);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", onScroll);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open, place]);

  // Focus the menu on open (the search box when there is one) so arrow keys
  // work without clicking a row first. Deliberately keyed on `open` alone:
  // `options` is rebuilt by the parent on every render, and re-running this
  // would snap the cursor back to the selected row mid-navigation.
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => (searchable ? searchRef.current?.focus() : menuRef.current?.focus()), 0);
    return () => clearTimeout(t);
  }, [open, searchable]);

  const openMenu = () => {
    setQuery("");
    onOpen?.();
    const i = options.findIndex((o) => o.value === value);
    setCursor(i >= 0 ? i : 0);
    setOpen(true);
  };

  // Every way out funnels through here so a server-backed list is restored
  // to its unfiltered self after a search (mirrors the PARAMS picker).
  const close = (refocus: boolean) => {
    if (searchable && query) onSearch?.("");
    setOpen(false);
    if (refocus) btnRef.current?.focus();
  };

  const pick = (o: PillOption) => {
    if (o.disabled) return;
    onChange(o.value);
    close(true);
  };

  const move = (dir: 1 | -1) => {
    if (selectable.length === 0) return;
    let i = cursor;
    for (let step = 0; step < visible.length; step++) {
      i = (i + dir + visible.length) % visible.length;
      if (!visible[i].disabled) break;
    }
    setCursor(i);
  };

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            openMenu();
          }
        }}
        className={`text-[12px] font-mono px-2.5 py-1.5 rounded-full outline-none cursor-pointer inline-flex items-center gap-1.5 focus-ring ${className}`}
        style={{
          color,
          border: `1px solid ${tint(color, 33)}`,
          background: tint(color, 8),
          letterSpacing: "0.04em",
          maxWidth,
          ...triggerStyle,
        }}
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
      >
        <span className="truncate">{active?.label ?? placeholder ?? value}</span>
        <span style={{ opacity: 0.55, fontSize: 10 }} aria-hidden="true">▾</span>
      </button>

      {open && anchor && createPortal(
        <>
          <div className="fixed inset-0" style={{ zIndex: menuZ }} onClick={() => close(false)} />
          <div
            ref={menuRef}
            role="listbox"
            tabIndex={-1}
            onKeyDown={(e) => {
              // Key events from the search input bubble up here, so one
              // handler drives both — but space has to keep typing spaces.
              if (e.key === "Escape") { e.preventDefault(); close(true); }
              else if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
              else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
              else if (e.key === "Enter" || (e.key === " " && !searchable)) { e.preventDefault(); const o = visible[cursor]; if (o) pick(o); }
            }}
            className="fixed rounded-xl overflow-y-auto py-1 outline-none"
            style={{
              zIndex: menuZ + 1,
              left: Math.max(8, Math.min(anchor.x, (typeof window !== "undefined" ? window.innerWidth : 1e4) - (menuWidth ?? Math.max(anchor.w, 180)) - 8)),
              [anchor.up ? "bottom" : "top"]: anchor.up
                ? (typeof window !== "undefined" ? window.innerHeight : 0) - anchor.y
                : anchor.y,
              minWidth: menuWidth ?? Math.max(anchor.w, 180),
              maxWidth: "90vw",
              maxHeight: MENU_MAX_HEIGHT,
              background: "var(--bg-primary)",
              border: `1px solid ${tint(color, 27)}`,
              boxShadow: `0 18px 44px -18px rgba(0,0,0,0.75), 0 0 0 1px ${tint(color, 9)}`,
              backdropFilter: "blur(14px) saturate(150%)",
            }}
          >
            {searchable && (
              // Sticky so the box stays put while the rows scroll under it.
              <div className="sticky z-[1] px-1 pb-1 -mt-1 pt-1" style={{ top: -4, background: "var(--bg-primary)" }}>
                <input
                  ref={searchRef}
                  type="text"
                  value={query}
                  onChange={(e) => { setQuery(e.target.value); setCursor(0); onSearch?.(e.target.value); }}
                  placeholder={searchPlaceholder ?? "search…"}
                  className="w-full text-[12px] font-mono px-2 py-1.5 rounded-lg outline-none"
                  style={{
                    color: "var(--text-primary)",
                    background: tint(color, 6),
                    border: `1px solid ${tint(color, 20)}`,
                  }}
                  aria-label={searchPlaceholder ?? "Search options"}
                />
              </div>
            )}
            {visible.length === 0 && (
              <div className="px-2.5 py-1.5 text-[11px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                no matches{query ? ` for “${query.trim()}”` : ""}
              </div>
            )}
            {visible.map((o, i) => {
              const on = o.value === value;
              const rowColor = o.color || color;
              return (
                <div
                  key={o.value}
                  role="option"
                  aria-selected={on}
                  aria-disabled={o.disabled || undefined}
                  onClick={() => pick(o)}
                  onMouseEnter={() => setCursor(i)}
                  className="px-2.5 py-1.5 text-[12px] font-mono flex items-center gap-2 transition-colors"
                  style={{
                    color: o.disabled ? "var(--text-tertiary)" : on ? rowColor : "var(--text-secondary)",
                    background: i === cursor && !o.disabled ? tint(rowColor, 8) : "transparent",
                    cursor: o.disabled ? "not-allowed" : "pointer",
                    opacity: o.disabled ? 0.5 : 1,
                  }}
                  title={o.hint}
                >
                  <span className="w-2.5 shrink-0" style={{ color: rowColor }} aria-hidden="true">{on ? "✓" : ""}</span>
                  <span className="truncate flex-1">{o.label}</span>
                  {o.note && (
                    <span className="text-[9px] uppercase tracking-wider shrink-0" style={{ color: "var(--text-tertiary)" }}>
                      {o.note}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </>,
        document.body,
      )}
    </>
  );
}

export default PillSelect;
