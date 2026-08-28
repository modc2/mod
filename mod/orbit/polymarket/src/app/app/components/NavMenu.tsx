"use client";

// Global nav in the top header — replaces the old LeftNav rail. Every
// destination (STRAT / TRADERS / MARKETS / DOCS) is laid out inline as tabs
// along the header, to the RIGHT of the active-strat readout. There is no
// dropdown fallback: when the header runs out of room the labels drop and the
// tabs become icons, which still fit on a phone and still show you where you
// are — a menu you have to open to learn what page you're on is worse.
// The global fills tape (/trades) is no longer a nav destination — fills live
// in the STRAT page's TRADES tab. Wallet + trading-wallet chrome is NOT here
// either — it's a WALLET tab inside the STRAT page.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode } from "react";
import { useEmbedded } from "../lib/embedded";

const ICON = "w-[16px] h-[16px] shrink-0";

// Below this width the tab labels drop and the row is icons only — enough for
// four destinations plus the strat readout on a phone. `nav-tab-label` lets
// globals.css drop them earlier when the strat sidebar is docked: a media
// query measures the VIEWPORT, and a docked column eats 340px of the header
// it can't see (at 1024px that left 684px and the tabs ran under the wallet
// chip).
const LABEL = "nav-tab-label hidden min-[900px]:inline";

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
    href: "/markets",
    label: "MARKETS",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="4" width="7" height="7" rx="1" />
        <rect x="14" y="4" width="7" height="7" rx="1" />
        <rect x="3" y="15" width="7" height="5" rx="1" />
        <rect x="14" y="15" width="7" height="5" rx="1" />
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

  // Split-screen iframe panes stay lightweight — no global nav.
  if (embedded) return null;

  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");

  return (
    <nav className="flex items-center gap-0.5 min-w-0">
      {/* The mark is the console's badge, not a button — it opened the nav
          menu back when there was one. */}
      <span
        className="hidden min-[480px]:grid place-items-center w-[22px] h-[22px] rounded-[6px] bg-green-400 shrink-0 mx-1.5"
        style={{ boxShadow: "0 0 12px rgba(74,222,128,0.55), inset 0 1px 0 rgba(255,255,255,0.4)" }}
      >
        <span className="w-[7px] h-[7px] rounded-[2px] bg-pixel-black" />
      </span>
      {NAV.map((t) => {
        const active = isActive(t.href);
        return (
          <Link
            key={t.href}
            href={t.href}
            title={t.label}
            className={`relative flex items-center gap-2 px-2.5 py-1.5 rounded-[var(--radius-sm)] transition-colors ${
              active
                ? "text-green-400 bg-green-400/10"
                : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
            }`}
          >
            <span className={active ? "glow-green" : ""}>{t.icon}</span>
            <span className={`${LABEL} text-[12px] font-semibold tracking-[0.14em] whitespace-nowrap`}>
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
  );
}
