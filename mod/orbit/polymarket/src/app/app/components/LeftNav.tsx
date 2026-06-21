"use client";

// Global left navigation rail. Holds the app's primary nav (formerly the
// horizontal links in TopBar) so the top bar can shrink to just search +
// wallet. Collapsible: a narrow icon rail by default, expands to show labels.
// State persists in localStorage so it stays the way the user left it.
//
// Rendered once in layout.tsx as a flex sibling of the page content — when
// its width changes the content reflows automatically (no margin syncing).

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useFilterParams } from "../context/FiltersContext";
import { useEmbedded } from "../lib/embedded";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

const ICON = "w-[18px] h-[18px] shrink-0";

const NAV: NavItem[] = [
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
    href: "/strats",
    label: "STRATS",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="6" cy="6" r="2.5" />
        <circle cx="18" cy="6" r="2.5" />
        <circle cx="12" cy="18" r="2.5" />
        <path d="M6 8.5v3a3 3 0 003 3h6a3 3 0 003-3v-3" />
        <path d="M12 14.5v1" />
      </svg>
    ),
  },
  {
    href: "/portfolio",
    label: "PORTFOLIO",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="7" width="18" height="13" rx="1.5" />
        <path d="M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2" />
      </svg>
    ),
  },
  {
    href: "/docs",
    label: "DOCS",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M6 3h9l4 4v14H6z" />
        <path d="M14 3v5h5M9 13h6M9 17h6" />
      </svg>
    ),
  },
];

export default function LeftNav() {
  const pathname = usePathname() || "";
  const filterQs = useFilterParams();
  const embedded = useEmbedded();
  const [expanded, setExpanded] = useState(false);
  // Avoid a hydration flash: read persisted state after mount.
  useEffect(() => {
    try {
      setExpanded(localStorage.getItem("leftNavExpanded") === "1");
    } catch {}
  }, []);
  const toggle = () => {
    setExpanded((v) => {
      const next = !v;
      try { localStorage.setItem("leftNavExpanded", next ? "1" : "0"); } catch {}
      return next;
    });
  };

  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");
  const hrefFor = (base: string) =>
    (base === "/traders" || base === "/strats") && filterQs ? `${base}?${filterQs}` : base;

  // Split-screen iframe panes stay lightweight — no global rail.
  if (embedded) return null;

  return (
    <aside
      className={`group/nav shrink-0 sticky top-0 self-start h-[calc(100vh-3rem)] border-r-2 border-pixel-border flex flex-col transition-[width] duration-200 ease-out backdrop-blur-md ${
        expanded ? "w-48" : "w-[58px]"
      }`}
      style={{
        background:
          "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.92), rgb(var(--pixel-bg-rgb)/0.86))",
        boxShadow: "1px 0 0 rgb(var(--pixel-border-rgb)/0.4), 8px 0 24px rgba(0,0,0,0.35)",
      }}
    >
      {/* Logo / brand */}
      <Link
        href="/"
        title="SUPER POLYMARKET BROS"
        className="flex items-center gap-2.5 h-12 px-4 border-b-2 border-pixel-border shrink-0 text-pixel-white hover:bg-pixel-white/5 transition-colors"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" className="shrink-0 glow-green text-green-400 transition-transform duration-200 group-hover/nav:scale-105">
          <path d="M12 2L22 12L12 22L2 12Z" stroke="currentColor" strokeWidth="2.5" fill="currentColor" fillOpacity="0.18" />
          <circle cx="12" cy="12" r="3" fill="currentColor" />
        </svg>
        {expanded && (
          <span className="text-[13px] font-bold tracking-[0.2em] whitespace-nowrap glow-green text-green-400">
            SPB
          </span>
        )}
      </Link>

      {/* Nav */}
      <nav className="flex flex-col py-2 gap-1 px-1.5">
        {NAV.map((t) => {
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
              {/* Active accent bar with glow */}
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
        })}
      </nav>

      {/* Expand / collapse toggle pinned to the bottom */}
      <button
        onClick={toggle}
        title={expanded ? "Collapse sidebar" : "Expand sidebar"}
        className="mt-auto flex items-center gap-3 px-4 py-3 border-t-2 border-pixel-border text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06] transition-colors"
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
