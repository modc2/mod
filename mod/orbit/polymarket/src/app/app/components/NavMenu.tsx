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
//
// The MARK is the agent's handle. It used to be inert badge ("not a button"),
// while the console agent was a robot icon in the crowded top-right cluster.
// Now clicking the logo slides the agent column out of the LEFT edge and
// clicking it again puts it away — the one piece of chrome that's on every
// page, holding the one thing that explains every page. It stays visible at
// every width (it was ≥480px-only as a badge) because it's the only handle
// the agent has.

import { useEffect, useLayoutEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEmbedded } from "../lib/embedded";
import { TOGGLE_AGENT_EVENT, AGENT_STATE_EVENT } from "./AgentShell";

const MAIN_TABS: { label: string; href: string }[] = [
  { label: "TRADERS", href: "/traders" },
  { label: "STRATS", href: "/strats" },
];

interface NavMenuProps {
  /** Agent column state — owned by TopBar, since the logo toggles it. */
  agentOpen?: boolean;
  onToggleAgent?: () => void;
}

export default function NavMenu({ agentOpen = false, onToggleAgent }: NavMenuProps) {
  const embedded = useEmbedded();
  const pathname = usePathname() || "/";

  // Split-screen iframe panes stay lightweight — no global nav.
  if (embedded) return null;

  return (
    <nav className="flex items-center gap-1 min-w-0">
      {/* The mark IS the agent toggle — see the note at the top. */}
      <button
        type="button"
        onClick={onToggleAgent}
        aria-expanded={agentOpen}
        aria-label="Console agent"
        title={`${agentOpen ? "Hide" : "Ask"} the console agent — where things are and how this console works`}
        className="grid place-items-center w-[22px] h-[22px] rounded-[6px] bg-green-400 shrink-0 mx-1.5 transition-transform hover:scale-110 active:scale-95"
        style={{
          boxShadow: agentOpen
            ? "0 0 0 2px rgb(var(--pixel-bg-rgb)), 0 0 0 3.5px rgba(74,222,128,0.9), 0 0 16px rgba(74,222,128,0.75), inset 0 1px 0 rgba(255,255,255,0.4)"
            : "0 0 12px rgba(74,222,128,0.55), inset 0 1px 0 rgba(255,255,255,0.4)",
        }}
      >
        {/* Open, the dot squares off into the agent's little head — the mark
            tells you which state you're in without a second glyph. */}
        <span
          className={`bg-pixel-black transition-all ${
            agentOpen ? "w-[11px] h-[9px] rounded-[2px]" : "w-[7px] h-[7px] rounded-[2px]"
          }`}
        />
      </button>
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
