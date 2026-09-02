"use client";

// The one workspace both /backtest and /live render, pinned to one screen.
//
// It was two near-identical page files with a mode flag between them, which is
// exactly the shape that drifts: an empty-bench warning fixed on one tab and
// not the other, a header that says one thing on TEST and another on LIVE.
// One component, one `mode` prop, and the routes are three lines each.
//
// The strat is read from the store, never the URL — the bench you built on
// TRADERS is the bench this runs. `key` on its id remounts everything below
// when the desk changes so no cached series survives the swap.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import TopBar from "./TopBar";
import CopyIndex from "./CopyIndex";
import { ensureActiveStrat } from "../lib/activeStrat";
import { isTraderIndex } from "../lib/traderIndex";
import type { SavedIndex } from "../lib/types";

type Mode = "BACKTEST" | "LIVE";

const COPY: Record<Mode, { label: string; blurb: string; accent: string }> = {
  BACKTEST: {
    label: "BACKTEST",
    blurb: "replaying your bench against real past trades — simulated money",
    accent: "text-amber-400",
  },
  LIVE: {
    label: "LIVE",
    blurb: "running your bench against the live book",
    accent: "text-cyan-300",
  },
};

export default function Workspace({ mode }: { mode: Mode }) {
  const [strat, setStrat] = useState<SavedIndex | null>(null);

  const refresh = useCallback(() => {
    // localStorage-backed, so it can only run in the browser — an effect,
    // never a render.
    setStrat(ensureActiveStrat());
  }, []);

  useEffect(() => {
    refresh();
    window.addEventListener("strat-updated", refresh);
    return () => window.removeEventListener("strat-updated", refresh);
  }, [refresh]);

  const bench = strat ? strat.traders.filter((t) => t.enabled !== false).length : 0;
  const copy = COPY[mode];

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar showSearch={false} />
      <div className="p-4 space-y-3">
        {/* One line: which screen, on which bench. No strat picker — the copy
            book in the side panel is the one place strats are switched. */}
        <div className="flex flex-wrap items-baseline gap-2 text-[12px] font-mono">
          <span className={`${copy.accent} font-bold tracking-[0.16em]`}>{copy.label}</span>
          <span className="text-pixel-gray-light">{copy.blurb}</span>
          <span className="text-pixel-border/60">·</span>
          <Link href="/traders" className="text-pixel-gray hover:text-pixel-white">
            {bench} {bench === 1 ? "trader" : "traders"}
            {strat && isTraderIndex(strat) ? " · sized to your capital" : ""} →
          </Link>
        </div>

        {strat && bench === 0 && !strat.momentum && (
          <div className="pixel-panel p-4 text-[12px] leading-relaxed text-pixel-gray-light">
            <span className="text-pixel-white tracking-[0.14em]">NOBODY ON THE BENCH YET. </span>
            You copy traders, and you have none — so a backtest replays an empty tape and a live
            session sits idle.{" "}
            <Link href="/traders" className="text-pixel-green underline">
              Pick some traders →
            </Link>
          </div>
        )}

        {strat ? (
          <CopyIndex key={strat.id} searchFilter="" forcedMode={mode} />
        ) : (
          <p className="text-[12px] text-pixel-gray font-mono">loading…</p>
        )}
      </div>
    </div>
  );
}
