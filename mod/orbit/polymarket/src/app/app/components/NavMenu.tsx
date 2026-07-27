"use client";

// Global nav in the top header — replaces the old LeftNav rail. On wide
// viewports every destination (STRAT / TRADERS / TRADES / DOCS) is laid out
// inline as tabs to the RIGHT of the strat picker; below COLLAPSE_BP the row
// folds into the square-mark dropdown so it never fights the search box.
// Market detail pages (/markets/[slug]) are still reachable via trade rows,
// just not from the nav. Wallet + trading-wallet chrome is NOT here — it's a
// WALLET tab inside the STRAT page.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useEmbedded } from "../lib/embedded";

const ICON = "w-[16px] h-[16px] shrink-0";

// Below this viewport width the inline tab row collapses into the dropdown.
// Tailwind needs the variants as static strings: keep these two in sync.
const INLINE = "hidden min-[1100px]:flex"; // inline tab row
const FOLDED = "min-[1100px]:hidden"; // dropdown fallback

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

const NAV: NavItem[] = [
  {
    href: "/strats",
    label: "STRAT",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="8" />
        <circle cx="12" cy="12" r="3" />
        <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
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

export default function NavMenu() {
  const pathname = usePathname() || "";
  const embedded = useEmbedded();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape.
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

  // Split-screen iframe panes stay lightweight — no global nav.
  if (embedded) return null;

  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");
  const current = NAV.find((t) => isActive(t.href));

  const mark = (
    <span
      className="grid place-items-center w-[22px] h-[22px] rounded-[6px] bg-green-400 shrink-0"
      style={{ boxShadow: "0 0 12px rgba(74,222,128,0.55), inset 0 1px 0 rgba(255,255,255,0.4)" }}
    >
      <span className="w-[7px] h-[7px] rounded-[2px] bg-pixel-black" />
    </span>
  );

  return (
    <>
      {/* ── Wide viewports: every destination inline along the top ── */}
      <nav className={`${INLINE} items-center gap-0.5`}>
        <span className="px-1.5">{mark}</span>
        {NAV.map((t) => {
          const active = isActive(t.href);
          return (
            <Link
              key={t.href}
              href={t.href}
              className={`relative flex items-center gap-2 px-2.5 py-1.5 rounded-[var(--radius-sm)] transition-colors ${
                active
                  ? "text-green-400 bg-green-400/10"
                  : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
              }`}
            >
              <span className={active ? "glow-green" : ""}>{t.icon}</span>
              <span className="text-[12px] font-semibold tracking-[0.14em] whitespace-nowrap">
                {t.label}
              </span>
              <span
                className={`absolute left-2.5 right-2.5 -bottom-[1px] h-[2px] rounded-full bg-green-400 transition-opacity duration-200 ${
                  active ? "opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]" : "opacity-0"
                }`}
              />
            </Link>
          );
        })}
      </nav>

      {/* ── Limited space: fold into the square-mark dropdown ── */}
      <div ref={rootRef} className={`relative ${FOLDED}`}>
      <button
        onClick={() => setOpen((v) => !v)}
        title="Navigate"
        aria-expanded={open}
        className={`flex items-center gap-2.5 px-2 py-1.5 rounded-[var(--radius-sm)] transition-colors ${
          open ? "bg-pixel-white/[0.06]" : "hover:bg-pixel-white/[0.06]"
        }`}
      >
        {mark}
        {/* On tiny screens the mark + caret alone identify the menu — the
            page label would shove the wallet chip off the bar. */}
        <span className="hidden min-[480px]:inline text-[12.5px] font-semibold tracking-[0.14em] text-pixel-white whitespace-nowrap">
          {current?.label ?? "MENU"}
        </span>
        <span
          className={`text-[10px] text-pixel-gray transition-transform duration-150 ${open ? "rotate-180" : ""}`}
        >
          ▾
        </span>
      </button>

      {open && (
        <nav
          className="absolute left-0 top-full mt-1.5 z-50 min-w-[190px] rounded-[var(--radius-sm)] backdrop-blur-md p-1.5 flex flex-col gap-0.5"
          style={{
            background:
              "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.96), rgb(var(--pixel-bg-rgb)/0.94))",
            border: "1px solid var(--border)",
            boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
          }}
        >
          {NAV.map((t) => {
            const active = isActive(t.href);
            return (
              <Link
                key={t.href}
                href={t.href}
                onClick={() => setOpen(false)}
                className={`relative flex items-center gap-3 rounded-[var(--radius-sm)] px-3 py-2 transition-colors ${
                  active
                    ? "text-green-400 bg-green-400/10"
                    : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
                }`}
              >
                <span
                  className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] rounded-full bg-green-400 transition-all duration-200 ${
                    active ? "h-5 opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]" : "h-0 opacity-0"
                  }`}
                />
                <span className={active ? "glow-green" : ""}>{t.icon}</span>
                <span className="text-[12px] font-semibold tracking-[0.14em] whitespace-nowrap">
                  {t.label}
                </span>
              </Link>
            );
          })}
        </nav>
      )}
      </div>
    </>
  );
}
