"use client";

// GLOBAL NAV — the badge and the console's MAIN TABS.
//
// History, because this row has flip-flopped: the TRADERS · BACKTEST · LIVE
// top tabs were removed 2026-09-08 ("don't have this") and the screens moved
// into the side panel's rail. On 2026-09-09 the user pulled STRATS back OUT
// of that rail ("this should be in the main tabs under strats tab in the
// main header") — building and sharing strats is a destination, not a
// drawer. So the header now carries exactly two tabs:
//
//   TRADERS  →  the board (/traders); / redirects there
//   STRATS   →  the strat manager page (/strats): the MY STRATS cards with
//               live money + backtest stats, the factory + lab, the gallery
//
// BACKTEST and LIVE stay in the side panel's rail (UserSidebar) — testing
// and running the bench happen BESIDE whatever page you're on. Don't re-add
// them here.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEmbedded } from "../lib/embedded";

const MAIN_TABS: { label: string; href: string }[] = [
  { label: "TRADERS", href: "/traders" },
  { label: "STRATS", href: "/strats" },
];

export default function NavMenu() {
  const embedded = useEmbedded();
  const pathname = usePathname() || "/";

  // Split-screen iframe panes stay lightweight — no global nav.
  if (embedded) return null;

  return (
    <nav className="flex items-center gap-1 min-w-0">
      {/* The mark is the console's badge, not a button. */}
      <span
        className="hidden min-[480px]:grid place-items-center w-[22px] h-[22px] rounded-[6px] bg-green-400 shrink-0 mx-1.5"
        style={{ boxShadow: "0 0 12px rgba(74,222,128,0.55), inset 0 1px 0 rgba(255,255,255,0.4)" }}
      >
        <span className="w-[7px] h-[7px] rounded-[2px] bg-pixel-black" />
      </span>
      {MAIN_TABS.map((t) => {
        // Everything that isn't the strat manager is a view of the board
        // (profiles, markets, the copy desk), so TRADERS is the default lit tab.
        const active = t.href === "/strats" ? pathname.startsWith("/strats") : !pathname.startsWith("/strats");
        return (
          <Link
            key={t.label}
            href={t.href}
            className={`relative px-2.5 py-1.5 text-[11px] font-semibold tracking-[0.16em] whitespace-nowrap rounded-[var(--radius-sm)] transition-colors ${
              active
                ? "text-green-400 bg-green-400/10"
                : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
            }`}
          >
            {t.label}
            <span
              className={`absolute left-2 right-2 -bottom-[1px] h-[2px] rounded-full transition-opacity ${
                active ? "bg-green-400 opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]" : "opacity-0"
              }`}
            />
          </Link>
        );
      })}
    </nav>
  );
}
