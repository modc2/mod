"use client";

// Global left sidebar — the app's home base. Square mark up top, the data
// views (Markets / Traders / Trades) under it, and the primary destinations
// (Strat World / Me / Docs) pinned at the bottom. Search + sign-in live in the
// top bar. Collapsible to a narrow icon rail; width state persists.
//
// Rendered once in layout.tsx as a flex sibling of the page content — when its
// width changes the content reflows automatically (no margin syncing).

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useFilterParams } from "../context/FiltersContext";
import { useEmbedded } from "../lib/embedded";

const ICON = "w-[18px] h-[18px] shrink-0";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

// Data views — toggle between live Markets, Traders, and Trades feeds. Sit in
// the middle of the rail, under search.
const DATA_NAV: NavItem[] = [
  {
    href: "/markets",
    label: "MARKETS",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M3 3v18h18" />
        <path d="M7 14l4-4 3 3 5-6" />
      </svg>
    ),
  },
  {
    href: "/traders",
    label: "TRADERS",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="9" cy="8" r="3" />
        <path d="M3 20c0-3 3-5 6-5s6 2 6 5" />
        <path d="M16 4a3 3 0 010 6M21 20c0-2.5-1.5-4.2-3.5-4.8" />
      </svg>
    ),
  },
  {
    href: "/trades",
    label: "TRADES",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M4 7h10M4 12h16M4 17h7" />
        <path d="M18 5l3 2-3 2" />
      </svg>
    ),
  },
];

// Bottom cluster — the three primary destinations the user asked for, plus
// Docs. Strat World is the strat-management home.
const FOOTER_NAV: NavItem[] = [
  {
    href: "/strats",
    label: "STRAT WORLD",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3c2.5 2.6 2.5 15.4 0 18M12 3c-2.5 2.6-2.5 15.4 0 18" />
      </svg>
    ),
  },
  {
    href: "/portfolio",
    label: "ME",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="8" r="3.2" />
        <path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6" />
      </svg>
    ),
  },
];

export default function LeftNav() {
  const pathname = usePathname() || "";
  const filterQs = useFilterParams();
  const embedded = useEmbedded();
  const [expanded, setExpanded] = useState(true);
  // Avoid a hydration flash: read persisted state after mount.
  useEffect(() => {
    try {
      setExpanded(localStorage.getItem("leftNavExpanded") !== "0");
    } catch {}
  }, []);
  const setExp = (v: boolean) => {
    setExpanded(v);
    try { localStorage.setItem("leftNavExpanded", v ? "1" : "0"); } catch {}
  };
  const toggle = () => setExp(!expanded);

  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");
  const hrefFor = (base: string) =>
    base === "/strats" && filterQs ? `${base}?${filterQs}` : base;

  // Split-screen iframe panes stay lightweight — no global rail.
  if (embedded) return null;

  const navRow = (t: NavItem) => {
    const active = isActive(t.href);
    return (
      <Link
        key={t.href}
        href={hrefFor(t.href)}
        title={t.label}
        className={`relative flex items-center gap-3 rounded-[var(--radius-sm)] px-3 py-2.5 transition-all duration-150 ${
          active
            ? "text-green-400 bg-green-400/10"
            : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
        }`}
      >
        <span
          className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] rounded-full bg-green-400 transition-all duration-200 ${
            active ? "h-6 opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]" : "h-0 opacity-0"
          }`}
        />
        <span className={active ? "glow-green" : ""}>{t.icon}</span>
        {expanded && (
          <span className="text-[12.5px] font-semibold tracking-[0.14em] whitespace-nowrap">
            {t.label}
          </span>
        )}
      </Link>
    );
  };

  return (
    <aside
      className={`group/nav shrink-0 sticky top-0 self-start h-[calc(100vh-3rem)] flex flex-col transition-[width] duration-200 ease-out backdrop-blur-md ${
        expanded ? "w-56" : "w-[58px]"
      }`}
      style={{
        background:
          "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.92), rgb(var(--pixel-bg-rgb)/0.86))",
        borderRight: "1px solid var(--border)",
        boxShadow: "8px 0 24px rgba(0,0,0,0.25)",
      }}
    >
      {/* ── Square mark (no wordmark) → Strat World ── */}
      <Link
        href="/strats"
        title="Strat World"
        className="flex items-center gap-2.5 h-14 px-[18px] shrink-0 hover:bg-pixel-white/5 transition-colors"
      >
        <span
          className="grid place-items-center w-[22px] h-[22px] rounded-[6px] bg-green-400 shrink-0 transition-transform duration-200 group-hover/nav:scale-105"
          style={{ boxShadow: "0 0 12px rgba(74,222,128,0.55), inset 0 1px 0 rgba(255,255,255,0.4)" }}
        >
          <span className="w-[7px] h-[7px] rounded-[2px] bg-pixel-black" />
        </span>
      </Link>

      {/* ── All destinations together at the top: Markets · Traders · Trades ·
          Strat World · Me · Docs — one continuous list, no split top/bottom. ── */}
      <nav className="flex flex-col gap-1 px-1.5 pt-1">
        {expanded && (
          <span className="px-3 pt-1 pb-0.5 text-[10px] text-pixel-gray tracking-[0.22em]">DATA</span>
        )}
        {DATA_NAV.map(navRow)}
        {FOOTER_NAV.map(navRow)}

        {/* Docs — secondary */}
        {navRow({
          href: "/docs",
          label: "DOCS",
          icon: (
            <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 3h9l4 4v14H6z" />
              <path d="M14 3v5h5M9 13h6M9 17h6" />
            </svg>
          ),
        })}
      </nav>

      {/* ── Open remaining space (intentional negative space) ── */}
      <div className="flex-1" />

      {/* ── Expand / collapse toggle ── */}
      <button
        onClick={toggle}
        title={expanded ? "Collapse sidebar" : "Expand sidebar"}
        className="flex items-center gap-3 px-4 py-3 border-t border-[var(--border)] text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06] transition-colors"
      >
        <span className="text-[15px] leading-none shrink-0 transition-transform duration-200">
          {expanded ? "«" : "»"}
        </span>
        {expanded && (
          <span className="text-[11px] font-semibold tracking-[0.18em] whitespace-nowrap">
            COLLAPSE
          </span>
        )}
      </button>
    </aside>
  );
}
