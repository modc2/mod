"use client";

// GLOBAL NAV — the badge, and nothing else.
//
// The TRADERS · BACKTEST · LIVE tab row is GONE (2026-09-08, user: "don't
// have this"). The console is ONE page now — the trader board — and what the
// tabs used to switch between lives in the SIDE PANEL's rail instead
// (components/UserSidebar.tsx: INDEX · BACKTEST · LIVE). Testing and running
// your bench are things you do beside the board, not places you leave it for.
//
// Old destinations, where they went:
//
//   TRADERS         →  the page itself; / and /traders render it
//   BACKTEST, LIVE  →  side-panel tabs; the routes forward
//                      (backtest/page.tsx, live/page.tsx →
//                      requestSidebarTab)
//   STRATS          →  the STRATS block on the panel's INDEX tab
//   WALLET / MONEY  →  the MONEY block on the panel's INDEX tab
//
// What's left here is the console's mark, so the header still says whose
// screen this is.

import { useEmbedded } from "../lib/embedded";

export default function NavMenu() {
  const embedded = useEmbedded();

  // Split-screen iframe panes stay lightweight — no global nav.
  if (embedded) return null;

  return (
    <nav className="flex items-center gap-2 min-w-0">
      {/* The mark is the console's badge, not a button. */}
      <span
        className="hidden min-[480px]:grid place-items-center w-[22px] h-[22px] rounded-[6px] bg-green-400 shrink-0 mx-1.5"
        style={{ boxShadow: "0 0 12px rgba(74,222,128,0.55), inset 0 1px 0 rgba(255,255,255,0.4)" }}
      >
        <span className="w-[7px] h-[7px] rounded-[2px] bg-pixel-black" />
      </span>
      <span className="hidden min-[560px]:inline text-[12px] font-semibold tracking-[0.18em] text-pixel-white whitespace-nowrap">
        TRADERS
      </span>
    </nav>
  );
}
