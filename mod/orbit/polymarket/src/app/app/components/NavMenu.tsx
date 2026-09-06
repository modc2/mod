"use client";

// GLOBAL NAV — three tabs, and that is the whole console.
//
//   TRADERS   pick who to copy      /traders
//   BACKTEST  test them on history  /backtest
//   LIVE      run them for real     /live
//
// They are one sentence read left to right, which is why they are in that
// order and why there is no fourth. Everything that was ever a tab is now
// either a view of one of these three or a place money lives:
//
//   STRATS          →  gone. A strat is capital + a bench; capital is edited
//                      in the workspace's SETTINGS panel and the bench IS the
//                      TRADERS tab, so the page only ever restated the two
//                      things you were already setting elsewhere. Switching
//                      between saved strats is the copy book in the side
//                      panel. /strats forwards (see strats/page.tsx).
//   COPY / RESULTS  →  folded into BACKTEST and LIVE (the DESK subtab under
//                      LIVE is the fills tape, mine against theirs)
//   MARKETS         →  a browser, not a decision; reachable from a trade row
//   DOCS            →  /docs, a link, not a destination you navigate to while
//                      trading
//   WALLET          →  the SIDE PANEL. Topping up and taking money out are
//                      not a page you visit; they are a drawer you open from
//                      wherever you are. See components/UserSidebar.tsx.
//
// BACKTEST and LIVE are the SAME workspace (components/Workspace.tsx) pinned
// to one screen each. That is why they are two tabs and not one tab with a
// toggle: the URL should answer "am I looking at a replay or at real money".
//
// Laid out inline as tabs; when the header runs out of room the labels drop
// and the tabs become icons, which still fit on a phone and still show you
// where you are. There is no dropdown fallback — a menu you have to open to
// learn what page you're on is worse than three glyphs.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode } from "react";
import { useEmbedded } from "../lib/embedded";

const ICON = "w-[16px] h-[16px] shrink-0";

// Below this width the tab labels drop and the row is icons only.
// `nav-tab-label` lets globals.css drop them earlier when the side panel is
// docked: a media query measures the VIEWPORT, and a docked column eats 340px
// of the header it can't see.
const LABEL = "nav-tab-label hidden min-[760px]:inline";

interface NavItem {
  href: string;
  label: string;
  /** Prefixes that also light this tab up — a drill-down belongs to its
      parent. Listed rather than inferred so `/copy/<leader>` can belong to
      TEST & LIVE without `/copy` having to be a tab of its own. */
  owns?: string[];
  icon: ReactNode;
}

const NAV: NavItem[] = [
  {
    // The front door: who goes on the bench.
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
    // Replay that bench against history, on simulated money.
    href: "/backtest",
    label: "BACKTEST",
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M3 3v6h6" />
        <path d="M3.5 9a9 9 0 103-6.3L3 6" />
        <path d="M12 7v5l3.5 2" />
      </svg>
    ),
  },
  {
    // Run it against the book. Per-leader sessions (/copy/<address>) and the
    // fills tape (/trades) are drill-downs of this screen, not tabs.
    href: "/live",
    label: "LIVE",
    owns: ["/copy", "/trades"],
    icon: (
      <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M3 17l5-6 4 3 5-7" />
        <path d="M17 7h4v4" />
        <path d="M3 21h18" />
      </svg>
    ),
  },
];

export default function NavMenu() {
  const pathname = usePathname() || "";
  const embedded = useEmbedded();

  // Split-screen iframe panes stay lightweight — no global nav.
  if (embedded) return null;

  const isActive = (t: NavItem) => {
    if (pathname === t.href) return true;
    if (pathname.startsWith(t.href + "/")) return true;
    return (t.owns ?? []).some((p) => pathname === p || pathname.startsWith(p + "/"));
  };

  return (
    <nav className="flex items-center gap-0.5 min-w-0">
      {/* The mark is the console's badge, not a button. */}
      <span
        className="hidden min-[480px]:grid place-items-center w-[22px] h-[22px] rounded-[6px] bg-green-400 shrink-0 mx-1.5"
        style={{ boxShadow: "0 0 12px rgba(74,222,128,0.55), inset 0 1px 0 rgba(255,255,255,0.4)" }}
      >
        <span className="w-[7px] h-[7px] rounded-[2px] bg-pixel-black" />
      </span>
      {NAV.map((t) => {
        const active = isActive(t);
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
