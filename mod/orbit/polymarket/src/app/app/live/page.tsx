"use client";

// /live — LIVE. The third of the console's three tabs.
//
// The console is: pick traders, test them, run them. This is RUN — the engine
// against the real book, over whichever strat is on the desk. Its twin is
// /backtest, which is the same workspace replaying history; they share one
// component (CopyIndex) on purpose, because a backtest that lives somewhere
// else from the engine is how a console ends up showing a number for a
// strategy it isn't running. The route pins the screen via `forcedMode`, so
// there is no TEST|LIVE switch inside the panel — the nav is that switch.
//
// The strat comes from the store, not from the URL (lib/activeStrat.ts).
// `key` on the strat id remounts the whole workspace when the desk changes,
// so nothing — a half-finished backtest, a subtab position, a chart's cached
// series — survives from the previous strat into this one.
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

import { Suspense } from "react";

import Workspace from "../components/Workspace";

export default function LiveWorkspacePage() {
  return (
    <Suspense>
      <Workspace mode="LIVE" />
    </Suspense>
  );
}
